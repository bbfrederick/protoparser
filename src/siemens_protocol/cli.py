"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .debug import write_debug
from .diff import diff_protocols, diff_scans
from .flatten import conflicts
from .model import Protocol
from .pipeline import (
    OCR_ALWAYS,
    OCR_AUTO,
    OCR_NEVER,
    ParseOptions,
    ParseResult,
    parse_document,
)
from .policy import PolicyError, PolicyReport, check_protocol, load_policy
from .profiles import REGISTRY
from .report import render_protocol, render_scan
from .vocabsuggest import suggest_aliases, verify_aliases
from .vocabulary import available, check, load_vocabulary

#: Suffixes treated as PDFs when walking a directory.
PDF_SUFFIXES = (".pdf", ".PDF")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns
    -------
    argparse.ArgumentParser
        A parser with the ``parse`` and ``versions`` subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="siemens-protocol",
        description="Parse Siemens MR protocol PDF exports into hierarchical JSON.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="parse a protocol PDF, or every PDF in a directory")
    parse_cmd.add_argument("input", help="a PDF file, or a directory of PDFs")
    parse_cmd.add_argument("--out", help="write JSON here (default: alongside the input, .json)")
    parse_cmd.add_argument(
        "--version",
        default="auto",
        choices=["auto", *REGISTRY.names()],
        help="force a version profile (default: auto)",
    )
    parse_cmd.add_argument(
        "--ocr",
        default=OCR_AUTO,
        choices=[OCR_AUTO, OCR_ALWAYS, OCR_NEVER],
        help="control the OCR fallback (default: auto)",
    )
    parse_cmd.add_argument("--dpi", type=int, default=300, help="rasterization DPI for OCR pages")
    flat = parse_cmd.add_mutually_exclusive_group()
    flat.add_argument(
        "--flatten",
        dest="flatten",
        action="store_true",
        default=True,
        help="include the flattened per-scan view (default)",
    )
    flat.add_argument(
        "--no-flatten",
        dest="flatten",
        action="store_false",
        help="omit the flattened per-scan view",
    )
    parse_cmd.add_argument(
        "--emit-debug",
        metavar="PATH",
        help="dump per-span geometry for tuning a new version",
    )
    parse_cmd.add_argument(
        "--stdout", action="store_true", help="write JSON to stdout instead of a file"
    )
    parse_cmd.add_argument("--quiet", action="store_true", help="suppress the summary")

    diff_cmd = sub.add_parser(
        "diff",
        help="compare two protocols, or two scans",
        description=(
            "Compare two protocols scan by scan, or compare two individual scans. "
            "Give one input plus two --scan names to compare scans within one "
            "protocol; give two inputs to compare across files."
        ),
    )
    diff_cmd.add_argument("left", help="a PDF or a previously parsed JSON file")
    diff_cmd.add_argument(
        "right",
        nargs="?",
        help="a second PDF or JSON; omit to compare two scans within LEFT",
    )
    diff_cmd.add_argument(
        "--scan",
        action="append",
        metavar="NAME",
        help=(
            "scan to compare, by name or zero-based index; give once to use the "
            "same name on both sides, or twice for the left and right scans"
        ),
    )
    diff_cmd.add_argument(
        "--version",
        default="auto",
        choices=["auto", *REGISTRY.names()],
        help="force a version profile for any PDF input (default: auto)",
    )
    diff_cmd.add_argument(
        "--exact-keys",
        action="store_true",
        help="do not match relabeled keys; compare key spellings literally",
    )
    diff_cmd.add_argument(
        "--no-vocabulary",
        action="store_true",
        help="ignore the per-release vocabularies that map renamed parameters",
    )
    diff_cmd.add_argument(
        "--vocabulary",
        metavar="DIR",
        help="a directory of vocabulary JSON files overlaying the shipped ones",
    )
    diff_cmd.add_argument(
        "--show-cosmetic",
        action="store_true",
        help="list relabeled, recased and reformatted differences individually",
    )
    diff_cmd.add_argument(
        "--show-identical", action="store_true", help="include scans with no differences"
    )
    diff_cmd.add_argument("--json", action="store_true", help="emit the comparison as JSON")
    diff_cmd.add_argument("--out", help="write the report here instead of stdout")

    check_cmd = sub.add_parser(
        "check",
        help="check a protocol's parameters against preferred values",
        description=(
            "Check each scan against a policy of preferred values. A rule applies "
            "only where its parameter is present, so a rule about multiband "
            "excitation stays silent on scans that never had the setting."
        ),
    )
    check_cmd.add_argument("input", help="a PDF, a parsed JSON file, or a directory of PDFs")
    check_cmd.add_argument(
        "--policy",
        default="default",
        metavar="NAME",
        help="a policy name or a path to a policy JSON file (default: default)",
    )
    check_cmd.add_argument(
        "--policy-dir",
        metavar="DIR",
        help="a directory of policies searched before the shipped ones",
    )
    check_cmd.add_argument(
        "--version",
        default="auto",
        choices=["auto", *REGISTRY.names()],
        help="force a version profile for any PDF input (default: auto)",
    )
    check_cmd.add_argument(
        "--warnings-ok",
        action="store_true",
        help="exit zero when only warnings were found",
    )
    check_cmd.add_argument("--json", action="store_true", help="emit the findings as JSON")
    check_cmd.add_argument("--out", help="write the report here instead of stdout")
    check_cmd.add_argument("--quiet", action="store_true", help="print only violations")

    vocab_cmd = sub.add_parser(
        "vocab",
        help="inspect the per-release parameter vocabularies",
        description=(
            "Vocabularies map each release's parameter labels onto shared canonical "
            "names, so a parameter the vendor renamed is still recognized as the "
            "same one. They are curated by hand: an incorrect entry hides a real "
            "difference, so 'suggest' proposes candidates with evidence rather than "
            "applying them."
        ),
    )
    vocab_action = vocab_cmd.add_subparsers(dest="vocab_command", required=True)

    vocab_list = vocab_action.add_parser("list", help="show the mappings for a release")
    vocab_list.add_argument("version", nargs="?", help="a release name; omit for all")
    vocab_list.add_argument(
        "--canonical", metavar="NAME", help="show what each release calls this canonical name"
    )
    vocab_list.add_argument("--vocabulary", metavar="DIR", help="an overlay directory")

    vocab_check = vocab_action.add_parser(
        "check", help="validate the vocabularies against each other and against real exports"
    )
    vocab_check.add_argument("--vocabulary", metavar="DIR", help="an overlay directory")
    vocab_check.add_argument(
        "--against",
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        help="two exports of one protocol from different releases, to check mappings against",
    )

    vocab_suggest = vocab_action.add_parser(
        "suggest", help="propose candidate mappings from a matched pair of exports"
    )
    vocab_suggest.add_argument("left", help="an export of one release")
    vocab_suggest.add_argument("right", help="the same protocol from another release")
    vocab_suggest.add_argument(
        "--min-support",
        type=int,
        default=8,
        help="ignore candidates seen in fewer scans than this (default: 8)",
    )
    vocab_suggest.add_argument("--vocabulary", metavar="DIR", help="an overlay directory")

    versions_cmd = sub.add_parser("versions", help="list the known version profiles")
    versions_cmd.set_defaults(command="versions")

    return parser


