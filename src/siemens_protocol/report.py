"""Human-readable rendering of a protocol or scan comparison.

The report leads with substantive differences, because those are what decide
whether a rebuilt protocol matches the original. Cosmetic differences are
summarized rather than hidden, and can be shown in full on request.
"""

from __future__ import annotations

from itertools import groupby
from typing import Sequence

from .diff import (
    CHANGED,
    ONLY_LEFT,
    ONLY_RIGHT,
    RECASED,
    REFORMATTED,
    RENAMED,
    ParameterDiff,
    ProtocolDiff,
    ScanDiff,
)

#: Marker printed against each kind of difference.
_MARK = {
    CHANGED: "~",
    ONLY_LEFT: "-",
    ONLY_RIGHT: "+",
    RENAMED: "R",
    REFORMATTED: "f",
    RECASED: "c",
}

_COSMETIC_LABEL = {
    RENAMED: "relabeled",
    REFORMATTED: "reformatted",
    RECASED: "recased",
}


def name_mismatch_note(name_left: str, name_right: str) -> str | None:
    """A note naming both spellings when two matched scans differ in name.

    Scans are aligned by their position in the acquisition sequence rather
    than by name, so a pair can be matched and still be spelled differently.
    Saying which side is which makes the rest of the report readable: without
    it a reader has to infer from the parameter lines which protocol they are
    looking at.

    Parameters
    ----------
    name_left, name_right : str
        The scan name on each side.

    Returns
    -------
    str or None
        The note, or ``None`` when the names are identical.
    """
    if name_left == name_right:
        return None
    return (
        f"Names do not match exactly - {name_left} (left) " f"corresponds to {name_right} (right)"
    )


def section_filter_note(sections: Sequence[str] | None) -> str | None:
    """A note naming the sections a report was restricted to.

    Filtering changes what the totals count, so the report has to say it was
    filtered. Without the note a reader would take "3 substantive
    differences" for the whole protocol.

    Parameters
    ----------
    sections : sequence of str or None
        The normalized card names in force, or ``None`` when unrestricted.

    Returns
    -------
    str or None
        The note, or ``None`` when nothing was filtered out.
    """
    if not sections:
        return None
    return "showing only sections: " + ", ".join(sections)


def _values(values: list[str]) -> str:
    """Render a parameter's readings.

    Parameters
    ----------
    values : list of str
        One reading, or several when the key repeats within the scan.

    Returns
    -------
    str
        The reading, or a bracketed list for a repeated key.
    """
    if not values:
        return "-"
    if len(values) == 1:
        return values[0] or "<empty>"
    return "[" + ", ".join(v or "<empty>" for v in values) + "]"


def _line(diff: ParameterDiff, pad: str = "    ") -> str:
    """Render one parameter difference as a single line.

    Parameters
    ----------
    diff : ParameterDiff
        The difference to render.
    pad : str, optional
        Indentation before the status marker. Default ``"    "``; parameters
        listed under a section label take one level more.

    Returns
    -------
    str
        A line beginning with the status marker.
    """
    mark = _MARK.get(diff.status, "?")
    if diff.status == ONLY_LEFT:
        return f"{pad}{mark} {diff.key_left}: {_values(diff.values_left)}"
    if diff.status == ONLY_RIGHT:
        return f"{pad}{mark} {diff.key_right}: {_values(diff.values_right)}"
    name = diff.key_left or ""
    if diff.renamed:
        name = f"{diff.key_left} -> {diff.key_right}"
    body = f"{_values(diff.values_left)}  |  {_values(diff.values_right)}"
    if diff.status == RENAMED:
        return f"{pad}{mark} {name}: {_values(diff.values_left)}"
    return f"{pad}{mark} {name}: {body}"


def _by_section(diffs: Sequence[ParameterDiff], indent: str) -> list[str]:
    """Render parameter differences beneath the section each belongs to.

    Parameters
    ----------
    diffs : sequence of ParameterDiff
        Differences already ordered by section, as
        :func:`~siemens_protocol.diff.diff_parameters` returns them. Grouping
        is by adjacency, so the order is what decides the grouping.
    indent : str
        Prefix applied to every line.

    Returns
    -------
    list of str
        A label line per section, each followed by that section's
        differences.
    """
    lines: list[str] = []
    for section, group in groupby(diffs, key=lambda d: d.section):
        lines.append(f"{indent}    {section or '(no section)'}")
        lines.extend(_line(diff, pad=f"{indent}      ") for diff in group)
    return lines


