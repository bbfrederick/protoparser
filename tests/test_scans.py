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
from siemens_protocol.extract.spans import Page, Span
from siemens_protocol.pipeline import parse_document
from siemens_protocol.profiles import REGISTRY
from siemens_protocol.profiles.base import SIZE_FIELDS
from siemens_protocol.split import HeaderBox, in_contents_listing

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
    # Keys may be "<VERSION>/<file>.pdf" as well as a bare file name. The same
    # protocol is exported under two releases with one base name, and the two
    # do not always hold the same number of scans: the XA60 copies of
    # Irritability_PRR and Aging_SZ_SPICE each run one extra scan, which the
    # contents page of each PDF confirms independently of the splitter. A
    # bare-name table could not state both, so these are qualified. Counts
    # for the pairs that do agree are cross-checked by that agreement -- two
    # releases, two different header grammars, one answer.
    "VE11C/Aging_SZ_SPICE_08192025.pdf": 21,
    "XA60/Aging_SZ_SPICE_08192025.pdf": 22,
    "VE11C/Irritability_PRR 1st.pdf": 13,
    "XA60/Irritability_PRR 1st.pdf": 14,
    "VE11C/Irritability_Posner 1st.pdf": 13,
    "XA60/Irritability_Posner 1st.pdf": 13,
    "VE11C/NSSI_ROUTINE.pdf": 12,
    "XA60/NSSI_ROUTINE.pdf": 12,
    "VE11C/Healthy Control NSSI HOOD.pdf": 18,
    "XA60/Healthy Control NSSI HOOD.pdf": 18,
    # Potpourri ships beside its own .exar1 export, so these counts are not
    # eyeballed off the printout: 18 is the number of measurement steps in the
    # archive's program chain, reached by a route that shares no code with the
    # PDF splitter. tests/test_exar.py asserts the names match too. P1 and P2
    # are the same protocol imported onto two XA60 scanners and agree, which
    # is a third independent check on the same number.
    # The three option scans hold one sequence repeated with a single Special
    # card option changed each time; their counts are the number of steps in
    # each archive's program chain, so they are cross-checked the same way
    # Potpourri is. The two VE11C spectroscopy protocols have no archive and
    # are checked against their own contents pages instead.
    # The _loadtest pair of each is the same protocol after a scanner loaded
    # a patched copy and wrote it back, so its count must equal its source's --
    # a scan the loader rejected would be missing, which is the whole point of
    # those files.
    "XA60/CMRR_optionscan_P1_loadtest.pdf": 33,
    "XA60/MEMPRAGE_optionscan_P1_loadtest.pdf": 7,
    "XA60/NAV_optionscan_P1_loadtest.pdf": 31,
    "XA60/Potpourri_P1_loadtest.pdf": 18,
    "XA60/Potpourri_P2_loadtest.pdf": 18,
    "XA60/CMRR_optionscan_P1.pdf": 33,
    "XA60/MEMPRAGE_optionscan_P1.pdf": 7,
    "XA60/NAV_optionscan_P1.pdf": 31,
    "VE11C/31P CSI 20230503 NOE.pdf": 13,
    "XA60/31P CSI 20230503 NOE.pdf": 13,
    # Both counts confirmed against their own contents pages. Note that these
    # two PDFs do NOT match the .exar1 shipped beside them: CHR-MDD's archive
    # holds 23 acquisitions against the printout's 24, and the 31P archive
    # holds 24 against its printout's 13. The pairs are different versions of
    # their protocols, which is a fact about the corpus rather than a parse
    # error -- ZMK23's pair does agree, at 23 each.
    "XA60/CHR-MDD.pdf": 24,
    "XA60/ZMK23 with Physio.pdf": 23,
    "VE11C/BEEST_SPICE_11112025.pdf": 24,
    "Potpourri_P1.pdf": 18,
    "Potpourri_P1_changed.pdf": 18,
    "Potpourri_P2.pdf": 18,
    "ELS2_20210802XA60.pdf": 15,
    "NOCICEPT_Ph2MRI515_SecondXA60.pdf": 19,
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

    An expectation may be keyed by release and file name together, which is
    what lets the two exports of one protocol state different counts when they
    genuinely hold different numbers of scans. The bare name is the fallback.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name, used to look up a qualified expectation.

    Returns
    -------
    None
    """
    name = os.path.basename(pdf)
    expected = EXPECTED_SCAN_COUNT.get(f"{_version}/{name}", EXPECTED_SCAN_COUNT.get(name))
    if expected is None:
        pytest.skip(f"no hand-checked expectation for {os.path.basename(pdf)}")
    assert len(parsed(pdf).protocol.scans) == expected


@requires_examples
@pytest.mark.parametrize("release", ["VE11C", "XA60"])
def test_scan_names_and_order(parsed: ParseFixture, release: str) -> None:
    """The same protocol yields the same leading scans in both releases.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    release : str
        Release the export is taken from. Both carry the same file name, so
        the release is what picks one.

    Returns
    -------
    None
    """
    names = [s.name for s in parsed(find_example("R01StressDyn.pdf", release)).protocol.scans]
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


def contents_page(heading: bool, number: int = 0) -> Page:
    """A page of contents entries, with or without the listing's heading.

    Parameters
    ----------
    heading : bool
        Whether to print ``Table of contents`` above the entries. Only the
        first page of a listing carries it.
    number : int, optional
        Zero-based page index. Default ``0``.

    Returns
    -------
    Page
        A page carrying entries at the geometry these listings use.
    """
    spans: list[Span] = []
    y = 54.3
    if heading:
        spans.append(Span("Table of contents", 258.2, y, 340.0, y + 13.8, size=11.0))
        y += 44.7
    for i in range(6):
        spans.append(Span(f"ep2d_bold_{i}", 35.7, y, 120.0, y + 11.0, size=9.0))
        spans.append(Span(str(20 + i), 548.0, y, 556.0, y + 11.0, size=9.0))
        y += 10.8
    return Page(number=number, width=596.0, height=842.0, spans=spans)


def test_a_contents_listing_runs_from_its_heading_to_the_next_scan() -> None:
    """Only the first page of a listing carries the heading.

    A protocol with enough scans overruns the page, and the pages after the
    first carry entries alone -- indistinguishable from a scan's parameters
    by the heading test, which is what handed one to a scan.

    Returns
    -------
    None
    """
    layout = REGISTRY.get("VB17A").layout
    box = HeaderBox(path="\\\\USER\\localizer", summary="TA: 1:00", bottom_y=100.0)

    opens = in_contents_listing(contents_page(heading=True), layout, None, False)
    assert opens, "the heading did not open the listing"
    assert in_contents_listing(
        contents_page(heading=False), layout, None, opens
    ), "the listing ended at the page that carried no heading of its own"
    assert not in_contents_listing(
        contents_page(heading=False), layout, box, opens
    ), "a header box starts a scan and must end the listing"
    # A page with no heading and no listing open is a scan's second page.
    assert not in_contents_listing(contents_page(heading=False), layout, None, False)


@requires_examples
def test_a_trailing_contents_listing_that_spills_is_not_given_to_the_last_scan(
    tmp_path: object,
) -> None:
    """VB17A appends its listing, so a spilled page lands on the last scan.

    No example spills, so the case is built: a copy of a real export with one
    more page of entries after its contents page. Before the run was tracked,
    that page reached the last scan, whose sections then included another
    scan's name.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    source = find_example("LiaCoilTest.pdf", "VB17A")
    spilled = os.path.join(str(tmp_path), "spilled.pdf")
    doc = pymupdf.open(source)
    try:
        before = parse_document(source).protocol
        page = doc.new_page(width=596, height=842)
        page.insert_text(
            (173.1, 32.0), "SIEMENS MAGNETOM TrioTim syngo MR B17", fontname="helv", fontsize=12
        )
        for i, name in enumerate(["PC_3D_sag_fast_mip", "vessels_head", "TOF_2D_obl"]):
            y = 108.0 + i * 10.8
            page.insert_text((35.7, y), name, fontname="helv", fontsize=9)
            page.insert_text((548.0, y), str(30 + i), fontname="helv", fontsize=9)
        doc.save(spilled)
    finally:
        doc.close()

    after = parse_document(spilled).protocol
    assert after.front_matter_pages == before.front_matter_pages + [before.page_count + 1]
    assert len(after.scans) == len(before.scans)
    last_before, last_after = before.scans[-1], after.scans[-1]
    assert last_after.pages == last_before.pages, "the spilled page was given to the last scan"
    assert last_after.sections() == last_before.sections()
