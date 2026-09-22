"""The protocol summary: the census, the totals and what they must conserve.

The summary is a roll-up, so most of what can go wrong with it is arithmetic
that still looks plausible -- a scan counted twice, a scan counted in no
sequence at all, a total that quietly drops the scans it could not read. None
of that is visible in a spot check of the output, because every line still
reads correctly on its own. So the tests here are mostly conservation laws:
every scan lands in exactly one census row, every second lands in exactly one
sum, and the summary agrees with the listing it is built from.

The one thing deliberately *not* asserted is that the total matches the
printed protocol duration. An archive omits ``lTotalScanTimeSec`` on its
one-second setter scans, so its total legitimately falls a few seconds short
of its own printout's, and the summary reports that gap rather than hiding it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import (
    EXAMPLE_FILES,
    EXAMPLE_IDS,
    ParseFixture,
    find_example,
    requires_examples,
)
from siemens_protocol.cli import main
from siemens_protocol.listing import build_listing
from siemens_protocol.sequences import STOCK, THIRD_PARTY, VERDICTS
from siemens_protocol.summary import (
    UNNAMED,
    build_summary,
    protocol_name,
    render_summary,
)


def _protocol(*scans: dict, **extra: object) -> dict:
    """Build a minimal serialized protocol around some scans.

    Parameters
    ----------
    *scans : dict
        Serialized scans.
    **extra : object
        Further top-level keys, such as ``program``.

    Returns
    -------
    dict
        A protocol carrying those scans.
    """
    return {
        "source_file": "test.pdf",
        "software_version": "VE11C",
        "scans": list(scans),
        **extra,
    }


def _scan(index: int, name: str, ta: str, sequence: str = "epfid") -> dict:
    """Build one serialized scan.

    Parameters
    ----------
    index : int
        Zero-based position.
    name : str
        Scan name.
    ta : str
        Acquisition time as printed.
    sequence : str, optional
        Sequence binary. Default ``"epfid"``.

    Returns
    -------
    dict
        The serialized scan.
    """
    return {"index": index, "name": name, "header": {"ta": ta, "sequence": sequence}}


# -- conservation -----------------------------------------------------------


def test_every_scan_lands_in_exactly_one_census_row() -> None:
    """The census partitions the scans; it neither drops nor duplicates one.

    A grouping bug is invisible in the rendered report -- every line still
    reads correctly -- so it has to be caught by the count.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "a", "1:00", "gre"),
            _scan(1, "b", "2:00", "gre"),
            _scan(2, "c", "3:00", "tse"),
        )
    )
    assert summary.scan_count == 3
    assert sum(row.scans for row in summary.sequences) == 3


def test_every_second_lands_in_exactly_one_census_sum() -> None:
    """The per-sequence times add up to the protocol total.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "a", "1:00", "gre"),
            _scan(1, "b", "2:30", "gre"),
            _scan(2, "c", "0:30", "tse"),
        )
    )
    assert summary.total_seconds == pytest.approx(240.0)
    assert sum(row.seconds for row in summary.sequences) == pytest.approx(240.0)


def test_the_verdict_counts_agree_with_the_census() -> None:
    """``counts`` and the census are two views of one identification pass.

    They are built from the same pass, so a disagreement means one of the two
    was derived rather than read.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "a", "1:00", "gre"),
            _scan(1, "b", "2:00", "cmrr_mbep2d_bold"),
            _scan(2, "c", "3:00", "cmrr_mbep2d_bold"),
        )
    )
    for verdict in VERDICTS:
        rows = [row for row in summary.sequences if row.verdict == verdict]
        assert summary.counts[verdict] == sum(row.scans for row in rows)


def test_a_repeated_scan_name_is_not_collapsed() -> None:
    """Two scans of one name are two scans.

    The census groups by sequence, never by name, which is what keeps it out
    of the trap every name-keyed join in this package has eventually met: a
    protocol may print the same scan name several times, and an option scan
    prints one thirty times over.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "T1_MEMPRAGE_64ch", "6:02", "tfl_me"),
            _scan(1, "T1_MEMPRAGE_64ch", "6:02", "tfl_me"),
        )
    )
    assert summary.scan_count == 2
    assert [row.scans for row in summary.sequences] == [2]
    assert summary.total_seconds == pytest.approx(724.0)


# -- the times --------------------------------------------------------------


def test_an_unreadable_time_is_counted_out_not_folded_in_as_zero() -> None:
    """A scan whose time cannot be read is excluded and reported.

    Folded in as zero it would be indistinguishable from an instant scan and
    would quietly shrink the total.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "a", "1:00", "gre"),
            _scan(1, "b", "", "gre"),
        )
    )
    assert summary.total_seconds == pytest.approx(60.0)
    assert summary.unreadable == 1
    assert sum(row.unreadable for row in summary.sequences) == 1
    assert "not counted in the total" in render_summary(summary)


