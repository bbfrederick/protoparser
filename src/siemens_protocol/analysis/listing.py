"""A one-line-per-scan inventory of a protocol, with the total scan time.

Each line also carries the mark :mod:`~..sequences` gives the scan, so the
inventory answers "how long is this protocol" and "how much of it will a
release migration make me rebuild" in one pass. The mark is recomputed here
rather than read from a stored verdict, so a listing of JSON parsed before a
catalog correction reflects the correction.

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

from ..exar.ascconv import CONVERSION_NEEDED
from .links import link_groups, link_marks, link_sets
from .sequences import MARKS, STOCK, Catalog, default_catalog, identify

#: ``6:02 min``, ``6:02``, ``1:42:33 h`` -- a colon-separated clock,
#: optionally followed by a unit word that adds nothing to the digits.
#:
#: The three-field form was written defensively before anything printed one.
#: It arrived with an `eja_csi_fid` CSI scan at ``1:42:33 h``, and the guess
#: was half right: the clock matched and the trailing ``h`` did not, because
#: only ``min`` and ``m`` were allowed there. A scan over an hour is rare
#: enough that the gap survived 973 protocols, and the printed banner is the
#: only place the total comes from, so an unreadable one drops that scan out
#: of the sum rather than failing loudly.
_CLOCK_RE = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d{1,2})(?:\s*(?:mins|min|m|hours|hrs|hr|h))?$",
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
    verdict : str
        One of :data:`~..sequences.VERDICTS`, saying whether the scan runs a
        third-party sequence.
    links : str
        The scan's copy-parameter marks -- ``(X>)`` as the source of link set
        ``X``, ``(>X)`` as a scan copying from it -- as
        :func:`~.links.link_marks` spells them. Empty for a scan in no link,
        and for every scan of a printout, which does not record links.
    link_group : str
        What the scan copies from its link set's source -- ``Slices``,
        ``TablePosition`` and so on. Empty unless the scan is a destination.
    needs_conversion : bool
        Whether the scan's protocol was saved under an older baseline and
        awaits conversion, which is what makes a console grey it out. Only an
        archive can say so; a printout of such a protocol cannot be made.
    """

    index: int
    name: str
    sequence: str
    acquisition_time: str
    seconds: float | None
    verdict: str = STOCK
    links: str = ""
    link_group: str = ""
    needs_conversion: bool = False

    def to_dict(self) -> dict:
        """Serialize the row.

        Returns
        -------
        dict
            The row's fields, with ``seconds`` rounded to a millisecond,
            ``links`` present only when the scan is in a link and
            ``link_group`` only when it copies from one, and
            ``needs_conversion`` only when it does.
        """
        out = {
            "index": self.index,
            "name": self.name,
            "sequence": self.sequence,
            "acquisition_time": self.acquisition_time,
            "seconds": None if self.seconds is None else round(self.seconds, 3),
            "verdict": self.verdict,
        }
        if self.links:
            out["links"] = self.links
        if self.link_group:
            out["link_group"] = self.link_group
        if self.needs_conversion:
            out["needs_conversion"] = True
        return out