def _inputs(target: str) -> list[str]:
    """Every PDF to parse for a given command-line target.

    Parameters
    ----------
    target : str
        A PDF path, or a directory to walk.

    Returns
    -------
    list of str
        Paths in sorted order. A single-element list for a file target.
    """
    if os.path.isdir(target):
        found = [
            os.path.join(root, name)
            for root, _dirs, files in os.walk(target)
            for name in sorted(files)
            if name.endswith(PDF_SUFFIXES) and not name.startswith(".")
        ]
        return sorted(found)
    return [target]


def _batch_relative_name(pdf: str, root: str | None) -> str:
    """The output file name for one PDF of a batch, relative to the out dir.

    The walked tree's shape is preserved rather than flattened. Flattening
    loses files outright: an ``examples/`` tree holding the same protocol
    exported from two software versions has the same base name in two
    subdirectories, and one silently overwrote the other. For a flat input
    directory this returns exactly the base name, so nothing changes there.

    Parameters
    ----------
    pdf : str
        Path of the PDF being parsed.
    root : str or None
        The directory the batch is walking. ``None`` falls back to the base
        name alone.

    Returns
    -------
    str
        A relative path ending in ``.json``, using the platform separator.
    """
    if root:
        try:
            relative = os.path.relpath(pdf, root)
        except ValueError:  # different drives on Windows
            relative = os.path.basename(pdf)
        if not relative.startswith(os.pardir):
            return os.path.splitext(relative)[0] + ".json"
    return os.path.splitext(os.path.basename(pdf))[0] + ".json"


