"""Per-span geometry dumps, for tuning a profile against a new release.

The numbers that matter when a new software version lands are the column
split, the label/value boundary and the row pitch. This dump prints all three
next to the spans they were derived from, so a threshold can be checked
against the file rather than guessed at.
"""

from __future__ import annotations

import dataclasses
import json

from .layout.columns import split_columns
from .layout.rows import build_rows, row_pitch
from .pipeline import ParseResult
from .profiles import REGISTRY
from .split import body_spans, find_header_box


def build_debug(result: ParseResult) -> dict:
    """Assemble the geometry dump for a parsed document.

    Parameters
    ----------
    result : ParseResult
        A parse result carrying its pages.

    Returns
    -------
    dict
        Source file, profile name, the profile's layout thresholds, and per
        page the header box, the columns with their derived boundaries and
        rows, and every span.
    """
    profile = REGISTRY.get(result.protocol.software_version)
    layout = profile.layout
    pages: list[dict] = []

    for page in result.pages:
        header = find_header_box(page, layout, profile)
        spans = body_spans(page, layout, header)
        columns = []
        for column in split_columns(spans, page.width, layout):
            rows = build_rows(column, layout)
            columns.append(
                {
                    "side": column.side,
                    "x_min": round(column.x_min, 2),
                    "x_max": round(column.x_max, 2),
                    "value_x": round(column.value_x, 2),
                    "row_pitch": round(row_pitch(rows), 2),
                    "rows": [r.to_dict() for r in rows],
                }
            )
        pages.append(
            {
                "page": page.label,
                "source": page.source,
                "printable_ratio": round(page.printable_ratio, 3),
                "width": round(page.width, 2),
                "height": round(page.height, 2),
                "header_box": header.to_dict() if header else None,
                "columns": columns,
                "spans": [s.to_dict() for s in page.spans],
            }
        )

    return {
        "source_file": result.protocol.source_file,
        "profile": profile.name,
        "layout": dataclasses.asdict(layout),
        "pages": pages,
    }


def write_debug(path: str, result: ParseResult) -> None:
    """Write the geometry dump to a JSON file.

    Parameters
    ----------
    path : str
        Destination path.
    result : ParseResult
        A parse result carrying its pages.

    Returns
    -------
    None
    """
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(build_debug(result), handle, indent=2, ensure_ascii=False)
