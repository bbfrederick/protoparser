"""OCR fallback: rasterize a page and rebuild spans from tesseract words.

The spans produced here have the same shape as the native ones, in the same
coordinate system (PDF points), so layout reconstruction is identical for
both. Two things are unavoidably weaker on this path:

* one span per *word* rather than per table cell, which is why cell text is
  reassembled by x-position rather than trusted per span, and
* no font weight and no usable font size, which is why section-title
  detection falls back to geometry.
"""

from __future__ import annotations

import io
from types import ModuleType

import pymupdf

from .spans import Page, Span

#: Words tesseract is less sure of than this are dropped.
_MIN_CONF = 30.0
#: Lone characters that are almost always an OCR'd table rule.
_RULE_NOISE = frozenset(".,;:'\"|_`~")
_NOISE_CONF = 75.0
#: Ink extent runs under the nominal point size; a rough hint only.
_SIZE_FROM_EXTENT = 1.25


class OCRUnavailable(RuntimeError):
    """Raised when the tesseract binary or its Python binding is missing."""


def _require_tesseract() -> ModuleType:
    """Import pytesseract and confirm the binary is reachable.

    Returns
    -------
    ModuleType
        The imported ``pytesseract`` module.

    Raises
    ------
    OCRUnavailable
        If the binding is not installed or the binary is not on ``PATH``.
    """
    try:
        import pytesseract
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OCRUnavailable(
            "pytesseract is not installed; install it or pass --ocr never"
        ) from exc
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - depends on environment
        raise OCRUnavailable(
            "the tesseract binary was not found on PATH; install tesseract or pass --ocr never"
        ) from exc
    return pytesseract


def ocr_page(
    doc: pymupdf.Document,
    number: int,
    dpi: int = 300,
    lang: str = "eng",
    psm: int = 6,
) -> Page:
    """Rasterize a page and return OCR-derived spans.

    Parameters
    ----------
    doc : pymupdf.Document
        An open document.
    number : int
        Zero-based page index.
    dpi : int, optional
        Rasterization resolution. Default 300.
    lang : str, optional
        Tesseract language pack. Default ``"eng"``.
    psm : int, optional
        Tesseract page segmentation mode. Default 6, a uniform text block.

    Returns
    -------
    Page
        The page with one span per recognized word, in PDF points.

    Raises
    ------
    OCRUnavailable
        If tesseract or its Python binding is unavailable.
    """
    pytesseract = _require_tesseract()
    from PIL import Image

    pdf_page = doc[number]
    rect = pdf_page.rect
    scale = dpi / 72.0
    pix = pdf_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes("png")))

    data = pytesseract.image_to_data(
        image,
        lang=lang,
        config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )

    spans: list[Span] = []
    for i, raw_text in enumerate(data["text"]):
        text = (raw_text or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if 0 <= conf < _MIN_CONF:
            continue
        # The table rules rasterize into stray marks that tesseract reads as
        # lone punctuation. Real content is never a single such character, so
        # dropping low-confidence ones keeps borders out of section names.
        if len(text) == 1 and text in _RULE_NOISE and conf < _NOISE_CONF:
            continue
        left, top = data["left"][i], data["top"][i]
        width, height = data["width"][i], data["height"][i]
        x0, y0 = left / scale, top / scale
        x1, y1 = (left + width) / scale, (top + height) / scale
        spans.append(
            Span(
                text=text,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                # Approximate only, and deliberately not trusted for layout
                # decisions: a word box hugs its ink, so "preparation" and
                # "Off" measure very differently inside one 8pt row. Layout
                # code checks ``Span.source`` before believing a size.
                size=round((y1 - y0) * _SIZE_FROM_EXTENT, 2),
                font="",
                bold=False,
                source="ocr",
            )
        )

    return Page(
        number=number,
        width=rect.width,
        height=rect.height,
        spans=spans,
        source="ocr",
        printable_ratio=1.0,
        raw_text="\n".join(s.text for s in spans),
    )
