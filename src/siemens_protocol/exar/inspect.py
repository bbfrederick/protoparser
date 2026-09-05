"""Read an ``.exar1`` archive into a hierarchical, queryable document.

The rest of this package exists to *change* an archive. This module exists to
*look* at one: it turns the tree :mod:`.archive` decodes into plain JSON-able
dictionaries, so an archive can be browsed and queried the way a parsed PDF
already can.

The two are not the same document, and pretending otherwise would misdescribe
both. A printout is what the console chose to display; an archive is what the
console stored. Three differences matter to anyone reading the output:

* The archive's printed-label view is ``Preview`` alone -- roughly forty
  console-summary parameters per scan, against the several hundred a PDF page
  prints. The complete parameter set is the ASCCONV block, which the PDF does
  not carry at all, and which is emitted here as ``ascconv``.
* The archive states which tree a sequence binary came from
  (``%SiemensSeq%`` or ``%CustomerSeq%``), which no Numaris/X printout does.
  That is the archive saying who supplied the sequence, so it identifies
  third-party sequences that a PDF of the same protocol cannot.
* Prescription links -- one scan slaved to another's slices, centre or table
  position -- exist only here. The printout of a linked scan is byte-identical
  to an unlinked one, so ``links`` has no counterpart on the PDF side at all.

Nothing new about the format is established here; every field read is one
:mod:`.archive`, :mod:`.patch` or :mod:`.geometry` already reads and tests.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from ..listing import format_duration
from ..sequences import Catalog, default_catalog, identify
from . import patch
from .archive import DIRECTORY, Archive, Program, Protocol, Step
from .geometry import agrees, read_group

#: The release profile a baseline's ``MAJORVERSION`` belongs to.
#:
#: Keyed on the same four characters the PDF profiles anchor their
#: discriminators on -- ``profiles/xa60.py`` requires ``\bVA60`` precisely
#: because a bare ``VA\d\d`` would also match VA30 and yield a confident wrong
#: answer. A baseline outside this table leaves ``software_version`` null and
#: is reported as a warning, rather than being guessed at from the digits.
RELEASES = {"VA30": "XA30", "VA60": "XA60"}

#: Prefixes ``tSequenceFileName`` uses to name the tree a binary came from.
#:
#: Siemens writes one of these itself, so it is a statement about the sequence
#: rather than an inference about it -- the same standing VB17A's printed
#: ``SIEMENS:``/``USER:`` owner has. Verified over 1031 scans in 31 corpus
#: archives: no binary appears under both prefixes, every ``%SiemensSeq%``
#: binary the catalog knows is one of its Siemens kernels, and every
#: ``%CustomerSeq%`` binary it knows matches a third-party signature.
SEQUENCE_TREES = ("%SiemensSeq%", "%CustomerSeq%")

#: One ASCCONV assignment: a dotted, optionally indexed key and its literal.
_ASSIGNMENT = re.compile(r"^[ \t]*([A-Za-z_][\w\[\].]*)[ \t]*=[ \t]*(.*?)[ \t]*$", re.M)

#: One component of such a key: a name, or a bracketed array index.
_TOKEN = re.compile(r"([A-Za-z_]\w*)|\[(\d+)\]")

#: A whole path component that is an index, written without brackets.
#:
#: ASCCONV spells an index two ways. ``alTE[0]`` is the usual one; the corpus
#: also carries ``sDiffusion.sFreeDiffusionData.sComment.0``, 2214 assignments
#: over one key family and the only bare-digit component in 1031 scans. Both
#: mean the same thing and both nest to the same decimal string key, so the
#: document does not record which spelling the file used -- the flat table
#: does, and it is what a writer reads.
_BARE_INDEX = re.compile(r"\d+")

#: Member every ASCCONV array carries beside its indexed elements.
#:
#: ``sSliceArray.asSlice.__attribute__.size`` sits alongside
#: ``sSliceArray.asSlice[0].dThickness``, so an array node is a map *and* a
#: sequence at once. It is the only such member in the corpus -- 30029
#: occurrences across 1031 scans, with nothing else beside an index -- and it
#: is why indices are nested as decimal string keys rather than as JSON list
#: positions. The two cannot collide: a name must start with a letter or an
#: underscore, so no member of an array node can look like an index.
ARRAY_ATTRIBUTE = "__attribute__"


def ascconv_table(text: str) -> "OrderedDict[str, str]":
    """Every ASCCONV assignment in a protocol, in the order it is written.

    The literal is kept exactly as stored rather than decoded to a number.
    That is not laziness: the console writes ``0x1`` for some flags and ``1``
    for others -- which is why :mod:`.patch` has to enumerate ``HEX_KEYS`` and
    ``INT_KEYS`` -- so normalizing the spelling would make this document
    disagree with the file it describes.

    Parameters
    ----------
    text : str
        The XProtocol text, ASCCONV block included.

    Returns
    -------
    OrderedDict
        Key to literal, in file order. Empty when there is no ASCCONV block,
        which is a readable state rather than an error.
    """
    start, end = patch.ascconv_bounds(text)
    if start < 0:
        return OrderedDict()
    found = _ASSIGNMENT.finditer(text, start, end)
    return OrderedDict((m.group(1), m.group(2)) for m in found)


def _tokens(key: str) -> list[str] | None:
    """Split an ASCCONV key into the path components its name describes.

    An array index comes back as its decimal digits, whichever of the two
    spellings the file used -- ``alTE[0]`` or a bare ``sComment.0``. Digits
    cannot be confused with a member name: a name must start with a letter or
    an underscore.

    Parameters
    ----------
    key : str
        A key such as ``sSliceArray.asSlice[0].dThickness``.

    Returns
    -------
    list of str or None
        The components, or ``None`` when the key is not spelled the way the
        grammar expects -- in which case the caller keeps it flat rather than
        guessing at its structure.
    """
    parts: list[str] = []
    for chunk in key.split("."):
        if _BARE_INDEX.fullmatch(chunk):
            parts.append(chunk)
            continue
        position = 0
        for match in _TOKEN.finditer(chunk):
            if match.start() != position:
                return None
            position = match.end()
            parts.append(match.group(1) if match.group(1) is not None else match.group(2))
        if position != len(chunk) or not parts:
            return None
    return parts


def nest(table: "OrderedDict[str, str]") -> dict[str, Any]:
    """Turn a flat ASCCONV table into the tree its key names describe.

    ``sSliceArray.asSlice[0].dThickness`` becomes a ``dThickness`` under
    ``"0"`` under ``asSlice`` under ``sSliceArray``. Indices are decimal
    string keys rather than JSON list positions, for two reasons that both
    come from the file: an array node also holds :data:`ARRAY_ATTRIBUTE`, so
    it is not a pure sequence; and the arrays are sparse -- ``alTE[0]`` and
    ``alTE[3]`` with nothing between -- so list positions would either close
    the gaps and renumber the elements or pad them with nulls.

    A key that cannot be placed -- an unparsable name, or one whose branch is
    already occupied by a scalar -- is kept verbatim as a flat key at the
    level that could hold it. No corpus protocol needs that, so it is a guard
    against a future export rather than a routine path, and it is lossless
    either way.

    Parameters
    ----------
    table : OrderedDict
        Key to literal, as :func:`ascconv_table` returns.

    Returns
    -------
    dict
        The nested document.
    """
    root: dict[str, Any] = {}
    for key, literal in table.items():
        parts = _tokens(key)
        if parts is None:
            root[key] = literal
            continue
        node: dict[str, Any] | None = root
        for token in parts[:-1]:
            child = node.setdefault(token, {}) if node is not None else None
            node = child if isinstance(child, dict) else None
        last = parts[-1]
        if node is None or isinstance(node.get(last), dict):
            root[key] = literal
        else:
            node[last] = literal
    return root


def sequence_file(protocol: Protocol) -> str:
    """The sequence binary a protocol runs, tree prefix included.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.

    Returns
    -------
    str
        For example ``%CustomerSeq%\\cmrr_mbep2d_bold``. Empty when the
        protocol carries no ASCCONV block.
    """
    raw = patch.read_ascconv(protocol.xprotocol, "tSequenceFileName")
    return (raw or "").strip().strip('"')


def sequence_owner(file_name: str) -> str:
    """The tree a sequence binary came from, as the protocol spells it.

    Returned under the archive's own spelling rather than translated into
    VB17A's ``USER``/``SIEMENS``, so the evidence line a catalog verdict
    prints quotes what the file actually says.

    Parameters
    ----------
    file_name : str
        A value as :func:`sequence_file` returns it.

    Returns
    -------
    str
        ``"%SiemensSeq%"``, ``"%CustomerSeq%"``, or empty for anything else.
        An unrecognized prefix is reported as no statement at all rather than
        as one of the two, since a wrong owner decides a verdict outright.
    """
    prefix = file_name.split("\\", 1)[0] if "\\" in file_name else ""
    return prefix if prefix in SEQUENCE_TREES else ""


def acquisition_time(protocol: Protocol) -> str:
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

    Named and spelled to match :attr:`..model.Scan.header` so the same
    readers work on both, which is what lets ``list`` and ``sequences`` accept
    an archive. ``sequence`` is the binary's bare name because that is what a
    catalog signature matches on; the prefix travels separately in
    ``sequence_owner``.

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
    stored = sequence_file(protocol)
    header: "OrderedDict[str, str]" = OrderedDict()
    binary = stored.rsplit("\\", 1)[-1] if stored else patch.sequence_of(protocol)
    if binary:
        header["sequence"] = binary
    owner = sequence_owner(stored)
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


def preview_of(protocol: Protocol) -> dict[str, Any]:
    """A protocol's ``Preview`` map, keyed by the label the console shows.

    ``Preview`` is the console's own summary listing, so its labels are the
    ones a printout uses -- ``TR``, ``Slice Thickness``, ``FOV Read`` -- which
    is what makes it the bridge between an archive and a PDF. It is a summary
    and not a mirror: a multi-echo scan carries only the first echo here, the
    rest living in ``alTE[1..3]`` in the ASCCONV block alone.

    A label used more than once -- ``Distance Factor``, once per slice group
    -- is suffixed ``#2``, ``#3`` in path order, the same way the PDF parser
    spells a repeated key.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.

    Returns
    -------
    dict
        Label to ``{"path", "unit", "value"}``, with the value left in the
        type the archive stores it as.
    """
    out: dict[str, Any] = {}
    seen: dict[str, int] = {}
    for path, entry in sorted(protocol.preview.items()):
        label = entry.label or path
        seen[label] = seen.get(label, 0) + 1
        key = label if seen[label] == 1 else f"{label} #{seen[label]}"
        out[key] = {"path": path, "unit": entry.unit, "value": entry.value}
    return out


def printed_view(protocol: Protocol) -> "OrderedDict[str, str]":
    """The ``Preview`` map as printed strings, for the PDF-shaped readers.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.

    Returns
    -------
    OrderedDict
        Label to value, with the unit appended the way a printout joins them.
    """
    out: "OrderedDict[str, str]" = OrderedDict()
    for label, entry in preview_of(protocol).items():
        value = "" if entry["value"] is None else str(entry["value"])
        unit = entry["unit"] or ""
        out[label] = f"{value} {unit}".strip()
    return out


def geometry_of(protocol: Protocol) -> dict[str, Any] | None:
    """The six quantities a protocol's slice array is computed from.

    The array itself is not reported: every one of its elements is a function
    of these six, verified to under 7e-12 mm on all 351 single-group arrays in
    the corpus, so listing sixty derived positions would be repetition rather
    than information.

    Parameters
    ----------
    protocol : Protocol
        The protocol to read.

    Returns
    -------
    dict or None
        ``None`` when the protocol has no readable single slice group -- a
        spectroscopy scan, or a multi-group array, whose layout is read but
        never written and cannot be summarized as one progression.
    """
    group = read_group(protocol.xprotocol)
    if group is None:
        return None
    return {
        "centre": list(group.centre),
        "normal": list(group.normal),
        "thickness_mm": group.thickness,
        "distance_factor": group.distance_factor,
        "slices": group.count,
        "in_plane_rotation_rad": group.in_plane_rotation,
        "step_mm": group.step,
        "extent_mm": group.extent,
        "stored_array_deviation_mm": agrees(protocol.xprotocol, group),
    }


def link_of(link: Any, names: dict[str, str]) -> dict[str, Any]:
    """One prescription link, with its endpoints resolved to scan names.

    Parameters
    ----------
    link : Link
        The decoded relation.
    names : dict of str to str
        Step ``ObjectId`` to displayed name. Endpoints are addressed in the
        object-id space, not the element-id space the packed ``Children``
        blobs use; looking one up in the wrong map finds nothing silently.

    Returns
    -------
    dict
        The link's source, target, group and flags. ``source``/``target`` are
        names where they resolve and the raw id where they do not, so a
        dangling reference stays visible.
    """
    out: dict[str, Any] = {
        "source": names.get(link.source, link.source),
        "target": names.get(link.target, link.target),
        "kind": link.kind,
        "constraint": link.constraint,
    }
    if link.group is not None:
        out["group"] = link.group
    if link.is_copy_reference:
        out["copies_phase_encoding_direction"] = link.copies_phase_encoding_direction
        out["copies_steps"] = link.copies_steps
        out["ignores_last_step"] = link.ignores_last_step
        out["ignores_measurements"] = link.ignores_measurements
    if link.extra:
        out["extra"] = dict(link.extra)
    return out


def _step_names(archive: Archive) -> dict[str, str]:
    """Map every step's object id to its displayed name.

    Parameters
    ----------
    archive : Archive
        The archive to index.

    Returns
    -------
    dict of str to str
        One entry per step across every program.
    """
    return {step.instance.object_id: step.name for step in archive.steps}


def scan_of(step: Step, index: int, catalog: Catalog, folder: str = "") -> dict[str, Any]:
    """A step in the shape :meth:`..model.Scan.to_dict` produces.

    This is the adapter that lets the readers written for parsed PDFs -- the
    listing, the sequence catalog, the policy checker -- run against an
    archive unchanged. ``sections`` holds one section, ``Preview``, because
    the archive has no cards: what a printout splits into Routine, Contrast
    and Geometry is a property of the page, not of the protocol. That is why a
    scan read from an archive never contributes a Special card to
    :func:`..sequences.special_keys`, and so is identified by its binary and
    its stated owner alone.

    Parameters
    ----------
    step : Step
        The step to describe.
    index : int
        Its zero-based position in the running order.
    catalog : Catalog
        Signatures to identify the sequence against.
    folder : str, optional
        The program's folder path, as :meth:`..archive.Archive.path_of`
        builds it. Default empty, which leaves ``path`` empty rather than
        inventing one.

    Returns
    -------
    dict
        Index, name, path, header, provenance and sections.
    """
    header = header_of(step)
    sections = OrderedDict()
    if step.runs_a_protocol:
        sections["Preview"] = printed_view(step.protocol)
    scan: dict[str, Any] = {
        "index": index,
        "name": step.name,
        "path": f"{folder}/{step.name}" if folder else "",
        "header": header,
        "sections": sections,
    }
    scan["provenance"] = identify(scan, catalog).to_dict()
    return scan


def step_of(
    step: Step,
    index: int,
    names: dict[str, str],
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
    names : dict of str to str
        Step object ids to names, for resolving links.
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
    out["provenance"] = identify(scan_of(step, index, catalog, folder), catalog).to_dict()
    out["preview"] = preview_of(protocol)
    geometry = geometry_of(protocol)
    if geometry is not None:
        out["geometry"] = geometry
    if ascconv:
        out["ascconv"] = nest(ascconv_table(protocol.xprotocol))
    return out


def program_of(
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
        A prebuilt :attr:`..archive.Archive.directory_parents`. Pass one when
        describing many programs: building it parses the root document, which
        is megabytes of JSON on a whole-scanner export.
    names : dict of str to str or None, optional
        A prebuilt step-name index. Building it walks every program in the
        archive, so computing it per program is quadratic -- on a
        whole-scanner export with 499 programs that is the difference between
        seconds and not finishing.

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
        names = _step_names(archive)
    # The steps sit *under* the program, so their folder is its whole path --
    # the printout agrees, ending \...\Frederick\Potpourri_P1\localizer.
    folder = "/".join(archive.path_of(program.instance, parents))
    steps = [
        step_of(step, position, names, catalog, ascconv=ascconv, folder=folder)
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
        "links": [link_of(link, names) for link in program.links],
        "steps": steps,
    }


def _directories(archive: Archive, parents: dict[str, str]) -> list[dict[str, str]]:
    """Every directory in the archive, with its path through the tree.

    An earlier version of this reported a flat list, on the evidence that the
    directory nodes carry no ``Children`` and the programs no
    ``ParentElementId`` -- both true, and both beside the point. The tree is
    in the root's ``ParentDirectoryId`` map, so it is recoverable; see
    :attr:`..archive.Archive.directory_parents`.

    Parameters
    ----------
    archive : Archive
        The archive to read.
    parents : dict of str to str
        A prebuilt :attr:`..archive.Archive.directory_parents`.

    Returns
    -------
    list of dict
        One ``{"name", "path"}`` per labelled directory, sorted by path so
        the tree reads in order.
    """
    found = []
    for instance in archive.instances.values():
        if instance.kind != DIRECTORY:
            continue
        label = archive.label_of(instance)
        if label:
            found.append({"name": label, "path": "/".join(archive.path_of(instance, parents))})
    return sorted(found, key=lambda entry: entry["path"])


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
    release = RELEASES.get(major[:4])
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
    directories = _directories(archive, parents)
    if directories:
        out["directories"] = directories
    names = {step.instance.object_id: step.name for p in programs for step in p.steps}
    out["programs"] = [
        program_of(archive, program, index, catalog, ascconv=ascconv, parents=parents, names=names)
        for index, program in enumerate(programs)
    ]
    return out


def as_protocol(
    archive: Archive,
    program: Program,
    source: str,
    *,
    catalog: Catalog | None = None,
    include_flat: bool = True,
) -> dict[str, Any]:
    """One program in the shape a parsed PDF has.

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
    catalog = catalog or default_catalog()
    folder = "/".join(archive.path_of(program.instance))
    scans = []
    for step in program.steps:
        if not step.runs_a_protocol:
            continue
        scan = scan_of(step, len(scans), catalog, folder)
        if include_flat:
            scan["flat"] = dict(scan["sections"].get("Preview", {}))
        scans.append(scan)
    return {
        "source_file": source,
        "software_version": RELEASES.get(archive.major_version[:4]),
        "detection": {"method": "baseline", "confidence": "high"},
        "scanner": archive.baseline,
        "program": program.name,
        "page_count": 0,
        "scans": scans,
    }