def _output_path(pdf: str, out: str | None, batch: bool, root: str | None = None) -> str:
    """Where one file's JSON should be written.

    Parameters
    ----------
    pdf : str
        Path of the PDF being parsed.
    out : str or None
        The ``--out`` value: a file in single mode, a directory in batch mode.
    batch : bool
        Whether this run is over a directory.
    root : str or None, optional
        In batch mode, the directory being walked, so the tree's shape is
        mirrored under ``out``. Defaults to ``None``.

    Returns
    -------
    str
        The destination path. Creates the output directory in batch mode,
        including any subdirectory the mirrored tree needs.
    """
    if out and batch:
        destination = os.path.join(out, _batch_relative_name(pdf, root))
        os.makedirs(os.path.dirname(destination) or out, exist_ok=True)
        return destination
    if out:
        return out
    return os.path.splitext(pdf)[0] + ".json"


def _summarize(protocol: Protocol, path: str) -> str:
    """One-line summary of a parsed file, for the console.

    Parameters
    ----------
    protocol : Protocol
        The parsed document.
    path : str
        Where its JSON was written.

    Returns
    -------
    str
        Version, scan count, page count, OCR pages and conflict count.
    """
    n_conflicts = sum(len(conflicts(scan.to_dict()["flat"])) for scan in protocol.scans)
    parts = [
        f"{os.path.basename(protocol.source_file)}: {protocol.software_version}",
        f"{len(protocol.scans)} scans",
        f"{protocol.page_count} pages",
    ]
    if protocol.ocr_pages:
        parts.append(f"{len(protocol.ocr_pages)} OCR pages")
    if n_conflicts:
        parts.append(f"{n_conflicts} cross-section conflicts")
    return " | ".join(parts) + f" -> {path}"


def _write_outputs(
    result: ParseResult,
    args: argparse.Namespace,
    pdf: str,
    batch: bool,
    root: str | None = None,
) -> str:
    """Write one file's JSON, and its debug dump when asked for.

    Parameters
    ----------
    result : ParseResult
        The parse result to serialize.
    args : argparse.Namespace
        Parsed command-line arguments.
    pdf : str
        Path of the PDF that was parsed.
    batch : bool
        Whether this run is over a directory.
    root : str or None, optional
        In batch mode, the directory being walked. Defaults to ``None``.

    Returns
    -------
    str
        Where the JSON went, or ``"<stdout>"``.

    Raises
    ------
    OSError
        If a destination cannot be written.
    """
    payload = result.protocol.to_json(include_flat=args.flatten)
    if args.stdout and not batch:
        print(payload)
        out_path = "<stdout>"
    else:
        out_path = _output_path(pdf, args.out, batch, root)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")

    if args.emit_debug:
        debug_path = args.emit_debug
        if batch:
            os.makedirs(debug_path, exist_ok=True)
            relative = _batch_relative_name(pdf, root)
            debug_path = os.path.join(debug_path, relative[: -len(".json")] + ".debug.json")
            os.makedirs(os.path.dirname(debug_path), exist_ok=True)
        write_debug(debug_path, result)
    return out_path


def _list_versions() -> int:
    """Print the registered version profiles.

    Returns
    -------
    int
        Always ``0``.
    """
    for name in REGISTRY.names():
        profile = REGISTRY.get(name)
        native = "native text" if profile.native_text_expected else "OCR expected"
        print(f"{name:8s} {native}")
    return 0


