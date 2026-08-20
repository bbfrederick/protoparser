"""Split a page into its two independent key/value columns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..extract.spans import Span
from ..profiles.base import LayoutConfig


@dataclass
class Column:
    """One of the two side-by-side tables on a page.

    Attributes
    ----------
    side : str
        ``"left"`` or ``"right"``.
    spans : list of Span
        Every span assigned to this column.
    x_min, x_max : float
        Horizontal extent of the column's content, in points.
    value_x : float
        Spans starting at or past this x are values, not labels.
    """

    side: str
    spans: list[Span]
    x_min: float
    x_max: float
    value_x: float

    def is_value(self, span: Span) -> bool:
        """Whether a span belongs to this column's value cell.

        Parameters
        ----------
        span : Span
            A span already assigned to this column.

        Returns
        -------
        bool
            ``True`` when the span starts at or past ``value_x``.
        """
        return span.x0 >= self.value_x


def split_columns(spans: Sequence[Span], page_width: float, layout: LayoutConfig) -> list[Column]:
    """Assign spans to the left or right column by their left edge.

    Classification uses ``x0`` rather than the span centre because a long
    value can spill well past the column midpoint while its cell still starts
    at the fixed value origin.

    Parameters
    ----------
    spans : sequence of Span
        Body spans for one page, with header, footer and box removed.
    page_width : float
        Page width in points.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.

    Returns
    -------
    list of Column
        One entry per non-empty column, left first.
    """
    midpoint = page_width * layout.column_split_ratio
    buckets: dict[str, list[Span]] = {"left": [], "right": []}
    for span in spans:
        buckets["left" if span.x0 < midpoint else "right"].append(span)

    columns: list[Column] = []
    for side in ("left", "right"):
        members = buckets[side]
        if not members:
            continue
        x_min = min(s.x0 for s in members)
        x_max = max(s.x1 for s in members)
        # The label/value boundary is a fixed fraction of the column's own
        # content width, so it tracks a column whose values all happen to be
        # short ("Off", "On") instead of assuming page-absolute coordinates.
        value_x = x_min + (x_max - x_min) * layout.value_x_ratio
        columns.append(Column(side=side, spans=members, x_min=x_min, x_max=x_max, value_x=value_x))
    return columns
