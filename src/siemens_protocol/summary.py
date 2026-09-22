"""A protocol at a glance: how big it is, how long it runs, what it runs.

Where :mod:`~.listing` prints one line per scan, this rolls the same material
up into a block a person can read in one breath -- the protocol's identity,
its scan count and total acquisition time, its longest and shortest scan, and
a census of the distinct sequences it uses with how many scans and how much
time each accounts for.

The census is the part the per-scan listing cannot give. A protocol that runs
one BOLD sequence twelve times and eleven other sequences once each reads as
twenty-three lines in the listing and as twelve entries here, ordered so the
sequences that force a manual rebuild come first.

Both views are built from the same two passes -- :func:`~.listing.build_listing`
for the times and :func:`~.sequences.identify_protocol` for the identities --
and those return one entry per scan in acquisition order, so they are aligned
by position rather than by name. That is deliberate: a protocol may print the
same scan name several times, and every name-keyed join in this package has
eventually met one that does.

The total carries the same caveat the listing states. An acquisition time the
export does not print, or prints in a spelling that cannot be read, is
excluded from the sum and counted out loud rather than folded in as zero --
an archive omits ``lTotalScanTimeSec`` on the one-second setter scans, so an
archive's total legitimately falls a few seconds short of its printout's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .listing import ScanRow, build_listing, format_duration
from .sequences import (
    MARKS,
    STOCK,
    VERDICTS,
    Catalog,
    Identification,
    default_catalog,
    describe,
    identify_protocol,
    summarize,
)

#: Stands in for the binary where an export prints none. Not left blank: a
#: blank cell in the census reads as a rendering fault, where the real finding
#: is that the export did not name the sequence.
UNNAMED = "(not printed)"


@dataclass
class SequenceRow:
    """One distinct sequence, and how much of the protocol runs it.

    Attributes
    ----------
    binary : str
        The sequence as the export names it, or :data:`UNNAMED` where it
        names none. A page prints the kernel (``epfid``) and an archive the
        sequence file (``cmrr_mbep2d_bold``), so the spelling follows the
        input rather than being normalized between them.
    verdict : str
        One of :data:`~.sequences.VERDICTS`, taken from the scans running it.
    description : str
        What the catalog says the sequence is, as :func:`~.sequences.describe`
        phrases it, and empty where no signature matched. A sequence the
        export merely labels as not Siemens' has no identity to report, only
        a verdict.
    scans : int
        How many of the protocol's scans run it.
    seconds : float
        Their acquisition times summed, over the scans whose time could be
        read.
    unreadable : int
        How many of those scans printed no readable acquisition time, and so
        contribute nothing to ``seconds``.
    """

    binary: str
    verdict: str
    description: str
    scans: int
    seconds: float
    unreadable: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize the row.

        Returns
        -------
        dict
            The fields above, with ``seconds`` rounded to a millisecond.
        """
        return {
            "sequence": self.binary,
            "verdict": self.verdict,
            "description": self.description,
            "scans": self.scans,
            "seconds": round(self.seconds, 3),
            "unreadable": self.unreadable,
        }


