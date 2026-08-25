"""Layout reconstruction: columns, then rows, then sections.

A page's spans are split into its two independent key/value tables, each
table is clustered into rows and label/value cells, and the rows are read
into an ordered stream of records and section markers.
"""

from __future__ import annotations

from .columns import Column, split_columns, value_origin
from .rows import Row, build_rows, row_pitch
from .sections import UNSECTIONED, Record, SectionMarker, current_section, parse_column

__all__ = [
    "Column",
    "split_columns",
    "value_origin",
    "Row",
    "build_rows",
    "row_pitch",
    "Record",
    "SectionMarker",
    "UNSECTIONED",
    "parse_column",
    "current_section",
]
