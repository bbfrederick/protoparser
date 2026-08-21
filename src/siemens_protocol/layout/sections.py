"""Turn rows into an ordered stream of (section, key, value) records.

Three shapes of row have to be told apart, and only one of them is a plain
key/value pair:

* ``Routine`` -- a section title: a label with nothing to its right.
* ``preparation`` -- the tail of a label that wrapped onto a second line,
  while its value stayed aligned with the first.
* ``Elliptical filter`` -- the tail of a *value* that wrapped.

Titles and wrapped tails are both label-only rows, so font weight alone
cannot separate them, and it is unavailable on the OCR path. The primary
structural discriminator is horizontal: titles hang to the left of the table
cells. Vertical spacing separates the two kinds of tail, which are set
tighter than the table's normal row pitch because they share a cell with the
line above.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

from ..profiles.base import LayoutConfig
from .columns import Column
from .rows import Row, build_rows, row_pitch

#: Section used for records that appear before any title has been seen.
UNSECTIONED = "(unsectioned)"


@dataclass
class Record:
    """One parameter reading, with enough provenance to debug it.

    Attributes
    ----------
    section : str
        The section in force where the reading was printed.
    key : str
        The parameter label.
    value : str
        The parameter value, as a raw string. May be empty.
    indent : int
        ``0`` for a top-level row, ``1`` for a sub-row of a repeating group.
    page : int
        One-based page number the reading was printed on.
    column : str
        ``"left"`` or ``"right"``.
    y : float
        Vertical position on the page, in points.
    """

    section: str
    key: str
    value: str
    indent: int = 0
    page: int = 0
    column: str = "left"
    y: float = 0.0

    def to_dict(self) -> dict:
        """Serialize the record for a debug dump.

        Returns
        -------
        dict
            The record's fields, tagged ``kind: "record"``.
        """
        return {
            "kind": "record",
            "section": self.section,
            "key": self.key,
            "value": self.value,
            "indent": self.indent,
            "page": self.page,
            "column": self.column,
            "y": round(self.y, 2),
        }


@dataclass
class SectionMarker:
    """A section title, emitted so that empty sections survive into the output.

    Attributes
    ----------
    section : str
        The title text.
    page : int
        One-based page number the title was printed on.
    column : str
        ``"left"`` or ``"right"``.
    y : float
        Vertical position on the page, in points.
    """

    section: str
    page: int
    column: str
    y: float

    def to_dict(self) -> dict:
        """Serialize the marker for a debug dump.

        Returns
        -------
        dict
            The marker's fields, tagged ``kind: "section"``, shaped like a
            record so a debug dump can be read as one stream.
        """
        return {
            "kind": "section",
            "section": self.section,
            "key": "",
            "value": "",
            "indent": 0,
            "page": self.page,
            "column": self.column,
            "y": round(self.y, 2),
        }


def _body_size(rows: Sequence[Row]) -> float:
    """Typical font size of the column's parameter text.

    Parameters
    ----------
    rows : sequence of Row
        Rows of one column.

    Returns
    -------
    float
        Median size of rows carrying a value, falling back to all rows, or
        ``0.0`` when no size is available.
    """
    sizes = [r.size for r in rows if r.has_value and r.size > 0]
    if not sizes:
        sizes = [r.size for r in rows if r.size > 0]
    return statistics.median(sizes) if sizes else 0.0


def _is_strong_title(row: Row, body_size: float, layout: LayoutConfig) -> bool:
    """Font-based title signals, used only where the font metrics are real.

    On native pages a title is 10pt bold against 8pt regular body text, which
    is decisive. OCR supplies neither weight nor a usable size -- a word box
    hugs its ink, so a descender in an 8pt row can measure taller than a 10pt
    title -- so this test abstains there and the geometric rules decide.

    Parameters
    ----------
    row : Row
        A label-only row.
    body_size : float
        Typical size of the column's parameter text.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.

    Returns
    -------
    bool
        ``True`` when the font metrics mark this row as a title.
    """
    if not row.has_font_metrics:
        return False
    if row.bold:
        return True
    return bool(body_size) and row.size >= body_size * layout.title_size_ratio


def _is_outdented_title(row: Row, label_x: float | None, layout: LayoutConfig) -> bool:
    """Structural title rule: titles hang to the left of the table cells.

    Parameter labels start at the table's inner edge; section titles start at
    its outer edge, a couple of points further left. Measuring that offset
    against rows that are *definitely* parameters -- the ones with a value
    beside them -- means a column containing no title at all cannot produce a
    false positive, because then nothing is outdented.

    This is the rule that carries the OCR path. Rasterized word boxes hold
    their left edge to well under a point, while row gaps there scatter from
    0.6 to 2.3 of the nominal pitch and separate nothing.

    Parameters
    ----------
    row : Row
        A label-only row.
    label_x : float or None
        Left edge of the column's parameter labels, or ``None`` when the
        column has no parameter row to measure against.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.

    Returns
    -------
    bool
        ``True`` when the row is outdented far enough to be a title.
    """
    if label_x is None:
        return False
    return (label_x - row.x0) >= layout.title_outdent * layout.title_outdent_ratio


def _is_gap_title(row: Row, pitch: float, layout: LayoutConfig) -> bool:
    """Last-resort rule for a column with no parameter rows to measure against.

    Parameters
    ----------
    row : Row
        A label-only row.
    pitch : float
        The column's normal row spacing.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.

    Returns
    -------
    bool
        ``True`` when the row opens a new block, including the first row of a
        column, which has nothing above it.
    """
    if row.gap_above <= 0:
        return True
    return pitch > 0 and row.gap_above >= pitch * layout.title_gap_ratio


def _continues_label(row: Row, layout: LayoutConfig) -> bool:
    """Whether a label-only row continues the label above it, by its wording.

    Some releases set a wrapped label at the same pitch as an ordinary row,
    so the gap says nothing. What does say something is capitalization: these
    exports capitalize the first word of every label, so a row opening with a
    lower-case word is the tail of a phrase rather than a label of its own.
    Across the example corpus the only rows this matches are genuine
    continuations, and the releases that do set continuations tighter produce
    none at all.

    Parameters
    ----------
    row : Row
        A row carrying a label and no value.
    layout : LayoutConfig
        Geometry thresholds, read for ``lowercase_continues_label``.

    Returns
    -------
    bool
        ``True`` when the row should be appended to the preceding label.
    """
    if not layout.lowercase_continues_label:
        return False
    label = row.label.strip()
    return bool(label) and label[:1].islower()


def parse_column(
    column: Column,
    layout: LayoutConfig,
    page_label: int,
    section: str | None,
) -> list[Record | SectionMarker]:
    """Read one column top to bottom into records and section markers.

    Parameters
    ----------
    column : Column
        A column produced by :func:`~siemens_protocol.layout.columns.split_columns`.
    layout : LayoutConfig
        Geometry thresholds for the release being parsed.
    page_label : int
        One-based page number, recorded on every item for provenance.
    section : str or None
        The section in force when the column starts, so a section that runs
        past a column or page break keeps its name.

    Returns
    -------
    list
        :class:`Record` and :class:`SectionMarker` items in reading order.
    """
    rows = build_rows(column, layout)
    if not rows:
        return []
    pitch = row_pitch(rows)
    body_size = _body_size(rows)
    pair_x = [r.x0 for r in rows if r.has_label and r.has_value]
    label_x = min(pair_x) if pair_x else None

    out: list[Record | SectionMarker] = []
    current = section or UNSECTIONED
    last_record: Record | None = None
    last_was_title = False

    def open_section(title: str, row: Row) -> None:
        """Start a new section at ``row``.

        Parameters
        ----------
        title : str
            The section title text.
        row : Row
            The row the title was read from.

        Returns
        -------
        None
        """
        nonlocal current, last_record, last_was_title
        current = title
        out.append(SectionMarker(section=title, page=page_label, column=column.side, y=row.y0))
        last_record = None
        last_was_title = True

    def open_record(key: str, value: str, row: Row) -> None:
        """Start a new parameter record at ``row``.

        Parameters
        ----------
        key : str
            The parameter label.
        value : str
            The parameter value, possibly empty.
        row : Row
            The row the reading was read from.

        Returns
        -------
        None
        """
        nonlocal last_record, last_was_title
        last_record = Record(
            section=current,
            key=key,
            value=value,
            indent=row.indent,
            page=page_label,
            column=column.side,
            y=row.y0,
        )
        out.append(last_record)
        last_was_title = False

    for row in rows:
        tight = pitch > 0 and 0 < row.gap_above < pitch * layout.continuation_gap_ratio

        if row.has_label and row.has_value:
            open_record(row.label, row.value, row)
            continue

        if row.has_value and not row.has_label:
            # A value that wrapped onto the next line.
            if last_record is not None and tight:
                last_record.value = f"{last_record.value} {row.value}".strip()
                last_was_title = False
            else:
                open_record("", row.value, row)
            continue

        if row.has_label:
            if tight and last_was_title and out:
                # A section title that wrapped onto a second line. Checked
                # first so that the title tests below do not split one title
                # into two sections.
                marker = out[-1]
                current = f"{marker.section} {row.label}".strip()
                marker.section = current
                continue
            if _is_strong_title(row, body_size, layout) or _is_outdented_title(
                row, label_x, layout
            ):
                open_section(row.label, row)
                continue
            if (tight or _continues_label(row, layout)) and last_record is not None:
                last_record.key = f"{last_record.key} {row.label}".strip()
                last_was_title = False
                continue
            if label_x is None and _is_gap_title(row, pitch, layout):
                open_section(row.label, row)
                continue
            # A parameter that is printed with no value at all.
            open_record(row.label, "", row)

    return out


def current_section(items: Sequence[Record | SectionMarker], fallback: str | None) -> str | None:
    """The section in force after reading a run of items.

    Parameters
    ----------
    items : sequence
        Records and markers, in reading order.
    fallback : str or None
        Section to return when ``items`` names none.

    Returns
    -------
    str or None
        The last section named, or ``fallback``.
    """
    for item in reversed(items):
        section = getattr(item, "section", None)
        if section:
            return section
    return fallback
