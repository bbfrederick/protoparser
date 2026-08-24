"""The scan inventory: acquisition-time parsing, layout and the total.

The acquisition time is the only field here that has to be *interpreted*
rather than copied, and it is spelled four different ways across the
releases, so most of these tests are about reading it correctly. A total that
is quietly wrong is the failure worth guarding against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import EXAMPLE_FILES, EXAMPLE_IDS, ParseFixture, find_example, requires_examples
from siemens_protocol.cli import main
from siemens_protocol.listing import (
    ScanRow,
    build_listing,
    format_duration,
    parse_acquisition_time,
    render_listing,
)


def _protocol(*scans: dict) -> dict:
    """Build a minimal serialized protocol around some scans.

    Parameters
    ----------
    *scans : dict
        Serialized scans.

    Returns
    -------
    dict
        A protocol carrying those scans.
    """
    return {"source_file": "test.pdf", "software_version": "VE11C", "scans": list(scans)}


def _scan(index: int, name: str, ta: str, sequence: str = "epfid") -> dict:
    """Build one serialized scan with a given acquisition time.

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


# -- reading the printed time -----------------------------------------------


@pytest.mark.parametrize(
    "text,seconds",
    [
        # The four spellings that actually occur in the corpus.
        ("0:19", 19.0),  # VE11C clock
        ("6:02", 362.0),
        ("8.0 s", 8.0),  # VE11C fractional seconds
        ("6.1 s", 6.1),
        ("9 sec", 9.0),  # Numaris/X seconds
        ("19 sec", 19.0),
        ("6:02 min", 362.0),  # Numaris/X clock, with a unit that adds nothing
        ("7:04 min", 424.0),
        # Tolerated variants.
        ("1:02:03", 3723.0),
        ("  9 sec  ", 9.0),
        ("9 SEC", 9.0),
        ("7 min", 420.0),
        ("90 ms", 0.09),
    ],
)
def test_acquisition_times_are_read_as_seconds(text: str, seconds: float) -> None:
    """Every spelling the exports use resolves to the same unit.

    Parameters
    ----------
    text : str
        The acquisition time as printed.
    seconds : float
        Its duration in seconds.

    Returns
    -------
    None
    """
    assert parse_acquisition_time(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "   ", "n/a", "-", "soon", "12", "6:2:", "min"])
def test_unreadable_times_are_none_not_zero(text: str) -> None:
    """An unrecognized spelling must not silently count as zero.

    Zero would be indistinguishable from a genuinely instant scan and would
    quietly shrink the total.

    Parameters
    ----------
    text : str
        Something that is not an acquisition time.

    Returns
    -------
    None
    """
    assert parse_acquisition_time(text) is None


def test_a_bare_number_is_not_guessed_at() -> None:
    """``12`` could be seconds or minutes, so it is refused rather than assumed.

    Returns
    -------
    None
    """
    assert parse_acquisition_time("12") is None


@pytest.mark.parametrize(
    "seconds,text",
    [(0, "0:00"), (9, "0:09"), (19, "0:19"), (362, "6:02"), (3600, "1:00:00"), (5211, "1:26:51")],
)
def test_durations_format_as_a_clock(seconds: float, text: str) -> None:
    """Totals read as ``M:SS``, growing an hours field only when needed.

    Parameters
    ----------
    seconds : float
        The duration.
    text : str
        Its rendering.

    Returns
    -------
    None
    """
    assert format_duration(seconds) == text


def test_fractional_seconds_round_rather_than_truncate() -> None:
    """VE11C prints tenths, so a total can land off a whole second.

    Returns
    -------
    None
    """
    assert format_duration(8.6) == "0:09"
    assert format_duration(8.4) == "0:08"


# -- building the listing ---------------------------------------------------