@dataclass
class Summary:
    """A whole protocol rolled up.

    Attributes
    ----------
    source_file : str
        The file the protocol was read from.
    software_version : str
        The Siemens release, as detected or as the archive's baseline states.
    program : str
        The protocol's own name -- the label on an archive's program node, or
        the protocol component of the path a printout puts in every scan
        header. Empty where the export declares none.
    scanner : str
        The scanner or baseline string the export carries, empty when it
        carries none.
    scan_count : int
        How many scans the protocol runs. Pause steps are not scans and an
        archive's view has already dropped them.
    total_seconds : float
        The acquisition times summed over the scans whose times could be read.
    unreadable : int
        How many scans printed no readable acquisition time.
    longest, shortest : ScanRow or None
        The extremes by acquisition time, over the readable ones. ``None``
        when no scan has a readable time, and the same row on both where
        only one has.
    sequences : list of SequenceRow
        The census, ordered by verdict and then by how many scans run each.
    counts : dict of str to int
        Scans per verdict, every verdict present including the ones at zero.
    """

    source_file: str = ""
    software_version: str = ""
    program: str = ""
    scanner: str = ""
    scan_count: int = 0
    total_seconds: float = 0.0
    unreadable: int = 0
    longest: ScanRow | None = None
    shortest: ScanRow | None = None
    sequences: list[SequenceRow] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the summary.

        Returns
        -------
        dict
            Every field above, with the two extremes rendered as scan rows
            and ``None`` where there are none.
        """
        return {
            "source_file": self.source_file,
            "software_version": self.software_version,
            "program": self.program,
            "scanner": self.scanner,
            "scan_count": self.scan_count,
            "total_seconds": round(self.total_seconds, 3),
            "unreadable": self.unreadable,
            "counts": dict(self.counts),
            "longest": None if self.longest is None else self.longest.to_dict(),
            "shortest": None if self.shortest is None else self.shortest.to_dict(),
            "sequences": [row.to_dict() for row in self.sequences],
        }


def protocol_name(protocol: Mapping) -> str:
    """Return the protocol's own name, as opposed to its file name.

    An archive states it outright, on the label of the program node. A
    printout does not state it as a field, but puts it in the path printed in
    every scan's header box, and the scanner requires that name to be unique
    within an exam -- so it identifies the protocol where the file name is
    only whatever someone called the export afterwards.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol.

    Returns
    -------
    str
        The name, or an empty string where the export declares none. VB17A
        and VE11C printouts that carry no path fall in the second case, as
        does one whose scans disagree about the path.
    """
    stated = protocol.get("program")
    if stated:
        return str(stated)
    # Deferred: reading the archive package is a heavier import than a PDF
    # summary needs, and this is the one function of it a printout wants.
    from .exar.build import program_name

    return program_name(protocol) or ""


def _census(rows: list[ScanRow], found: list[Identification]) -> list[SequenceRow]:
    """Group the scans by the sequence they run.

    Grouped by binary *and* verdict rather than by binary alone, so a binary
    two scans disagree about is reported as the two findings it is instead of
    one of them being chosen. Nothing in the corpus does that, and reporting
    it would be the point if something did.

    Parameters
    ----------
    rows : list of ScanRow
        The per-scan rows, carrying the acquisition times.
    found : list of Identification
        The identifications, positionally aligned with ``rows``.

    Returns
    -------
    list of SequenceRow
        One row per distinct sequence, ordered by verdict as
        :data:`~.sequences.VERDICTS` orders them and then by descending scan
        count, so the sequences that force a rebuild lead and the ones that
        dominate the protocol lead within that.
    """
    grouped: dict[tuple[str, str], SequenceRow] = {}
    for row, item in zip(rows, found):
        key = (item.binary or UNNAMED, item.verdict)
        entry = grouped.get(key)
        if entry is None:
            entry = SequenceRow(
                binary=key[0],
                verdict=item.verdict,
                # Only where a signature matched. The owner-only descriptions
                # are evidence lines -- "the protocol names its binary under
                # the customer tree" -- written for the sequence report's
                # --explain, and repeating one down a census column says
                # nothing the verdict mark has not already said.
                description=describe(item) if item.signature else "",
                scans=0,
                seconds=0.0,
                unreadable=0,
            )
            grouped[key] = entry
        entry.scans += 1
        if row.seconds is None:
            entry.unreadable += 1
        else:
            entry.seconds += row.seconds
    order = {verdict: position for position, verdict in enumerate(VERDICTS)}
    return sorted(
        grouped.values(),
        key=lambda entry: (order.get(entry.verdict, len(order)), -entry.scans, entry.binary),
    )


def build_summary(protocol: Mapping, catalog: Catalog | None = None) -> Summary:
    """Roll a protocol up into one block of findings.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol, carrying ``scans``. Only the scan headers and
        sections are read, never the flattened view, so JSON written with
        ``--no-flatten`` is summarized too.
    catalog : Catalog or None, optional
        Signatures to identify the sequences against. The shipped catalog is
        loaded when omitted. One catalog serves both passes, so the verdict a
        scan's row carries and the one its census entry carries cannot differ.

    Returns
    -------
    Summary
        The rolled-up protocol.
    """
    catalog = catalog or default_catalog()
    rows = build_listing(protocol, catalog)
    found = identify_protocol(protocol, catalog)
    timed = [row for row in rows if row.seconds is not None]
    return Summary(
        source_file=str(protocol.get("source_file", "")),
        software_version=str(protocol.get("software_version") or ""),
        program=protocol_name(protocol),
        scanner=str(protocol.get("scanner") or ""),
        scan_count=len(rows),
        total_seconds=sum(row.seconds or 0.0 for row in timed),
        unreadable=len(rows) - len(timed),
        longest=max(timed, key=lambda row: row.seconds or 0.0) if timed else None,
        shortest=min(timed, key=lambda row: row.seconds or 0.0) if timed else None,
        sequences=_census(rows, found),
        counts=summarize(found),
    )


def _heading(summary: Summary) -> list[str]:
    """Render the lines that identify the protocol.

    Parameters
    ----------
    summary : Summary
        The summary being rendered.

    Returns
    -------
    list of str
        The protocol's name, where it declares one, above its source file and
        release.
    """
    release = summary.software_version or "unknown release"
    source = f"{summary.source_file} ({release})"
    return [summary.program, source] if summary.program else [source]


def _total(seconds: float, unreadable: int, scans: int) -> str:
    """Render a sum of acquisition times over scans that may not all be read.

    A sum missing some of its scans is marked, the way the listing marks the
    individual scan, rather than being printed as if it covered them. Where
    none could be read there is no sum to print at all: an archive's setter
    scans would total ``0:00``, which claims they take no time rather than
    that nothing is known about how long they take.

    Used for the protocol total and for each census row, so the two cannot
    answer the same question differently.

    Parameters
    ----------
    seconds : float
        The times that could be read, summed.
    unreadable : int
        How many scans contributed nothing to that sum.
    scans : int
        How many scans the sum is over.

    Returns
    -------
    str
        The total, suffixed with ``?`` where some scan is missing from it, or
        ``?`` alone where every scan is.
    """
    if scans and unreadable >= scans:
        return "?"
    return format_duration(seconds) + ("?" if unreadable else "")


def _facts(summary: Summary) -> list[tuple[str, str]]:
    """Render the scan count, total and extremes as labelled values.

    Parameters
    ----------
    summary : Summary
        The summary being rendered.

    Returns
    -------
    list of tuple
        ``(label, value)`` pairs, in the order they should be printed.
    """
    facts = [
        ("scans", str(summary.scan_count)),
        ("total TA", _total(summary.total_seconds, summary.unreadable, summary.scan_count)),
    ]
    extremes: list[tuple[str, ScanRow]] = []
    if summary.longest is not None:
        extremes.append(("longest", summary.longest))
    # The shortest is suppressed where the two are the same scan, which they
    # are whenever only one scan has a readable time. Printing it twice reads
    # as a fault rather than as the protocol being one scan long.
    if summary.shortest is not None and summary.shortest is not summary.longest:
        extremes.append(("shortest", summary.shortest))
    # Padded to a common width because the times are printed as the export
    # prints them, and a release is not consistent with itself about that:
    # ``13:24 min`` sits above ``8 sec`` on the same page.
    width = max([0] + [len(row.acquisition_time) for _, row in extremes])
    facts.extend(
        (label, f"{row.acquisition_time:>{width}}  {row.name}") for label, row in extremes
    )
    return facts


def _census_lines(summary: Summary) -> list[str]:
    """Render the sequence census as an aligned table.

    Parameters
    ----------
    summary : Summary
        The summary being rendered.

    Returns
    -------
    list of str
        A header, a rule and one line per distinct sequence, each marked with
        the verdict mark the listing and the sequence report use.
    """
    rows = summary.sequences
    if not rows:
        return []
    times = [_total(row.seconds, row.unreadable, row.scans) for row in rows]
    w_binary = max([len("sequence")] + [len(row.binary) for row in rows])
    w_count = max([len("scans")] + [len(str(row.scans)) for row in rows])
    w_time = max([len("TA")] + [len(text) for text in times])
    lines = [
        f"    {'sequence':<{w_binary}}  {'scans':>{w_count}}  {'TA':>{w_time}}",
        f"    {'-' * w_binary}  {'-' * w_count}  {'-' * w_time}",
    ]
    for row, time in zip(rows, times):
        tail = f"  {row.description}" if row.description else ""
        lines.append(
            f"  {MARKS[row.verdict]} {row.binary:<{w_binary}}  {row.scans:>{w_count}}  "
            f"{time:>{w_time}}{tail}".rstrip()
        )
    return lines


def render_summary(summary: Summary) -> str:
    """Render a summary for reading in a terminal.

    Parameters
    ----------
    summary : Summary
        The summary to render, as built by :func:`build_summary`.

    Returns
    -------
    str
        The complete report.
    """
    lines = _heading(summary)
    if not summary.scan_count:
        return "\n".join(lines + ["", "no scans found"])

    facts = _facts(summary)
    width = max(len(label) for label, _ in facts)
    lines.append("")
    lines.extend(f"  {label:<{width}}  {value}" for label, value in facts)

    distinct = len(summary.sequences)
    tally = ", ".join(f"{summary.counts.get(v, 0)} {v}" for v in VERDICTS)
    lines.append("")
    lines.append(
        f"  {distinct} distinct sequence" + ("s" if distinct != 1 else "") + f" -- {tally} scans"
    )
    lines.append("")
    lines.extend(_census_lines(summary))

    flagged = summary.scan_count - summary.counts.get(STOCK, 0)
    if flagged:
        # The marks are the ones the listing and the sequence report print,
        # and the census is where a reader meets them first. Naming the
        # command that explains them is cheaper than explaining them here.
        lines.append(
            "\n  * runs a third-party sequence, ? not accounted for by the catalog. "
            "Run 'sequences --explain' for the evidence."
        )

    if summary.unreadable:
        # Said out loud rather than folded into the total as zero. An archive
        # omits the field on its one-second setter scans, so this line is the
        # ordinary reason an archive's total sits a few seconds under the
        # printout's rather than a sign that anything went wrong.
        lines.append(
            f"\n{summary.unreadable} scan(s) printed no readable acquisition time "
            "and are not counted in the total"
        )
    return "\n".join(lines)
