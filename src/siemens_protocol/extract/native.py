"""Native text extraction with PyMuPDF, plus the usability check."""

from __future__ import annotations

import pymupdf

from .spans import Page, Span

# Characters that legitimately occur in these printouts. Anything outside this
# set is a symptom of a broken subset font mapping to unassigned code points.
_EXTRA_PRINTABLE = set("×÷³²°µ±–—‘’“” −")


def printable_ratio(text: str) -> float:
    """Fraction of characters that are plausible protocol text.

    A page rendered in a scrambled CID font extracts as private-use or
    unmapped code points, which drives this ratio down. That is the signal
    used to decide a page needs OCR.

    Parameters
    ----------
    text : str
        The page's extracted text.

    Returns
    -------
    float
        A value in ``[0, 1]``. ``0.0`` for empty text.
    """
    if not text:
        return 0.0
    good = 0
    for ch in text:
        if ch.isspace() or ch.isalnum() or ch in _EXTRA_PRINTABLE:
            good += 1
        elif 32 <= ord(ch) < 127:
            good += 1
    return good / len(text)


def extract_page(doc: pymupdf.Document, number: int) -> Page:
    """Pull spans, geometry and the printable ratio for one page.

    Parameters
    ----------
    doc : pymupdf.Document
        An open document.
    number : int
        Zero-based page index.

    Returns
    -------
    Page
        The page with its native spans; image blocks are ignored.
    """
    pdf_page = doc[number]
    rect = pdf_page.rect
    raw = pdf_page.get_text()
    spans: list[Span] = []
    for block in pdf_page.get_text("dict")["blocks"]:
        if block.get("type") != 0:  # skip images
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if span["text"].strip():
                    spans.append(Span.from_pymupdf(span))
    return Page(
        number=number,
        width=rect.width,
        height=rect.height,
        spans=spans,
        source="native",
        printable_ratio=printable_ratio(raw),
        raw_text=raw,
    )


def is_usable(page: Page, min_ratio: float, min_spans: int = 5) -> bool:
    """Whether the native layer is good enough to skip OCR for this page.

    Parameters
    ----------
    page : Page
        A page carrying native spans.
    min_ratio : float
        Minimum acceptable printable-character ratio.
    min_spans : int, optional
        Minimum number of spans a real content page must have. Default 5.

    Returns
    -------
    bool
        ``True`` when the native text can be used as is.
    """
    if len(page.spans) < min_spans:
        return False
    return page.printable_ratio >= min_ratio
