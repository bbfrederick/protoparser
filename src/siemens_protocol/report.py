"""Human-readable rendering of a protocol or scan comparison.

The report leads with substantive differences, because those are what decide
whether a rebuilt protocol matches the original. Cosmetic differences are
summarized rather than hidden, and can be shown in full on request.
"""

from __future__ import annotations

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


def _line(diff: ParameterDiff) -> str:
    """Render one parameter difference as a single line.

    Parameters
    ----------
    diff : ParameterDiff
        The difference to render.

    Returns
    -------
    str
        A line beginning with the status marker.
    """
    mark = _MARK.get(diff.status, "?")
    if diff.status == ONLY_LEFT:
        return f"    {mark} {diff.key_left}: {_values(diff.values_left)}"
    if diff.status == ONLY_RIGHT:
        return f"    {mark} {diff.key_right}: {_values(diff.values_right)}"
    name = diff.key_left or ""
    if diff.renamed:
        name = f"{diff.key_left} -> {diff.key_right}"
    body = f"{_values(diff.values_left)}  |  {_values(diff.values_right)}"
    if diff.status == RENAMED:
        return f"    {mark} {name}: {_values(diff.values_left)}"
    return f"    {mark} {name}: {body}"


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
        Report lines, without trailing newlines.
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
        for diff in substantive:
            lines.append(indent + _line(diff))

    if cosmetic and show_cosmetic:
        lines.append(f"{indent}  cosmetic")
        for diff in cosmetic:
            lines.append(indent + _line(diff))
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

    Returns
    -------
    str
        The complete report.
    """
    left = f"{result.left_file} ({result.left_version})"
    right = f"{result.right_file} ({result.right_version})"
    lines = [f"--- {left}", f"+++ {right}", ""]

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
