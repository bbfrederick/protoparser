"""Scans needing conversion, and the ``%`` mark the listing gives them.

A protocol saved under an older baseline carries
``sProtConsistencyInfo.tBaselineString = "ConversionNeeded"``, and the console
greys such a scan out on import. The flag is per scan, so the listing marks
each one, in a column left of the verdict mark that appears only when some
scan needs it. A printout never carries the flag -- a protocol needing
conversion cannot be printed -- so a PDF listing is unchanged.

The corpus test compares the marks against the field itself, archive-wide,
and requires both a marked and an unmarked scan somewhere, so it cannot pass
by marking everything or nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import EXAR_FILES, requires_exar
from siemens_protocol import exar
from siemens_protocol.analysis import archive_view
from siemens_protocol.analysis.listing import CONVERSION_MARK, build_listing, render_listing
from siemens_protocol.cli import main
from siemens_protocol.exar.ascconv import CONVERSION_NEEDED, baseline_string


def _protocol(*stale: bool) -> dict:
    """Build a protocol whose scans need conversion where ``stale`` says so.

    Parameters
    ----------
    *stale : bool
        One entry per scan.

    Returns
    -------
    dict
        A serialized protocol.
    """
    scans = []
    for index, flag in enumerate(stale):
        header = {"ta": "0:10", "sequence": "gre"}
        if flag:
            header["baseline"] = CONVERSION_NEEDED
        scans.append({"index": index, "name": f"s{index}", "header": header})
    return {"source_file": "test.exar1", "software_version": "XA60", "scans": scans}


def test_a_scan_needing_conversion_is_marked_left_of_the_verdict() -> None:
    """The mark takes a column of its own, and every table line keeps alignment.

    Returns
    -------
    None
    """
    protocol = _protocol(False, True)
    lines = render_listing(protocol, build_listing(protocol)).splitlines()
    table = lines[2:8]
    assert table[2].startswith("   0  s0")
    assert table[3].startswith(f"{CONVERSION_MARK}  1  s1")
    assert len({len(line.rstrip()) for line in table[:4]}) == 1
    assert "1 of 2 scans need conversion" in "\n".join(lines)


def test_no_column_appears_when_nothing_needs_conversion() -> None:
    """A listing with no stale scan is exactly what it was before the mark.

    Returns
    -------
    None
    """
    protocol = _protocol(False, False)
    lines = render_listing(protocol, build_listing(protocol)).splitlines()
    assert lines[4].startswith("  0  s0")
    assert "conversion" not in "\n".join(lines)


@requires_exar
def test_every_corpus_scan_is_marked_exactly_when_its_protocol_says_so() -> None:
    """The listing's mark agrees with the stored field on every archive scan.

    Returns
    -------
    None
    """
    marked = unmarked = 0
    for path, _version in EXAR_FILES:
        try:
            archive = exar.read(path)
        except Exception:  # an unreadable corpus file is another test's concern
            continue
        for program in archive.programs:
            expected = [
                baseline_string(step.protocol) == CONVERSION_NEEDED
                for step in program.steps
                if step.runs_a_protocol
            ]
            protocol = archive_view.as_protocol(archive, program, path, include_flat=False)
            rows = build_listing(protocol)
            assert [row.needs_conversion for row in rows] == expected, (path, program.name)
            marked += sum(expected)
            unmarked += len(expected) - sum(expected)
    assert marked and unmarked


@requires_exar
def test_cli_list_marks_the_stale_scan(tmp_path: Path) -> None:
    """``GAPS_ONE_1`` holds one current scan and one needing conversion.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    path = next((p for p, _ in EXAR_FILES if os.path.basename(p) == "GAPS_ONE_1.exar1"), None)
    if path is None:
        pytest.skip("GAPS_ONE_1.exar1 not available")
    text = tmp_path / "list.txt"
    assert main(["list", path, "--out", str(text)]) == 0
    rows = [line for line in text.read_text(encoding="utf-8").splitlines() if line[:1] == "%"]
    assert len(rows) == 1 and "ZPL_RG_EPSI_FID_v2f" in rows[0]

    out = tmp_path / "list.json"
    assert main(["list", path, "--json", "--out", str(out)]) == 0
    scans = json.loads(out.read_text(encoding="utf-8"))["scans"]
    assert [scan.get("needs_conversion", False) for scan in scans] == [False, True]
