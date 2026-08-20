"""Scan splitting: counts, names and page coverage.

Expectations are hand-checked against the printouts and confirmed against the
raw PDF text, so a failure points at the parser rather than at a stale
fixture.
"""

from __future__ import annotations

import os

import pytest

from conftest import EXAMPLE_FILES, EXAMPLE_IDS, ParseFixture, find_example, requires_examples

#: Hand-checked scan counts, one per example file.
EXPECTED_SCAN_COUNT = {
    "ELS2_20210802.pdf": 15,
    "NOCICEPT_Ph2MRI515_Second.pdf": 18,
    "R01StressDyn.pdf": 21,
    "SYNCT.pdf": 14,
    "ELS2_20210802XA60.pdf": 15,
    "NOCICEPT_Ph2MRI515_SecondXA60.pdf": 19,
    "R01StressDynXA60.pdf": 21,
}

#: The first few scans of the R01StressDyn protocol, in order, in both releases.
R01_FIRST_SCANS = [
    "localizer",
    "AAHScout",
    "T1_MEMPRAGE_64ch",
    "slice_positioning 22 degree angle",
    "rfMRI_REST1_ME_AP",
]


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_scan_count(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """Each file splits into the hand-checked number of scans.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    expected = EXPECTED_SCAN_COUNT.get(os.path.basename(pdf))
    if expected is None:
        pytest.skip(f"no hand-checked expectation for {os.path.basename(pdf)}")
    assert len(parsed(pdf).protocol.scans) == expected


@requires_examples
@pytest.mark.parametrize("name", ["R01StressDyn.pdf", "R01StressDynXA60.pdf"])
def test_scan_names_and_order(parsed: ParseFixture, name: str) -> None:
    """The same protocol yields the same leading scans in both releases.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    name : str
        Base file name of the example.

    Returns
    -------
    None
    """
    names = [s.name for s in parsed(find_example(name)).protocol.scans]
    assert names[: len(R01_FIRST_SCANS)] == R01_FIRST_SCANS


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_every_scan_has_a_name_and_a_path(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """Every scan carries a name, a path ending in it, and at least one page.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    for scan in parsed(pdf).protocol.scans:
        assert scan.name, f"scan {scan.index} has no name"
        assert scan.path.endswith(scan.name)
        assert scan.pages, f"scan {scan.index} has no pages"


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_pages_are_covered_once_and_in_order(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """A scan runs to the next header box, so pages partition the document.

    Only the table of contents sits outside a scan.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    result = parsed(pdf)
    seen: list[int] = []
    for scan in result.protocol.scans:
        assert scan.pages == sorted(scan.pages)
        seen.extend(scan.pages)
    assert seen == sorted(seen), "scan pages are out of order"
    assert len(seen) == len(set(seen)), "a page was assigned to two scans"
    assert set(range(min(seen), result.protocol.page_count + 1)) == set(seen)


@requires_examples
def test_a_header_path_that_wraps_is_rejoined(parsed: ParseFixture) -> None:
    """The formatter breaks a long path mid-word across two lines.

    Without rejoining, this scan's name is the fragment ``"ne"``.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    pdf = find_example("NOCICEPT_Ph2MRI515_Second.pdf")
    names = [s.name for s in parsed(pdf).protocol.scans]
    assert "slice_positioning-Angle to ACPC line" in names
