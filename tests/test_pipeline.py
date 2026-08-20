"""End-to-end behaviour: losslessness, the OCR fallback, and the CLI."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from conftest import (
    EXAMPLE_FILES,
    EXAMPLE_IDS,
    EXAMPLES,
    ParseFixture,
    find_example,
    requires_examples,
)
from siemens_protocol.cli import main
from siemens_protocol.extract.ocr import OCRUnavailable
from siemens_protocol.pipeline import ParseOptions, parse_document
from siemens_protocol.profiles import REGISTRY
from siemens_protocol.split import body_spans, find_header_box


def _tesseract_available() -> bool:
    """Whether the OCR path can run in this environment.

    Returns
    -------
    bool
        ``True`` when pytesseract imports and finds its binary.
    """
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


needs_tesseract = pytest.mark.skipif(
    not _tesseract_available(), reason="tesseract is not installed"
)


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_layout_reconstruction_loses_nothing(pdf: str, _version: str) -> None:
    """Every body token must reappear in a key, a value or a section title.

    This is the check that a mis-tuned threshold cannot pass quietly. A value
    paired with the wrong label still looks plausible in the output; a value
    dropped on the floor does not, and this is what catches it.

    Parameters
    ----------
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    result = parse_document(pdf, ParseOptions(debug=True))
    profile = REGISTRY.get(result.protocol.software_version)
    layout = profile.layout
    scanned = {page for scan in result.protocol.scans for page in scan.pages}
    by_page = {page["page"]: page for page in result.debug["pages"]}

    missing: Counter = Counter()
    for page in result.pages:
        if page.label not in scanned:
            continue  # the table of contents is front matter, not a scan
        header = find_header_box(page, layout, profile)
        raw = Counter(
            token for span in body_spans(page, layout, header) for token in span.text.split()
        )
        got: Counter = Counter()
        for record in by_page[page.label]["records"]:
            if record["kind"] == "section":
                got.update(record["section"].split())
            else:
                got.update(record["key"].split())
                got.update(record["value"].split())
        missing.update(raw - got)

    assert not missing, f"tokens dropped during layout reconstruction: {missing}"


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_native_text_is_used_when_it_is_usable(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """These exports carry a real text layer, so values stay exact.

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
    assert parsed(pdf).protocol.ocr_pages == []
    assert all(page.source == "native" for page in parsed(pdf).pages)


@requires_examples
def test_ocr_never_leaves_unusable_pages_visible() -> None:
    """With OCR disabled, an unusable page is reported rather than hidden.

    Returns
    -------
    None
    """
    pdf, _version = EXAMPLE_FILES[0]
    protocol = parse_document(pdf, ParseOptions(ocr="never")).protocol
    assert protocol.software_version
    assert protocol.scans


@requires_examples
@needs_tesseract
def test_ocr_path_reconstructs_the_same_structure() -> None:
    """Forced OCR on a native page must recover the same sections, in order.

    Returns
    -------
    None
    """
    import pymupdf

    from siemens_protocol.extract import Page, extract_page, ocr_page
    from siemens_protocol.layout.columns import split_columns
    from siemens_protocol.layout.sections import SectionMarker, current_section, parse_column

    pdf = find_example("R01StressDyn.pdf")
    profile = REGISTRY.get("VE11C")
    layout = profile.layout
    doc = pymupdf.open(pdf)
    try:

        def sections_of(page: Page) -> list[str]:
            """Section titles found on one page, in reading order.

            Parameters
            ----------
            page : Page
                A page with its spans acquired, native or OCR.

            Returns
            -------
            list of str
                The page's section titles.
            """
            header = find_header_box(page, layout, profile)
            found: list[str] = []
            carried: str | None = None
            columns = sorted(
                split_columns(body_spans(page, layout, header), page.width, layout),
                key=lambda c: c.side != "left",
            )
            for column in columns:
                items = parse_column(column, layout, page.label, carried)
                carried = current_section(items, carried)
                found += [i.section for i in items if isinstance(i, SectionMarker)]
            return found

        page_index = 6  # a dense page with sixteen sections across both columns
        assert sections_of(extract_page(doc, page_index)) == sections_of(
            ocr_page(doc, page_index, dpi=300)
        )
    finally:
        doc.close()


@requires_examples
@needs_tesseract
def test_ocr_recovers_the_header_box() -> None:
    """The scan banner survives rasterization, name and TA field included.

    Returns
    -------
    None
    """
    import pymupdf

    from siemens_protocol.extract import ocr_page

    pdf, version = EXAMPLE_FILES[0]
    profile = REGISTRY.get(version)
    doc = pymupdf.open(pdf)
    try:
        page = ocr_page(doc, 1, dpi=300)
        header = find_header_box(page, profile.layout, profile)
        assert header is not None
        assert header.name
        assert profile.parse_header_summary(header.summary).get("ta")
    finally:
        doc.close()


def test_missing_tesseract_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing binary names itself rather than failing obscurely.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to simulate an environment without tesseract.

    Returns
    -------
    None
    """
    import siemens_protocol.extract.ocr as ocr_module

    def boom() -> None:
        """Stand in for a failed tesseract lookup.

        Returns
        -------
        None

        Raises
        ------
        OCRUnavailable
            Always.
        """
        raise OCRUnavailable("the tesseract binary was not found on PATH")

    monkeypatch.setattr(ocr_module, "_require_tesseract", boom)
    with pytest.raises(OCRUnavailable) as exc:
        ocr_module.ocr_page(None, 0)
    assert "tesseract" in str(exc.value)


@requires_examples
def test_cli_single_file(tmp_path: Path) -> None:
    """Parsing one file writes JSON carrying the detected version and scans.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    pdf, version = EXAMPLE_FILES[0]
    out = tmp_path / "one.json"
    assert main(["parse", pdf, "--out", str(out), "--quiet"]) == 0
    payload = json.loads(out.read_text())
    assert payload["software_version"] == version
    assert payload["scans"]
    assert "flat" in payload["scans"][0]


@requires_examples
def test_cli_can_omit_the_flat_view(tmp_path: Path) -> None:
    """``--no-flatten`` drops the flattened view but keeps the hierarchy.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    pdf, _version = EXAMPLE_FILES[0]
    out = tmp_path / "lean.json"
    assert main(["parse", pdf, "--out", str(out), "--no-flatten", "--quiet"]) == 0
    payload = json.loads(out.read_text())
    assert "flat" not in payload["scans"][0]
    assert payload["scans"][0]["sections"]


@requires_examples
def test_cli_batch_writes_one_json_per_pdf(tmp_path: Path) -> None:
    """A directory target writes one JSON per PDF found beneath it.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "batch"
    assert main(["parse", EXAMPLES, "--out", str(out), "--quiet"]) == 0
    # The tree's shape is mirrored, not flattened: two releases of the same
    # protocol share a base name, and flattening silently dropped one.
    written = sorted(str(p.relative_to(out)) for p in out.rglob("*.json"))
    expected = sorted(
        os.path.splitext(os.path.relpath(p, EXAMPLES))[0] + ".json" for p, _ in EXAMPLE_FILES
    )
    assert written == expected
    assert len(written) == len(EXAMPLE_FILES), "a file was overwritten by a same-named one"


@requires_examples
def test_cli_emit_debug_dumps_geometry(tmp_path: Path) -> None:
    """``--emit-debug`` writes the thresholds and spans a new profile needs.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    pdf, _version = EXAMPLE_FILES[0]
    out = tmp_path / "one.json"
    dump = tmp_path / "geometry.json"
    assert main(["parse", pdf, "--out", str(out), "--emit-debug", str(dump), "--quiet"]) == 0
    payload = json.loads(dump.read_text())
    assert payload["layout"]["value_x_ratio"]
    page = payload["pages"][1]
    assert page["columns"] and page["columns"][0]["rows"]
    assert page["spans"][0]["bbox"]


def test_cli_versions_lists_profiles(capsys: pytest.CaptureFixture) -> None:
    """The ``versions`` subcommand names every registered profile.

    Parameters
    ----------
    capsys : pytest.CaptureFixture
        Captures the printed listing.

    Returns
    -------
    None
    """
    assert main(["versions"]) == 0
    printed = capsys.readouterr().out
    for name in REGISTRY.names():
        assert name in printed


@requires_examples
def test_batch_does_not_overwrite_same_named_files_in_different_folders(
    tmp_path: Path,
) -> None:
    """Two releases of one protocol both survive a batch parse.

    The example tree holds ``Keto MRS 20240709.pdf`` under both ``VE11C`` and
    ``XA60``. Flattening the output onto base names wrote one over the other,
    losing a file with no error and no warning.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    shared = [
        p
        for p, _ in EXAMPLE_FILES
        if sum(1 for q, _ in EXAMPLE_FILES if os.path.basename(q) == os.path.basename(p)) > 1
    ]
    if not shared:
        pytest.skip("no two examples share a base name")

    out = tmp_path / "batch"
    assert main(["parse", EXAMPLES, "--out", str(out), "--quiet"]) == 0
    for pdf in shared:
        expected = out / (os.path.splitext(os.path.relpath(pdf, EXAMPLES))[0] + ".json")
        assert expected.exists(), f"{pdf} produced no output of its own"
        payload = json.loads(expected.read_text(encoding="utf-8"))
        # and each one really is the file it claims to be
        assert os.path.basename(payload["source_file"]) == os.path.basename(pdf)
        assert payload["software_version"] == os.path.basename(os.path.dirname(pdf))
