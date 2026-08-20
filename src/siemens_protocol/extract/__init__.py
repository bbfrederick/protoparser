"""Text acquisition: native PyMuPDF spans, with an OCR fallback.

Both paths produce :class:`~siemens_protocol.extract.spans.Span` objects in
PDF points, so layout reconstruction is identical for either source.
"""

from __future__ import annotations

from .native import extract_page, is_usable, printable_ratio
from .ocr import OCRUnavailable, ocr_page
from .spans import Page, Span, join_spans, sort_reading_order, x_bounds

__all__ = [
    "Page",
    "Span",
    "extract_page",
    "is_usable",
    "printable_ratio",
    "ocr_page",
    "OCRUnavailable",
    "join_spans",
    "sort_reading_order",
    "x_bounds",
]
