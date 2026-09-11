"""A declarative description of the command line surface the GUI exposes.

The GUI is a front end for :mod:`siemens_protocol.cli`, not a second
implementation of it. Every command it can run is described here once, as
data, and that one description does two jobs: it is serialized to the browser,
which renders a form from it, and it is read back by :func:`build_argv`, which
turns the submitted form into the argument list. A flag added to the CLI
therefore needs one entry here rather than an edit to a hand-written form and a
matching edit to a hand-written argument builder, which are free to disagree.

Choices that the CLI derives from the installed package -- release profiles,
policy names, vocabulary releases -- are resolved when the specification is
built rather than written out, so a newly registered profile appears in the
GUI without any change to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..pipeline import OCR_ALWAYS, OCR_AUTO, OCR_NEVER
from ..policy import available as policy_available
from ..profiles import REGISTRY
from ..sequences import SELECTORS
from ..vocabulary import available as vocabulary_available

#: Field kinds the browser knows how to render.
#:
#: ``path``   a text box with a Browse button opening the file dialog
#: ``text``   a plain text box
#: ``int``    a numeric text box
#: ``choice`` a drop-down over ``choices``
#: ``flag``   a checkbox, contributing the bare flag when ticked
#: ``list``   a text box whose comma-separated entries repeat the flag
#: ``pair``   two path boxes, contributing the flag and both values
KINDS = ("path", "text", "int", "choice", "flag", "list", "pair")

#: What a ``path`` field is asking for, which selects the browser's dialog.
#:
#: ``file``    an existing file
#: ``dir``     an existing directory
#: ``any``     either one
#: ``save``    a path to write, which need not exist yet
PICKERS = ("file", "dir", "any", "save")


@dataclass(frozen=True)
class Field:
    """One control on a command's form, and one argument on its command line.

    Attributes
    ----------
    name : str
        Key the browser submits this control under. Unique within a command.
    kind : str
        One of :data:`KINDS`, which decides both the control and how the
        value is turned into arguments.
    label : str
        Short caption shown beside the control.
    help : str
        One line of explanation, shown under the control. Taken from the
        CLI's own help text so the two cannot drift apart in meaning.
    flag : str or None, optional
        The option this field supplies, such as ``"--out"``. ``None`` marks a
        positional argument, which is emitted without a flag. Default ``None``.
    default : Any, optional
        Value the form starts with. Also the value suppressed from the command
        line when ``omit_if_default`` is set. Default ``""``.
    choices : tuple of str, optional
        Permitted values for a ``choice`` field. Default empty.
    picker : str, optional
        For a ``path`` field, which of :data:`PICKERS` to offer. Default
        ``"file"``.
    accept : tuple of str, optional
        Lower-case suffixes the file dialog should highlight, such as
        ``(".pdf",)``. Empty means every file. Default empty.
    required : bool, optional
        Whether the form refuses to submit without a value. Default ``False``.
    """

    name: str
    kind: str
    label: str
    help: str
    flag: str | None = None
    default: Any = ""
    choices: tuple[str, ...] = ()
    picker: str = "file"
    accept: tuple[str, ...] = ()
    required: bool = False

    def __post_init__(self) -> None:
        """Reject a field the browser or the argument builder could not honour.

        Returns
        -------
        None

        Raises
        ------
        ValueError
            If the kind or picker is not one this module defines, or a
            ``choice`` field offers nothing to choose from.
        """
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: unknown field kind {self.kind!r}")
        if self.picker not in PICKERS:
            raise ValueError(f"{self.name}: unknown picker {self.picker!r}")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"{self.name}: a choice field needs choices")

    def to_dict(self) -> dict:
        """Serialize this field for the browser.

        Returns
        -------
        dict
            A JSON-ready mapping of every attribute, with tuples as lists.
        """
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "help": self.help,
            "flag": self.flag,
            "default": self.default,
            "choices": list(self.choices),
            "picker": self.picker,
            "accept": list(self.accept),
            "required": self.required,
        }


@dataclass(frozen=True)
class Command:
    """One runnable command: a tab's worth of form, and one ``argv`` to build.

    Attributes
    ----------
    name : str
        Identifier the browser submits to say which command to run.
    group : str
        Tab this command belongs to. Several commands sharing a group get a
        selector within that tab, which is how ``vocab``'s three actions are
        presented without inventing three top-level tabs.
    title : str
        Caption for the command within its group.
    summary : str
        A sentence describing what running it does, shown above the form.
    argv : tuple of str
        The subcommand words leading the argument list, such as
        ``("vocab", "suggest")``.
    fields : tuple of Field, optional
        Controls, in the order they are shown. Positional fields must come
        before any others, because they are emitted in declaration order.
        Default empty, which is a command with no options at all.
    """

    name: str
    group: str
    title: str
    summary: str
    argv: tuple[str, ...]
    fields: tuple[Field, ...] = ()

    def to_dict(self) -> dict:
        """Serialize this command for the browser.

        Returns
        -------
        dict
            A JSON-ready mapping including every field's serialization.
        """
        return {
            "name": self.name,
            "group": self.group,
            "title": self.title,
            "summary": self.summary,
            "argv": list(self.argv),
            "fields": [item.to_dict() for item in self.fields],
        }


def _scan_field(what: str) -> Field:
    """Describe the flag narrowing a one-file command to a single scan.

    Parameters
    ----------
    what : str
        What the command produces, completing "restrict the ... to one scan".

    Returns
    -------
    Field
        The control, shared by every command that reads one protocol.
    """
    return Field(
        name="scan",
        kind="text",
        label="Scan",
        help=(
            f"Restrict the {what} to one scan. Its name, a zero-based index, or as "
            "much of its path as it takes to name one, such as 'CMRR spectro "
            "scans/eja_svs_slaser'. Add '#2' for a name the protocol uses twice. "
            "Leave empty for the whole protocol."
        ),
        flag="--scan",
    )


def _side_program_fields() -> tuple[Field, ...]:
    """Describe the flags picking a protocol per side of a two-input command.

    ``diff`` and the two ``vocab`` actions take two exports, so the single
    ``--program`` the one-input commands use cannot say which protocol
    belongs to which side.

    Returns
    -------
    tuple of Field
        The left and right controls, in that order.
    """
    return tuple(
        Field(
            name=f"{side}_program",
            kind="text",
            label=f"{side.capitalize()} protocol",
            help=(
                f"Which protocol to take from the {side} .exar1 archive. Needed "
                "only when it holds more than one and no scan address says which. "
                "Give as much of its path as it takes to name one."
            ),
            flag=f"--{side}-program",
        )
        for side in ("left", "right")
    )


def _program_field() -> Field:
    """Describe the flag picking one protocol out of a multi-program archive.

    Returns
    -------
    Field
        The control, shared by every command that accepts an archive.
    """
    return Field(
        name="program",
        kind="text",
        label="Protocol",
        help=(
            "Which protocol of an .exar1 archive to read. Needed only when the "
            "archive holds more than one, which a scanner backup does. Give as "
            "much of its path as it takes to name one."
        ),
        flag="--program",
    )


def _release_field(help_text: str) -> Field:
    """Build the release-profile drop-down shared by four commands.

    The choices come from the profile registry rather than a written-out list,
    which is the same source ``--release`` validates against, so a release
    added to the registry appears here with no change to this module.

    Parameters
    ----------
    help_text : str
        The CLI's help for ``--release`` on the command in question.

    Returns
    -------
    Field
        A ``choice`` field over ``auto`` and every registered profile.
    """
    return Field(
        name="release",
        kind="choice",
        label="Release",
        help=help_text,
        flag="--release",
        default="auto",
        choices=("auto", *REGISTRY.names()),
    )


def _parse_command() -> Command:
    """Describe the ``parse`` subcommand.

    Returns
    -------
    Command
        The form and argument list for parsing one PDF or a directory of them.
    """
    return Command(
        name="parse",
        group="Parse",
        title="Parse",
        summary=(
            "Parse a protocol PDF into hierarchical JSON, or every PDF under a "
            "directory. A batch run mirrors the input tree rather than "
            "flattening it, so two releases of one protocol cannot overwrite "
            "each other."
        ),
        argv=("parse",),
        fields=(
            Field(
                name="input",
                kind="path",
                label="Input",
                help="A PDF file, or a directory of PDFs.",
                picker="any",
                accept=(".pdf",),
                required=True,
            ),
            Field(
                name="out",
                kind="path",
                label="Output",
                help="Write JSON here. Left empty, it lands beside the input with a .json suffix.",
                flag="--out",
                picker="save",
            ),
            _release_field("Force a Siemens release profile instead of detecting one."),
            _scan_field("JSON"),
            Field(
                name="ocr",
                kind="choice",
                label="OCR",
                help=(
                    "Control the OCR fallback. Every shipped example has a clean text "
                    "layer, so 'always' is how you exercise this path deliberately."
                ),
                flag="--ocr",
                default=OCR_AUTO,
                choices=(OCR_AUTO, OCR_ALWAYS, OCR_NEVER),
            ),
            Field(
                name="dpi",
                kind="int",
                label="DPI",
                help="Rasterization resolution for pages that take the OCR path.",
                flag="--dpi",
                default="300",
            ),
            Field(
                name="tesseract",
                kind="path",
                label="tesseract",
                help=(
                    "Path to the tesseract binary, needed only where it is installed "
                    "off PATH, as the Windows installer leaves it."
                ),
                flag="--tesseract",
                picker="file",
            ),
            Field(
                name="no_flatten",
                kind="flag",
                label="Omit the flattened view",
                help=(
                    "Leave out the per-scan flattened view. Note that diff and check "
                    "both need it, so JSON written this way cannot feed them."
                ),
                flag="--no-flatten",
                default=False,
            ),
            Field(
                name="stdout",
                kind="flag",
                label="Show JSON here",
                help="Write the JSON to the output pane below instead of to a file.",
                flag="--stdout",
                default=False,
            ),
            Field(
                name="emit_debug",
                kind="path",
                label="Debug dump",
                help="Dump per-span geometry here, for tuning a new release profile.",
                flag="--emit-debug",
                picker="save",
            ),
            Field(
                name="quiet",
                kind="flag",
                label="Quiet",
                help="Suppress the per-file summary.",
                flag="--quiet",
                default=False,
            ),
        ),
    )


def _diff_command() -> Command:
    """Describe the ``diff`` subcommand.

    Returns
    -------
    Command
        The form and argument list for comparing protocols or single scans.
    """
    return Command(
        name="diff",
        group="Diff",
        title="Diff",
        summary=(
            "Compare two protocols scan by scan, or compare two individual scans. "
            "Naming a scan on either side switches to comparing single scans: one "
            "from each input, or two from the same input when only one is given. "
            "A side left unnamed takes the other side's name."
        ),
        argv=("diff",),
        fields=(
            Field(
                name="left",
                kind="path",
                label="Left",
                help="A PDF, or JSON this tool wrote earlier.",
                picker="file",
                accept=(".pdf", ".exar1", ".json"),
                required=True,
            ),
            Field(
                name="right",
                kind="path",
                label="Right",
                help=(
                    "A second PDF or JSON. Leave empty to compare two scans within "
                    "the left file, which then needs a scan named on both sides."
                ),
                picker="file",
                accept=(".pdf", ".exar1", ".json"),
            ),
            Field(
                name="left_scan",
                kind="text",
                label="Left scan",
                help=(
                    "Scan to take from the left input: its name, a zero-based index, "
                    "or as much of its path as it takes to name one, such as "
                    "'CMRR spectro scans/eja_svs_slaser'. Add '#2' for a name the "
                    "protocol uses twice."
                ),
                flag="--left-scan",
            ),
            Field(
                name="right_scan",
                kind="text",
                label="Right scan",
                help="Scan to take from the right input, spelled as the left one is.",
                flag="--right-scan",
            ),
            *_side_program_fields(),
            _release_field("Force a Siemens release profile for any PDF input."),
            Field(
                name="sections",
                kind="list",
                label="Sections",
                help=(
                    "Report only these top-level sections -- routine, contrast, "
                    "resolution, geometry, header and so on. Comma-separated for "
                    "several; a full name such as 'Contrast - Common' is folded to "
                    "its top level."
                ),
                flag="--filter",
            ),
            Field(
                name="exact_keys",
                kind="flag",
                label="Exact keys",
                help="Do not match relabeled keys; compare key spellings literally.",
                flag="--exact-keys",
                default=False,
            ),
            Field(
                name="no_vocabulary",
                kind="flag",
                label="Ignore vocabularies",
                help="Ignore the per-release vocabularies that map renamed parameters.",
                flag="--no-vocabulary",
                default=False,
            ),
            Field(
                name="vocabulary",
                kind="path",
                label="Vocabulary overlay",
                help="A directory of vocabulary JSON files overlaying the shipped ones.",
                flag="--vocabulary",
                picker="dir",
            ),
            Field(
                name="show_cosmetic",
                kind="flag",
                label="Show cosmetic differences",
                help="List relabeled, recased and reformatted differences individually.",
                flag="--show-cosmetic",
                default=False,
            ),
            Field(
                name="show_identical",
                kind="flag",
                label="Show identical scans",
                help="Include scans that have no differences at all.",
                flag="--show-identical",
                default=False,
            ),
            Field(
                name="json",
                kind="flag",
                label="JSON output",
                help="Emit the comparison as JSON rather than a report.",
                flag="--json",
                default=False,
            ),
            Field(
                name="out",
                kind="path",
                label="Write report to",
                help="Write the report here. Left empty, it appears in the pane below.",
                flag="--out",
                picker="save",
            ),
        ),
    )


def _check_command() -> Command:
    """Describe the ``check`` subcommand.

    Returns
    -------
    Command
        The form and argument list for checking scans against a policy.
    """
    return Command(
        name="check",
        group="Check",
        title="Check",
        summary=(
            "Check each scan against a policy of preferred values. A rule applies "
            "only where its parameter is present, so a rule about multiband "
            "excitation stays silent on scans that never had the setting."
        ),
        argv=("check",),
        fields=(
            Field(
                name="input",
                kind="path",
                label="Input",
                help="A PDF, an .exar1 archive, a parsed JSON file, or a directory of PDFs.",
                picker="any",
                accept=(".pdf", ".exar1", ".json"),
                required=True,
            ),
            _program_field(),
            _scan_field("check"),
            Field(
                name="policy",
                kind="choice",
                label="Policy",
                help="A shipped policy name. Add a directory below to offer your own.",
                flag="--policy",
                default="default",
                choices=tuple(policy_available()) or ("default",),
            ),
            Field(
                name="policy_dir",
                kind="path",
                label="Policy directory",
                help="A directory of policies searched before the shipped ones.",
                flag="--policy-dir",
                picker="dir",
            ),
            _release_field("Force a Siemens release profile for any PDF input."),
            Field(
                name="warnings_ok",
                kind="flag",
                label="Warnings are acceptable",
                help="Report success when only warnings were found.",
                flag="--warnings-ok",
                default=False,
            ),
            Field(
                name="json",
                kind="flag",
                label="JSON output",
                help="Emit the findings as JSON rather than a report.",
                flag="--json",
                default=False,
            ),
            Field(
                name="quiet",
                kind="flag",
                label="Quiet",
                help="Print only violations.",
                flag="--quiet",
                default=False,
            ),
            Field(
                name="out",
                kind="path",
                label="Write report to",
                help="Write the report here. Left empty, it appears in the pane below.",
                flag="--out",
                picker="save",
            ),
        ),
    )


def _list_command() -> Command:
    """Describe the ``list`` subcommand.

    Returns
    -------
    Command
        The form and argument list for listing a protocol's scans.
    """
    return Command(
        name="list",
        group="List",
        title="List scans",
        summary=(
            "List one protocol's scans in acquisition order -- index, name, sequence "
            "and acquisition time -- and total the scan time. Times are shown as the "
            "export prints them, which differs by release; the total is normalized."
        ),
        argv=("list",),
        fields=(
            Field(
                name="input",
                kind="path",
                label="Input",
                help="A PDF, an .exar1 archive, or JSON this tool wrote earlier.",
                picker="file",
                accept=(".pdf", ".exar1", ".json"),
                required=True,
            ),
            _release_field("Force a Siemens release profile for a PDF input."),
            _program_field(),
            _scan_field("listing"),
            Field(
                name="json",
                kind="flag",
                label="JSON output",
                help="Emit the listing as JSON rather than a table.",
                flag="--json",
                default=False,
            ),
            Field(
                name="out",
                kind="path",
                label="Write listing to",
                help="Write the listing here. Left empty, it appears in the pane below.",
                flag="--out",
                picker="save",
            ),
        ),
    )


def _summary_command() -> Command:
    """Describe the ``summary`` subcommand.

    Returns
    -------
    Command
        The form for summarizing one protocol.
    """
    return Command(
        name="summary",
        group="List",
        title="Summarize",
        summary=(
            "Summarize one protocol in a block rather than a line per scan: how many "
            "scans it runs, how long it takes, its longest and shortest scan, and a "
            "census of the distinct sequences with the scans and time each accounts "
            "for. A scan printing no readable acquisition time is excluded from the "
            "total and counted, which is why an archive can total a few seconds under "
            "its own printout."
        ),
        argv=("summary",),
        fields=(
            Field(
                name="input",
                kind="path",
                label="Input",
                help="A PDF, an .exar1 archive, or JSON this tool wrote earlier.",
                picker="file",
                accept=(".pdf", ".exar1", ".json"),
                required=True,
            ),
            _release_field("Force a Siemens release profile for a PDF input."),
            _program_field(),
            _scan_field("summary"),
            Field(
                name="catalog",
                kind="path",
                label="Catalog overlay",
                help="A directory of signature catalogs overlaying the shipped one.",
                flag="--catalog",
                picker="dir",
            ),
            Field(
                name="json",
                kind="flag",
                label="JSON output",
                help="Emit the summary as JSON rather than a report.",
                flag="--json",
                default=False,
            ),
            Field(
                name="out",
                kind="path",
                label="Write summary to",
                help="Write the summary here. Left empty, it appears in the pane below.",
                flag="--out",
                picker="save",
            ),
        ),
    )


def _sequences_command() -> Command:
    """Describe the ``sequences`` subcommand.

    Returns
    -------
    Command
        The form and argument list for reporting third-party sequences.
    """
    return Command(
        name="sequences",
        group="Sequences",
        title="Third-party sequences",
        summary=(
            "Say which of a protocol's scans run a sequence Siemens did not supply. "
            "Siemens' own conversion handles stock sequences between releases; a "
            "third-party sequence is what has to be rebuilt and checked by hand, so "
            "this is the list of work a migration implies. A scan the catalog cannot "
            "account for is reported as unrecognized rather than guessed at."
        ),
        argv=("sequences",),
        fields=(
            Field(
                name="input",
                kind="path",
                label="Input",
                help="A PDF, an .exar1 archive, or JSON this tool wrote earlier.",
                picker="file",
                accept=(".pdf", ".exar1", ".json"),
                required=True,
            ),
            _release_field("Force a Siemens release profile for a PDF input."),
            _program_field(),
            _scan_field("report"),
            Field(
                name="only",
                kind="choice",
                label="List only",
                help=(
                    "Restrict which scans the table lists. 'flagged' means third-party "
                    "and unrecognized together, which is the rebuild list. The counts "
                    "always cover every scan."
                ),
                flag="--only",
                default="",
                choices=("", *sorted(SELECTORS)),
            ),
            Field(
                name="explain",
                kind="flag",
                label="Explain",
                help="Show the evidence behind each identification, and the catalog's notes.",
                flag="--explain",
                default=False,
            ),
            Field(
                name="catalog",
                kind="path",
                label="Catalog overlay",
                help="A directory of signature catalogs overlaying the shipped one.",
                flag="--catalog",
                picker="dir",
            ),
            Field(
                name="json",
                kind="flag",
                label="JSON output",
                help="Emit the findings as JSON rather than a table.",
                flag="--json",
                default=False,
            ),
            Field(
                name="out",
                kind="path",
                label="Write report to",
                help="Write the report here. Left empty, it appears in the pane below.",
                flag="--out",
                picker="save",
            ),
        ),
    )


def _vocab_commands() -> tuple[Command, ...]:
    """Describe the three ``vocab`` actions.

    They share one tab, selected within it, because they are three views of a
    single subject rather than three unrelated jobs.

    Returns
    -------
    tuple of Command
        The ``list``, ``check`` and ``suggest`` actions, in that order.
    """
    overlay = Field(
        name="vocabulary",
        kind="path",
        label="Overlay directory",
        help="A directory of vocabulary JSON files overlaying the shipped ones.",
        flag="--vocabulary",
        picker="dir",
    )
    releases = tuple(vocabulary_available())
    return (
        Command(
            name="vocab-list",
            group="Vocabulary",
            title="List mappings",
            summary=(
                "Show how one release's parameter labels map onto shared canonical "
                "names, or how every release spells one canonical name."
            ),
            argv=("vocab", "list"),
            fields=(
                Field(
                    name="version",
                    kind="choice",
                    label="Release",
                    help="A release name. Leave on 'all' for every release at once.",
                    default="",
                    choices=("", *releases),
                ),
                Field(
                    name="canonical",
                    kind="text",
                    label="Canonical name",
                    help="Show what each release calls this one canonical name.",
                    flag="--canonical",
                ),
                overlay,
            ),
        ),
        Command(
            name="vocab-check",
            group="Vocabulary",
            title="Check vocabularies",
            summary=(
                "Validate the vocabularies against each other, and optionally "
                "against two real exports of one protocol from different releases."
            ),
            argv=("vocab", "check"),
            fields=(
                Field(
                    name="against",
                    kind="pair",
                    label="Check against",
                    help=(
                        "Two exports of one protocol from different releases. Give "
                        "both or neither."
                    ),
                    flag="--against",
                    picker="file",
                    accept=(".pdf", ".exar1", ".json"),
                ),
                *_side_program_fields(),
                overlay,
            ),
        ),
        Command(
            name="vocab-suggest",
            group="Vocabulary",
            title="Suggest mappings",
            summary=(
                "Propose candidate mappings from a matched pair of exports. "
                "Candidates come with evidence and are never applied: an incorrect "
                "entry hides a real difference, so adding one is a human decision."
            ),
            argv=("vocab", "suggest"),
            fields=(
                Field(
                    name="left",
                    kind="path",
                    label="Left export",
                    help="An export of one release.",
                    picker="file",
                    accept=(".pdf", ".exar1", ".json"),
                    required=True,
                ),
                Field(
                    name="right",
                    kind="path",
                    label="Right export",
                    help="The same protocol exported from another release.",
                    picker="file",
                    accept=(".pdf", ".exar1", ".json"),
                    required=True,
                ),
                *_side_program_fields(),
                Field(
                    name="min_support",
                    kind="int",
                    label="Minimum support",
                    help="Ignore candidates seen in fewer scans than this.",
                    flag="--min-support",
                    default="8",
                ),
                overlay,
            ),
        ),
    )


def _archive_command() -> Command:
    """Describe the ``archive`` subcommand.

    Returns
    -------
    Command
        Its form and its command line.
    """
    return Command(
        name="archive",
        group="Archive",
        title="Read an .exar1 archive",
        summary=(
            "Read a protocol archive into JSON that can be browsed or queried. It "
            "carries the console's Preview summary under each scan's printed labels, "
            "the whole ASCCONV parameter block nested by the structure its key names "
            "describe, the slice geometry, and the prescription links between scans "
            "-- which a printout does not record at all."
        ),
        argv=("archive",),
        fields=(
            Field(
                name="input",
                kind="path",
                label="Archive",
                help="The .exar1 archive to read. It is not modified.",
                picker="file",
                accept=(".exar1",),
                required=True,
            ),
            _program_field(),
            _scan_field("document"),
            Field(
                name="out",
                kind="path",
                label="Write JSON to",
                help="Where to write the document. Left empty, it goes beside the archive.",
                flag="--out",
                picker="save",
                accept=(".json",),
            ),
            Field(
                name="stdout",
                kind="flag",
                label="Show instead of writing",
                help="Write the JSON to the output pane rather than to a file.",
                flag="--stdout",
            ),
            Field(
                name="no_ascconv",
                kind="flag",
                label="Omit the parameter tree",
                help=(
                    "Leave out the ASCCONV block, which is the bulk of the document "
                    "-- 514 to 2020 assignments a scan."
                ),
                flag="--no-ascconv",
            ),
        ),
    )


def _exar_command() -> Command:
    """Describe the archive-writing command.

    Returns
    -------
    Command
        The command's form and argument construction.
    """
    return Command(
        name="exar",
        group="Archive",
        title="Write into an .exar1 archive",
        summary=(
            "Take a template .exar1 archive and a protocol PDF, write every parameter "
            "that has a verified mapping, and report what could not be written. Only a "
            "fraction of what a protocol prints is mapped, so the result is mostly the "
            "template it started from -- the manifest says how much, and names the "
            "parameters no mapping covers. Nothing is written without a destination."
        ),
        argv=("exar",),
        fields=(
            Field(
                name="archive",
                kind="path",
                label="Template archive",
                help="The .exar1 archive to write into. It is not modified in place.",
                picker="file",
                accept=(".exar1",),
                required=True,
            ),
            Field(
                name="input",
                kind="path",
                label="Protocol",
                help="A PDF, or JSON this tool wrote earlier.",
                picker="file",
                accept=(".pdf", ".json"),
                required=True,
            ),
            _release_field("Force a Siemens release profile for a PDF input."),
            _program_field(),
            Field(
                name="out",
                kind="path",
                label="Write archive to",
                help=(
                    "Where to write the result. Left empty, the manifest is shown and "
                    "no archive is written."
                ),
                flag="--out",
                picker="save",
                accept=(".exar1",),
            ),
            Field(
                name="show",
                kind="int",
                label="Entries to list",
                help="How many written and unmapped entries the manifest names.",
                flag="--show",
                default=12,
            ),
        ),
    )


def _versions_command() -> Command:
    """Describe the ``versions`` subcommand.

    Returns
    -------
    Command
        A command with no options, listing the registered release profiles.
    """
    return Command(
        name="versions",
        group="Releases",
        title="Known releases",
        summary=(
            "List the release profiles this build knows about, and whether each "
            "expects a native text layer or the OCR fallback."
        ),
        argv=("versions",),
    )


def command_specs() -> tuple[Command, ...]:
    """Build the full specification of what the GUI can run.

    Rebuilt on each call rather than cached at import, so a vocabulary or
    policy directory added to the installation is picked up by reloading the
    page instead of restarting the server.

    Returns
    -------
    tuple of Command
        Every command, in the order their tabs are shown.
    """
    return (
        _parse_command(),
        _diff_command(),
        _check_command(),
        _list_command(),
        _summary_command(),
        _archive_command(),
        _exar_command(),
        _sequences_command(),
        *_vocab_commands(),
        _versions_command(),
    )


def command_index() -> dict[str, Command]:
    """Index the specification by command name.

    Returns
    -------
    dict of str to Command
        Every command from :func:`command_specs`, keyed by its ``name``.
    """
    return {item.name: item for item in command_specs()}


def _split_list(raw: str) -> list[str]:
    """Split a list field's text into individual values.

    Commas and newlines both separate, because a long ``--filter`` list reads
    better one per line while a short one reads better inline.

    Parameters
    ----------
    raw : str
        The submitted text.

    Returns
    -------
    list of str
        Non-empty values, stripped of surrounding whitespace.
    """
    parts = raw.replace("\n", ",").split(",")
    return [part.strip() for part in parts if part.strip()]


def _field_argv(spec: Field, value: Any) -> list[str]:
    """Turn one field's submitted value into command line arguments.

    Parameters
    ----------
    spec : Field
        The field's declaration.
    value : Any
        What the browser submitted for it.

    Returns
    -------
    list of str
        Zero or more arguments. Empty when the field was left at a value that
        the CLI would default to anyway, which keeps the displayed command
        line short enough to read.

    Raises
    ------
    ValueError
        If a required field is empty, an integer field does not hold one, or a
        ``pair`` field was given exactly one of its two values.
    """
    if spec.kind == "flag":
        return [spec.flag] if bool(value) and spec.flag else []

    if spec.kind == "pair":
        pair = [str(part).strip() for part in (value or ["", ""])]
        pair = (pair + ["", ""])[:2]
        if not any(pair):
            return []
        if not all(pair):
            raise ValueError(f"{spec.label}: give both values or neither")
        return [spec.flag, *pair] if spec.flag else pair

    text = "" if value is None else str(value).strip()

    if spec.kind == "list":
        entries = _split_list(text)
        if not entries or not spec.flag:
            return []
        return [argument for entry in entries for argument in (spec.flag, entry)]

    if not text:
        if spec.required:
            raise ValueError(f"{spec.label} is required")
        return []

    if spec.kind == "int":
        try:
            int(text)
        except ValueError:
            raise ValueError(f"{spec.label}: expected a whole number, got {text!r}") from None

    # A value the CLI would have chosen anyway is left off, so the command
    # line shown to the user is the short one they would have typed.
    if text == str(spec.default):
        return []

    return [spec.flag, text] if spec.flag else [text]


def build_argv(name: str, values: Mapping[str, Any]) -> list[str]:
    """Build the argument list for a submitted form.

    Fields are emitted in declaration order, which is what keeps positional
    arguments in the order ``argparse`` expects them.

    Parameters
    ----------
    name : str
        The command's ``name``, as submitted by the browser.
    values : Mapping
        Field name to submitted value.

    Returns
    -------
    list of str
        Arguments to pass to ``siemens-protocol-tool``, subcommand words first.

    Raises
    ------
    KeyError
        If ``name`` is not a command this module describes.
    ValueError
        If any field's value cannot be turned into arguments.
    """
    command = command_index()[name]
    argv = list(command.argv)
    for spec in command.fields:
        argv.extend(_field_argv(spec, values.get(spec.name)))
    return argv


def display_command(argv: Sequence[str]) -> str:
    """Render an argument list as a command line the user could retype.

    Parameters
    ----------
    argv : Sequence of str
        Arguments, as :func:`build_argv` returns them.

    Returns
    -------
    str
        The full command line, with any argument containing whitespace or a
        shell metacharacter quoted.
    """
    parts = ["siemens-protocol-tool"]
    for argument in argv:
        if argument and not any(character in argument for character in " \t'\"\\$`*?()[]{}|&;<>#"):
            parts.append(argument)
        else:
            parts.append("'" + argument.replace("'", "'\\''") + "'")
    return " ".join(parts)