def test_a_sum_over_no_readable_time_shows_no_total() -> None:
    """A sum over nothing prints ``?`` rather than ``0:00``.

    The setter scans of an archive are exactly this: the console stores no
    acquisition time for them, and a rendered ``0:00`` would claim they take
    no time rather than that nothing is known about how long they take. The
    rule covers the protocol total and each census row alike, so the report
    cannot answer the same question two ways in one page.

    Returns
    -------
    None
    """
    text = render_summary(build_summary(_protocol(_scan(0, "setter", "", "ep_moco_nav_set_ABCD"))))
    assert "0:00" not in text
    assert "total TA  ?" in text


def test_a_partly_readable_sequence_marks_its_total() -> None:
    """A sum missing one of its scans says so, as the listing marks a row.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "a", "1:00", "gre"),
            _scan(1, "b", "", "gre"),
        )
    )
    assert "1:00?" in render_summary(summary)


def test_the_extremes_are_the_extremes() -> None:
    """``longest`` and ``shortest`` are read off the times, not the order.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "middle", "2:00"),
            _scan(1, "longest", "9:00"),
            _scan(2, "shortest", "0:30"),
        )
    )
    assert summary.longest is not None and summary.longest.name == "longest"
    assert summary.shortest is not None and summary.shortest.name == "shortest"


def test_one_scan_is_not_reported_as_both_extremes() -> None:
    """A single-scan protocol names its scan once, not twice.

    Returns
    -------
    None
    """
    text = render_summary(build_summary(_protocol(_scan(0, "only", "6:02"))))
    # Named once, on the "longest" line. The census names the sequence rather
    # than the scan, so it does not name it a second time either.
    assert text.count("only") == 1
    assert "shortest" not in text


def test_a_protocol_with_no_readable_time_has_no_extremes() -> None:
    """Nothing readable means no longest and no shortest, rather than zero.

    Returns
    -------
    None
    """
    summary = build_summary(_protocol(_scan(0, "a", ""), _scan(1, "b", "")))
    assert summary.longest is None and summary.shortest is None
    assert summary.total_seconds == 0.0
    assert summary.unreadable == 2


# -- the census -------------------------------------------------------------


def test_the_census_leads_with_what_has_to_be_rebuilt() -> None:
    """Third-party first, then unrecognized, then stock.

    That is the order the verdicts are declared in, and it is the order a
    person deciding whether to convert a protocol reads them in.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "a", "1:00", "gre"),
            _scan(1, "b", "1:00", "cmrr_mbep2d_bold"),
        )
    )
    verdicts = [row.verdict for row in summary.sequences]
    assert verdicts == sorted(verdicts, key=VERDICTS.index)
    assert verdicts[0] == THIRD_PARTY


def test_within_a_verdict_the_biggest_share_leads() -> None:
    """A sequence run four times is listed above one run once.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "a", "1:00", "tse"),
            _scan(1, "b", "1:00", "gre"),
            _scan(2, "c", "1:00", "gre"),
        )
    )
    stock = [row for row in summary.sequences if row.verdict == STOCK]
    assert [row.binary for row in stock] == ["gre", "tse"]


def test_a_sequence_the_export_does_not_name_is_labelled_not_blank() -> None:
    """A missing binary is a finding, not an empty cell.

    Returns
    -------
    None
    """
    summary = build_summary(_protocol(_scan(0, "a", "1:00", "")))
    assert [row.binary for row in summary.sequences] == [UNNAMED]
    assert UNNAMED in render_summary(summary)


def test_only_an_attributed_sequence_carries_a_description() -> None:
    """The owner-only descriptions are evidence lines, not identities.

    ``%CustomerSeq%`` says a sequence is not Siemens', never whose it is, and
    its description is a sentence written for ``sequences --explain``.
    Repeating it down a census column says nothing the ``*`` has not said.

    Returns
    -------
    None
    """
    summary = build_summary(
        _protocol(
            _scan(0, "a", "1:00", "cmrr_mbep2d_bold"),
            _scan(1, "b", "1:00", "gre"),
        )
    )
    described = {row.binary: row.description for row in summary.sequences}
    assert "CMRR" in described["cmrr_mbep2d_bold"]
    assert described["gre"] == ""


def test_an_empty_protocol_says_so() -> None:
    """No scans is a statement, not a table with no rows.

    Returns
    -------
    None
    """
    summary = build_summary(_protocol())
    assert summary.scan_count == 0
    assert "no scans found" in render_summary(summary)


# -- naming the protocol ----------------------------------------------------


def test_a_stated_program_name_is_used() -> None:
    """An archive names its program outright, and that name wins.

    Returns
    -------
    None
    """
    protocol = _protocol(_scan(0, "a", "1:00"), program="Potpourri_P1")
    assert protocol_name(protocol) == "Potpourri_P1"
    assert render_summary(build_summary(protocol)).startswith("Potpourri_P1\n")


