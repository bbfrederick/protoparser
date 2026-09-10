"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TYPE_CHECKING, Mapping, Sequence

from . import __version__
from .debug import write_debug
from .diff import diff_protocols, diff_scans, normalize_section, section_groups
from .extract import TESSERACT_ENV
from .flatten import conflicts
from .listing import build_listing, render_listing
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
from .report import name_mismatch_note, render_protocol, render_scan, section_filter_note
from .sequences import FLAGGED, SELECTORS, STOCK, THIRD_PARTY, UNRECOGNIZED
from .sequences import check as check_catalog
from .sequences import describe, identify_protocol, load_catalog
from .sequences import render as render_sequences
from .sequences import summarize
from .summary import build_summary, render_summary
from .vocabsuggest import suggest_aliases, verify_aliases
from .vocabulary import available, check, load_vocabulary

if TYPE_CHECKING:  # imported for annotations only -- reading an archive is
    # a heavier import than the PDF path needs, so it stays deferred at runtime.
    from .exar.archive import Archive, Program

#: Suffixes treated as PDFs when walking a directory.
PDF_SUFFIXES = (".pdf", ".PDF")

#: Suffix identifying an XA protocol archive.
#:
#: Matched case-insensitively on the input path, which is what lets the
#: commands that query a protocol take an archive wherever they take a PDF.
EXAR_SUFFIX = ".exar1"


def add_program_option(parser: argparse.ArgumentParser) -> None:
    """Add the flag that picks one protocol out of a multi-program archive.

    Only an archive needs it. A PDF export prints one protocol, and a backup
    taken at the exam or region level holds several, so a command that accepts
    both needs a way to say which -- and refusing to guess is why
    :func:`_select_program` raises rather than taking the first.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The subcommand parser to add the flag to.

    Returns
    -------
    None
    """
    parser.add_argument(
        "--program",
        metavar="NAME",
        help=(
            "which protocol of an .exar1 archive to read, needed only when it "
            "holds more than one"
        ),
    )


def add_side_program_options(parser: argparse.ArgumentParser) -> None:
    """Add the per-side flags picking a protocol out of a multi-program archive.

    The two-input commands need one flag a side rather than the single
    ``--program`` the one-input commands take, for the same reason ``diff``
    already spells ``--left-scan`` and ``--right-scan``: the two inputs may be
    two backups, or one backup twice, and a lone flag cannot say which
    protocol belongs to which side.

    Naming a different protocol on each side of one file is a supported
    request, not an accident -- it is how two protocols of a single backup are
    compared -- so a caller must not reuse one side's parse for the other
    without checking the programs agree as well as the paths.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The subcommand parser to add the flags to.

    Returns
    -------
    None
    """
    for side in ("left", "right"):
        parser.add_argument(
            f"--{side}-program",
            metavar="NAME",
            help=(
                f"which protocol to take from the {side} .exar1 archive, "
                "needed only when it holds more than one"
            ),
        )