def _load_protocol(path: str, version: str) -> dict:
    """Load a protocol from a PDF, or from JSON this tool wrote earlier.

    Accepting JSON means a protocol can be parsed once and compared many
    times, which matters because parsing dominates the runtime.

    Parameters
    ----------
    path : str
        A ``.pdf`` to parse, or a ``.json`` file to read.
    version : str
        Version profile to force for a PDF, or ``"auto"``.

    Returns
    -------
    dict
        The serialized protocol, always with the flattened view present,
        since that is what the comparison reads.

    Raises
    ------
    ValueError
        If a JSON input carries no flattened view.
    """
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        for scan in payload.get("scans", []):
            if "flat" not in scan:
                raise ValueError(
                    f"{path} was written with --no-flatten; diff needs the flattened view"
                )
        return payload
    result = parse_document(path, ParseOptions(version=version))
    return result.protocol.to_dict(include_flat=True)


def _select_scan(protocol: dict, wanted: str, label: str) -> dict:
    """Find one scan of a protocol by name or index.

    Parameters
    ----------
    protocol : dict
        A serialized protocol.
    wanted : str
        A scan name, or a zero-based index.
    label : str
        Which side this is, for the error message.

    Returns
    -------
    dict
        The serialized scan.

    Raises
    ------
    ValueError
        If no scan matches, listing what is available.
    """
    scans = protocol.get("scans", [])
    if wanted.isdigit():
        index = int(wanted)
        if 0 <= index < len(scans):
            return scans[index]
        raise ValueError(f"{label}: no scan at index {index}; the file has {len(scans)}")
    for scan in scans:
        if scan.get("name") == wanted:
            return scan
    matches = [s.get("name", "") for s in scans if wanted.lower() in s.get("name", "").lower()]
    hint = f"; did you mean {matches[0]!r}?" if matches else ""
    raise ValueError(f"{label}: no scan named {wanted!r}{hint}")


