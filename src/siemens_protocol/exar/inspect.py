"""Read the low-level facts an ``.exar1`` archive stores.

This module turns the tree :mod:`.archive` decodes into the plain values a
higher-level reader needs -- the sequence binary and the tree it came from,
the ``Preview`` map, the slice-geometry summary, a prescription link, the
folder tree -- without assembling the cross-format document itself, and
without interpreting a value in terms of what a printed label means (that is
:mod:`siemens_protocol.analysis.generate.mappings`' job, one layer up; its
``card_view`` is what shows an archive's mapped parameters under the cards a
printout would show them on). The adapter that assembles the cross-format
document, and lets the readers written for parsed PDFs run against an
archive unchanged, lives in :mod:`siemens_protocol.analysis.archive_view`;
this module supplies it every low-level fact it reads.

An archive and a printout are not the same document, and pretending
otherwise would misdescribe both. A printout is what the console chose to
display; an archive is what the console stored. Three differences matter to
anyone reading either:

* The archive's printed-label view is ``Preview`` alone -- roughly forty
  console-summary parameters per scan, against the several hundred a PDF page
  prints. The complete parameter set is the ASCCONV block, which the PDF does
  not carry at all, and which :func:`ascconv_table`/:func:`nest` expose.
* The archive states which tree a sequence binary came from
  (``%SiemensSeq%`` or ``%CustomerSeq%``), which no Numaris/X printout does.
  That is the archive saying who supplied the sequence, so it identifies
  third-party sequences that a PDF of the same protocol cannot.
* Prescription links -- one scan slaved to another's slices, centre or table
  position -- exist only here. The printout of a linked scan is byte-identical
  to an unlinked one, so :func:`link_of` has no counterpart on the PDF side at
  all.

Nothing new about the format is established here; every field read is one
:mod:`.archive`, :mod:`.ascconv` or :mod:`.geometry` already reads and tests.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from . import ascconv
from .archive import DIRECTORY, Archive, Protocol
from .geometry import agrees, read_group

#: Section title carrying an archive scan's whole ASCCONV block. Not a card:
#: the archive has none, and no release prints a section by this name, so it
#: cannot be confused with one by anything reading section titles.
ASCCONV_SECTION = "ASCCONV"

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
    for others -- which is why :mod:`.ascconv` has to enumerate ``HEX_KEYS``
    and ``INT_KEYS`` -- so normalizing the spelling would make this document
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
    start, end = ascconv.ascconv_bounds(text)
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
    raw = ascconv.read_ascconv(protocol.xprotocol, "tSequenceFileName")
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


def step_names(archive: Archive) -> dict[str, str]:
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


def directories(archive: Archive, parents: dict[str, str]) -> list[dict[str, str]]:
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