def build_listing(protocol: Mapping, catalog: Catalog | None = None) -> list[ScanRow]:
    """One row per scan, in acquisition order.

    Takes the serialized form rather than a :class:`~.model.Protocol` so a
    previously parsed JSON file can be listed too. Only the scan header and
    its sections are read -- never the flattened view -- so JSON written with
    ``--no-flatten`` works as well.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol, carrying ``scans``.
    catalog : Catalog or None, optional
        Signatures to identify each scan against. The shipped catalog is
        loaded when omitted. A caller that also identifies the scans for
        itself passes its own, so the two passes cannot disagree about
        what a scan is running.

    Returns
    -------
    list of ScanRow
        The rows, ordered as the scans appear in the document.
    """
    catalog = catalog or default_catalog()
    sets = link_sets(protocol)
    marks, groups = link_marks(sets), link_groups(sets)
    rows: list[ScanRow] = []
    for position, scan in enumerate(protocol.get("scans", [])):
        header = scan.get("header", {}) or {}
        printed = header.get("ta", "")
        index = scan.get("index", position)
        rows.append(
            ScanRow(
                index=index,
                name=scan.get("name", ""),
                sequence=header.get("sequence", ""),
                acquisition_time=printed,
                seconds=parse_acquisition_time(printed),
                verdict=identify(scan, catalog).verdict,
                links=marks.get(index, ""),
                link_group=groups.get(index, ""),
                needs_conversion=header.get("baseline") == CONVERSION_NEEDED,
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
        Widths for the index, name, sequence and time columns. The verdict
        mark is one character wide by construction and needs none.
    """
    return (
        max([len("#")] + [len(str(r.index)) for r in rows]),
        max([len("scan")] + [len(r.name) for r in rows]),
        max([len("sequence")] + [len(r.sequence) for r in rows]),
        max([len("TA")] + [len(r.acquisition_time) for r in rows]),
    )


#: Printed left of the verdict mark on a scan whose protocol needs
#: conversion (``sProtConsistencyInfo.tBaselineString = "ConversionNeeded"``).
CONVERSION_MARK = "%"

#: Printed in the sequence column of a pause step's line, which has no
#: sequence, number or acquisition time of its own.
PAUSE = "(pause)"


def _pauses_before(protocol: Mapping, rows: list[ScanRow]) -> dict[int, list[str]]:
    """Group the protocol's pause steps by the row they are printed above.

    Parameters
    ----------
    protocol : mapping
        The serialized protocol. Its ``pauses`` entry, where present, is a
        list of ``{"before", "name"}``.
    rows : list of ScanRow
        The rows being rendered.

    Returns
    -------
    dict of int to list of str
        Pause names keyed by the position in ``rows`` they precede, with
        ``len(rows)`` for pauses after the last scan.
    """
    position = {row.index: at for at, row in enumerate(rows)}
    grouped: dict[int, list[str]] = {}
    for pause in protocol.get("pauses", []) or []:
        at = position.get(int(pause["before"]), len(rows))
        grouped.setdefault(at, []).append(str(pause.get("name", "")))
    return grouped


def render_listing(
    protocol: Mapping,
    rows: list[ScanRow],
    link_options: bool = False,
    pauses: bool = False,
) -> str:
    """Render the inventory as an aligned table with a total.

    Parameters
    ----------
    protocol : mapping
        The serialized protocol, read for the heading.
    rows : list of ScanRow
        The rows to render, as built by :func:`build_listing`.
    link_options : bool, optional
        Whether to print, after each destination's ``(>X)`` mark, what it
        copies from its source. Default ``False``, leaving that to
        ``summary``.
    pauses : bool, optional
        Whether to print the protocol's pause steps in running order. They
        are not scans, so they carry no number, sequence or time and are not
        counted in the total. Default ``False``.

    Returns
    -------
    str
        The complete listing.
    """
    heading = f"{protocol.get('source_file', '')} ({protocol.get('software_version', '')})"
    if not rows:
        return f"{heading}\n\nno scans found"

    w_index, w_name, w_seq, w_time = _column_widths(rows)
    # A conversion column, left of the verdict mark, only where some scan
    # needs conversion -- never on a printout, which cannot hold one. Every
    # table line gains the same leading character so the columns stay
    # aligned; ``lead`` is that character on lines that carry no mark.
    converting = any(row.needs_conversion for row in rows)
    lead = " " if converting else ""
    between = _pauses_before(protocol, rows) if pauses else {}
    if between:
        w_name = max([w_name] + [len(name) for names in between.values() for name in names])
        w_seq = max(w_seq, len(PAUSE))

    def pause_lines(at: int) -> list[str]:
        """Render the pauses printed above row ``at``, unnumbered.

        Parameters
        ----------
        at : int
            Position in ``rows``, or ``len(rows)`` for after the last.

        Returns
        -------
        list of str
            One line per pause.
        """
        return [
            f"{lead}  {'':>{w_index}}  {name:<{w_name}}  {PAUSE:<{w_seq}}".rstrip()
            for name in between.get(at, [])
        ]

    # The links column appears only where some scan is linked, which is
    # never on a printout: an always-empty column would read as links that
    # failed to load rather than as a format that does not record them.
    linked = any(row.links for row in rows)
    lines = [
        heading,
        "",
        f"{lead}  {'#':>{w_index}}  {'scan':<{w_name}}  {'sequence':<{w_seq}}  {'TA':>{w_time}}"
        + ("  links" if linked else ""),
        f"{lead}  {'-' * w_index}  {'-' * w_name}  {'-' * w_seq}  {'-' * w_time}"
        + ("  -----" if linked else ""),
    ]
    for at, row in enumerate(rows):
        lines.extend(pause_lines(at))
        time = row.acquisition_time if row.seconds is not None else f"{row.acquisition_time}?"
        tail = f"  {row.links}" if row.links else ""
        if link_options and row.link_group:
            tail += f" {row.link_group}"
        convert = (CONVERSION_MARK if row.needs_conversion else " ") if converting else ""
        lines.append(
            f"{convert}{MARKS[row.verdict]} {row.index:>{w_index}}  {row.name:<{w_name}}  "
            f"{row.sequence:<{w_seq}}  {time:>{w_time}}{tail}"
        )

    lines.extend(pause_lines(len(rows)))

    known = [r.seconds for r in rows if r.seconds is not None]
    total = format_duration(sum(known))
    label = f"total ({len(rows)} scan{'' if len(rows) == 1 else 's'})"
    lines.append(f"{lead}  {'-' * w_index}  {'-' * w_name}  {'-' * w_seq}  {'-' * w_time}")
    lines.append(f"{lead}  {'':>{w_index}}  {label:<{w_name}}  {'':<{w_seq}}  {total:>{w_time}}")

    marked = sum(1 for r in rows if r.verdict != STOCK)
    if marked:
        # Named here rather than only in the 'sequences' report, because the
        # inventory is what a person reads when deciding whether a protocol
        # is worth converting at all.
        lines.append(
            f"\n{marked} of {len(rows)} scans do not run a recognized Siemens sequence "
            "(* third-party, ? not accounted for). Run 'sequences' for what they are."
        )

    if converting:
        stale = sum(1 for r in rows if r.needs_conversion)
        lines.append(
            f"\n{stale} of {len(rows)} scans need conversion ({CONVERSION_MARK}): saved under "
            "an older baseline, so the console will grey them out on import."
        )

    if linked:
        lines.append(
            "\n(X>) is the source of copy-parameter link set X, (>X) copies from it."
            + (
                ""
                if link_options
                else " Run 'summary' or add --link-options for what each copies."
            )
        )

    unread = len(rows) - len(known)
    if unread:
        # Said out loud rather than folded into the total as zero: a total
        # that quietly omits a scan reads as if it covered everything.
        lines.append(
            f"\n{unread} scan(s) had an unreadable acquisition time, "
            "marked ? above and excluded from the total"
        )
    return "\n".join(lines)