def add_release_option(parser: argparse.ArgumentParser, help_text: str) -> None:
    """Add the flag that forces a Siemens release profile.

    Spelled ``--release`` because ``--version`` on the top-level command
    reports the tool's own version, and one word meaning two unrelated things
    one level apart is a trap. ``--version`` stays as a hidden alias so
    existing scripts and habits keep working.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The subcommand parser to add the flag to.
    help_text : str
        Help for the visible spelling.

    Returns
    -------
    None
    """
    choices = ["auto", *REGISTRY.names()]
    parser.add_argument(
        "--release", dest="version", default="auto", choices=choices, help=help_text
    )
    parser.add_argument(
        "--version",
        dest="version",
        default="auto",
        choices=choices,
        help=argparse.SUPPRESS,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns
    -------
    argparse.ArgumentParser
        A parser with the ``parse`` and ``versions`` subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="siemens-protocol-tool",
        description="Parse Siemens MR protocol PDF exports into hierarchical JSON.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"siemens-protocol-tool {__version__}",
        help="show the tool's version and exit",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="parse a protocol PDF, or every PDF in a directory")
    parse_cmd.add_argument("input", help="a PDF file, or a directory of PDFs")
    parse_cmd.add_argument("--out", help="write JSON here (default: alongside the input, .json)")
    add_release_option(parse_cmd, "force a Siemens release profile (default: auto)")
    parse_cmd.add_argument(
        "--ocr",
        default=OCR_AUTO,
        choices=[OCR_AUTO, OCR_ALWAYS, OCR_NEVER],
        help="control the OCR fallback (default: auto)",
    )
    parse_cmd.add_argument("--dpi", type=int, default=300, help="rasterization DPI for OCR pages")
    parse_cmd.add_argument(
        "--tesseract",
        metavar="PATH",
        help=(
            "path to the tesseract binary for the OCR fallback. Only needed "
            "where it is installed off PATH, as the Windows installer leaves "
            f"it; the {TESSERACT_ENV} environment variable does the same"
        ),
    )
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
            "Name a scan per side with --left-scan and --right-scan: with two "
            "inputs that compares one scan of each file, and with one input it "
            "compares two scans of that file. Naming only one side uses the same "
            "name on the other. With neither, two inputs are compared in full. "
            "An .exar1 archive holding several protocols needs --left-program "
            "and --right-program to say which; naming a different one on each "
            "side of a single archive compares two of its protocols."
        ),
    )
    diff_cmd.add_argument(
        "left", help="a PDF, an .exar1 archive, or a previously parsed JSON file"
    )
    diff_cmd.add_argument(
        "right",
        nargs="?",
        help=(
            "a second PDF, .exar1 archive or JSON; omit to compare two scans "
            "within LEFT, or two of its protocols"
        ),
    )
    diff_cmd.add_argument(
        "--left-scan",
        metavar="NAME",
        help="scan to take from LEFT, by name or zero-based index",
    )
    diff_cmd.add_argument(
        "--right-scan",
        metavar="NAME",
        help="scan to take from RIGHT, by name or zero-based index",
    )
    diff_cmd.add_argument(
        "--scan",
        action="append",
        metavar="NAME",
        help=(
            "shorthand for the pair above: give once to use the same name on "
            "both sides, or twice for the left and right scans"
        ),
    )
    add_side_program_options(diff_cmd)
    add_release_option(
        diff_cmd, "force a Siemens release profile for any PDF input (default: auto)"
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
        "--filter",
        action="append",
        dest="sections",
        metavar="SECTION",
        help=(
            "report only differences in this top-level section -- properties, "
            "routine, contrast, resolution, geometry, header and so on. Repeat "
            "the option or give a comma-separated list for several; a full "
            "section name such as 'Contrast - Common' is accepted and folded "
            "to its top level"
        ),
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
    check_cmd.add_argument(
        "input", help="a PDF, an .exar1 archive, a parsed JSON file, or a directory of PDFs"
    )
    add_program_option(check_cmd)
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
    add_release_option(
        check_cmd, "force a Siemens release profile for any PDF input (default: auto)"
    )
    check_cmd.add_argument(
        "--warnings-ok",
        action="store_true",
        help="exit zero when only warnings were found",
    )
    check_cmd.add_argument("--json", action="store_true", help="emit the findings as JSON")
    check_cmd.add_argument("--out", help="write the report here instead of stdout")
    check_cmd.add_argument("--quiet", action="store_true", help="print only violations")

    list_cmd = sub.add_parser(
        "list",
        help="list a protocol's scans with their acquisition times",
        description=(
            "List one protocol's scans in acquisition order -- index, name, "
            "sequence and acquisition time -- and total the scan time. Times "
            "are shown as the export prints them, which differs by release; "
            "the total is normalized."
        ),
    )
    list_cmd.add_argument(
        "input", help="a PDF, an .exar1 archive, or a previously parsed JSON file"
    )
    add_program_option(list_cmd)
    add_release_option(list_cmd, "force a Siemens release profile for a PDF input (default: auto)")
    list_cmd.add_argument("--json", action="store_true", help="emit the listing as JSON")
    list_cmd.add_argument("--out", help="write the listing here instead of stdout")

    summary_cmd = sub.add_parser(
        "summary",
        help="summarize a protocol: scans, run time and the sequences it uses",
        description=(
            "Summarize one protocol in a block rather than a line per scan: "
            "how many scans it runs, how long it takes, its longest and "
            "shortest scan, and a census of the distinct sequences with the "
            "scans and time each accounts for. A scan printing no readable "
            "acquisition time is excluded from the total and counted, which "
            "is why an archive can total a few seconds under its own "
            "printout -- the console omits the field on the one-second "
            "setter scans. Run 'list' for the per-scan detail."
        ),
    )
    summary_cmd.add_argument(
        "input", help="a PDF, an .exar1 archive, or a previously parsed JSON file"
    )
    add_program_option(summary_cmd)
    add_release_option(
        summary_cmd, "force a Siemens release profile for a PDF input (default: auto)"
    )
    summary_cmd.add_argument(
        "--catalog",
        metavar="DIR",
        help="a directory of additional signature catalogs, overlaying the shipped one",
    )
    summary_cmd.add_argument("--json", action="store_true", help="emit the summary as JSON")
    summary_cmd.add_argument("--out", help="write the summary here instead of stdout")

    sequences_cmd = sub.add_parser(
        "sequences",
        help="report which scans run third-party sequences",
        description=(
            "Say which of a protocol's scans run a sequence Siemens did not "
            "supply. Siemens' own conversion handles stock sequences between "
            "releases; a third-party sequence is what has to be rebuilt and "
            "checked by hand, so this is the list of work a migration implies. "
            "A scan the catalog cannot account for is reported as unrecognized "
            "rather than guessed at in either direction."
        ),
    )
    sequences_cmd.add_argument(
        "input", help="a PDF, an .exar1 archive, or a previously parsed JSON file"
    )
    add_program_option(sequences_cmd)
    add_release_option(
        sequences_cmd, "force a Siemens release profile for a PDF input (default: auto)"
    )
    sequences_cmd.add_argument(
        "--only",
        choices=sorted(SELECTORS),
        help=(
            f"list only these scans; {FLAGGED!r} means third-party and unrecognized "
            "together, which is the rebuild list. The counts always cover every scan"
        ),
    )
    sequences_cmd.add_argument(
        "--explain",
        action="store_true",
        help="show the evidence behind each identification, and the catalog's notes",
    )
    sequences_cmd.add_argument(
        "--catalog",
        metavar="DIR",
        help="a directory of additional signature catalogs, overlaying the shipped one",
    )
    sequences_cmd.add_argument("--json", action="store_true", help="emit the findings as JSON")
    sequences_cmd.add_argument("--out", help="write the report here instead of stdout")

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
        help=(
            "two exports of one protocol from different releases, to check mappings "
            "against; each may be a PDF, an .exar1 archive or JSON"
        ),
    )
    add_side_program_options(vocab_check)

    vocab_suggest = vocab_action.add_parser(
        "suggest", help="propose candidate mappings from a matched pair of exports"
    )
    vocab_suggest.add_argument(
        "left", help="an export of one release: a PDF, an .exar1 archive or JSON"
    )
    vocab_suggest.add_argument(
        "right",
        help="the same protocol from another release: a PDF, an .exar1 archive or JSON",
    )
    vocab_suggest.add_argument(
        "--min-support",
        type=int,
        default=8,
        help="ignore candidates seen in fewer scans than this (default: 8)",
    )
    add_side_program_options(vocab_suggest)
    vocab_suggest.add_argument("--vocabulary", metavar="DIR", help="an overlay directory")

    archive_cmd = sub.add_parser(
        "archive",
        help="read an XA .exar1 archive into hierarchical JSON",
        description=(
            "Read a protocol archive into JSON that can be browsed or queried. "
            "This is the reading half of the .exar1 support, where 'exar' is the "
            "writing half. The document is not a parsed printout and does not "
            "pretend to be one: it carries the console's Preview summary under "
            "each scan's printed labels, the whole ASCCONV parameter block nested "
            "by the structure its key names describe, the slice geometry, and the "
            "prescription links between scans -- which a printout does not record "
            "at all, since a linked scan prints exactly like an unlinked one."
        ),
    )
    archive_cmd.add_argument("input", help="an .exar1 archive")
    add_program_option(archive_cmd)
    archive_cmd.add_argument(
        "--out", metavar="FILE", help="write the JSON here (default: alongside the archive)"
    )
    archive_cmd.add_argument(
        "--stdout", action="store_true", help="write the JSON to standard output instead"
    )
    archive_cmd.add_argument(
        "--no-ascconv",
        dest="ascconv",
        action="store_false",
        help=(
            "omit the ASCCONV parameter tree, which is the bulk of the document "
            "-- 514 to 2020 assignments a scan"
        ),
    )
    archive_cmd.add_argument(
        "--quiet", action="store_true", help="suppress the summary line on stderr"
    )

    exar_cmd = sub.add_parser(
        "exar",
        help="write a protocol PDF's parameters into an XA .exar1 archive",
        description=(
            "Take a template .exar1 archive and a protocol PDF, write every "
            "parameter that has a verified mapping, and report what could not "
            "be written. Only a fraction of what a protocol prints is mapped, "
            "so the result is mostly the template it started from -- the "
            "manifest says how much, and names the parameters no mapping "
            "covers so the gap is visible rather than implied. Nothing is "
            "written without --out."
        ),
    )
    exar_cmd.add_argument("archive", help="the template .exar1 archive")
    exar_cmd.add_argument("input", help="a protocol PDF, or a previously parsed JSON file")
    add_program_option(exar_cmd)
    add_release_option(exar_cmd, "force a Siemens release profile for a PDF input (default: auto)")
    exar_cmd.add_argument("--out", help="write the resulting archive here")
    exar_cmd.add_argument(
        "--show", type=int, default=12, metavar="N", help="how many entries to list (default: 12)"
    )

    versions_cmd = sub.add_parser("versions", help="list the known version profiles")
    versions_cmd.set_defaults(command="versions")

    gui_cmd = sub.add_parser(
        "gui",
        help="open the graphical front end in a browser",
        description=(
            "Serve the graphical front end and open it in the default browser. "
            "It is a page rather than a window because that needs no toolkit "
            "installed, and it runs these same subcommands: every form shows "
            "the command line it is about to run."
        ),
    )
    gui_cmd.add_argument(
        "--port", type=int, default=0, help="port to serve on (default: any free port)"
    )
    gui_cmd.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "interface to bind (default: 127.0.0.1). Widening this exposes a "
            "server that runs commands as you"
        ),
    )
    gui_cmd.add_argument(
        "--dir",
        dest="cwd",
        metavar="DIR",
        help="directory commands run in, which relative paths resolve against",
    )
    gui_cmd.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        help="print the URL instead of opening a browser",
    )

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


