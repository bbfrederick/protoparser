"""Orchestration of the five stages: load, acquire text, lay out, split, assemble."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pymupdf

from .extract import OCRUnavailable, Page, extract_page, is_usable, ocr_page
from .layout.columns import split_columns
from .layout.sections import Record, SectionMarker, current_section, parse_column
from .model import Protocol, Scan
from .profiles import REGISTRY
from .profiles.base import VersionProfile
from .split import HeaderBox, body_spans, find_header_box, is_contents_page, running_header

#: Values accepted by :attr:`ParseOptions.ocr`.
OCR_AUTO, OCR_ALWAYS, OCR_NEVER = "auto", "always", "never"


@dataclass
class ParseOptions:
    """Knobs for one parse run.

    Attributes
    ----------
    version : str
        ``"auto"``, or a profile name to force.
    ocr : str
        ``"auto"``, ``"always"`` or ``"never"``.
    dpi : int
        Rasterization resolution for OCR pages.
    include_flat : bool
        Whether serialized scans carry the flattened view.
    debug : bool
        Whether to collect the per-page record stream for a debug dump.
    """

    version: str = "auto"
    ocr: str = OCR_AUTO
    dpi: int = 300
    include_flat: bool = True
    debug: bool = False


@dataclass
class ParseResult:
    """Everything one parse run produced.

    Attributes
    ----------
    protocol : Protocol
        The parsed document.
    pages : list of Page
        Pages with their acquired spans, kept for debug dumps.
    debug : dict
        Per-page record stream, empty unless ``ParseOptions.debug`` was set.
    """

    protocol: Protocol
    pages: list[Page] = field(default_factory=list)
    debug: dict[str, object] = field(default_factory=dict)


def _sample_text(doc: pymupdf.Document, limit: int = 3) -> str:
    """Text from the document's first pages, for version detection.

    Parameters
    ----------
    doc : pymupdf.Document
        An open document.
    limit : int, optional
        How many leading pages to sample. Default 3.

    Returns
    -------
    str
        The sampled text, newline-joined.
    """
    return "\n".join(doc[i].get_text() for i in range(min(limit, len(doc))))


def _resolve_profile(
    doc: pymupdf.Document,
    options: ParseOptions,
    warnings: list[str],
) -> tuple[VersionProfile, dict]:
    """Auto-detect the software version, or honour an explicit override.

    Parameters
    ----------
    doc : pymupdf.Document
        An open document.
    options : ParseOptions
        Run options, read for ``version``.
    warnings : list of str
        Appended to when a forced version disagrees with detection.

    Returns
    -------
    tuple
        ``(profile, detection info)``.

    Raises
    ------
    ValueError
        If the version cannot be detected and none was forced.
    """
    detected, info = REGISTRY.detect(_sample_text(doc))
    if options.version and options.version != "auto":
        forced = REGISTRY.get(options.version)
        if detected is not None and detected.name != forced.name:
            warnings.append(
                f"version forced to {forced.name} but the file looks like {detected.name}"
            )
        return forced, {
            "method": "forced",
            "confidence": "high",
            "auto_detected": detected.name if detected else None,
        }
    if detected is None:
        raise ValueError(
            "could not detect the Siemens software version; pass --version "
            f"({', '.join(REGISTRY.names())})"
        )
    return detected, info


def acquire_pages(
    doc: pymupdf.Document,
    profile: VersionProfile,
    options: ParseOptions,
    warnings: list[str],
) -> list[Page]:
    """Stages one and two: page inventory, then native text or OCR per page.

    Parameters
    ----------
    doc : pymupdf.Document
        An open document.
    profile : VersionProfile
        The release profile, read for its printable-ratio threshold.
    options : ParseOptions
        Run options, read for ``ocr`` and ``dpi``.
    warnings : list of str
        Appended to when a page is left unusable.

    Returns
    -------
    list of Page
        One page per page of the PDF, in order.

    Raises
    ------
    OCRUnavailable
        If ``ocr="always"`` was asked for and tesseract is missing.
    """
    pages: list[Page] = []
    for number in range(len(doc)):
        page = extract_page(doc, number)
        native_ok = is_usable(page, profile.layout.min_printable_ratio)
        need_ocr = options.ocr == OCR_ALWAYS or (options.ocr == OCR_AUTO and not native_ok)
        if need_ocr:
            try:
                page = ocr_page(doc, number, dpi=options.dpi)
            except OCRUnavailable as exc:
                if options.ocr == OCR_ALWAYS:
                    raise
                warnings.append(
                    f"page {number + 1} has unusable native text but OCR is "
                    f"unavailable ({exc})"
                )
        elif not native_ok and options.ocr == OCR_NEVER:
            warnings.append(
                f"page {number + 1} has a printable ratio of "
                f"{page.printable_ratio:.2f} and OCR is disabled"
            )
        pages.append(page)
    return pages


def _fill_header(scan: Scan, profile: VersionProfile) -> None:
    """Complete a scan header from parameters the box does not carry.

    XA60 leaves the sequence binary name out of the box and prints it as the
    ``Sequence Name`` parameter instead; the profile declares that mapping.

    Parameters
    ----------
    scan : Scan
        The scan to complete, modified in place.
    profile : VersionProfile
        The release profile, read for ``param_fallbacks``.

    Returns
    -------
    None
    """
    missing = {k: v for k, v in profile.param_fallbacks.items() if not scan.header.get(k)}
    for header_key, param_key in missing.items():
        for record in scan.records:
            if getattr(record, "key", None) == param_key and getattr(record, "value", ""):
                scan.header[header_key] = record.value
                break


def _read_page_body(
    page: Page,
    profile: VersionProfile,
    header: HeaderBox | None,
    section: str | None,
) -> tuple[list[Record | SectionMarker], str | None]:
    """Stage three: read one page's two columns, left column first.

    Parameters
    ----------
    page : Page
        A page with its spans acquired.
    profile : VersionProfile
        The release profile, read for its layout thresholds.
    header : HeaderBox or None
        The page's header box, so its spans are excluded from the body.
    section : str or None
        The section in force when the page starts.

    Returns
    -------
    tuple
        ``(items, section)`` -- the page's records and markers, and the
        section still in force at the end of the page.
    """
    layout = profile.layout
    spans = body_spans(page, layout, header)
    items: list[Record | SectionMarker] = []
    columns = sorted(
        split_columns(spans, page.width, layout),
        key=lambda c: 0 if c.side == "left" else 1,
    )
    for column in columns:
        found = parse_column(column, layout, page.label, section)
        section = current_section(found, section)
        items.extend(found)
    return items, section


def parse_document(path: str, options: ParseOptions | None = None) -> ParseResult:
    """Parse one protocol PDF.

    Parameters
    ----------
    path : str
        Path to the PDF.
    options : ParseOptions or None, optional
        Run options. Defaults are used when omitted.

    Returns
    -------
    ParseResult
        The parsed protocol, its pages, and any debug dump.

    Raises
    ------
    OSError
        If the file is missing, unreadable or not a PDF.
    ValueError
        If the software version cannot be detected and none was forced.
    """
    options = options or ParseOptions()
    warnings: list[str] = []
    try:
        doc = pymupdf.open(path)
    except (pymupdf.FileDataError, pymupdf.FileNotFoundError) as exc:
        # PyMuPDF's exceptions derive from RuntimeError, and its
        # FileNotFoundError is *not* the builtin one despite the name, so they
        # slip straight through an OSError handler. Translated at this
        # boundary so callers can treat a bad file like any other IO failure
        # instead of importing pymupdf to catch it.
        # PyMuPDF names the file in every one of these messages, so nothing
        # is added here beyond the exception type the callers expect.
        raise OSError(str(exc)) from exc
    try:
        profile, detection = _resolve_profile(doc, options, warnings)
        pages = acquire_pages(doc, profile, options, warnings)

        protocol = Protocol(
            source_file=os.fspath(path),
            software_version=profile.name,
            detection=detection,
            page_count=len(pages),
            ocr_pages=[p.label for p in pages if p.source == "ocr"],
            warnings=warnings,
        )

        debug_pages: list[dict] = []
        scans: list[Scan] = []
        section: str | None = None

        for page in pages:
            header = find_header_box(page, profile.layout, profile)
            if not protocol.scanner:
                protocol.scanner = running_header(page, profile.layout)

            if header is None and is_contents_page(page, profile.layout):
                # Front matter wherever it sits. VB17A appends its contents
                # page, so position alone would hand it to the last scan as
                # if it were that scan's parameters.
                protocol.front_matter_pages.append(page.label)
                if options.debug:
                    debug_pages.append(
                        {
                            "page": page.label,
                            "source": page.source,
                            "printable_ratio": round(page.printable_ratio, 3),
                            "header_box": None,
                            "records": [],
                        }
                    )
                continue

            if header is not None:
                scan = Scan(index=len(scans), name=header.name, path=header.path)
                scan.header_summary = header.summary
                scan.header = profile.parse_header_summary(header.summary)
                scans.append(scan)
                section = None

            items, section = _read_page_body(page, profile, header, section)

            if scans:
                scans[-1].records.extend(items)
                if page.label not in scans[-1].pages:
                    scans[-1].pages.append(page.label)
            elif items:
                # Pages before the first header box are front matter -- the
                # table of contents, which every export opens with. Recorded
                # rather than warned about, so that a real warning stands out.
                protocol.front_matter_pages.append(page.label)

            if options.debug:
                debug_pages.append(
                    {
                        "page": page.label,
                        "source": page.source,
                        "printable_ratio": round(page.printable_ratio, 3),
                        "header_box": header.to_dict() if header else None,
                        "records": [item.to_dict() for item in items],
                    }
                )

        for scan in scans:
            _fill_header(scan, profile)
        protocol.scans = scans

        return ParseResult(
            protocol=protocol,
            pages=pages,
            debug={"profile": profile.name, "pages": debug_pages} if options.debug else {},
        )
    finally:
        doc.close()
