"""Header-box detection, which is also what cuts the page stream into scans.

Every scan opens with a bordered box holding two lines: a UNC-style path
whose last component is the protocol name, and a summary line beginning
``TA:``. A page either opens a scan (it starts with that box) or continues
the previous one, so finding the box gives the split points and the per-scan
metadata in one pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from .extract.spans import Page, Span, join_spans
from .layout.rows import cluster_by_overlap
from .profiles.base import LayoutConfig, VersionProfile

#: The summary line is the reliable anchor: it is the only line in these
#: documents that opens with ``TA:``. The path line is then simply the row
#: directly above it inside the box.
_SUMMARY_RE = re.compile(r"^\s*TA\s*[:.]", re.IGNORECASE)
#: How far down the page, in rows, the box may start.
_MAX_ROWS = 3


@dataclass
class HeaderBox:
    """The boxed banner that opens a scan.

    Attributes
    ----------
    path : str
        The UNC-style protocol path, rejoined if it wrapped.
    summary : str
        The ``TA: ...`` summary line, unparsed.
    bottom_y : float
        Bottom of the box, in points; body content starts below it.
    spans : list of Span
        The spans the box was built from.
    """

    path: str
    summary: str
    bottom_y: float
    spans: list[Span] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Protocol name: the last component of the path.

        Returns
        -------
        str
            The final path component, or an empty string for an empty path.
        """
        parts = [p for p in re.split(r"[\\/]+", self.path) if p.strip()]
        return parts[-1].strip() if parts else ""

    def to_dict(self) -> dict:
        """Serialize the box for a debug dump.

        Returns
        -------
        dict
            Path, summary line and derived protocol name.
        """
        return {"path": self.path, "summary": self.summary, "name": self.name}


def _rows_in_region(spans: Sequence[Span], layout: LayoutConfig) -> list[list[Span]]:
    """Cluster header-region spans into full-page-width rows.

    The box straddles both table columns, so unlike the body it must not be
    column-split before rows are formed. Lines are formed by the same
    vertical-overlap rule as the body: a protocol path may contain a hyphen
    (``\\Research\\Investigators - validated on FIT\\...``), and under OCR that
    hyphen sits low enough to fall outside a top-edge tolerance. Split onto
    its own line it would be appended to the scan name, because path lines
    are concatenated with no separator to repair mid-word wraps.

    Parameters
    ----------
    spans : sequence of Span
        Every span on the page.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.

    Returns
    -------
    list of list of Span
        Rows in top-to-bottom order, each ordered left to right.
    """
    region = [s for s in spans if layout.page_header_max_y <= s.y0 <= layout.header_box_max_y]
    return cluster_by_overlap(region, layout)


def _header_label_hits(text: str, profile: VersionProfile) -> int:
    """How many of the profile's header labels a line carries.

    Parameters
    ----------
    text : str
        A candidate summary line.
    profile : VersionProfile
        The release profile, which declares the labels.

    Returns
    -------
    int
        The number of declared labels found in the line.
    """
    return sum(
        1 for _key, pattern in profile.header_labels if re.search(pattern, text, re.IGNORECASE)
    )


def find_header_box(page: Page, layout: LayoutConfig, profile: VersionProfile) -> HeaderBox | None:
    """Return the header box on a page, or ``None`` if it continues a scan.

    Anchoring on the summary line rather than the path is what makes this
    safe. An ordinary body row can look path-like once the two columns are
    read as one wide line -- ``1st Signal/Mode`` next to ``None`` reads as a
    slash-separated path -- so a candidate is only accepted when the row
    carries at least two of the profile's header fields, which no parameter
    row ever does.

    Parameters
    ----------
    page : Page
        A page with its spans acquired.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.
    profile : VersionProfile
        The release profile, used to confirm the summary line.

    Returns
    -------
    HeaderBox or None
        The box, or ``None`` when this page continues the previous scan.
    """
    rows = _rows_in_region(page.spans, layout)
    if not rows:
        return None

    for index, row in enumerate(rows[:_MAX_ROWS]):
        text = join_spans(row)
        if not _SUMMARY_RE.match(text) or _header_label_hits(text, profile) < 2:
            continue
        # The path can be too long for the box and wrap onto a second line,
        # and this formatter breaks it mid-word (".../ACPC li" + "ne"), so the
        # lines are concatenated with no separator. Words *within* a line are
        # still space-joined, which is what the OCR path needs. Everything
        # above the summary belongs to the box: the box is always the topmost
        # thing on a page that has one.
        path_rows = rows[:index]
        used = [span for r in path_rows for span in r] + list(row)
        return HeaderBox(
            path="".join(join_spans(r) for r in path_rows).strip(),
            summary=text.strip(),
            bottom_y=max(s.y1 for s in used),
            spans=used,
        )
    return None


def body_spans(page: Page, layout: LayoutConfig, header: HeaderBox | None) -> list[Span]:
    """Page spans with the running header, page number and any box removed.

    Parameters
    ----------
    page : Page
        A page with its spans acquired.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.
    header : HeaderBox or None
        The page's header box, if it has one.

    Returns
    -------
    list of Span
        Spans belonging to the two key/value tables.
    """
    top = header.bottom_y + 1.0 if header else layout.page_header_max_y
    return [s for s in page.spans if top <= s.y0 <= layout.page_footer_min_y]


def running_header(page: Page, layout: LayoutConfig) -> str:
    """The ``SIEMENS MAGNETOM ...`` line printed at the top of every page.

    Parameters
    ----------
    page : Page
        A page with its spans acquired.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.

    Returns
    -------
    str
        The running header text, empty if the page has none.
    """
    top = [s for s in page.spans if s.y0 < layout.page_header_max_y]
    return join_spans(top)
