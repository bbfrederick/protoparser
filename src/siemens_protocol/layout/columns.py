"""Split a page into its two independent key/value columns."""

from __future__ import annotations

import bisect
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


def value_origin(spans: Sequence[Span], layout: LayoutConfig) -> float | None:
    """The x these tables actually start their value cell at, measured.

    Every value in a column is set flush to one origin, so that origin is the
    densest cluster of left edges anywhere right of the labels -- each value,
    and each line a value wrapped onto, contributes its first span to it.
    Measuring it costs a pass over the spans and is worth it because the
    ratio in ``value_x_ratio`` is derived from ``x_max``, which one
    over-running value is enough to poison; see :func:`split_columns`.

    The floor at ``value_origin_min_ratio`` is what keeps the sub-rows of a
    repeating group out of the answer: those are indented a few points from
    the label origin, not half a column. It is measured across where spans
    *start* rather than where they end, so that the overhang this function
    exists to survive cannot raise the floor over the origin it is looking
    for: a column's rightmost cell origin sits under half the page, which
    leaves the floor at an eighth of it and the origin at a half.

    Parameters
    ----------
    spans : sequence of Span
        Every span assigned to one column.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.

    Returns
    -------
    float or None
        Left edge of the value cell, or ``None`` when no cluster is dense
        enough to be trusted -- a column of two or three spans names no
        origin, and guessing one there would be worse than the ratio.
    """
    lefts = sorted(s.x0 for s in spans)
    if not lefts:
        return None
    floor = lefts[0] + (lefts[-1] - lefts[0]) * layout.value_origin_min_ratio
    best_x: float | None = None
    best_count = 0
    for left in lefts:
        if left <= floor:
            continue
        count = bisect.bisect_left(
            lefts, left + layout.value_origin_tolerance
        ) - bisect.bisect_left(lefts, left)
        if count > best_count:
            best_x, best_count = left, count
    if best_x is None or best_count < max(3, len(lefts) * layout.value_origin_min_share):
        return None
    return best_x


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
        # That fraction is only as good as x_max, and x_max is a maximum: one
        # value wide enough to overhang the column -- a sampling-table file
        # name in a spectroscopy sequence's Special card -- drags the boundary
        # right of the value cell itself, and then every value on the page
        # reads as label text. Never let the boundary cross a measured origin.
        origin = value_origin(members, layout)
        if origin is not None:
            value_x = min(value_x, origin)
        columns.append(Column(side=side, spans=members, x_min=x_min, x_max=x_max, value_x=value_x))
    return columns
