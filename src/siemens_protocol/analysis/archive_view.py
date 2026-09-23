"""Build a parsed-PDF-shaped document out of an ``.exar1`` archive.

:mod:`siemens_protocol.exar.inspect` reads the low-level facts an archive
stores -- the sequence binary and the tree it came from, the ``Preview``
map, the mapped Special-card view, the slice geometry, a prescription link.
This module is the adapter that assembles those facts into the same
document shape a parsed PDF produces, through the same
:class:`~siemens_protocol.model.Scan`/:class:`~siemens_protocol.model.Protocol`
that :mod:`~siemens_protocol.pipeline` builds for a PDF, so the readers
written for one -- the listing, the sequence catalog, the policy checker,
the comparison -- run against an archive unchanged.

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

from collections import OrderedDict
from typing import Any

from .. import model
from ..exar import inspect, patch
from ..exar.archive import Archive, Program
from ..exar.archive import Protocol as ArchiveProtocol
from ..exar.archive import Step
from .listing import format_duration
from .sequences import Catalog, default_catalog


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
    raw = patch.read_ascconv(protocol.xprotocol, "lTotalScanTimeSec")
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
    binary = stored.rsplit("\\", 1)[-1] if stored else patch.sequence_of(protocol)
    if binary:
        header["sequence"] = binary
    owner = inspect.sequence_owner(stored)
    if owner:
        header["sequence_owner"] = owner
    if stored:
        header["sequence_file"] = stored
    stamp = patch.sequence_stamp(protocol)
    if stamp:
        header["sequence_build"] = stamp
    time = acquisition_time(protocol)
    if time:
        header["ta"] = time
    return dict(header)


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
        Whether to carry the whole ASCCONV block as a second section.
        Default ``False``, which is what the ``archive`` document wants --
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
        preview = inspect.printed_view(step.protocol)
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
            # block still follows: the table covers a fraction of what a
            # protocol holds, and the remainder is where the differences a
            # Preview-only view was missing actually live.
            sections.update(inspect.card_view(step.protocol, preview))
            sections[inspect.ASCCONV_SECTION] = inspect.ascconv_table(step.protocol.xprotocol)
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
        The step's identity, what it runs, and what it stores.
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
    folder = "/".join(archive.path_of(program.instance, parents))
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
    list it as a scan.

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
    folder = "/".join(archive.path_of(program.instance))
    protocol = model.Protocol(
        source_file=source,
        software_version=inspect.RELEASES.get(archive.major_version[:4]),
        detection={"method": "baseline", "confidence": "high"},
        scanner=archive.baseline,
        program=program.name,
    )
    for step in program.steps:
        if not step.runs_a_protocol:
            continue
        protocol.scans.append(scan_from_step(step, len(protocol.scans), folder, parameters=True))
    return protocol


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