def _run_gui(args: argparse.Namespace) -> int:
    """Run the ``gui`` subcommand.

    The GUI is imported here rather than at module scope so that the ordinary
    command line pays nothing for a front end it does not use.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        ``0`` once the server has stopped, ``1`` if ``--dir`` is not one.
    """
    from .gui import launch

    if args.cwd is not None and not os.path.isdir(args.cwd):
        print(f"not a directory: {args.cwd}", file=sys.stderr)
        return 1
    return launch(host=args.host, port=args.port, cwd=args.cwd, open_browser=args.open_browser)


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


def _load_protocol(
    path: str, version: str, need_flat: bool = True, program: str | None = None
) -> dict:
    """Load a protocol from a PDF, an ``.exar1`` archive, or JSON.

    Accepting JSON means a protocol can be parsed once and used many times,
    which matters because parsing dominates the runtime.

    An archive is rendered into the same shape a parsed PDF has, so the
    listing, the sequence report and the policy checker read it unchanged.
    What it carries there is the console's own ``Preview`` summary -- roughly
    forty parameters a scan -- rather than the several hundred a page prints,
    so a policy written against a printout will find most of its keys missing.
    The whole parameter set is in the archive's ASCCONV block, which the
    ``archive`` subcommand emits and this shape has no room for.

    Parameters
    ----------
    path : str
        A ``.pdf`` to parse, an ``.exar1`` to read, or a ``.json`` file.
    version : str
        Version profile to force for a PDF, or ``"auto"``. Ignored for an
        archive, whose release is stated by its own baseline.
    need_flat : bool, optional
        Whether the caller reads the flattened view. Comparison and policy
        checking do; listing reads only the scan headers, so it accepts JSON
        written with ``--no-flatten``. Default ``True``.
    program : str or None, optional
        Which protocol of a multi-program archive to read. Default ``None``,
        which is unambiguous only when the archive holds one.

    Returns
    -------
    dict
        The serialized protocol.

    Raises
    ------
    ValueError
        If ``need_flat`` and a JSON input carries no flattened view, or if an
        archive holds no protocol, or holds several and ``program`` names
        none of them.
    """
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if need_flat:
            for scan in payload.get("scans", []):
                if "flat" not in scan:
                    raise ValueError(
                        f"{path} was written with --no-flatten; "
                        "this command needs the flattened view"
                    )
        return payload
    if path.lower().endswith(EXAR_SUFFIX):
        from .exar import inspect as exar_inspect
        from .exar import read as read_exar

        archive = read_exar(path)
        return exar_inspect.as_protocol(
            archive, _select_program(archive, program, path), path, include_flat=need_flat
        )
    result = parse_document(path, ParseOptions(version=version))
    return result.protocol.to_dict(include_flat=True)


