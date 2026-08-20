"""Protocol and Scan objects, and their JSON serialization."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Sequence

from .flatten import flatten_sections
from .layout.sections import Record, SectionMarker

#: Key used for a value whose label the layout could not recover.
UNLABELED = "(unlabeled)"


def build_sections(
    items: Sequence[Record | SectionMarker],
) -> OrderedDict[str, OrderedDict[str, str]]:
    """Fold a record stream into ordered ``{section: {key: value}}``.

    Keys legitimately repeat inside one section: a scan with three slice
    groups prints ``Slice Group`` three times, each followed by its own
    indented ``Slices`` and ``Distance Factor``. Dropping the repeats would
    lose real readings, so the second and later occurrences are suffixed
    ``#2``, ``#3`` and so on, positionally and deterministically.

    A section title repeated at the top of a new column or page folds back
    into the same section rather than starting another.

    Parameters
    ----------
    items : sequence
        Records and section markers in reading order.

    Returns
    -------
    OrderedDict
        Sections in first-seen order, each an ordered mapping of key to value.
        Sections with no parameters are kept as empty mappings.
    """
    sections: OrderedDict[str, OrderedDict[str, str]] = OrderedDict()
    for item in items:
        if isinstance(item, SectionMarker):
            sections.setdefault(item.section, OrderedDict())
            continue
        if not isinstance(item, Record):
            continue
        entries = sections.setdefault(item.section, OrderedDict())
        key = item.key.strip() or UNLABELED
        if key in entries:
            n = 2
            while f"{key} #{n}" in entries:
                n += 1
            key = f"{key} #{n}"
        entries[key] = item.value
    return sections


@dataclass
class Scan:
    """One acquisition: its header banner, its sections, and its pages.

    Attributes
    ----------
    index : int
        Zero-based position of the scan within the protocol.
    name : str
        Protocol name, the last component of the header path.
    path : str
        The full UNC-style path from the header box.
    header : dict of str to str
        Parsed header summary fields, plus any recovered from parameters.
    header_summary : str
        The raw ``TA: ...`` line, kept for debugging a new release.
    records : list
        Records and section markers, in reading order.
    pages : list of int
        One-based page numbers this scan spans.
    """

    index: int
    name: str = ""
    path: str = ""
    header: dict[str, str] = field(default_factory=dict)
    header_summary: str = ""
    records: list[Record | SectionMarker] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)

    def sections(self) -> OrderedDict[str, OrderedDict[str, str]]:
        """The scan's parameters, grouped by the section they were printed in.

        Returns
        -------
        OrderedDict
            Sections in printed order; see :func:`build_sections`.
        """
        return build_sections(self.records)

    def to_dict(self, include_flat: bool = True) -> dict:
        """Serialize the scan.

        Parameters
        ----------
        include_flat : bool, optional
            Whether to include the flattened per-key view. Default ``True``.

        Returns
        -------
        dict
            Index, name, path, header, sections, optional flat view and pages.
        """
        sections = self.sections()
        out: OrderedDict[str, object] = OrderedDict()
        out["index"] = self.index
        out["name"] = self.name
        out["path"] = self.path
        out["header"] = OrderedDict(self.header)
        out["sections"] = sections
        if include_flat:
            out["flat"] = flatten_sections(sections)
        out["pages"] = self.pages
        return out


@dataclass
class Protocol:
    """A whole parsed protocol export.

    Attributes
    ----------
    source_file : str
        Path of the PDF this was parsed from.
    software_version : str or None
        The detected or forced version profile name.
    detection : dict of str to str
        How the version was decided, and with what confidence.
    scanner : str
        The running page header, naming the scanner and software build.
    scans : list of Scan
        The protocol's scans, in printed order.
    page_count : int
        Number of pages in the PDF.
    front_matter_pages : list of int
        Pages before the first scan header -- the table of contents.
    ocr_pages : list of int
        Pages whose text came from OCR; treat their values as approximate.
    warnings : list of str
        Anything the caller should know before trusting the result.
    """

    source_file: str
    software_version: str | None = None
    detection: dict[str, str] = field(default_factory=dict)
    scanner: str = ""
    scans: list[Scan] = field(default_factory=list)
    page_count: int = 0
    front_matter_pages: list[int] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, include_flat: bool = True) -> dict:
        """Serialize the protocol.

        Parameters
        ----------
        include_flat : bool, optional
            Whether each scan includes the flattened view. Default ``True``.

        Returns
        -------
        dict
            The full document, with optional keys omitted when empty.
        """
        out: OrderedDict[str, object] = OrderedDict()
        out["source_file"] = self.source_file
        out["software_version"] = self.software_version
        out["detection"] = OrderedDict(self.detection)
        out["scanner"] = self.scanner
        out["page_count"] = self.page_count
        if self.front_matter_pages:
            out["front_matter_pages"] = self.front_matter_pages
        if self.ocr_pages:
            out["ocr_pages"] = self.ocr_pages
        if self.warnings:
            out["warnings"] = self.warnings
        out["scans"] = [s.to_dict(include_flat) for s in self.scans]
        return out

    def to_json(self, include_flat: bool = True, indent: int = 2) -> str:
        """Serialize the protocol as JSON.

        Parameters
        ----------
        include_flat : bool, optional
            Whether each scan includes the flattened view. Default ``True``.
        indent : int, optional
            JSON indentation. Default 2.

        Returns
        -------
        str
            The serialized document, with non-ASCII characters preserved.
        """
        return json.dumps(self.to_dict(include_flat), indent=indent, ensure_ascii=False)