def test_a_printout_name_comes_from_the_printed_path() -> None:
    """A page states the protocol in every scan's header path.

    Not from the file name, which is whatever someone called the export
    afterwards and can be renamed or duplicated.

    Returns
    -------
    None
    """
    scan = _scan(0, "localizer", "0:14")
    scan["path"] = "\\\\Research\\Investigators\\Frederick\\Potpourri_P1\\localizer"
    assert protocol_name(_protocol(scan)) == "Potpourri_P1"


def test_a_protocol_declaring_no_name_gets_no_heading_line() -> None:
    """An export naming no protocol is not given an empty first line.

    Returns
    -------
    None
    """
    text = render_summary(build_summary(_protocol(_scan(0, "a", "1:00"))))
    assert text.startswith("test.pdf (VE11C)")


# -- against the listing it is built from -----------------------------------


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_the_summary_agrees_with_the_listing(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """Both commands describe one protocol, so they must not drift apart.

    They are separately rendered but share their two passes, and this is what
    says so: the same scan count, the same total, the same per-verdict counts.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    protocol = parsed(pdf).protocol.to_dict()
    rows = build_listing(protocol)
    summary = build_summary(protocol)

    assert summary.scan_count == len(rows)
    assert summary.total_seconds == pytest.approx(
        sum(row.seconds for row in rows if row.seconds is not None)
    )
    assert summary.unreadable == sum(1 for row in rows if row.seconds is None)
    for verdict in VERDICTS:
        assert summary.counts[verdict] == sum(1 for row in rows if row.verdict == verdict)


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_the_census_conserves_every_example(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """Across the whole corpus, nothing is dropped from or double-counted in
    the census.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    summary = build_summary(parsed(pdf).protocol.to_dict())
    assert sum(row.scans for row in summary.sequences) == summary.scan_count
    assert sum(row.unreadable for row in summary.sequences) == summary.unreadable
    assert sum(row.seconds for row in summary.sequences) == pytest.approx(summary.total_seconds)
    # Rendering must not raise on any real protocol, whatever it prints.
    assert render_summary(summary)


@requires_examples
def test_a_known_summary() -> None:
    """A hand-checked protocol, so an arithmetic error cannot pass unnoticed.

    ``CRISP`` sums to 5211 s: 1:26:51, the same total the listing tests pin.

    Returns
    -------
    None
    """
    from siemens_protocol.pipeline import ParseOptions, parse_document

    protocol = parse_document(find_example("CRISP.pdf"), ParseOptions()).protocol.to_dict()
    summary = build_summary(protocol)
    assert summary.total_seconds == pytest.approx(5211.0)
    assert summary.unreadable == 0
    assert "1:26:51" in render_summary(summary)


# -- the command line -------------------------------------------------------


@requires_examples
def test_cli_summary_writes_a_report(tmp_path: Path) -> None:
    """``summary`` renders the heading, the facts and the census.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory for the output file.

    Returns
    -------
    None
    """
    out = tmp_path / "summary.txt"
    assert main(["summary", find_example("CRISP.pdf"), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "scans" in text
    assert "total TA" in text
    assert "1:26:51" in text
    assert "distinct sequence" in text


@requires_examples
def test_cli_summary_json(tmp_path: Path) -> None:
    """``--json`` emits the same findings in a machine-readable shape.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory for the output file.

    Returns
    -------
    None
    """
    out = tmp_path / "summary.json"
    assert main(["summary", find_example("CRISP.pdf"), "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scan_count"] == sum(row["scans"] for row in payload["sequences"])
    assert payload["total_seconds"] == pytest.approx(5211.0)
    assert set(payload["counts"]) == set(VERDICTS)
    assert payload["longest"]["seconds"] >= payload["shortest"]["seconds"]


@requires_examples
def test_cli_summary_reads_previously_parsed_json(tmp_path: Path) -> None:
    """A protocol parsed once can be summarized without parsing it again.

    Also covers ``--no-flatten`` JSON: the summary reads scan headers and
    sections, never the flattened view.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory for the intermediate files.

    Returns
    -------
    None
    """
    parsed = tmp_path / "crisp.json"
    assert (
        main(["parse", find_example("CRISP.pdf"), "--no-flatten", "--out", str(parsed), "--quiet"])
        == 0
    )
    out = tmp_path / "summary.txt"
    assert main(["summary", str(parsed), "--out", str(out)]) == 0
    assert "1:26:51" in out.read_text(encoding="utf-8")


def test_cli_summary_reports_a_missing_file() -> None:
    """A path that is not there is an error, not a traceback.

    Returns
    -------
    None
    """
    assert main(["summary", "no-such-file.pdf"]) == 1
