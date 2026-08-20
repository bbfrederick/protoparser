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
from .profiles import REGISTRY
from .report import render_protocol, render_scan

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
        "--show-cosmetic",
        action="store_true",
        help="list relabeled, recased and reformatted differences individually",
    )
    diff_cmd.add_argument(
        "--show-identical", action="store_true", help="include scans with no differences"
    )
    diff_cmd.add_argument("--json", action="store_true", help="emit the comparison as JSON")
    diff_cmd.add_argument("--out", help="write the report here instead of stdout")

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


def _output_path(pdf: str, out: str | None, batch: bool) -> str:
    """Where one file's JSON should be written.

    Parameters
    ----------
    pdf : str
        Path of the PDF being parsed.
    out : str or None
        The ``--out`` value: a file in single mode, a directory in batch mode.
    batch : bool
        Whether this run is over a directory.

    Returns
    -------
    str
        The destination path. Creates the output directory in batch mode.
    """
    if out and batch:
        os.makedirs(out, exist_ok=True)
        return os.path.join(out, os.path.splitext(os.path.basename(pdf))[0] + ".json")
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


def _write_outputs(result: ParseResult, args: argparse.Namespace, pdf: str, batch: bool) -> str:
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
        out_path = _output_path(pdf, args.out, batch)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")

    if args.emit_debug:
        debug_path = args.emit_debug
        if batch:
            os.makedirs(debug_path, exist_ok=True)
            debug_path = os.path.join(
                debug_path, os.path.splitext(os.path.basename(pdf))[0] + ".debug.json"
            )
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
            scan = diff_scans(
                _select_scan(left, name_left, args.left),
                _select_scan(right, name_right, args.right or args.left),
                normalize=normalize,
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
            result = diff_protocols(left, right, normalize=normalize)
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
            out_path = _write_outputs(result, args, pdf, batch)
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