def _select_program(archive: "Archive", wanted: str | None, path: str) -> "Program":
    """Pick the protocol to read out of an archive.

    An archive may hold several: an export taken at the exam or region level
    rather than at one protocol, which is what a scanner backup is. Picking
    the first would describe one protocol while looking like a reading of the
    whole file, so this refuses instead and names the choices.

    Parameters
    ----------
    archive : Archive
        The archive to choose from.
    wanted : str or None
        The program name asked for, or ``None`` to accept a lone one.
    path : str
        The archive's path, for the message.

    Returns
    -------
    Program
        The chosen protocol.

    Raises
    ------
    ValueError
        If the archive holds no protocol, if ``wanted`` names none of them, or
        if it holds several and ``wanted`` is ``None``.
    """
    programs = archive.programs
    if not programs:
        raise ValueError(
            f"{path} holds no protocol. An archive exported from an empty folder node "
            "rather than from the protocol tree reads correctly and carries nothing"
        )
    if wanted is not None:
        for program in programs:
            if program.name == wanted:
                return program
        names = ", ".join(repr(p.name) for p in programs)
        raise ValueError(f"{path} holds no protocol named {wanted!r}. It holds: {names}")
    if len(programs) > 1:
        names = ", ".join(repr(p.name) for p in programs)
        raise ValueError(
            f"{path} holds {len(programs)} protocols, so --program is needed to say "
            f"which one. It holds: {names}"
        )
    return programs[0]


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