def _run_diff(args: argparse.Namespace) -> int:
    """Run the ``diff`` subcommand.

    Two modes. With two inputs the protocols are compared scan by scan, or a
    single named scan from each when ``--scan`` is given. With one input and
    two ``--scan`` names, two scans of that protocol are compared against
    each other.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        ``0`` when no substantive difference was found, ``1`` when there were
        differences or the request could not be satisfied.
    """
    wanted = args.scan or []
    if len(wanted) > 2:
        print("--scan may be given at most twice", file=sys.stderr)
        return 1
    if args.right is None and len(wanted) != 2:
        print(
            "comparing within one file needs two --scan names; "
            "pass a second file to compare protocols",
            file=sys.stderr,
        )
        return 1

    try:
        left = _load_protocol(args.left, args.version)
        right = left if args.right is None else _load_protocol(args.right, args.version)
    except (OSError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    normalize = not args.exact_keys
    try:
        if wanted:
            name_left = wanted[0]
            name_right = wanted[1] if len(wanted) == 2 else wanted[0]
            vocabularies = (None, None)
            if normalize and not args.no_vocabulary:
                vocabularies = (
                    load_vocabulary(left.get("software_version") or "", args.vocabulary),
                    load_vocabulary(right.get("software_version") or "", args.vocabulary),
                )
            scan = diff_scans(
                _select_scan(left, name_left, args.left),
                _select_scan(right, name_right, args.right or args.left),
                normalize=normalize,
                vocabulary_left=vocabularies[0],
                vocabulary_right=vocabularies[1],
            )
            payload = scan.to_dict()
            text = "\n".join(
                [
                    f"--- {args.left}: {scan.name_left}",
                    f"+++ {args.right or args.left}: {scan.name_right}",
                    "",
                    *render_scan(scan, show_cosmetic=args.show_cosmetic, note_rename=False),
                ]
            )
            differences = len(scan.substantive)
        else:
            result = diff_protocols(
                left,
                right,
                normalize=normalize,
                use_vocabulary=not args.no_vocabulary,
                extra_vocabulary_dir=args.vocabulary,
            )
            payload = result.to_dict()
            text = render_protocol(
                result,
                show_cosmetic=args.show_cosmetic,
                show_identical=args.show_identical,
            )
            differences = result.substantive_count
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(payload, indent=2, ensure_ascii=False) if args.json else text
    try:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(rendered + "\n")
        else:
            print(rendered)
    except OSError as exc:
        print(f"could not write the report: {exc}", file=sys.stderr)
        return 1

    return 1 if differences else 0


def _render_policy_report(report: PolicyReport, quiet: bool) -> str:
    """Render a policy report as text.

    Parameters
    ----------
    report : PolicyReport
        The findings for one protocol.
    quiet : bool
        Whether to omit the passing summary lines.

    Returns
    -------
    str
        The report.
    """
    lines: list[str] = []
    name = os.path.basename(report.source_file) or report.source_file
    header = f"{name} ({report.software_version}) against policy {report.policy!r}"
    lines.append(header)
    if not report.violations:
        if not quiet:
            lines.append(f"  {report.checked} readings checked, all within preference")
    else:
        by_scan: dict[tuple, list] = {}
        for violation in report.violations:
            by_scan.setdefault((violation.scan_index, violation.scan_name), []).append(violation)
        for (index, scan_name), found in sorted(by_scan.items()):
            lines.append(f"  scan {index}: {scan_name}")
            for violation in found:
                mark = "!" if violation.severity == "error" else "?"
                lines.append(
                    f"    {mark} {violation.key} = {violation.value!r} "
                    f"[{violation.section}] -- prefer {violation.expected}"
                )
                if violation.detail:
                    lines.append(f"        {violation.detail}")
                if violation.reason:
                    lines.append(f"        {violation.reason}")
    if report.unused_rules and not quiet:
        lines.append(
            "  rules that matched nothing: " + ", ".join(repr(r) for r in report.unused_rules)
        )
    if not quiet or report.violations:
        errors = len(report.errors)
        warnings = len(report.violations) - errors
        lines.append(f"  {report.checked} readings checked, {errors} errors, {warnings} warnings")
    return "\n".join(lines)


def _run_check(args: argparse.Namespace) -> int:
    """Run the ``check`` subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        ``0`` when nothing failed, ``1`` when a violation was found or an
        input could not be read. Warnings alone pass with ``--warnings-ok``.
    """
    try:
        policy = load_policy(args.policy, args.policy_dir)
    except PolicyError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    targets = _inputs(args.input)
    if not targets:
        print(f"no PDFs found under {args.input}", file=sys.stderr)
        return 1

    reports = []
    failures = 0
    for target in targets:
        try:
            protocol = _load_protocol(target, args.version)
        except (OSError, ValueError) as exc:
            print(f"{exc}", file=sys.stderr)
            failures += 1
            continue
        vocabulary = load_vocabulary(protocol.get("software_version") or "")
        report = check_protocol(protocol, policy, vocabulary)
        report.source_file = report.source_file or target
        reports.append(report)

    if args.json:
        rendered = json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False)
    else:
        rendered = "\n\n".join(_render_policy_report(r, args.quiet) for r in reports)

    try:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(rendered + "\n")
        else:
            print(rendered)
    except OSError as exc:
        print(f"could not write the report: {exc}", file=sys.stderr)
        return 1

    errors = sum(len(r.errors) for r in reports)
    warnings = sum(len(r.violations) - len(r.errors) for r in reports)
    if failures or errors:
        return 1
    if warnings and not args.warnings_ok:
        return 1
    return 0


def _run_vocab(args: argparse.Namespace) -> int:
    """Run the ``vocab`` subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        ``0`` on success, ``1`` when a check found problems or an input could
        not be read.
    """
    extra = getattr(args, "vocabulary", None)
    if args.vocab_command == "list":
        return _vocab_list(args, extra)
    if args.vocab_command == "check":
        problems = check(available(extra), extra)
        if args.against:
            try:
                problems += verify_aliases(
                    _load_protocol(args.against[0], "auto"),
                    _load_protocol(args.against[1], "auto"),
                    extra,
                )
            except (OSError, ValueError) as exc:
                print(f"{exc}", file=sys.stderr)
                return 1
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print(f"{len(available(extra))} vocabularies, no problems found")
        return 1 if problems else 0
    return _vocab_suggest(args, extra)


