"""Group spans into table rows and split each row into label and value cells."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Sequence

from ..extract.spans import Span, join_spans
from ..profiles.base import LayoutConfig
from .columns import Column


@dataclass
class Row:
    """One horizontal line of a column's table.

    Attributes
    ----------
    y0 : float
        Top of the row, in points.
    label_spans : list of Span
        Spans in the label cell, left of the column's value origin.
    value_spans : list of Span
        Spans in the value cell.
    gap_above : float
        Distance from the previous row's ``y0``. Zero for the first row.
    """

    y0: float
    label_spans: list[Span] = field(default_factory=list)
    value_spans: list[Span] = field(default_factory=list)
    gap_above: float = 0.0
    _indent: int = 0

    @property
    def label(self) -> str:
        """Text of the label cell.

        Returns
        -------
        str
            The label spans joined in x order. Empty when there is no label.
        """
        return join_spans(self.label_spans)

    @property
    def value(self) -> str:
        """Text of the value cell.

        Returns
        -------
        str
            The value spans joined in x order. Empty when there is no value.
        """
        return join_spans(self.value_spans)

    @property
    def has_label(self) -> bool:
        """Whether the row carries label text.

        Returns
        -------
        bool
            ``True`` when :attr:`label` is non-empty.
        """
        return bool(self.label)

    @property
    def has_value(self) -> bool:
        """Whether the row carries value text.

        Returns
        -------
        bool
            ``True`` when :attr:`value` is non-empty.
        """
        return bool(self.value)

    @property
    def x0(self) -> float:
        """Left edge of the row's leading cell.

        Returns
        -------
        float
            The smallest ``x0`` among the label spans, or among the value
            spans for a value-only row.
        """
        spans = self.label_spans or self.value_spans
        return min(s.x0 for s in spans)

    @property
    def size(self) -> float:
        """Font size of the row's leading cell.

        Returns
        -------
        float
            The largest span size in the cell. Approximate on the OCR path;
            see :attr:`has_font_metrics`.
        """
        spans = self.label_spans or self.value_spans
        return max(s.size for s in spans)

    @property
    def bold(self) -> bool:
        """Whether any span in the leading cell is bold.

        Returns
        -------
        bool
            Always ``False`` on the OCR path, which supplies no font weight.
        """
        spans = self.label_spans or self.value_spans
        return any(s.bold for s in spans)

    @property
    def has_font_metrics(self) -> bool:
        """Whether this row's font size and weight come from the PDF itself.

        OCR supplies neither, so layout rules that lean on them must fall
        back to geometry.

        Returns
        -------
        bool
            ``True`` only when every span in the leading cell is native and
            carries a real size.
        """
        spans = self.label_spans or self.value_spans
        return bool(spans) and all(s.source == "native" and s.size > 0 for s in spans)

    @property
    def indent(self) -> int:
        """Indentation level of the label.

        Returns
        -------
        int
            ``0`` for a top-level label, ``1`` for a sub-row of a repeating
            group such as the slices under a slice group.
        """
        return self._indent

    def to_dict(self) -> dict:
        """Serialize the row for a debug dump.

        Returns
        -------
        dict
            Position, label, value, indent, weight, size and gap above.
        """
        return {
            "y": round(self.y0, 2),
            "label": self.label,
            "value": self.value,
            "indent": self.indent,
            "bold": self.bold,
            "size": round(self.size, 2),
            "gap_above": round(self.gap_above, 2),
        }


def build_rows(column: Column, layout: LayoutConfig) -> list[Row]:
    """Cluster a column's spans into rows, then into label and value cells.

    Parameters
    ----------
    column : Column
        A column produced by :func:`~siemens_protocol.layout.columns.split_columns`.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.

    Returns
    -------
    list of Row
        Rows in top-to-bottom order, with indent levels and gaps filled in.
    """
    ordered = sorted(column.spans, key=lambda s: (s.y0, s.x0))
    if not ordered:
        return []

    # Rows are formed by vertical *overlap*, not by matching top edges. A
    # top-edge test breaks on the OCR path, where a word box hugs the ink:
    # the hyphen in "Contrast - Common" starts several points below the
    # letters around it and would be split off into a row of its own.
    # Overlap relative to the shorter span keeps small, vertically centred
    # glyphs with their line while still separating a wrapped label, whose
    # box only clips the line above it.
    clusters: list[list[Span]] = []
    bounds: list[list[float]] = []
    for span in ordered:
        if clusters:
            top, bottom = bounds[-1]
            overlap = min(bottom, span.y1) - max(top, span.y0)
            shorter = min(bottom - top, span.height) or 1.0
            if overlap >= shorter * layout.row_overlap_ratio or (
                abs(span.y0 - clusters[-1][0].y0) <= layout.row_tolerance
            ):
                clusters[-1].append(span)
                bounds[-1] = [min(top, span.y0), max(bottom, span.y1)]
                continue
        clusters.append([span])
        bounds.append([span.y0, span.y1])

    rows: list[Row] = []
    for cluster in clusters:
        row = Row(y0=min(s.y0 for s in cluster))
        for span in cluster:
            if column.is_value(span):
                row.value_spans.append(span)
            else:
                row.label_spans.append(span)
        rows.append(row)

    # Indentation is measured against the column's leftmost label origin.
    label_x = [r.x0 for r in rows if r.has_label]
    base_x = min(label_x) if label_x else column.x_min
    for row in rows:
        if row.has_label and row.x0 > base_x + layout.indent_threshold:
            row._indent = 1

    for i, row in enumerate(rows):
        row.gap_above = row.y0 - rows[i - 1].y0 if i else 0.0

    return rows


def row_pitch(rows: Sequence[Row]) -> float:
    """The column's normal line spacing.

    Wrapped continuation lines sit *tighter* than this, which is the signal
    used to merge them back into the row above. The median is used because
    section titles introduce a handful of deliberately larger gaps.

    Parameters
    ----------
    rows : sequence of Row
        Rows of one column, in order.

    Returns
    -------
    float
        Median gap between consecutive rows, or ``0.0`` for fewer than two.
    """
    gaps = [r.gap_above for r in rows[1:] if r.gap_above > 0]
    if not gaps:
        return 0.0
    return statistics.median(gaps)
