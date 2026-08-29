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
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Any
from typing import Mapping as MappingType

from . import patch
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
            steps.setdefault(step.name, []).append(step)

    scans: dict[str, list[Any]] = {}
    for scan in parsed.get("scans", []):
        scans.setdefault(scan.get("name", ""), []).append(scan)

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
    for label, value in printed_parameters(scan).items():
        mapping, _reason = patch.resolve(step.protocol, label)
        if mapping is None:
            report.inherited[label] += 1
            continue
        wanted = printed_value(value)
        entry = (
            step.protocol.preview.get(mapping.preview_path)
            if mapping.preview_path is not None
            else None
        )
        if entry is not None and agrees_at_printed_precision(wanted, entry.value):
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
        archive.replace_content(step.protocol.instance, document)
