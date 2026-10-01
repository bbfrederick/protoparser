"""Build a parsed-PDF-shaped document out of an ``.exar1`` archive.

:mod:`siemens_protocol.exar.inspect` reads the low-level facts an archive
stores -- the sequence binary and the tree it came from, the ``Preview``
map, the slice geometry, a prescription link. This module is the adapter
that assembles those facts into the same document shape a parsed PDF
produces, through the same
:class:`~siemens_protocol.model.Scan`/:class:`~siemens_protocol.model.Protocol`
that :mod:`~siemens_protocol.pipeline` builds for a PDF, so the readers
written for one -- the listing, the sequence catalog, the policy checker,
the comparison -- run against an archive unchanged. :func:`card_view` lives
here rather than in :mod:`~siemens_protocol.exar.inspect` for the same
reason: showing an archive's parameters under the cards a printout would
show them on means interpreting a stored value through
:mod:`~siemens_protocol.analysis.generate.mappings`' business-rules table,
which is exactly the domain knowledge the low-level module does not carry.

An archive and a printout are still not the same document -- see the module
docstring of :mod:`siemens_protocol.exar.inspect` for why -- so this module
does not make an archive-backed :class:`~siemens_protocol.model.Scan` carry
the same content a PDF-backed one would. It only removes the duplicated
glue that used to build the equivalent document by hand: both producers now
call :func:`~siemens_protocol.analysis.sequences.identify` and
:func:`~siemens_protocol.analysis.flatten.flatten_sections` exactly once,
inside :meth:`~siemens_protocol.model.Scan.to_dict`, rather than each
carrying its own copy of that call.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any, Mapping

from .. import model, paths, timing
from ..exar import ascconv, inspect
from ..exar.archive import Archive, Program
from ..exar.archive import Protocol as ArchiveProtocol
from ..exar.archive import Step
from .generate import mappings
from .listing import format_duration
from .sequences import Catalog, default_catalog

#: Where a decoded parameter goes when the corpus records no card for it.
#: Every mapping currently has one, so this is a guard rather than a case.
UNCARDED_SECTION = "Parameters"


def acquisition_time(protocol: ArchiveProtocol) -> str:
    """A protocol's total scan time, formatted as the listing reads it.

    ``lTotalScanTimeSec`` is a *derived* field: the console recomputes it when
    a parameter it depends on moves, and a protocol this package has patched
    carries a stale one until a scanner reopens it. It agrees with the printed
    ``TA`` on every corpus scan that stores one.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.

    Returns
    -------
    str
        ``M:SS``, or empty when the assignment is absent -- which it is on the
        one-second setter scans, where the console omits it as a zero.
    """
    raw = ascconv.read_ascconv(protocol.xprotocol, "lTotalScanTimeSec")
    if raw is None:
        return ""
    try:
        return format_duration(float(raw))
    except ValueError:
        return ""


def header_of(step: Step) -> dict[str, str]:
    """The summary fields a scan would print in its header box.

    Named and spelled to match :attr:`~siemens_protocol.model.Scan.header` so
    the same readers work on both, which is what lets ``list`` and
    ``sequences`` accept an archive. ``sequence`` is the binary's bare name
    because that is what a catalog signature matches on; the prefix travels
    separately in ``sequence_owner``.

    Parameters
    ----------
    step : Step
        The step to describe. One that runs no protocol yields an empty
        header rather than raising.

    Returns
    -------
    dict of str to str
        Keys present only when the protocol carries them.
    """
    if not step.runs_a_protocol:
        return {}
    protocol = step.protocol
    stored = inspect.sequence_file(protocol)
    header: "OrderedDict[str, str]" = OrderedDict()
    binary = stored.rsplit("\\", 1)[-1] if stored else ascconv.sequence_of(protocol)
    if binary:
        header["sequence"] = binary
    owner = inspect.sequence_owner(stored)
    if owner:
        header["sequence_owner"] = owner
    if stored:
        header["sequence_file"] = stored
    stamp = ascconv.sequence_stamp(protocol)
    if stamp:
        header["sequence_build"] = stamp
    time = acquisition_time(protocol)
    if time:
        header["ta"] = time
    baseline = ascconv.baseline_string(protocol)
    if baseline:
        # Verbatim, as sequence_owner is: the file stating that this scan's
        # protocol awaits conversion, which a printout can never say -- a
        # protocol needing conversion cannot be printed.
        header["baseline"] = baseline
    return dict(header)


def card_view(
    protocol: ArchiveProtocol,
    printed: "OrderedDict[str, str] | None" = None,
) -> "OrderedDict[str, OrderedDict[str, str]]":
    """A protocol's mapped parameters, under the cards a printout prints them on.

    An archive stores no cards -- what a page splits into Routine, Contrast
    and Geometry is a property of the page -- but the parameters are the same
    parameters, and a person changing a protocol works from the card. So the
    mapping table is read backwards: each entry it can decode is emitted
    under its printed label, on every card :data:`~.generate.mappings.CARDS`
    records it printed on.

    A quantity really does appear on several cards, and the console keeps
    them in sync -- change ``TR`` on Routine and Contrast shows the new value
    -- so emitting it under each is faithful rather than duplication, and the
    flattened view folds the repeats back into one reading whose ``sections``
    name where it was found.

    This is a fraction of what a page prints: the table covers the parameters
    a controlled edit has pinned, not the several hundred a printout carries.
    The rest stays in the raw parameter section, which is what makes the
    archive comparison complete even where it cannot be eloquent.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.
    printed : OrderedDict or None, optional
        Unit-bearing rendering from :func:`legible_preview`, which uses
        authoritative ASCCONV readings where mapped. Cosmetic spelling and
        characterized percentage rounding are retained when they agree.

    Returns
    -------
    OrderedDict
        ``{card title: {printed label: displayed value}}``, cards in the
        order the mapping table first reaches them.
    """
    cards: "OrderedDict[str, OrderedDict[str, str]]" = OrderedDict()
    for mapping, shown in _readings(protocol, printed):
        for card in mappings.cards_for(mapping.label) or (UNCARDED_SECTION,):
            cards.setdefault(card, OrderedDict())[mapping.label] = shown
    return cards


def legible_preview(protocol: ArchiveProtocol) -> "OrderedDict[str, str]":
    """The ``Preview`` rendering, with each coded choice spelled as printed.

    ``Preview`` is keyed by the label a printout uses, but for a choice it
    often stores the console's internal code rather than the text:
    ``Gradient Mode`` holds ``107`` where the card prints ``Fast``,
    ``AutoAlign`` ``6148`` where it prints ``Head > Brain``. Nobody at a
    console sees or picks the number. Where a mapping decodes the choice or
    the checkbox, its text replaces the code. Mapped numeric controls use
    ASCCONV as the source of truth; Preview spelling is retained only when it
    agrees, allowing the characterized phase-FOV display rounding. Unmapped
    quantities retain their Preview reading without claiming an ASCCONV mapping.

    Doing it here rather than in :func:`card_view` keeps the ``Preview``
    section and the cards agreeing: the flattened view folds a label from
    both, and two spellings of one value there read as a conflict.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.

    Returns
    -------
    OrderedDict
        Label to displayed value, in :func:`~.exar.inspect.printed_view`'s
        order.
    """
    shown = inspect.printed_view(protocol)
    for mapping in mappings.MAPPINGS:
        if mapping.label not in shown:
            continue
        decoded = mappings.display(mapping, protocol)
        if decoded is None:
            continue
        if mapping.choices or mapping.bit is not None:
            shown[mapping.label] = decoded
            continue
        entry = protocol.preview.get(mapping.preview_path or "")
        if entry is None:
            continue
        try:
            displayed, stored = float(entry.value), float(decoded)
            tolerance = 0.050001 if mapping.label == "FOV Phase" else 1e-6
            if math.isfinite(displayed) and math.isclose(
                displayed, stored, rel_tol=1e-6, abs_tol=tolerance
            ):
                continue
        except (TypeError, ValueError):
            pass
        shown[mapping.label] = f"{decoded} {entry.unit or ''}".strip()
    return shown


def _readings(
    protocol: ArchiveProtocol, printed: "OrderedDict[str, str] | None"
) -> "list[tuple[mappings.Mapping, str]]":
    """Every mapping a card can show for this protocol, with what it shows.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.
    printed : OrderedDict or None
        Unit-bearing authoritative rendering from :func:`legible_preview`.

    Returns
    -------
    list of tuple
        ``(mapping, displayed value)`` in table order. A label several
        mappings share appears once per mapping; the card keeps the last.
    """
    preview = printed or {}
    found = []
    for mapping in mappings.MAPPINGS:
        shown = preview.get(mapping.label, mappings.display(mapping, protocol))
        if shown is not None:
            found.append((mapping, shown))
    return found


def unlabelled_ascconv(
    protocol: ArchiveProtocol, printed: "OrderedDict[str, str] | None" = None
) -> "OrderedDict[str, str]":
    """The ASCCONV assignments no printed label on the cards already says.

    ASCCONV is the protocol's ground truth, and the printout is the version
    an operator acts on: its labels are the quantities that can be changed on
    the console. So where a mapping gives an assignment a label, the card
    shows it and this leaves it out -- otherwise one edit to ``TE`` reads as
    two differences, and the second, ``alTE[0]: 1690 | 1790``, is in terms
    nobody at the scanner can use.

    What remains is everything the table has not pinned, which is most of the
    block and is what keeps a comparison complete. A packed flags word stays
    as the bits no label claims, under ``<key> (unlabelled bits)``: the
    driver's answer-key run differed from the console in exactly such a bit,
    and dropping the whole word would have hidden it.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.
    printed : OrderedDict or None, optional
        The console's ``Preview`` rendering, as :func:`card_view` takes it.

    Returns
    -------
    OrderedDict
        Key to literal, in file order, as :func:`~.exar.inspect.ascconv_table`
        spells them.
    """
    winners: dict[str, mappings.Mapping] = {}
    for mapping, _shown in _readings(protocol, printed):
        # Only a mapping that applies here may claim: the Preview can show a
        # label for a sequence whose mapping it is not, and a sWipMemBlock
        # index then means some other parameter. Among those that do, the
        # last wins, as it does on the card.
        if mappings.applies_to(mapping, protocol):
            winners[mapping.label] = mapping
    whole, bits = mappings.accounted_for(protocol, list(winners.values()))
    out: "OrderedDict[str, str]" = OrderedDict()
    for key, literal in inspect.ascconv_table(protocol.xprotocol).items():
        if key in whole:
            continue
        rest = mappings.unlabelled_bits(literal, bits[key]) if key in bits else None
        if key in bits and rest is not None:
            if rest:
                out[f"{key} (unlabelled bits)"] = ", ".join(str(bit) for bit in rest)
            continue
        out[key] = literal
    return out


def scan_from_step(
    step: Step,
    index: int,
    folder: str = "",
    parameters: bool = False,
) -> "model.Scan":
    """A step, as a :class:`~siemens_protocol.model.Scan`.

    This is the per-scan half of the adapter described in the module
    docstring. ``stored_sections`` holds one section, ``Preview``, because
    the archive has no cards: what a printout splits into Routine, Contrast
    and Geometry is a property of the page, not of the protocol. That is why
    a scan read from an archive never contributes a Special card to
    :func:`~siemens_protocol.analysis.sequences.special_keys`, and so is
    identified by its binary and its stated owner alone.

    Parameters
    ----------
    step : Step
        The step to describe.
    index : int
        Its zero-based position in the running order.
    folder : str, optional
        The program's folder path, as
        :meth:`~siemens_protocol.exar.archive.Archive.path_of` builds it.
        Default empty, which leaves ``path`` empty rather than inventing one.
    parameters : bool, optional
        Whether to carry the mapped parameters under their cards, followed
        by the ASCCONV assignments no card label already says -- see
        :func:`unlabelled_ascconv`. Default ``False``, which is what the
        ``archive`` document wants --
        it emits the parameter tree nested under ``ascconv`` instead, and
        carrying it twice would double a document that is already the bulk
        of the file.

    Returns
    -------
    Scan
        Archive-backed (``source="exar1"``); call
        :meth:`~siemens_protocol.model.Scan.to_dict` for index, name, path,
        header, provenance and sections.
    """
    header = header_of(step)
    sections: "OrderedDict[str, OrderedDict[str, str]]" = OrderedDict()
    if step.runs_a_protocol:
        preview = legible_preview(step.protocol)
        sections["Preview"] = preview
        if parameters:
            # The console's Preview is a ~40-parameter summary, so a reader
            # given that alone sees about two percent of what the protocol
            # holds -- and sees it without being told, which is how a
            # comparison of two archives came to report differences confined
            # to Preview while 82 ASCCONV assignments differed beneath it.
            # One section rather than one per struct: the archive has no
            # cards, the key path already carries the structure, and a
            # section named after a struct would be read as a card by
            # anything matching on section titles.
            # The cards first, because they speak in the labels a console
            # shows and are what someone changing a protocol reads. The raw
            # block still follows, less what the cards already say: the table
            # covers a fraction of what a protocol holds, and the remainder
            # is where the differences a Preview-only view was missing live.
            sections.update(card_view(step.protocol, preview))
            sections[inspect.ASCCONV_SECTION] = unlabelled_ascconv(step.protocol, preview)
    return model.Scan(
        index=index,
        name=step.name,
        path=f"{folder}/{step.name}" if folder else "",
        header=header,
        source="exar1",
        stored_sections=sections,
    )


def step_document(
    step: Step,
    index: int,
    catalog: Catalog,
    *,
    ascconv: bool = True,
    folder: str = "",
) -> dict[str, Any]:
    """One step of a program, described in the archive's own terms.

    Parameters
    ----------
    step : Step
        The step to describe.
    index : int
        Its zero-based position in the running order, which comes from the
        ``FirstStepId``/``LinksFrom`` chain rather than from the ``Children``
        blob -- the blob holds the same steps permuted, which yields every
        value correct and every scan under the wrong name.
    catalog : Catalog
        Signatures to identify the sequence against.
    ascconv : bool, optional
        Whether to include the nested ASCCONV tree, which is by far the
        largest part of the document -- 514 to 2020 assignments per scan.
        Default ``True``.
    folder : str, optional
        The program's folder path. Default empty.

    Returns
    -------
    dict
        The step's identity, what it runs, and what it stores: the Preview
        summary, the mapped parameters under their printed cards, the slice
        geometry, and the ASCCONV tree.
    """
    out: dict[str, Any] = {
        "index": index,
        "name": step.name,
        "kind": step.instance.kind,
        "acquires": step.acquires,
        "is_pause": step.is_pause,
        "runs_a_protocol": step.runs_a_protocol,
    }
    if not step.runs_a_protocol:
        return out
    protocol = step.protocol
    out["header"] = header_of(step)
    out["path"] = f"{folder}/{step.name}" if folder else ""
    out["provenance"] = scan_from_step(step, index, folder).to_dict(
        include_flat=False, catalog=catalog
    )["provenance"]
    out["preview"] = inspect.preview_of(protocol)
    # The printed labels an operator can act on, ahead of the raw tree that
    # is their ground truth. The tree stays whole: it is the document's
    # record of the file, and a query against it must find every assignment.
    cards = card_view(protocol, legible_preview(protocol))
    if cards:
        out["cards"] = cards
    geometry = inspect.geometry_of(protocol)
    if geometry is not None:
        out["geometry"] = geometry
    if ascconv:
        out["ascconv"] = inspect.nest(inspect.ascconv_table(protocol.xprotocol))
    return out


def program_document(
    archive: Archive,
    program: Program,
    index: int,
    catalog: Catalog,
    *,
    ascconv: bool = True,
    parents: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One protocol of an archive: its steps in running order, and its links.

    Parameters
    ----------
    archive : Archive
        The archive the program belongs to.
    program : Program
        The program to describe.
    index : int
        Its position among the archive's programs.
    catalog : Catalog
        Signatures to identify sequences against.
    ascconv : bool, optional
        Whether each step includes its ASCCONV tree. Default ``True``.
    parents : dict of str to str or None, optional
        A prebuilt :attr:`~siemens_protocol.exar.archive.Archive.directory_parents`.
        Pass one when describing many programs: building it parses the root
        document, which is megabytes of JSON on a whole-scanner export.
    names : dict of str to str or None, optional
        A prebuilt step-name index, for resolving link endpoints. Building it
        walks every program in the archive, so computing it per program is
        quadratic -- on a whole-scanner export with 499 programs that is the
        difference between seconds and not finishing.

    Returns
    -------
    dict
        Name, folder path, counts, the steps, and every relation the program
        carries.
        ``relation_counts`` breaks those down by kind, because a relation
        count is not a link count: 1248 of the corpus's 1440 relations have
        an empty ``Kind`` and carry no payload, and they arrive duplicated up
        to eleven deep between the same two steps. Reporting the total as
        "links" made a 19-link protocol look like a 117-link one.
    """
    if names is None:
        names = inspect.step_names(archive)
    # The steps sit *under* the program, so their folder is its whole path --
    # the printout agrees, ending \...\Frederick\Potpourri_P1\localizer.
    folder = paths.join(archive.path_of(program.instance, parents))
    steps = [
        step_document(step, position, catalog, ascconv=ascconv, folder=folder)
        for position, step in enumerate(program.steps)
    ]
    counts: dict[str, int] = {}
    for link in program.links:
        kind = link.kind or ""
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "index": index,
        "name": program.name,
        "path": folder,
        "step_count": len(program.steps),
        "scan_count": sum(1 for step in program.steps if step.runs_a_protocol),
        "pause_count": sum(1 for step in program.steps if step.is_pause),
        "relation_counts": counts,
        "links": [inspect.link_of(link, names) for link in program.links],
        "steps": steps,
    }


