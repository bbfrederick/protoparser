"""Protocol and Scan objects, and their JSON serialization."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Sequence

from .analysis.flatten import flatten_sections
from .analysis.sequences import Catalog, default_catalog, identify
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
        ``sequence`` here is what identifies a third-party sequence on
        VB17A, which prints no Special card.
    header_summary : str
        The raw ``TA: ...`` line, kept for debugging a new release.
    records : list
        Records and section markers, in reading order. PDF-only: set when
        ``source`` is ``"pdf"``, left empty otherwise.
    pages : list of int
        One-based page numbers this scan spans. PDF-only, for the same
        reason as ``records`` -- an archive-backed scan has no pages, and
        ``to_dict`` omits the key entirely rather than reporting an empty one.
    source : str, optional
        Where this scan came from: ``"pdf"`` (default) or ``"exar1"``. Decides
        which of ``records``/``stored_sections`` :meth:`sections` reads, and
        whether ``to_dict`` reports ``pages``.
    stored_sections : OrderedDict or None, optional
        The archive-only equivalent of ``records`` -- sections already
        assembled by the archive reader, rather than a record stream to fold.
        Mutually exclusive with ``records``: set this when ``source`` is
        ``"exar1"``, leave it ``None`` for a PDF-backed scan.
    """

    index: int
    name: str = ""
    path: str = ""
    header: dict[str, str] = field(default_factory=dict)
    header_summary: str = ""
    records: list[Record | SectionMarker] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    source: str = "pdf"
    stored_sections: OrderedDict[str, OrderedDict[str, str]] | None = None

    def sections(self) -> OrderedDict[str, OrderedDict[str, str]]:
        """The scan's parameters, grouped by the section they were printed in.

        Returns
        -------
        OrderedDict
            ``stored_sections`` verbatim when set (an archive-backed scan);
            otherwise :func:`build_sections` folded over ``records`` (a
            PDF-backed scan), in printed order either way.
        """
        if self.stored_sections is not None:
            return self.stored_sections
        return build_sections(self.records)

    def to_dict(self, include_flat: bool = True, catalog: Catalog | None = None) -> dict:
        """Serialize the scan.

        Parameters
        ----------
        include_flat : bool, optional
            Whether to include the flattened per-key view. Default ``True``.
        catalog : Catalog or None, optional
            Signatures to identify the sequence against. The shipped catalog
            is used when omitted -- the only case any current caller needs,
            since a caller wanting a different catalog re-identifies from the
            serialized document afterwards (see
            :func:`~siemens_protocol.analysis.sequences.identify_protocol`)
            rather than relying on what is baked in here.

        Returns
        -------
        dict
            Index, name, path, header, provenance, sections, optional flat
            view, and ``pages`` when ``source`` is ``"pdf"``.
        """
        sections = self.sections()
        out: OrderedDict[str, object] = OrderedDict()
        out["index"] = self.index
        out["name"] = self.name
        out["path"] = self.path
        out["header"] = OrderedDict(self.header)
        # Recorded rather than left to the caller: whether a scan runs a
        # third-party sequence is what decides how much of a release
        # migration has to be rebuilt by hand, and anything reading this
        # JSON needs it as much as the report does. Recomputed on every
        # serialization, so a catalog correction reaches old parses too --
        # see sequences.identify. The same call site now serves both a
        # PDF-backed and an archive-backed scan; only the sections fed to it
        # differ, per source.
        out["provenance"] = identify(
            {
                "index": self.index,
                "name": self.name,
                "path": self.path,
                "header": self.header,
                "sections": sections,
            },
            catalog or default_catalog(),
        ).to_dict()
        out["sections"] = sections
        if include_flat:
            out["flat"] = flatten_sections(sections)
        if self.source == "pdf":
            out["pages"] = self.pages
        return out


@dataclass
class ScanLink:
    """One copy-parameter link between two scans of a protocol.

    The console can slave a scan's slices, centre, table position and so on
    to another scan's. The printout does not record this at all, so only an
    archive-backed protocol carries any; a parsed PDF has none.

    Attributes
    ----------
    source : int
        Index of the scan the parameters are copied *from*.
    target : int
        Index of the scan they are copied *to*.
    group : str
        What is copied, as the archive names it -- ``Slices``,
        ``CenterOfSlicesAndSaturationRegions`` and so on. Empty when the link
        names no group.
    """

    source: int
    target: int
    group: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize the link.

        Returns
        -------
        dict
            ``source``, ``target`` and ``group``.
        """
        return {"source": self.source, "target": self.target, "group": self.group}


@dataclass
class Pause:
    """A pause step: an operator instruction in the running order, not a scan.

    "Pause for saliva collection", "Count down with RA to start of scan" --
    an archive keeps these in the running order beside the scans, and a
    printout does not print them at all. They carry no protocol and so no
    scan index; ``before`` places one among the scans instead.

    Attributes
    ----------
    before : int
        Index of the scan the pause precedes. Equal to the number of scans
        when the pause comes after the last one.
    name : str
        The pause step's displayed text.
    """

    before: int
    name: str

    def to_dict(self) -> dict[str, object]:
        """Serialize the pause.

        Returns
        -------
        dict
            ``before`` and ``name``.
        """
        return {"before": self.before, "name": self.name}


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
    program : str, optional
        The protocol's name as an archive's ``EdfProgram`` node states it.
        Empty for a PDF-backed protocol, which has no such node to read;
        ``to_dict`` omits the key rather than reporting an empty one.
    scans : list of Scan
        The protocol's scans, in printed order.
    links : list of ScanLink
        Copy-parameter links between scans, in the order the archive stores
        them. Always empty for a PDF-backed protocol, whose printout does not
        record links; ``to_dict`` omits the key when empty.
    pauses : list of Pause
        Pause steps, in running order. Always empty for a PDF-backed
        protocol; ``to_dict`` omits the key when empty.
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
    program: str = ""
    scans: list[Scan] = field(default_factory=list)
    links: list[ScanLink] = field(default_factory=list)
    pauses: list[Pause] = field(default_factory=list)
    page_count: int = 0
    front_matter_pages: list[int] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, include_flat: bool = True, catalog: Catalog | None = None) -> dict:
        """Serialize the protocol.

        Parameters
        ----------
        include_flat : bool, optional
            Whether each scan includes the flattened view. Default ``True``.
        catalog : Catalog or None, optional
            Signatures to identify each scan's sequence against, passed to
            :meth:`Scan.to_dict`. The shipped catalog is used when omitted.

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
        if self.program:
            out["program"] = self.program
        out["page_count"] = self.page_count
        if self.front_matter_pages:
            out["front_matter_pages"] = self.front_matter_pages
        if self.ocr_pages:
            out["ocr_pages"] = self.ocr_pages
        if self.warnings:
            out["warnings"] = self.warnings
        out["scans"] = [s.to_dict(include_flat, catalog) for s in self.scans]
        if self.links:
            out["links"] = [link.to_dict() for link in self.links]
        if self.pauses:
            out["pauses"] = [pause.to_dict() for pause in self.pauses]
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