def render_scan(
    scan: ScanDiff,
    show_cosmetic: bool = False,
    indent: str = "",
    note_rename: bool = True,
) -> list[str]:
    """Render one scan comparison.

    Parameters
    ----------
    scan : ScanDiff
        The comparison to render.
    show_cosmetic : bool, optional
        Whether to list cosmetic differences individually rather than count
        them. Default ``False``.
    indent : str, optional
        Prefix applied to every line. Default ``""``.
    note_rename : bool, optional
        Whether differing names mean the scan was renamed. True when aligning
        two protocols; false when the caller deliberately picked two
        different scans, where the names differ by request rather than by
        change. Default ``True``.

    Returns
    -------
    list of str
        Report lines, without trailing newlines. Parameters are listed under
        the section that prints them in the right-hand protocol, which is the
        one being edited.
    """
    lines: list[str] = []
    title = scan.name_left
    if scan.renamed_scan:
        title = f"{scan.name_left} -> {scan.name_right}"
    lines.append(f"{indent}{title}")
    if note_rename:
        note = name_mismatch_note(scan.name_left, scan.name_right)
        if note is not None:
            lines.append(f"{indent}  ! {note}")

    if scan.header:
        lines.append(f"{indent}  header")
        for diff in scan.header:
            lines.append(indent + _line(diff))

    substantive = [d for d in scan.parameters if d.substantive]
    cosmetic = [d for d in scan.parameters if not d.substantive]

    if substantive:
        lines.append(f"{indent}  parameters")
        lines.extend(_by_section(substantive, indent))

    if cosmetic and show_cosmetic:
        lines.append(f"{indent}  cosmetic")
        lines.extend(_by_section(cosmetic, indent))
    elif cosmetic:
        counts: dict[str, int] = {}
        for diff in cosmetic:
            counts[diff.status] = counts.get(diff.status, 0) + 1
        summary = ", ".join(
            f"{n} {_COSMETIC_LABEL.get(status, status)}" for status, n in sorted(counts.items())
        )
        lines.append(f"{indent}  cosmetic: {summary} (use --show-cosmetic to list)")

    if scan.identical:
        lines.append(f"{indent}  no differences ({scan.unchanged} parameters match)")
    return lines


def render_protocol(
    result: ProtocolDiff,
    show_cosmetic: bool = False,
    show_identical: bool = False,
    sections: Sequence[str] | None = None,
) -> str:
    """Render a whole-protocol comparison.

    Parameters
    ----------
    result : ProtocolDiff
        The comparison to render.
    show_cosmetic : bool, optional
        Whether to list cosmetic differences individually. Default ``False``.
    show_identical : bool, optional
        Whether to include scans with no differences at all. Default
        ``False``.
    sections : sequence of str or None, optional
        The section filter the comparison was run under, named in the report
        so the counts are not read as covering the whole protocol. Default
        ``None``.

    Returns
    -------
    str
        The complete report.
    """
    left = f"{result.left_file} ({result.left_version})"
    right = f"{result.right_file} ({result.right_version})"
    note = section_filter_note(sections)
    lines = [f"--- {left}", f"+++ {right}", *([note] if note is not None else []), ""]

    shown = 0
    for scan in result.scans:
        if scan.identical and not show_identical:
            continue
        shown += 1
        lines.extend(render_scan(scan, show_cosmetic=show_cosmetic))
        lines.append("")

    for name in result.only_left:
        lines.append(f"- scan only in left: {name}")
    for name in result.only_right:
        lines.append(f"+ scan only in right: {name}")
    if result.only_left or result.only_right:
        lines.append("")

    identical = sum(1 for s in result.scans if s.identical)
    lines.append(
        f"{len(result.scans)} scans compared, {identical} identical, "
        f"{result.substantive_count} substantive differences"
    )
    if not shown and not (result.only_left or result.only_right):
        lines.append("no substantive differences found")
    return "\n".join(lines)