def describe(
    archive: Archive,
    source: str,
    *,
    ascconv: bool = True,
    catalog: Catalog | None = None,
) -> dict[str, Any]:
    """Turn a whole archive into a JSON-able document.

    Parameters
    ----------
    archive : Archive
        The archive to describe.
    source : str
        Path to report as the document's origin.
    ascconv : bool, optional
        Whether each step includes its nested ASCCONV tree. Default ``True``.
    catalog : Catalog or None, optional
        Signatures to identify sequences against. The shipped catalog is
        loaded when omitted.

    Returns
    -------
    dict
        The archive, its programs and their steps. ``programs`` is a list
        because an archive may hold several: an export taken at the exam or
        region level rather than at one protocol, which is what a scanner
        backup is.
    """
    catalog = catalog or default_catalog()
    major = archive.major_version
    release = inspect.RELEASES.get(major[:4])
    warnings: list[str] = []
    if release is None:
        warnings.append(
            f"baseline names release {major!r}, which this build has no profile for; "
            "the parameters are still read, only the release label is unknown"
        )
    programs = archive.programs
    out: dict[str, Any] = {
        "source_file": source,
        "format": "exar1",
        "software_version": release,
        "baseline": archive.baseline,
        "major_version": major,
        "program_count": len(programs),
    }
    if warnings:
        out["warnings"] = warnings
    parents = archive.directory_parents
    found_directories = inspect.directories(archive, parents)
    if found_directories:
        out["directories"] = found_directories
    names = {step.instance.object_id: step.name for p in programs for step in p.steps}
    out["programs"] = [
        program_document(
            archive, program, index, catalog, ascconv=ascconv, parents=parents, names=names
        )
        for index, program in enumerate(programs)
    ]
    return out


