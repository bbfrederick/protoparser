"""Unit tests for the layout primitives, on synthetic spans.

Working on hand-built spans rather than a PDF makes it possible to isolate
each rule and to construct the cases the example files happen not to contain.
"""

from __future__ import annotations

from siemens_protocol.extract.spans import Span
from siemens_protocol.layout.columns import Column, split_columns
from siemens_protocol.layout.rows import build_rows, row_pitch
from siemens_protocol.layout.sections import Record, SectionMarker, parse_column
from siemens_protocol.model import build_sections
from siemens_protocol.profiles.base import LayoutConfig

LAYOUT = LayoutConfig()
PAGE_WIDTH = 595.0

# Geometry copied from the real printouts: titles at 56.7, labels at 59.1
# (70.3 when indented), values at 197.9, and an 11pt row pitch.
TITLE_X, LABEL_X, INDENT_X, VALUE_X = 56.7, 59.1, 70.3, 197.9


def title(text: str, y: float, bold: bool = True, source: str = "native") -> Span:
    """Build a synthetic section-title span.

    Parameters
    ----------
    text : str
        The title text.
    y : float
        Top of the row, in points.
    bold : bool, optional
        Whether the span is bold. Default ``True``.
    source : str, optional
        ``"native"`` or ``"ocr"``. Default ``"native"``.

    Returns
    -------
    Span
        A 10pt span at the column's outer edge.
    """
    return Span(text, TITLE_X, y, TITLE_X + 50, y + 13.8, size=10.0, bold=bold, source=source)


def label(text: str, y: float, x: float = LABEL_X, source: str = "native") -> Span:
    """Build a synthetic parameter-label span.

    Parameters
    ----------
    text : str
        The label text.
    y : float
        Top of the row, in points.
    x : float, optional
        Left edge. Defaults to the column's inner edge.
    source : str, optional
        ``"native"`` or ``"ocr"``. Default ``"native"``.

    Returns
    -------
    Span
        An 8pt span in the label cell.
    """
    return Span(text, x, y, x + 60, y + 11.0, size=8.0, source=source)


def value(text: str, y: float, source: str = "native") -> Span:
    """Build a synthetic parameter-value span.

    Parameters
    ----------
    text : str
        The value text.
    y : float
        Top of the row, in points.
    source : str, optional
        ``"native"`` or ``"ocr"``. Default ``"native"``.

    Returns
    -------
    Span
        An 8pt span at the column's value origin.
    """
    return Span(text, VALUE_X, y, VALUE_X + 30, y + 11.0, size=8.0, source=source)


def one_column(spans: list[Span]) -> Column:
    """Split spans and assert they all landed in a single column.

    Parameters
    ----------
    spans : list of Span
        Synthetic spans, all on the left half of the page.

    Returns
    -------
    Column
        The single column the spans form.
    """
    columns = split_columns(spans, PAGE_WIDTH, LAYOUT)
    assert len(columns) == 1
    return columns[0]


def test_columns_split_on_the_left_edge_not_the_centre() -> None:
    """A long left-column value may spill past the page midpoint.

    Returns
    -------
    None
    """
    spans = [
        label("TR", 100.0),
        Span("a very long value running past centre", 197.9, 100.0, 320.0, 111.0, size=8.0),
        label("FoV", 100.0, x=309.0),
    ]
    columns = {c.side: c for c in split_columns(spans, PAGE_WIDTH, LAYOUT)}
    assert len(columns["left"].spans) == 2
    assert len(columns["right"].spans) == 1


def test_value_boundary_tracks_a_column_of_short_values() -> None:
    """Every value is "Off", so the column's content is narrow.

    Returns
    -------
    None
    """
    spans: list[Span] = []
    for i in range(5):
        y = 100.0 + i * 11.0
        spans += [label(f"Parameter {i}", y), value("Off", y)]
    column = one_column(spans)
    assert column.x_min < column.value_x < VALUE_X
    rows = build_rows(column, LAYOUT)
    assert all(r.has_label and r.value == "Off" for r in rows)


def test_rows_group_by_vertical_overlap_not_by_top_edge() -> None:
    """A hyphen sits mid-line, so its box starts well below the letters.

    Returns
    -------
    None
    """
    row_y = 100.0
    spans = [
        Span("Contrast", TITLE_X, row_y, TITLE_X + 40, row_y + 13.8, size=10.0, source="ocr"),
        Span("-", TITLE_X + 44, row_y + 6.0, TITLE_X + 48, row_y + 8.0, size=2.5, source="ocr"),
        Span("Common", TITLE_X + 52, row_y, TITLE_X + 95, row_y + 13.8, size=10.0, source="ocr"),
    ]
    rows = build_rows(one_column(spans), LAYOUT)
    assert len(rows) == 1
    assert rows[0].label == "Contrast - Common"


def test_wrapped_label_is_merged_into_the_row_above() -> None:
    """A label continuation is set tighter than the table's row pitch.

    Returns
    -------
    None
    """
    spans = [
        title("Properties", 88.0),
        label("Start measurement without further", 110.0),
        value("Off", 110.0),
        label("preparation", 119.0),  # tighter than the 11pt pitch
        label("Wait for user to start", 130.0),
        value("On", 130.0),
    ]
    items = parse_column(one_column(spans), LAYOUT, 1, None)
    records = [i for i in items if isinstance(i, Record)]
    assert records[0].key == "Start measurement without further preparation"
    assert records[0].value == "Off"
    assert records[1].key == "Wait for user to start"


