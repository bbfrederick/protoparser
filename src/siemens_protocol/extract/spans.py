"""The one text primitive the whole pipeline speaks.

Native PyMuPDF extraction and the tesseract OCR fallback both produce
``Span`` objects, so nothing downstream of :mod:`siemens_protocol.extract`
needs to know where a page's text came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

# PyMuPDF packs font style into a bit field; bit 4 is the bold flag.
_FLAG_BOLD = 1 << 4


@dataclass
class Span:
    """A run of text with its position on the page, in PDF points.

    Attributes
    ----------
    text : str
        The text of the run.
    x0, y0, x1, y1 : float
        Bounding box in PDF points, origin at the top left of the page.
    size : float
        Font size in points. Exact for native spans, approximate for OCR.
    font : str
        Font name. Empty on the OCR path.
    bold : bool
        Whether the run is bold. Always ``False`` on the OCR path.
    source : str
        ``"native"`` or ``"ocr"``, the acquisition path this span came from.
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float = 0.0
    font: str = ""
    bold: bool = False
    source: str = "native"

    @property
    def cx(self) -> float:
        """Horizontal centre of the span.

        Returns
        -------
        float
            The x coordinate midway between ``x0`` and ``x1``.
        """
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        """Vertical centre of the span.

        Returns
        -------
        float
            The y coordinate midway between ``y0`` and ``y1``.
        """
        return (self.y0 + self.y1) / 2.0

    @property
    def height(self) -> float:
        """Height of the bounding box.

        Returns
        -------
        float
            ``y1 - y0``, in points.
        """
        return self.y1 - self.y0

    @classmethod
    def from_pymupdf(cls, span: dict) -> Span:
        """Build a span from one PyMuPDF ``dict``-mode span.

        Parameters
        ----------
        span : dict
            A span as returned inside ``page.get_text("dict")``, with at least
            ``bbox`` and ``text`` keys.

        Returns
        -------
        Span
            The equivalent span in this module's representation.
        """
        x0, y0, x1, y1 = span["bbox"]
        return cls(
            text=span["text"],
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            size=span.get("size", 0.0),
            font=span.get("font", ""),
            bold=bool(span.get("flags", 0) & _FLAG_BOLD),
            source="native",
        )

    def to_dict(self) -> dict:
        """Serialize the span for a debug dump.

        Returns
        -------
        dict
            Text, rounded bounding box, size, font, weight and source.
        """
        return {
            "text": self.text,
            "bbox": [round(v, 2) for v in (self.x0, self.y0, self.x1, self.y1)],
            "size": round(self.size, 2),
            "font": self.font,
            "bold": self.bold,
            "source": self.source,
        }


@dataclass
class Page:
    """A single PDF page after text acquisition.

    Attributes
    ----------
    number : int
        Zero-based index into the document.
    width, height : float
        Page size in points.
    spans : list of Span
        Every non-blank text run on the page.
    source : str
        ``"native"`` or ``"ocr"``, how this page's spans were obtained.
    printable_ratio : float
        Fraction of the native text that is plausible protocol text; the
        signal used to decide whether the page needs OCR.
    raw_text : str
        The page's text as one string, kept for version detection.
    """

    number: int
    width: float
    height: float
    spans: list[Span] = field(default_factory=list)
    source: str = "native"
    printable_ratio: float = 1.0
    raw_text: str = ""

    @property
    def label(self) -> int:
        """One-based page number, which is what users and reports quote.

        Returns
        -------
        int
            ``number + 1``.
        """
        return self.number + 1

    def body_spans(self, top: float, bottom: float) -> list[Span]:
        """Spans between the running page header and the page-number footer.

        Parameters
        ----------
        top : float
            Lowest y coordinate to keep, in points.
        bottom : float
            Highest y coordinate to keep, in points.

        Returns
        -------
        list of Span
            Spans whose ``y0`` falls within ``[top, bottom]``.
        """
        return [s for s in self.spans if top <= s.y0 <= bottom]

    def to_dict(self) -> dict:
        """Serialize the page for a debug dump.

        Returns
        -------
        dict
            Page label, source, printable ratio and every span.
        """
        return {
            "page": self.label,
            "source": self.source,
            "printable_ratio": round(self.printable_ratio, 3),
            "spans": [s.to_dict() for s in self.spans],
        }


def sort_reading_order(spans: Sequence[Span]) -> list[Span]:
    """Sort spans top to bottom, then left to right.

    Parameters
    ----------
    spans : sequence of Span
        Spans to order.

    Returns
    -------
    list of Span
        A new list in reading order.
    """
    return sorted(spans, key=lambda s: (round(s.y0, 1), s.x0))


def join_spans(spans: Sequence[Span], sep: str = " ") -> str:
    """Concatenate spans in x order.

    Native extraction usually gives one span per table cell, so this is a
    no-op there. OCR gives one span per word, and this is what puts the words
    of a cell back together.

    Parameters
    ----------
    spans : sequence of Span
        Spans belonging to a single cell or line.
    sep : str, optional
        Separator inserted between spans. Default ``" "``.

    Returns
    -------
    str
        The joined, stripped text. Empty if no span carries text.
    """
    ordered = sorted(spans, key=lambda s: s.x0)
    return sep.join(s.text.strip() for s in ordered if s.text.strip()).strip()


def x_bounds(spans: Sequence[Span]) -> tuple[float, float] | None:
    """Horizontal extent of a group of spans.

    Parameters
    ----------
    spans : sequence of Span
        Spans to measure.

    Returns
    -------
    tuple of float or None
        ``(leftmost x0, rightmost x1)``, or ``None`` if ``spans`` is empty.
    """
    if not spans:
        return None
    return min(s.x0 for s in spans), max(s.x1 for s in spans)
