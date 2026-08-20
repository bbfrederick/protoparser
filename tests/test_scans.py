"""Scan splitting: counts, names and page coverage.

Expectations are hand-checked against the printouts and confirmed against the
raw PDF text, so a failure points at the parser rather than at a stale
fixture.
"""

from __future__ import annotations

import os
import re

import pymupdf
import pytest

from conftest import EXAMPLE_FILES, EXAMPLE_IDS, ParseFixture, find_example, requires_examples
from siemens_protocol.profiles.base import SIZE_FIELDS

#: Hand-checked scan counts, one per example file.
EXPECTED_SCAN_COUNT = {
    "ELS2_20210802.pdf": 15,
    "NOCICEPT_Ph2MRI515_Second.pdf": 18,
    "R01StressDyn.pdf": 21,
    "SYNCT.pdf": 14,
    "Keto MRS 20240709.pdf": 24,
    "MIND BASELINE 202603.pdf": 25,
    "R01_Mindfulness.pdf": 13,
    "ELS2_20210802XA60.pdf": 15,
    "NOCICEPT_Ph2MRI515_SecondXA60.pdf": 19,
    "R01StressDynXA60.pdf": 21,
    # XA30 counts are cross-checked against each export's table of contents,
    # which lists every scan and which the parser never reads.
    "ATE_Study.pdf": 27,
    "ATE_Study_6_21_2023.pdf": 25,
    "Brady_TMSstudy_240208.pdf": 20,
    "Brady_TMSstudy_Feb2024.pdf": 20,
    "BreakthroughDiscoveries-BD2-nonav-FIXED.pdf": 19,
    "BreakthroughDiscoveries.pdf": 16,
    "CRISP.pdf": 21,
    "Copersino - TMS.pdf": 20,
    "Copersino - baseline.pdf": 16,
    "Halko_TMS.pdf": 14,
    "KRUSEGROUP.pdf": 16,
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


#: The contents page truncates a long entry at this many characters. Observed
#: exactly once in the corpus, on a protocol whose name was accidentally
#: doubled on the scanner: the header box prints the full 62-character name
#: over two lines while the contents page cuts it at 53. Only this prefix can
#: be checked, so the tail of a very long name goes unverified here.
TOC_ENTRY_MAX_CHARS = 53


def _table_of_contents(pdf: str) -> str | None:
    """The scan list printed on an export's front page, as one string.

    The table of contents is front matter the parser never reads, which makes
    it an independent oracle for scan splitting. It is returned joined rather
    than as a list because a scan name containing spaces wraps across several
    printed lines, so line boundaries carry no meaning.

    Parameters
    ----------
    pdf : str
        Path to a protocol PDF.

    Returns
    -------
    str or None
        The contents entries joined by single spaces, or ``None`` when the
        export has no table of contents page.
    """
    document = pymupdf.open(pdf)
    try:
        lines = [line.strip() for line in document[0].get_text().splitlines() if line.strip()]
    finally:
        document.close()
    if not lines or lines[0] != "Table of contents":
        return None
    entries: list[str] = []
    for line in lines[1:]:
        if line.startswith("SIEMENS MAGNETOM") or re.fullmatch(r"-\s*\d+\s*-", line):
            break
        entries.append(line)
    return " ".join(entries)


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_scan_names_appear_in_the_table_of_contents(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """Every scan the splitter finds is named on the contents page.

    This is the one check in the suite whose expectation the parser did not
    produce: the contents page is front matter, discarded before splitting.
    It catches a split that invents a scan or mangles a name, which a count
    alone would not.

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
    contents = _table_of_contents(pdf)
    if contents is None:
        pytest.skip(f"{os.path.basename(pdf)} has no table of contents page")
    names = [scan.name for scan in parsed(pdf).protocol.scans]
    assert names, "no scans were found"
    missing = [name for name in names if name and name[:TOC_ENTRY_MAX_CHARS] not in contents]
    assert not missing, f"scans not listed in the table of contents: {missing}"


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_no_header_field_absorbs_an_undeclared_label(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """No parsed header value may still contain a ``label:`` of its own.

    ``parse_header_summary`` gives each declared label the text running to the
    next one, so a label the profile does not declare is silently absorbed by
    the field before it -- taking everything after it along. That is how
    spectroscopy's ``VoI:`` swallowed the SNR and the sequence binary. A
    surviving colon is the signature, and it is cheap to check across every
    scan in the corpus rather than on hand-written lines only.

    ``ta`` is exempt: its value is legitimately a clock time such as ``6:02``.

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
    leaked: list[str] = []
    for scan in parsed(pdf).protocol.scans:
        for key, value in scan.header.items():
            if key != "ta" and ":" in value:
                leaked.append(f"{scan.name}: {key}={value!r}")
    assert not leaked, f"header fields absorbed an undeclared label: {leaked[:5]}"


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_every_scan_reports_a_spatial_extent(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """Each scan records either a voxel size or a volume of interest.

    Both are printed in the header box by every release, so a scan carrying
    neither means the line was parsed with the wrong grammar.

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
    missing = [
        scan.name
        for scan in parsed(pdf).protocol.scans
        if not any(scan.header.get(key) for key in SIZE_FIELDS)
    ]
    assert not missing, f"scans with neither a voxel size nor a VoI: {missing}"