def test_wrapped_value_is_merged_into_the_row_above() -> None:
    """A value continuation rejoins the value, not the next label.

    Returns
    -------
    None
    """
    spans = [
        title("Routine", 88.0),
        label("Filter", 110.0),
        value("Prescan Normalize,", 110.0),
        value("Elliptical filter", 119.0),
        label("Coil elements", 130.0),
        value("HC1-7", 130.0),
    ]
    records = [
        i for i in parse_column(one_column(spans), LAYOUT, 1, None) if isinstance(i, Record)
    ]
    assert records[0].value == "Prescan Normalize, Elliptical filter"
    assert records[1].key == "Coil elements"


def test_section_titles_are_detected_without_font_weight() -> None:
    """The OCR path has no bold flag and no usable size, only geometry.

    Returns
    -------
    None
    """
    spans = [
        title("Properties", 88.0, bold=False, source="ocr"),
        label("Prio recon", 110.0, source="ocr"),
        value("Off", 110.0, source="ocr"),
        title("Routine", 130.0, bold=False, source="ocr"),
        label("TR", 152.0, source="ocr"),
        value("20.0 ms", 152.0, source="ocr"),
    ]
    items = parse_column(one_column(spans), LAYOUT, 1, None)
    assert [i.section for i in items if isinstance(i, SectionMarker)] == ["Properties", "Routine"]
    assert [(r.section, r.key) for r in items if isinstance(r, Record)] == [
        ("Properties", "Prio recon"),
        ("Routine", "TR"),
    ]


def test_a_column_without_titles_invents_none() -> None:
    """Nothing is outdented, so nothing may be promoted to a section.

    Returns
    -------
    None
    """
    spans: list[Span] = []
    for i in range(4):
        y = 100.0 + i * 11.0
        spans += [label(f"Key {i}", y, source="ocr"), value("Off", y, source="ocr")]
    spans.append(label("orphan", 100.0 + 4 * 11.0, source="ocr"))
    items = parse_column(one_column(spans), LAYOUT, 1, "Carried over")
    assert not [i for i in items if isinstance(i, SectionMarker)]
    assert all(i.section == "Carried over" for i in items)


def test_indented_sub_rows_are_marked() -> None:
    """A sub-row of a repeating group is recorded at indent level one.

    Returns
    -------
    None
    """
    spans = [
        title("Geometry - Common", 88.0),
        label("Slice group", 110.0),
        value("1", 110.0),
        label("Slices", 121.0, x=INDENT_X),
        value("5", 121.0),
    ]
    records = [
        i for i in parse_column(one_column(spans), LAYOUT, 1, None) if isinstance(i, Record)
    ]
    assert [r.indent for r in records] == [0, 1]


def test_row_pitch_is_the_median_not_the_mean() -> None:
    """Titles introduce deliberately larger gaps that must not skew it.

    Returns
    -------
    None
    """
    spans = [label("a", 100.0), label("b", 111.0), label("c", 122.0), label("d", 150.0)]
    assert row_pitch(build_rows(one_column(spans), LAYOUT)) == 11.0


def test_repeated_keys_are_suffixed_in_order() -> None:
    """Repeats within one section are kept, suffixed positionally.

    Returns
    -------
    None
    """
    records = [
        Record("Geometry", "Slice group", "1"),
        Record("Geometry", "Slices", "5"),
        Record("Geometry", "Slice group", "2"),
        Record("Geometry", "Slices", "1"),
    ]
    assert build_sections(records)["Geometry"] == {
        "Slice group": "1",
        "Slices": "5",
        "Slice group #2": "2",
        "Slices #2": "1",
    }


def test_an_empty_section_survives_assembly() -> None:
    """A section printed with no parameters is kept as an empty mapping.

    Returns
    -------
    None
    """
    items = [
        SectionMarker("Geometry - Navigator", 1, "left", 100.0),
        SectionMarker("Geometry - Common", 1, "left", 120.0),
        Record("Geometry - Common", "TR", "20.0 ms"),
    ]
    sections = build_sections(items)
    assert sections["Geometry - Navigator"] == {}
    assert sections["Geometry - Common"] == {"TR": "20.0 ms"}


def test_a_section_split_across_a_page_break_merges() -> None:
    """A title repeated at the top of the next page folds into one section.

    Returns
    -------
    None
    """
    items = [
        SectionMarker("Sequence - Part 1", 8, "left", 100.0),
        Record("Sequence - Part 1", "Sequence Name", "tfl_me", page=8),
        SectionMarker("Sequence - Part 1", 9, "left", 100.0),
        Record("Sequence - Part 1", "Bandwidth 1", "650 Hz/Px", page=9),
    ]
    sections = build_sections(items)
    assert list(sections) == ["Sequence - Part 1"]
    assert sections["Sequence - Part 1"] == {
        "Sequence Name": "tfl_me",
        "Bandwidth 1": "650 Hz/Px",
    }