def test_rows_carry_index_name_sequence_and_time() -> None:
    """Each row reports the four requested columns.

    Returns
    -------
    None
    """
    rows = build_listing(_protocol(_scan(0, "localizer", "0:19", "fl")))
    assert rows == [ScanRow(0, "localizer", "fl", "0:19", 19.0)]


def test_a_missing_sequence_leaves_the_column_empty() -> None:
    """One XA30 export prints an empty ``Sequence Name``; that is not an error.

    Returns
    -------
    None
    """
    scan = {"index": 0, "name": "T1w", "header": {"ta": "1 sec"}}
    assert build_listing(_protocol(scan))[0].sequence == ""


def test_listing_preserves_acquisition_order() -> None:
    """Rows come back in document order, not sorted by name or time.

    Returns
    -------
    None
    """
    rows = build_listing(
        _protocol(_scan(0, "zeta", "1 sec"), _scan(1, "alpha", "9 sec"), _scan(2, "mid", "5 sec"))
    )
    assert [r.name for r in rows] == ["zeta", "alpha", "mid"]


# -- the rendered table -----------------------------------------------------


def test_the_total_is_the_sum_of_the_rows() -> None:
    """The printed total equals the sum of the parsed times.

    Returns
    -------
    None
    """
    protocol = _protocol(_scan(0, "a", "1:00"), _scan(1, "b", "30 sec"), _scan(2, "c", "0:30"))
    text = render_listing(protocol, build_listing(protocol))
    assert "2:00" in text.splitlines()[-1]


def test_an_unreadable_time_is_flagged_and_excluded() -> None:
    """A time that could not be read is marked and called out, not counted.

    Returns
    -------
    None
    """
    protocol = _protocol(_scan(0, "good", "1:00"), _scan(1, "bad", "whenever"))
    text = render_listing(protocol, build_listing(protocol))
    assert "whenever?" in text
    assert "1 scan(s) had an unreadable acquisition time" in text
    assert "1:00" in text


def test_columns_line_up() -> None:
    """Every body row shares the header row's column offsets.

    Parameters
    ----------
    None

    Returns
    -------
    None
    """
    protocol = _protocol(
        _scan(0, "short", "1:00"),
        _scan(1, "a_considerably_longer_scan_name", "30 sec", sequence="resolve"),
    )
    lines = render_listing(protocol, build_listing(protocol)).splitlines()
    header = lines[2]
    rule = lines[3]
    assert len(header) == len(rule)
    for row in lines[4:6]:
        assert len(row) == len(rule)


def test_an_empty_protocol_says_so() -> None:
    """A protocol with no scans renders a message rather than an empty table.

    Returns
    -------
    None
    """
    assert "no scans found" in render_listing(_protocol(), [])