def _same_file(left: str, right: str) -> bool:
    """Whether two command-line paths name the same file on disk.

    Compared by resolved path rather than by string, so ``./a.pdf`` and
    ``a.pdf`` are recognized as one file.

    Parameters
    ----------
    left, right : str
        Paths as given on the command line.

    Returns
    -------
    bool
        ``True`` when both resolve to the same existing file.
    """
    try:
        return os.path.samefile(left, right)
    except OSError:  # one of them does not exist; let the loader report it
        return False


def _scan_selection(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Which scan was requested for each side, before any defaulting.

    ``None`` on a side means no scan was named for it, which is what
    distinguishes "compare the whole protocols" from "compare one scan
    against a scan of the same name".

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments, read for ``scan``, ``left_scan`` and
        ``right_scan``.

    Returns
    -------
    tuple
        ``(left, right)``, each a scan name, an index, or ``None``.

    Raises
    ------
    ValueError
        If the two spellings are mixed, or ``--scan`` is given too often.
    """
    repeated = list(args.scan or [])
    explicit = (args.left_scan, args.right_scan)
    if repeated and any(name is not None for name in explicit):
        raise ValueError("--scan cannot be combined with --left-scan or --right-scan")
    if any(name is not None for name in explicit):
        return explicit
    if len(repeated) > 2:
        raise ValueError("--scan may be given at most twice")
    if not repeated:
        return None, None
    if len(repeated) == 1:
        return repeated[0], None
    return repeated[0], repeated[1]


def _run_list(args: argparse.Namespace) -> int:
    """Run the ``list`` subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the input could not be read.
    """
    try:
        protocol = _load_protocol(args.input, args.version, need_flat=False, program=args.program)
    except (OSError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    rows = build_listing(protocol)
    if args.json:
        payload = {
            "source_file": protocol.get("source_file", ""),
            "software_version": protocol.get("software_version", ""),
            "scans": [row.to_dict() for row in rows],
            "total_seconds": round(sum(r.seconds for r in rows if r.seconds is not None), 3),
            "unreadable": sum(1 for r in rows if r.seconds is None),
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        text = render_listing(protocol, rows)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


def _run_summary(args: argparse.Namespace) -> int:
    """Run the ``summary`` subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the input or the catalog could not be
        read. A protocol running third-party sequences is the expected case
        on a research protocol and is not an error.
    """
    try:
        catalog = load_catalog(args.catalog)
        protocol = _load_protocol(args.input, args.version, need_flat=False, program=args.program)
    except (OSError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    for problem in check_catalog(catalog):
        # Reported rather than raised, as in 'sequences': a flawed overlay
        # entry should not stop the shipped signatures from being useful.
        print(f"catalog: {problem}", file=sys.stderr)

    summary = build_summary(protocol, catalog)
    text = (
        json.dumps(summary.to_dict(), indent=2, ensure_ascii=False)
        if args.json
        else render_summary(summary)
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


def _run_sequences(args: argparse.Namespace) -> int:
    """Run the ``sequences`` subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the input or the catalog could not be
        read. Finding third-party sequences is not a failure: they are the
        expected result on a research protocol, and making them an exit code
        would turn every such run into a scripted error.
    """
    try:
        catalog = load_catalog(args.catalog)
        protocol = _load_protocol(args.input, args.version, need_flat=False, program=args.program)
    except (OSError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    for problem in check_catalog(catalog):
        # Reported rather than raised: a flawed overlay entry should not stop
        # the shipped signatures from being useful.
        print(f"catalog: {problem}", file=sys.stderr)

    found = identify_protocol(protocol, catalog)
    if args.json:
        counts = summarize(found)
        payload = {
            "source_file": protocol.get("source_file", ""),
            "software_version": protocol.get("software_version", ""),
            "counts": counts,
            "families": sorted({describe(i) for i in found if i.verdict == THIRD_PARTY}),
            "scans": [
                {
                    "index": i.index,
                    "name": i.name,
                    "sequence": i.binary,
                    **i.to_dict(include_note=args.explain),
                }
                for i in found
                if i.verdict in SELECTORS.get(args.only, (THIRD_PARTY, UNRECOGNIZED, STOCK))
            ],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        text = render_sequences(protocol, found, explain=args.explain, only=args.only)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


def _section_filter(requested: Sequence[str] | None, *protocols: Mapping) -> list[str] | None:
    """Resolve ``--filter`` against the sections these files actually print.

    Validating against the loaded files rather than a fixed list keeps the
    error message honest as releases come and go: VB17A prints ``Angio`` and
    ``Perf`` where XA60 prints neither, so what can be asked for depends on
    what is being compared.

    Parameters
    ----------
    requested : sequence of str or None
        The raw option values, each possibly a comma-separated list.
    *protocols : mapping
        The loaded protocols whose sections bound the request.

    Returns
    -------
    list of str or None
        Normalized card names in the order asked for, without repeats, or
        ``None`` when no filter was requested.

    Raises
    ------
    ValueError
        If a name matches no section in any of the protocols.
    """
    if not requested:
        return None
    available: list[str] = []
    for protocol in protocols:
        for name in section_groups(protocol):
            if name not in available:
                available.append(name)

    wanted: list[str] = []
    for value in requested:
        for part in value.split(","):
            name = normalize_section(part)
            if not name:
                continue
            if name not in available:
                raise ValueError(
                    f"no section named {name!r}; these files have: " + ", ".join(sorted(available))
                )
            if name not in wanted:
                wanted.append(name)
    return wanted


def _run_diff(args: argparse.Namespace) -> int:
    """Run the ``diff`` subcommand.

    Two modes, chosen by whether any scan was named. With two inputs and no
    scan named, the protocols are compared scan by scan. Naming a scan for
    either side compares single scans instead: one from each input, or two
    from the same input when only one is given. ``--left-scan`` and
    ``--right-scan`` say which side each name belongs to; ``--scan`` is the
    positional shorthand for the same thing.

    Which *protocol* each side takes from an ``.exar1`` archive is a separate
    question, since a backup holds every protocol on the scanner:
    ``--left-program`` and ``--right-program`` answer it. Naming a different
    one on each side of a single archive compares two of its protocols, which
    is why the guard on a lone input asks for a scan pair only when both
    sides want the same program, and why one side's parse is reused for the
    other only when the paths *and* the programs agree.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        ``0`` when the two sides matched, ``1`` when they differed or the
        request could not be satisfied. Comparing whole protocols, "differed"
        covers a scan present on one side only as well as a substantive
        parameter difference; comparing two named scans, only the latter can
        arise.
    """
    try:
        name_left, name_right = _scan_selection(args)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    # One input names one protocol, so something has to say what the two
    # sides are. Two scans of it is the original answer; two *programs* of it
    # is the other, which a multi-program archive makes possible -- a backup
    # holds every protocol on the scanner, and comparing two of them should
    # not require exporting either one first.
    one_program = args.left_program == args.right_program
    if args.right is None and one_program and (name_left is None or name_right is None):
        print(
            "comparing within one file needs a scan for each side "
            "(--left-scan NAME --right-scan NAME), or a protocol for each side "
            "of an .exar1 archive (--left-program NAME --right-program NAME); "
            "pass a second file to compare protocols",
            file=sys.stderr,
        )
        return 1

    # A side left unnamed takes the other side's name, so naming one scan
    # compares it against its counterpart without repeating the name.
    if name_left is not None or name_right is not None:
        name_left = name_left if name_left is not None else name_right
        name_right = name_right if name_right is not None else name_left

    right_path = args.right if args.right is not None else args.left
    try:
        left = _load_protocol(args.left, args.version, program=args.left_program)
        # Naming the same file on both sides is the same request as omitting
        # the second one, so it costs one parse rather than two -- but only
        # when the same protocol is wanted from it. Two programs of one
        # backup are two different protocols, and reusing the parse would
        # compare one of them against itself.
        if _same_file(args.left, right_path) and args.left_program == args.right_program:
            right = left
        else:
            right = _load_protocol(right_path, args.version, program=args.right_program)
    except (OSError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    try:
        sections = _section_filter(args.sections, left, right)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    normalize = not args.exact_keys
    try:
        if name_left is not None:
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
                sections=sections,
            )
            payload = scan.to_dict()
            # The note leads the report here rather than sitting inside the
            # scan block: in this mode the two scans were named separately, so
            # which name belongs to which file is the first thing to state.
            note = name_mismatch_note(scan.name_left, scan.name_right)
            scoped = section_filter_note(sections)
            text = "\n".join(
                [
                    f"--- {args.left}: {scan.name_left}",
                    f"+++ {args.right or args.left}: {scan.name_right}",
                    *([note] if note is not None else []),
                    *([scoped] if scoped is not None else []),
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
                sections=sections,
            )
            payload = result.to_dict()
            text = render_protocol(
                result,
                show_cosmetic=args.show_cosmetic,
                show_identical=args.show_identical,
                sections=sections,
            )
            # Not the parameter count alone: a protocol carrying a scan the
            # other does not have differs from it, and that scan has no
            # parameters to contribute to the count.
            differences = result.differs
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    if sections is not None:
        # The counts in the payload describe the filtered view, so the
        # filter travels with them.
        payload["sections"] = sections
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
            protocol = _load_protocol(target, args.version, program=args.program)
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
                    _load_protocol(args.against[0], "auto", program=args.left_program),
                    _load_protocol(args.against[1], "auto", program=args.right_program),
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
        left = _load_protocol(args.left, "auto", program=args.left_program)
        right = _load_protocol(args.right, "auto", program=args.right_program)
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


def use_utf8_output() -> None:
    """Make standard output carry the characters these protocols contain.

    Values printed from a protocol routinely include multiplication signs,
    superscripts, degree and micro signs. Windows leaves a *redirected*
    stdout on the legacy code page, so a report that renders fine in a
    console raises ``UnicodeEncodeError`` the moment it is piped to a file.
    JSON output is already written as UTF-8 explicitly, so this only brings
    the human-readable output in line with it.

    Returns
    -------
    None
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # a replaced stream, under capture or a pipe
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):  # pragma: no cover - depends on stream
            pass