def protocol_from_archive(archive: Archive, program: Program, source: str) -> "model.Protocol":
    """One program of an archive, as a :class:`~siemens_protocol.model.Protocol`.

    Only the steps that run a protocol become scans, because that is what a
    printout prints: a pause step is an instruction an operator put in the
    running order -- "Pause for saliva collection" -- and the PDF does not
    list it as a scan. Pauses are kept on the side, each placed by the scan
    it precedes, so a listing can show them without numbering them.

    Parameters
    ----------
    archive : Archive
        The archive the program belongs to.
    program : Program
        The program to render.
    source : str
        Path to report as the document's origin.

    Returns
    -------
    Protocol
        Archive-backed; call :meth:`~siemens_protocol.model.Protocol.to_dict`
        for the document the listing, the sequence report, the policy
        checker and the comparison all accept.
    """
    folder = paths.join(archive.path_of(program.instance))
    protocol = model.Protocol(
        source_file=source,
        software_version=inspect.RELEASES.get(archive.major_version[:4]),
        detection={"method": "baseline", "confidence": "high"},
        scanner=archive.baseline,
        program=program.name,
        execution_metadata_available=True,
    )
    positions: dict[str, int] = {}
    for step in program.steps:
        if step.is_pause:
            protocol.pauses.append(model.Pause(before=len(protocol.scans), name=step.name))
        if not step.runs_a_protocol:
            continue
        positions.setdefault(step.instance.object_id, len(protocol.scans))
        protocol.scans.append(scan_from_step(step, len(protocol.scans), folder, parameters=True))
    protocol.links = scan_links(program, positions)
    return protocol