def _vocab_list(args: argparse.Namespace, extra: str | None) -> int:
    """Print vocabulary mappings.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments, read for ``version`` and ``canonical``.
    extra : str or None
        An overlay directory.

    Returns
    -------
    int
        ``0`` on success, ``1`` if a named release has no vocabulary.
    """
    versions = [args.version] if args.version else available(extra)
    if args.version and args.version not in available(extra):
        print(
            f"no vocabulary for {args.version!r}; have {', '.join(available(extra))}",
            file=sys.stderr,
        )
        return 1

    if args.canonical:
        print(f"{args.canonical}:")
        for version in versions:
            labels = load_vocabulary(version, extra).labels(args.canonical)
            print(f"  {version:8s} {', '.join(labels) if labels else '(no mapping)'}")
        return 0

    for version in versions:
        vocabulary = load_vocabulary(version, extra)
        print(f"{version} ({len(vocabulary.aliases)} mappings)")
        for label, canonical in sorted(vocabulary.aliases.items()):
            note = vocabulary.notes.get(label, "")
            print(f"  {label:34s} -> {canonical}")
            if note:
                print(f"  {'':34s}    note: {note}")
    return 0


def _vocab_suggest(args: argparse.Namespace, extra: str | None) -> int:
    """Propose candidate vocabulary entries from a matched pair of exports.

    Candidates are printed with the evidence behind them and are never
    applied. Co-occurrence alone is weak evidence: any two parameters that
    exist in only one release and sit in the same section co-occur perfectly,
    which is how a naive pass proposes nonsense like ``Save uncombined`` ->
    ``Radial Sorting``. Read the value columns before accepting anything.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments, read for the two inputs and ``min_support``.
    extra : str or None
        An overlay directory.

    Returns
    -------
    int
        ``0`` on success, ``1`` if an input could not be read.
    """
    try:
        left = _load_protocol(args.left, "auto")
        right = _load_protocol(args.right, "auto")
    except (OSError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    candidates = suggest_aliases(left, right, min_support=args.min_support, extra_dir=extra)
    if not candidates:
        print("no candidates above the support threshold")
        return 0

    print(
        f"# candidates for {left.get('software_version')} -> "
        f"{right.get('software_version')}, strongest first."
    )
    print("# Evidence only. Check the values agree in meaning before accepting one.\n")
    for c in candidates:
        print(f"{c.left_label!r} -> {c.right_label!r}")
        print(
            f"    seen in {c.support} scans | same section {c.section_ratio:.0%} "
            f"| same value {c.value_ratio:.0%}"
        )
        print(f"    {left.get('software_version')}: {c.left_values}")
        print(f"    {right.get('software_version')}: {c.right_values}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface.

    Parameters
    ----------
    argv : list of str or None, optional
        Arguments to parse. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        ``0`` on success, ``1`` if any file failed. A batch run continues
        past a failing file and reports at the end.
    """
    args = build_parser().parse_args(argv)

    if args.command == "versions":
        return _list_versions()

    if args.command == "diff":
        return _run_diff(args)

    if args.command == "check":
        return _run_check(args)

    if args.command == "vocab":
        return _run_vocab(args)

    targets = _inputs(args.input)
    if not targets:
        print(f"no PDFs found under {args.input}", file=sys.stderr)
        return 1
    batch = os.path.isdir(args.input)

    options = ParseOptions(
        version=args.version,
        ocr=args.ocr,
        dpi=args.dpi,
        include_flat=args.flatten,
        debug=bool(args.emit_debug),
    )

    failures = 0
    for pdf in targets:
        try:
            result = parse_document(pdf, options)
        except Exception as exc:  # keep a batch run going past one bad file
            failures += 1
            print(f"{pdf}: {exc}", file=sys.stderr)
            if not batch:
                return 1
            continue

        try:
            out_path = _write_outputs(result, args, pdf, batch, args.input if batch else None)
        except OSError as exc:
            failures += 1
            print(f"could not write output for {pdf}: {exc}", file=sys.stderr)
            if not batch:
                return 1
            continue

        if not args.quiet:
            print(_summarize(result.protocol, out_path), file=sys.stderr)
        for warning in result.protocol.warnings:
            print(f"  warning: {warning}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
