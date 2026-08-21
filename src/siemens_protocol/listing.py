"""A one-line-per-scan inventory of a protocol, with the total scan time.

The acquisition time is printed differently by each release -- VE11C writes
``6:02`` and ``8.0 s``, the Numaris/X releases write ``6:02 min`` and
``9 sec`` -- so the durations have to be parsed before they can be added up.
Parsing is deliberately strict: an unrecognized spelling is reported as such
rather than silently counted as zero, because a total that quietly omits a
scan is worse than one that says it could not read it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

#: ``6:02 min``, ``6:02``, and defensively ``1:02:03`` -- a colon-separated
#: clock, optionally followed by a unit word that adds nothing.
_CLOCK_RE = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d{1,2})(?:\s*(?:min|m))?$",
    re.IGNORECASE,
)
#: ``9 sec``, ``8.0 s``, ``90 ms``, ``7 min`` -- a bare number plus a unit.
_UNIT_RE = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|msec|s|sec|secs|seconds|m|min|mins|minutes|h|hr|hours)$",
    re.IGNORECASE,
)
#: Seconds per unit, keyed by the spellings above folded to lower case.
_UNIT_SECONDS = {
    "ms": 0.001,
    "msec": 0.001,
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hours": 3600.0,
}


def parse_acquisition_time(text: str) -> float | None:
    """Read a printed ``TA`` value as a number of seconds.

    Parameters
    ----------
    text : str
        The acquisition time as printed, such as ``"6:02 min"`` or ``"9 sec"``.

    Returns
    -------
    float or None
        The duration in seconds, or ``None`` when the spelling is not
        recognized. ``None`` is never treated as zero by the caller.
    """
    value = (text or "").strip()
    if not value:
        return None
    clock = _CLOCK_RE.match(value)
    if clock:
        hours = int(clock.group("h") or 0)
        return hours * 3600.0 + int(clock.group("m")) * 60.0 + int(clock.group("s"))
    unit = _UNIT_RE.match(value)
    if unit:
        return float(unit.group("value")) * _UNIT_SECONDS[unit.group("unit").lower()]
    return None


def format_duration(seconds: float) -> str:
    """Render a number of seconds as ``M:SS``, or ``H:MM:SS`` past an hour.

    Parameters
    ----------
    seconds : float
        A duration. Fractions are rounded to the nearest second, which is the
        precision the exports themselves print.

    Returns
    -------
    str
        The formatted duration.
    """
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@dataclass
class ScanRow:
    """One scan's line in the inventory.

    Attributes
    ----------
    index : int
        The scan's zero-based position in the protocol.
    name : str
        The protocol name from the scan's header box.
    sequence : str
        The sequence binary, empty when the export does not print one.
    acquisition_time : str
        The acquisition time as printed, kept verbatim for display.
    seconds : float or None
        The parsed duration, or ``None`` when it could not be read.
    """

    index: int
    name: str
    sequence: str
    acquisition_time: str
    seconds: float | None

    def to_dict(self) -> dict:
        """Serialize the row.

        Returns
        -------
        dict
            The row's fields, with ``seconds`` rounded to a millisecond.
        """
        return {
            "index": self.index,
            "name": self.name,
            "sequence": self.sequence,
            "acquisition_time": self.acquisition_time,
            "seconds": None if self.seconds is None else round(self.seconds, 3),
        }


def build_listing(protocol: Mapping) -> list[ScanRow]:
    """One row per scan, in acquisition order.

    Takes the serialized form rather than a :class:`~.model.Protocol` so a
    previously parsed JSON file can be listed too. Only the scan header is
    read, so JSON written with ``--no-flatten`` works as well.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol, carrying ``scans``.

    Returns
    -------
    list of ScanRow
        The rows, ordered as the scans appear in the document.
    """
    rows: list[ScanRow] = []
    for position, scan in enumerate(protocol.get("scans", [])):
        header = scan.get("header", {}) or {}
        printed = header.get("ta", "")
        rows.append(
            ScanRow(
                index=scan.get("index", position),
                name=scan.get("name", ""),
                sequence=header.get("sequence", ""),
                acquisition_time=printed,
                seconds=parse_acquisition_time(printed),
            )
        )
    return rows


def _column_widths(rows: list[ScanRow]) -> tuple[int, int, int, int]:
    """Width of each column, sized to its widest entry.

    Parameters
    ----------
    rows : list of ScanRow
        The rows to be rendered.

    Returns
    -------
    tuple of int
        Widths for the index, name, sequence and time columns.
    """
    return (
        max([len("#")] + [len(str(r.index)) for r in rows]),
        max([len("scan")] + [len(r.name) for r in rows]),
        max([len("sequence")] + [len(r.sequence) for r in rows]),
        max([len("TA")] + [len(r.acquisition_time) for r in rows]),
    )


def render_listing(protocol: Mapping, rows: list[ScanRow]) -> str:
    """Render the inventory as an aligned table with a total.

    Parameters
    ----------
    protocol : mapping
        The serialized protocol, read for the heading.
    rows : list of ScanRow
        The rows to render, as built by :func:`build_listing`.

    Returns
    -------
    str
        The complete listing.
    """
    heading = f"{protocol.get('source_file', '')} ({protocol.get('software_version', '')})"
    if not rows:
        return f"{heading}\n\nno scans found"

    w_index, w_name, w_seq, w_time = _column_widths(rows)
    lines = [
        heading,
        "",
        f"{'#':>{w_index}}  {'scan':<{w_name}}  {'sequence':<{w_seq}}  {'TA':>{w_time}}",
        f"{'-' * w_index}  {'-' * w_name}  {'-' * w_seq}  {'-' * w_time}",
    ]
    for row in rows:
        time = row.acquisition_time if row.seconds is not None else f"{row.acquisition_time}?"
        lines.append(
            f"{row.index:>{w_index}}  {row.name:<{w_name}}  "
            f"{row.sequence:<{w_seq}}  {time:>{w_time}}"
        )

    known = [r.seconds for r in rows if r.seconds is not None]
    total = format_duration(sum(known))
    label = f"total ({len(rows)} scans)"
    lines.append(f"{'-' * w_index}  {'-' * w_name}  {'-' * w_seq}  {'-' * w_time}")
    lines.append(f"{'':>{w_index}}  {label:<{w_name}}  {'':<{w_seq}}  {total:>{w_time}}")

    unread = len(rows) - len(known)
    if unread:
        # Said out loud rather than folded into the total as zero: a total
        # that quietly omits a scan reads as if it covered everything.
        lines.append(
            f"\n{unread} scan(s) had an unreadable acquisition time, "
            "marked ? above and excluded from the total"
        )
    return "\n".join(lines)