# -- against the real examples ----------------------------------------------


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_every_example_time_is_readable(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """No scan in the corpus has an acquisition time the parser cannot read.

    This is what would catch a fifth spelling arriving with a new release:
    the total would otherwise silently omit those scans.

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
    rows = build_listing(parsed(pdf).protocol.to_dict())
    unreadable = [(r.index, r.name, r.acquisition_time) for r in rows if r.seconds is None]
    assert not unreadable, f"unreadable acquisition times: {unreadable}"


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_one_row_per_scan(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """The listing accounts for every scan, once.

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
    protocol = parsed(pdf).protocol
    rows = build_listing(protocol.to_dict())
    assert [r.index for r in rows] == list(range(len(protocol.scans)))
    assert [r.name for r in rows] == [s.name for s in protocol.scans]


@requires_examples
def test_a_known_total() -> None:
    """A hand-checked total, so a units error cannot pass unnoticed.

    ``CRISP`` sums to 5211 s: 1:26:51. Checked against the printed times.

    Returns
    -------
    None
    """
    from siemens_protocol.pipeline import ParseOptions, parse_document

    protocol = parse_document(find_example("CRISP.pdf"), ParseOptions()).protocol.to_dict()
    rows = build_listing(protocol)
    assert sum(r.seconds for r in rows) == pytest.approx(5211.0)
    assert format_duration(5211.0) == "1:26:51"


# -- the command line -------------------------------------------------------


@requires_examples
def test_cli_list_writes_a_table(tmp_path: Path) -> None:
    """``list`` renders the four columns and a total.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "list.txt"
    assert main(["list", find_example("R01StressDyn.pdf", "VE11C"), "--out", str(out)]) == 0
    text = out.read_text()
    assert "scan" in text and "sequence" in text and "TA" in text
    assert "localizer" in text
    assert "total (21 scans)" in text


@requires_examples
def test_cli_list_json(tmp_path: Path) -> None:
    """The listing is available as JSON, with the total in seconds.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "list.json"
    assert main(["list", find_example("CRISP.pdf"), "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["software_version"] == "XA30"
    assert len(payload["scans"]) == 21
    assert payload["total_seconds"] == pytest.approx(5211.0)
    assert payload["unreadable"] == 0
    assert payload["scans"][0]["index"] == 0
    assert set(payload["scans"][0]) == {
        "index",
        "name",
        "sequence",
        "acquisition_time",
        "seconds",
    }


@requires_examples
def test_cli_list_reads_previously_parsed_json(tmp_path: Path) -> None:
    """A parsed JSON file lists identically to its PDF, flattened or not.

    Listing reads only the scan headers, so unlike ``diff`` it does not need
    the flattened view.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    pdf = find_example("CRISP.pdf")
    parsed_json = tmp_path / "crisp.json"
    assert main(["parse", pdf, "--no-flatten", "--out", str(parsed_json), "--quiet"]) == 0

    from_pdf = tmp_path / "a.json"
    from_json = tmp_path / "b.json"
    assert main(["list", pdf, "--json", "--out", str(from_pdf)]) == 0
    assert main(["list", str(parsed_json), "--json", "--out", str(from_json)]) == 0
    assert json.loads(from_json.read_text())["scans"] == json.loads(from_pdf.read_text())["scans"]


def test_cli_list_reports_a_missing_file() -> None:
    """A path that does not exist fails rather than printing an empty table.

    Returns
    -------
    None
    """
    assert main(["list", "no_such_file.pdf"]) == 1


# -- bad input --------------------------------------------------------------


@pytest.mark.parametrize("command", ["list", "check", "parse"])
def test_a_missing_file_is_an_error_not_a_traceback(command: str) -> None:
    """Every command reports a bad path rather than raising.

    PyMuPDF's exceptions derive from ``RuntimeError``, and its
    ``FileNotFoundError`` is not the builtin one despite the name, so they
    used to slip past the handlers here and escape as tracebacks.

    Parameters
    ----------
    command : str
        The subcommand to exercise.

    Returns
    -------
    None
    """
    assert main([command, "no_such_file.pdf"]) == 1


def test_a_missing_file_is_an_error_for_diff_too() -> None:
    """``diff`` takes two inputs, so it is checked separately.

    Returns
    -------
    None
    """
    assert main(["diff", "no_such_file.pdf", "also_missing.pdf"]) == 1


@pytest.mark.parametrize("command", ["list", "check", "parse"])
def test_an_unreadable_file_is_an_error_not_a_traceback(command: str, tmp_path: Path) -> None:
    """A file that is not a PDF fails cleanly.

    Parameters
    ----------
    command : str
        The subcommand to exercise.
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    bad = tmp_path / "not_really.pdf"
    bad.write_text("this is not a PDF", encoding="utf-8")
    assert main([command, str(bad)]) == 1


@pytest.mark.parametrize("command", ["list", "check", "parse"])
def test_an_empty_file_is_an_error_not_a_traceback(command: str, tmp_path: Path) -> None:
    """A zero-byte file fails cleanly.

    Parameters
    ----------
    command : str
        The subcommand to exercise.
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert main([command, str(empty)]) == 1
