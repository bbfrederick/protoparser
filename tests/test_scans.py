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
    # VB17A counts are cross-checked against the number of "TA:" summary
    # lines in the raw text, which the splitter reaches by a different route.
    "LiaCoilTest.pdf": 20,
    "Multiband_development.pdf": 17,
    "NIRS_CBV_connectome.pdf": 29,
    "NIRS_CBV_toscan_old.pdf": 28,
    "rtNIRS_12ch.pdf": 16,
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

    Only the contents page sits outside a scan, and it may sit at either end:
    VE11C and the Numaris/X releases lead with it, VB17A appends it.

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

    front = set(result.protocol.front_matter_pages)
    assert not front & set(seen), "a contents page was also given to a scan"
    # Together they account for the whole document, with no page unclaimed...
    every_page = set(range(1, result.protocol.page_count + 1))
    assert set(seen) | front == every_page
    # ...and the scans themselves form one unbroken run.
    assert set(seen) == set(range(min(seen), max(seen) + 1))


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
        pages = [
            [line.strip() for line in page.get_text().splitlines() if line.strip()]
            for page in document
        ]
    finally:
        document.close()

    # The heading may lead the document or trail it: VE11C and the Numaris/X
    # releases put it first, VB17A last. It is never the running page header,
    # so allow it to be the first or second line of the page.
    for lines in pages:
        if "Table of contents" not in lines[:2]:
            continue
        entries: list[str] = []
        for line in lines[lines.index("Table of contents") + 1 :]:
            if line.startswith("SIEMENS MAGNETOM") or re.fullmatch(r"-\s*\d+\s*-", line):
                break
            entries.append(line)
        return " ".join(entries)
    return None


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


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_a_contents_page_never_becomes_scan_data(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """The contents listing is front matter wherever it appears.

    VB17A appends its contents page instead of leading with it, so treating
    front matter as "whatever precedes the first scan" handed that page to
    the last scan and turned a list of protocol names into parameters.

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
    contents = _table_of_contents(pdf)
    if contents is None:
        # No contents page: then nothing may be classed as front matter.
        assert protocol.front_matter_pages == []
        return
    assert protocol.front_matter_pages, "the contents page was read as scan data"
    for scan in protocol.scans:
        assert not set(scan.pages) & set(protocol.front_matter_pages)


@requires_examples
def test_a_trailing_contents_page_is_recognized(parsed: ParseFixture) -> None:
    """VB17A's contents page sits at the end, and is still front matter.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    protocol = parsed(find_example("LiaCoilTest.pdf")).protocol
    assert protocol.front_matter_pages == [protocol.page_count]
    assert protocol.scans[-1].pages[-1] == protocol.page_count - 1


@requires_examples
def test_a_label_wrapped_onto_a_second_line_is_rejoined(parsed: ParseFixture) -> None:
    """VB17A wraps two Properties labels, and both must come back whole.

    VB17A sets a continuation at the same pitch as an ordinary row and puts
    the value on the first line, so the usual gap test cannot see it. Getting
    this wrong leaves a truncated key plus an empty record, and the truncated
    key then fails to match the same parameter in any other release.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    scan = parsed(find_example("LiaCoilTest.pdf")).protocol.scans[0]
    keys = {getattr(record, "key", "") for record in scan.records}
    assert "Load images to graphic segments" in keys
    assert "Start measurement without further preparation" in keys
    # ...and the fragments are gone rather than left behind as empty records.
    assert "segments" not in keys
    assert "further preparation" not in keys


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_no_label_is_left_as_a_lower_case_fragment(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """A label-only record starting lower-case is an unjoined continuation.

    These exports capitalize the first word of every label, so such a record
    is the tail of a wrapped phrase that was not rejoined.

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
    fragments = [
        record.key
        for scan in parsed(pdf).protocol.scans
        for record in scan.records
        if getattr(record, "key", "") and not record.value and record.key[:1].islower()
    ]
    assert not fragments, f"labels left unjoined: {sorted(set(fragments))}"