def scan_links(program: Program, positions: Mapping[str, int]) -> "list[model.ScanLink]":
    """A program's copy-parameter links, addressed by scan index.

    The archive names a link's ends by step ``ObjectId``; a protocol document
    names scans by index. Only copy references are kept -- the payload-less
    relations some programs carry are not links -- and a link whose end is
    not a scan (a pause step, say) is dropped rather than pointed at the
    wrong one. No corpus link has such an end.

    Parameters
    ----------
    program : Program
        The program whose links to read.
    positions : mapping of str to int
        Each scan-running step's ``ObjectId``, mapped to its scan index.

    Returns
    -------
    list of ScanLink
        The links, in the order the archive stores them.
    """
    links: list[model.ScanLink] = []
    for link in program.copy_references:
        source, target = positions.get(link.source), positions.get(link.target)
        if source is None or target is None:
            continue
        links.append(
            model.ScanLink(
                source=source,
                target=target,
                group=link.group or "",
                copies_phase_encoding_direction=link.copies_phase_encoding_direction,
                copies_steps=link.copies_steps,
                ignores_last_step=link.ignores_last_step,
                ignores_measurements=link.ignores_measurements,
                extra=dict(link.extra),
            )
        )
    return links


@timing.timed_function(timing.ARCHIVE_VIEW)
def as_protocol(
    archive: Archive,
    program: Program,
    source: str,
    *,
    catalog: Catalog | None = None,
    include_flat: bool = True,
) -> dict[str, Any]:
    """One program in the shape a parsed PDF has.

    Parameters
    ----------
    archive : Archive
        The archive the program belongs to.
    program : Program
        The program to render.
    source : str
        Path to report as the document's origin.
    catalog : Catalog or None, optional
        Signatures to identify sequences against. The shipped catalog is
        loaded when omitted.
    include_flat : bool, optional
        Whether each scan carries the flattened per-key view. Default
        ``True``.

    Returns
    -------
    dict
        A document the listing, the sequence report, the policy checker and
        the comparison all accept.
    """
    return protocol_from_archive(archive, program, source).to_dict(
        include_flat=include_flat, catalog=catalog
    )