def _archive_output_path(source: str) -> str:
    """Where an archive's JSON goes when no destination is given.

    The suffix is appended rather than replaced. ``Potpourri_P1.exar1`` and
    ``Potpourri_P1.pdf`` share a stem, and several corpus directories hold
    both, so replacing the extension the way ``parse`` does would let a
    reading of the archive silently overwrite a parse of the printout.

    Parameters
    ----------
    source : str
        Path of the archive being read.

    Returns
    -------
    str
        The output path, such as ``Potpourri_P1.exar1.json``.
    """
    return source + ".json"


def _run_archive(args: argparse.Namespace) -> int:
    """Run the ``archive`` subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments carrying ``input``, ``program``, ``out``, ``stdout``,
        ``ascconv`` and ``quiet``.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the archive could not be read or written.
    """
    from .exar import inspect as exar_inspect
    from .exar import read as read_exar

    try:
        archive = read_exar(args.input)
    except (OSError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    document = exar_inspect.describe(archive, args.input, ascconv=args.ascconv)
    if args.program is not None:
        try:
            wanted = _select_program(archive, args.program, args.input)
        except ValueError as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
        document["programs"] = [p for p in document["programs"] if p["name"] == wanted.name]
        document["program_count"] = len(document["programs"])

    text = json.dumps(document, indent=2, ensure_ascii=False)
    if args.stdout:
        print(text)
        destination = "<stdout>"
    else:
        destination = args.out or _archive_output_path(args.input)
        try:
            with open(destination, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError as exc:
            print(f"could not write {destination}: {exc}", file=sys.stderr)
            return 1

    if not args.quiet:
        print(_summarize_archive(document, destination), file=sys.stderr)
    for warning in document.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _summarize_archive(document: Mapping, destination: str) -> str:
    """One line describing what was read out of an archive.

    Counts copy references as well as scans because they are the part of an
    archive a printout cannot carry, so a run that found some has recovered
    something no PDF of the same protocol would have shown. It counts *those*
    rather than every relation: most relations in the corpus have an empty
    kind and no payload, and calling them links made a 19-link protocol
    report 117.

    Parameters
    ----------
    document : mapping
        The document :func:`..exar.inspect.describe` built.
    destination : str
        Where it was written.

    Returns
    -------
    str
        The summary line.
    """
    from .exar import COPY_REFERENCE

    programs = document.get("programs", [])
    scans = sum(int(p.get("scan_count", 0)) for p in programs)
    pauses = sum(int(p.get("pause_count", 0)) for p in programs)
    links = sum(
        sum(n for kind, n in p.get("relation_counts", {}).items() if kind == COPY_REFERENCE)
        for p in programs
    )
    other = sum(sum(p.get("relation_counts", {}).values()) for p in programs) - links
    parts = [
        str(document.get("software_version") or document.get("major_version", "?")),
        f"{len(programs)} protocols" if len(programs) != 1 else "1 protocol",
        f"{scans} scans",
    ]
    if pauses:
        parts.append(f"{pauses} pauses")
    if links:
        parts.append(f"{links} copy references")
    if other:
        parts.append(f"{other} other relations")
    return f"{' | '.join(parts)} -> {destination}"


def _run_exar(args: argparse.Namespace) -> int:
    """Write a PDF's mapped parameters into a template archive.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments carrying ``archive``, ``input``, ``out`` and ``show``.

    Returns
    -------
    int
        Process exit status.
    """
    from .exar import build as exar_build
    from .exar import read as read_exar
    from .exar import validate as exar_validate

    try:
        archive = read_exar(args.archive)
        protocol = _load_protocol(args.input, getattr(args, "release", "auto"))
    except (OSError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    try:
        target = (
            _select_program(archive, args.program, args.archive)
            if args.program is not None
            else None
        )
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    report = exar_build.apply_protocol(archive, protocol, target)
    print(report.report(limit=args.show))

    problems = exar_validate.problems(archive)
    if problems:
        print("\nthe result is not structurally sound:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1

    if args.out:
        archive.write(args.out)
        print(f"\nwrote {args.out}")
    else:
        print("\n(no --out given, so nothing was written)")
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
    use_utf8_output()
    args = build_parser().parse_args(argv)

    if args.command == "versions":
        return _list_versions()

    if args.command == "gui":
        return _run_gui(args)

    if args.command == "diff":
        return _run_diff(args)

    if args.command == "check":
        return _run_check(args)

    if args.command == "list":
        return _run_list(args)

    if args.command == "summary":
        return _run_summary(args)

    if args.command == "archive":
        return _run_archive(args)

    if args.command == "exar":
        return _run_exar(args)

    if args.command == "sequences":
        return _run_sequences(args)

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
        tesseract=args.tesseract,
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
