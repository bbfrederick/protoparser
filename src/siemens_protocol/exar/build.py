"""Drive an archive from a parsed PDF: apply what is mapped, report the rest.

This is the layer a person actually uses. Give it a template archive and a
protocol parsed out of a PDF, and it writes every parameter both sides agree
on, then says plainly what it could not write.

The reporting is the point as much as the writing. Only a fraction of what a
protocol prints has a verified mapping, so an archive this produces is mostly
the template it started from. A tool that reported only its successes would
be describing a small part of the result and implying the whole; the manifest
therefore counts inherited values, names the printed parameters no mapping
covers, and orders them by how often they actually differ between scans --
which is what says where the next mapping is worth deriving.

Scans are matched to the template by name. The PDF's sequence field is the
kernel (``epfid``) and the archive's is the sequence file (``cmrr_mbep2d_bold``),
so an unmatched scan cannot be paired with a donor to copy without guessing,
and this module does not guess: it reports the scan as unmatched and leaves
adding it to a caller who knows which template scan it resembles.

Matching normalizes the printed name first, because a printout may decorate
it. A protocol the scanner converted from an earlier release prints an
asterisk after every scan name -- in the contents listing and in the header
path both, ``...\\K23EB_20210802\\localizer *`` -- while the archive's own
label carries none. Comparing the two raw therefore matched none of the 24
scans in that export and wrote nothing, which reads exactly like a template
holding a different protocol. The parser is right to keep the asterisk, since
it is really printed; the join is what has to tolerate it.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Any
from typing import Mapping as MappingType

from . import geometry, patch
from .archive import Archive

#: Units the card prints beside a value and the protocol does not store.
UNIT_SUFFIX = re.compile(
    r"\s+(ms|s|mm|cm|deg|degree|degrees|Hz|Hz/Px|kHz|%|TRs|TR|min|sec|mT/m|ppm)\s*$",
    re.I,
)


def printed_value(text: Any) -> Any:
    """Strip the unit a printout appends, leaving what the protocol stores.

    Parameters
    ----------
    text : Any
        A value as the PDF prints it, for example ``"650.0 ms"``.

    Returns
    -------
    Any
        The value without its unit, unchanged when it carries none.
    """
    if not isinstance(text, str):
        return text
    # The unit has to follow whitespace. Matching it anywhere turns "RMS" into
    # "R", because "MS" is a unit and the comparison is case-insensitive --
    # which then fails to resolve as an Averaging choice. Strip repeatedly,
    # since a value can carry more than one suffix.
    stripped = text.strip()
    while True:
        shorter = UNIT_SUFFIX.sub("", stripped).strip()
        if shorter == stripped:
            return stripped
        stripped = shorter


def printed_parameters(scan: MappingType[str, Any]) -> dict[str, Any]:
    """Flatten one parsed scan to ``{parameter: printed value}``.

    Parameters
    ----------
    scan : mapping
        One entry of a parsed protocol's ``scans``.

    Returns
    -------
    dict
        Every parameter the scan prints, keyed by printed name.
    """
    return {
        key: (item.get("value") if isinstance(item, dict) else item)
        for key, item in (scan.get("flat") or {}).items()
    }


@dataclass
class BuildReport:
    """What a build wrote, refused, and left as it found it.

    Attributes
    ----------
    applied : list
        Values written, as :class:`patch.Applied` records.
    skipped : list
        Values a mapping claimed and could not write, with reasons.
    unchanged : int
        Mapped parameters whose printed value already matched the template.
    inherited : collections.Counter
        Printed parameters no mapping covers, counted across matched scans.
    out_of_scope : collections.Counter
        The subset of ``inherited`` that some mapping *does* cover, for a
        different sequence or a different build of the same one. These are
        the next derivations worth making, so they are named rather than
        left indistinguishable from parameters nothing knows about.
    reasons : dict
        One representative reason per label in ``out_of_scope``, as
        :func:`patch.resolve` phrased it.
    matched : list of str
        Scan names present in both the PDF and the template.
    unmatched : list of str
        Scans the PDF prints that the template does not hold.
    untouched : list of str
        Template scans the PDF does not mention, left exactly as they were.
    """

    applied: list[patch.Applied] = field(default_factory=list)
    skipped: list[patch.Skipped] = field(default_factory=list)
    unchanged: int = 0
    inherited: collections.Counter = field(default_factory=collections.Counter)
    out_of_scope: collections.Counter = field(default_factory=collections.Counter)
    reasons: dict[str, str] = field(default_factory=dict)
    matched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    untouched: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> tuple[int, int]:
        """Return how much of what the PDF prints this build can write.

        Returns
        -------
        tuple of int
            Parameters written or confirmed, and parameters printed in total.
        """
        writable = len(self.applied) + self.unchanged
        return (writable, writable + sum(self.inherited.values()))

    def report(self, limit: int = 12) -> str:
        """Render the manifest for a person to read.

        Parameters
        ----------
        limit : int, optional
            How many unmapped parameters to name.

        Returns
        -------
        str
            The manifest.
        """
        written, total = self.coverage
        share = f"{100 * written / total:.0f}%" if total else "n/a"
        lines = [
            f"matched {len(self.matched)} scan(s); "
            f"{len(self.unmatched)} in the PDF are not in the template; "
            f"{len(self.untouched)} template scan(s) untouched",
            f"wrote {len(self.applied)} value(s), {self.unchanged} already matched, "
            f"{len(self.skipped)} refused",
            f"coverage: {written} of {total} printed parameters ({share}) have a mapping",
        ]
        for one in self.applied[:limit]:
            lines.append(f"  set   {one.step}: {one.label} {one.previous} -> {one.value}")
        if len(self.applied) > limit:
            lines.append(f"  ... and {len(self.applied) - limit} more")
        for miss in self.skipped[:limit]:
            lines.append(f"  skip  {miss.step}: {miss.label} -- {miss.reason}")
        if self.unmatched:
            lines.append("scans with no template counterpart: " + ", ".join(self.unmatched[:8]))
        if self.inherited:
            lines.append("most common printed parameters with no mapping:")
            for name, count in self.inherited.most_common(limit):
                lines.append(f"  {count:4d}x {name}")
        if self.out_of_scope:
            lines.append("of those, already mapped for another sequence or build:")
            for name, count in self.out_of_scope.most_common(limit):
                lines.append(f"  {count:4d}x {name} -- {self.reasons.get(name, '')}")
        return "\n".join(lines)


def apply_protocol(archive: Archive, parsed: MappingType[str, Any]) -> BuildReport:
    """Write every mapped parameter a parsed PDF and a template agree on.

    The archive is edited in memory; call :meth:`Archive.write` to save, and
    :func:`validate.problems` to check the result before trusting it.

    Parameters
    ----------
    archive : Archive
        Template archive, modified in place.
    parsed : mapping
        A protocol as ``siemens_protocol`` parses it, with a ``scans`` list.

    Returns
    -------
    BuildReport
        What was written, refused and inherited.
    """
    report = BuildReport()
    # A pause step carries no protocol and the PDF does not print it as a scan,
    # so it can never be the counterpart of one.
    steps: dict[str, list[Any]] = {}
    for step in archive.steps:
        if step.runs_a_protocol:
            steps.setdefault(match_name(step.name), []).append(step)

    scans: dict[str, list[Any]] = {}
    for scan in parsed.get("scans", []):
        scans.setdefault(match_name(scan.get("name", "")), []).append(scan)

    seen: set[str] = set()
    for name, printed in scans.items():
        held = steps.get(name, [])
        # A name repeats: a protocol may run four scans called Localizer. Pair
        # them in running order, which both sides preserve -- but only when the
        # two sides agree on how many there are. Otherwise which is which is a
        # guess, and guessing would write one scan's values into another.
        if not held or len(held) != len(printed):
            report.unmatched.extend(name for _ in printed)
            continue
        seen.add(name)
        for step, scan in zip(held, printed):
            report.matched.append(name)
            _apply_scan(archive, step, scan, report)
    report.untouched = [n for n in steps if n not in seen]
    return report


#: Decoration a printout may add after a scan name. The console appends this
#: to every scan of a protocol it converted from an earlier release; what it
#: signifies is not documented, and nothing here depends on knowing.
NAME_DECORATION = re.compile(r"\s*\*+\s*$")


def match_name(name: str) -> str:
    """Return a scan name in the form both sides of the join can be compared in.

    Parameters
    ----------
    name : str
        A scan name as the PDF prints it or as the archive labels it.

    Returns
    -------
    str
        The name with any trailing decoration and surrounding space removed.
        Applied to both sides, so an archive whose label somehow carried one
        would still pair.
    """
    return NAME_DECORATION.sub("", name).strip()


@dataclass
class Pairing:
    """Which programs an archive and a set of printouts have in common.

    Attributes
    ----------
    matched : list of tuple
        ``(program name, export key)``, sorted by name.
    unmatched_programs : list of str
        Programs the archive holds that no printout covers. Normal rather
        than exceptional: an investigator export carries every protocol and
        the PDFs are usually a subset.
    unmatched_exports : list of str
        Printouts naming a program the archive does not hold, or naming none
        at all.
    """

    matched: list[tuple[str, str]] = field(default_factory=list)
    unmatched_programs: list[str] = field(default_factory=list)
    unmatched_exports: list[str] = field(default_factory=list)


def program_name(parsed: MappingType[str, Any]) -> str | None:
    """Return the protocol name a printout declares in its scan paths.

    Read from the header rather than the file name. The scanner requires a
    program name to be unique within an exam, so the printed path identifies
    the protocol exactly; a file name is whatever someone called it after
    export and can be renamed, duplicated or truncated.

    Parameters
    ----------
    parsed : mapping
        A protocol as ``siemens_protocol`` parses it.

    Returns
    -------
    str or None
        The protocol component of the printed path, or ``None`` when the
        scans declare no path or disagree about it -- both of which are
        reported rather than raised, since a caller pairing a directory of
        exports wants the other files regardless.
    """
    found = {
        match_name(
            scan["path"].replace("\\", "/").rstrip("/").rsplit("/", 1)[0].rsplit("/", 1)[-1]
        )
        for scan in parsed.get("scans", [])
        if scan.get("path")
    }
    return found.pop() if len(found) == 1 else None


def pair_programs(archive: Archive, exports: MappingType[str, MappingType[str, Any]]) -> Pairing:
    """Pair an archive's programs with the printouts that cover them.

    An archive exported at the exam or region level holds every protocol an
    investigator has, and a set of PDFs beside it is usually a fraction of
    them. So an unmatched program is the ordinary case and never an error:
    everything that can be paired is, and the rest is reported.

    Parameters
    ----------
    archive : Archive
        The archive, which may hold one program or many.
    exports : mapping
        Parsed printouts keyed by whatever the caller wants back -- a path,
        typically.

    Returns
    -------
    Pairing
        What paired, and what did not on either side.
    """
    by_name: dict[str, list[str]] = {}
    for key, parsed in exports.items():
        name = program_name(parsed)
        if name is not None:
            by_name.setdefault(name, []).append(key)

    pairing = Pairing()
    held = {match_name(program.name) for program in archive.programs}
    for name in sorted(held):
        keys = by_name.get(name, [])
        # Two printouts naming one program is a duplicate export, and which
        # to believe is a guess. Report both sides rather than picking.
        if len(keys) == 1:
            pairing.matched.append((name, keys[0]))
        else:
            pairing.unmatched_programs.append(name)
    paired = {key for _name, key in pairing.matched}
    pairing.unmatched_exports = sorted(key for key in exports if key not in paired)
    return pairing


def agrees_at_printed_precision(printed: str, stored: Any) -> bool:
    """Return whether a stored value and a printed one are the same number.

    A printout carries fewer digits than the protocol: one scan prints
    ``TE 1 = 54 ms`` for a stored 54.16. Writing the printed value back would
    quietly drop 0.16 ms, so a printed value is treated as agreeing when the
    stored one rounds to it at the precision actually printed.

    Parameters
    ----------
    printed : str
        The value as the card prints it, units already removed.
    stored : Any
        What the protocol holds.

    Returns
    -------
    bool
        ``True`` when the two are the same number to the printed precision.
    """
    if not isinstance(stored, (int, float)) or isinstance(stored, bool):
        return False
    try:
        wanted = float(printed)
    except (TypeError, ValueError):
        return False
    _whole, _, fraction = str(printed).strip().partition(".")
    return round(float(stored), len(fraction)) == wanted


def stored_display(protocol: Any, mapping: Any) -> float | None:
    """Return the value a plain scaled mapping currently displays, if any.

    The preview is the usual way to learn what a protocol already shows, but
    it carries only about forty console-summary parameters: a multi-echo scan
    has ``TE`` there and nothing for ``TE 2``, and the Special card is absent
    from it entirely. Those mappings therefore reach the writer with no
    previous displayed value, and comparing the ASCCONV literals instead is an
    exact test -- so a stored 92.06 ms met a printed ``92`` and was written,
    silently dropping 0.06 ms on a no-op run.

    Only a plain scaled scalar can be inverted this way. A flag bit, an enum
    and a derived value are compared as they always were.

    Parameters
    ----------
    protocol : Protocol
        The protocol holding the ASCCONV block.
    mapping : patch.Mapping
        The mapping to invert.

    Returns
    -------
    float or None
        The displayed value, or ``None`` when the mapping is not a plain
        scaled scalar or the assignment is absent or unparsable.
    """
    if mapping.bit is not None or mapping.choices or mapping.basis is not None:
        return None
    if "[*]" in mapping.ascconv_key:
        return None
    literal = patch.read_ascconv(protocol.xprotocol, mapping.ascconv_key)
    if literal is None:
        return None
    try:
        return float(literal) / mapping.scale - mapping.offset
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _moved(record: patch.Applied) -> bool:
    """Return whether a written record actually changed anything.

    Parameters
    ----------
    record : patch.Applied
        One written value.

    Returns
    -------
    bool
        ``True`` when the stored value differs from what was there.
    """
    if record.previous is None:
        return record.ascconv_previous != record.ascconv_value
    return str(record.previous) != str(record.value)


def apply_direction(magnitude: Any, letter: Any, mapping: patch.Mapping) -> Any:
    """Turn a printed magnitude and its direction letter into a signed value.

    Siemens prints a position as a magnitude beside a letter naming the
    direction -- ``F32`` for a protocol holding ``-32`` -- and on a
    two-column card the letter lands in a field of its own. The magnitude on
    its own does not say which side of zero the value is on.

    Parameters
    ----------
    magnitude : Any
        The printed number, already stripped of its unit.
    letter : Any
        The companion field, or ``None`` when the printout does not carry one.
    mapping : Mapping
        The parameter being written, for its
        :attr:`~.patch.Mapping.negative_letters`.

    Returns
    -------
    Any
        The signed value, or ``None`` when the letter is absent or is not one
        this mapping knows -- which the caller must treat as "leave it alone"
        rather than writing the magnitude.
    """
    if letter is None:
        return None
    direction = str(letter).strip().upper()
    if not direction:
        return None
    if direction not in mapping.negative_letters and direction not in POSITIVE_LETTERS:
        return None
    try:
        number = float(str(magnitude).strip())
    except (TypeError, ValueError):
        return None
    return -abs(number) if direction in mapping.negative_letters else abs(number)


#: The letters naming the positive half of each axis. Right, anterior,
#: superior and head; their opposites are the negative half. Listed so a
#: letter belonging to neither -- a release spelling one differently -- is
#: refused rather than read as positive by default.
POSITIVE_LETTERS = ("R", "A", "S", "H")


def covered_elsewhere(protocol: Any, label: str) -> bool:
    """Say whether some mapping carries this label but not for this protocol.

    A label nothing in :data:`patch.MAPPINGS` knows about and one that is
    mapped for a different sequence -- or a different build of the same
    sequence -- both reach the manifest as "inherited", and they are not the
    same finding. The second is a mapping away from working, so the report
    names it. `Averaging` is the example: derived from ``tfl_mgh_multiecho``
    and therefore refused on ``tfl_mgh_epinav_ABCD``, which prints it too.

    The question is asked of the table rather than of :func:`patch.resolve`'s
    prose, so a reworded reason cannot silently reclassify anything.

    Parameters
    ----------
    protocol : Protocol
        The protocol the label was printed by.
    label : str
        The printed label.

    Returns
    -------
    bool
        True when the label is mapped somewhere and out of scope here.
    """
    wanted = label.strip().casefold()
    carried = [m for m in patch.MAPPINGS if m.label.strip().casefold() == wanted]
    return bool(carried) and not any(patch.applies_to(m, protocol) for m in carried)


def _apply_scan(
    archive: Archive, step: Any, scan: MappingType[str, Any], report: BuildReport
) -> None:
    """Write one scan's mapped parameters and account for the rest.

    Parameters
    ----------
    archive : Archive
        The archive being built.
    step : Step
        The template step matching this scan.
    scan : mapping
        The parsed scan.
    report : BuildReport
        Accumulates the outcome.

    Returns
    -------
    None
    """
    requests: dict[str, Any] = {}
    printed = printed_parameters(scan)
    for label, value in printed.items():
        mapping, reason = patch.resolve(step.protocol, label)
        if mapping is None:
            report.inherited[label] += 1
            if covered_elsewhere(step.protocol, label):
                report.out_of_scope[label] += 1
                report.reasons.setdefault(label, reason)
            continue
        wanted = printed_value(value)
        if mapping.sign_from is not None:
            wanted = apply_direction(wanted, printed.get(mapping.sign_from), mapping)
            if wanted is None:
                # The magnitude without its letter does not say which side of
                # zero the value is on, and guessing would flip a sign.
                report.inherited[label] += 1
                continue
        entry = (
            step.protocol.preview.get(mapping.preview_path)
            if mapping.preview_path is not None
            else None
        )
        if entry is not None and agrees_at_printed_precision(wanted, entry.value):
            report.unchanged += 1
            continue
        if entry is None:
            # No preview side, so the printed-precision check has to come off
            # the ASCCONV value instead. Without this the comparison further
            # down is exact and degrades the protocol to what the card prints.
            shown = stored_display(step.protocol, mapping)
            if shown is not None and agrees_at_printed_precision(wanted, shown):
                report.unchanged += 1
                continue
        requests[label] = wanted
    if not requests:
        return
    document, applied, skipped = patch.patch_document(step.protocol, requests, step=step.name)
    # A value the template already holds is confirmation, not a write. The
    # Special card has no preview side, so its records carry no previous
    # displayed value at all -- comparing that would count every one of them
    # as a change and rewrite content that did not move.
    changed = [a for a in applied if _moved(a)]
    report.unchanged += len(applied) - len(changed)
    report.applied.extend(changed)
    report.skipped.extend(skipped)
    if changed:
        document["Data"] = recentre(step.protocol.xprotocol, document["Data"])
        archive.replace_content(step.protocol.instance, document)


def recentre(before: str, after: str) -> str:
    """Replace the slice array when a write has invalidated it.

    ``Slice Thickness`` and ``Distance Factor`` both set the *spacing* between
    slices, and every ``sSliceArray.asSlice[]`` position is a function of it,
    so writing either one alone leaves every position describing the geometry
    that was replaced. The console recomputes; a patcher does not, and the
    result is an array that still loads -- a scanner returned one 3.15 mm out
    without complaint -- while describing no coherent slice group.

    Only an array this write broke is rebuilt. One that arrived disagreeing
    with its own inputs is left exactly as it was, because repairing it would
    be a change nothing asked for, and a multi-group array is skipped outright
    since :func:`geometry.read_group` refuses to describe one.

    Parameters
    ----------
    before : str
        The XProtocol text as the template held it.
    after : str
        The same text after this scan's values were written.

    Returns
    -------
    str
        ``after``, with the slice positions recomputed when they need to be.
    """
    was = geometry.agrees(before)
    if was is None or was >= geometry.TOLERANCE:
        return after
    group = geometry.read_group(after)
    if group is None:
        return after
    now = geometry.agrees(after, group)
    if now is None or now < geometry.TOLERANCE:
        return after
    return geometry.rebuild(after, group)
