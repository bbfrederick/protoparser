"""``--debug-timings``: what it reports, where, and that it costs nothing off.

The end-to-end tests read the report the way a person does -- off stderr --
and check it names the operations the run actually performed. The unit tests
pin the two properties a table of totals cannot show: a label nested in
itself is counted once, and a block that raises is still counted.
"""

from __future__ import annotations

import json
import os
from typing import Iterator

import pytest

from conftest import EXAMPLES
from siemens_protocol import timing
from siemens_protocol.cli import build_parser, main

PDF = os.path.join(EXAMPLES, "XA60", "Potpourri_P1.pdf")
ARCHIVE = os.path.join(EXAMPLES, "XA60", "Potpourri_P1.exar1")
HEADING = "timings (totals are inclusive"


@pytest.fixture(autouse=True)
def _timing_off() -> Iterator[None]:
    """Leave timing off around every test, whatever a failing one did.

    Yields
    ------
    None
    """
    timing.disable()
    yield
    timing.disable()


def _operations(stderr: str) -> dict[str, int]:
    """Read the report's rows back into ``{operation: calls}``.

    Parameters
    ----------
    stderr : str
        Everything the run wrote to stderr.

    Returns
    -------
    dict
        One entry per operation row; the heading, column titles and wall
        clock are not operations.
    """
    lines = stderr[stderr.index(HEADING) :].splitlines()[2:]
    rows: dict[str, int] = {}
    for line in lines:
        if line.strip().startswith(("wall clock", "(no timed")):
            continue
        name, calls = line.strip().rsplit(None, 5)[0], line.split()[-5]
        rows[name] = int(calls)
    return rows


@pytest.mark.parametrize("before", [True, False], ids=["before-subcommand", "after-subcommand"])
def test_the_flag_is_accepted_on_either_side_of_the_subcommand(before: bool) -> None:
    """A subparser's own default must not overwrite the top-level flag."""
    argv = ["list", PDF]
    argv = ["--debug-timings", *argv] if before else [*argv, "--debug-timings"]
    assert build_parser().parse_args(argv).debug_timings is True
    assert build_parser().parse_args(["list", PDF]).debug_timings is False


def test_every_subcommand_takes_the_flag() -> None:
    """Including ``vocab``'s actions, which sit a level further down."""
    parser = build_parser()
    for argv in (["versions"], ["vocab", "check", "--debug-timings"]):
        assert parser.parse_args([*argv, "--debug-timings"]).debug_timings is True


def test_parsing_a_pdf_reports_the_parse_and_the_json_write(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parse, its serialization and the file write each get a row."""
    out = os.path.join(str(tmp_path), "out.json")
    assert main(["parse", PDF, "--out", out, "--debug-timings"]) == 0
    rows = _operations(capsys.readouterr().err)
    assert rows[timing.PARSE_PDF] == 1
    assert rows[timing.EXTRACT_PDF] == 1
    assert rows[timing.SERIALIZE_JSON] == 1
    assert rows[timing.WRITE_FILE] == 1
    with open(out, encoding="utf-8") as handle:
        assert json.load(handle)["scans"]


def test_diffing_an_archive_against_a_printout_reports_every_stage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Archive read and decode, PDF parse, scan matching and one diff per scan."""
    main(["--debug-timings", "diff", ARCHIVE, PDF, "--json"])
    captured = capsys.readouterr()
    rows = _operations(captured.err)
    for operation in (
        timing.READ_EXAR,
        timing.DECODE_EXAR,
        timing.ARCHIVE_VIEW,
        timing.PARSE_PDF,
        timing.ALIGN_SCANS,
        timing.DIFF_PROTOCOLS,
    ):
        assert rows[operation] == 1, operation
    scans = len(json.loads(captured.out)["scans"])
    assert rows[timing.DIFF_SCANS] == scans
    # The report goes to stderr, so JSON on stdout stays parseable.
    assert HEADING not in captured.out


def test_driving_an_archive_reports_the_write(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exar driver's name pairing, application, validation and write."""
    out = os.path.join(str(tmp_path), "built.exar1")
    assert main(["exar", ARCHIVE, PDF, "--out", out, "--debug-timings"]) == 0
    rows = _operations(capsys.readouterr().err)
    for operation in (
        timing.PAIR_NAMES,
        timing.APPLY_PRINTOUT,
        timing.VALIDATE_EXAR,
        timing.WRITE_EXAR,
    ):
        assert rows[operation] == 1, operation


def test_a_failing_run_still_reports(capsys: pytest.CaptureFixture[str]) -> None:
    """The run that fails is often the one worth timing."""
    assert main(["list", ARCHIVE, "--program", "no such protocol", "--debug-timings"]) == 1
    rows = _operations(capsys.readouterr().err)
    assert rows[timing.READ_EXAR] == 1
    assert rows[timing.MATCH_NAME] == 1
    # Choosing the protocol lists every program and places each in the
    # folder tree first, which on a whole-scanner export is most of the run.
    assert rows[timing.LIST_EXAR] == 1
    assert rows[timing.PATH_EXAR] >= 1
    assert rows[timing.INDEX_EXAR] >= 1
    assert not timing.enabled()


def test_without_the_flag_nothing_is_collected_or_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Off by default, and a later run does not inherit an earlier one's state."""
    main(["list", ARCHIVE, "--json"])
    assert HEADING not in capsys.readouterr().err
    assert timing.tallies() == {}


def test_a_label_nested_inside_itself_is_counted_once() -> None:
    """``select_all`` calling ``select`` is one match, not two."""
    timing.enable()
    with timing.timed("outer"):
        with timing.timed("outer"):
            pass
    assert timing.tallies()["outer"][0] == 1


def test_a_block_that_raises_is_still_counted() -> None:
    """Dropping failed calls would make an erroring run look faster."""
    timing.enable()
    with pytest.raises(RuntimeError):
        with timing.timed("fails"):
            raise RuntimeError("boom")
    assert timing.tallies()["fails"][0] == 1
    with timing.timed("fails"):
        pass
    assert timing.tallies()["fails"][0] == 2


def test_a_timed_function_passes_its_result_through_when_off() -> None:
    """Off, the decorator must be invisible: same result, nothing recorded."""

    @timing.timed_function("add")
    def add(left: int, right: int) -> int:
        """Add two numbers.

        Parameters
        ----------
        left : int
            One addend.
        right : int
            The other.

        Returns
        -------
        int
            Their sum.
        """
        return left + right

    assert add(2, right=3) == 5
    assert timing.tallies() == {}
    timing.enable()
    assert add(2, 3) == 5
    assert timing.tallies()["add"][0] == 1
