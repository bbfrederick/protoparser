"""Create nodes in an ``.exar1`` archive, rather than only editing them.

A scanner has accepted archives built by this module: four added scans loaded,
kept their running order, and the one carrying an edit kept its own protocol.
Getting there cost two defects worth naming, because neither is visible to a
reader and both look like success.

The first is that :data:`STEP_KEYED_MAPS` is five maps, not one.
``EdfProgramContent`` describes the running order in ``LinksFrom``,
``LinksTo``, ``Ranks``, ``RelationsFrom`` and ``RelationsTo``, all keyed by
step id. A step present in only some of them leaves the console unable to
build the program, which it reports by showing the folder tree with no
protocols in it -- indistinguishable, from the outside, from an archive
exported off an empty folder node.

The second is that a protocol and a label each carry a ``ParentElementId``
pointing at *their own step*, and the console resolves a step's protocol
through that reverse link rather than through the step's ``Children`` blob. A
copy that keeps the source's pointer is quietly served the source's protocol.
While the copy is identical that is invisible; once it is edited, the edit
disappears. Any test of this must therefore change something in the copy, or
it cannot tell "created correctly" from "aliased to the original".
"""

from __future__ import annotations

import uuid
from typing import Any

from . import envelope
from .archive import (
    COPY_REFERENCE,
    COPY_REFERENCE_GROUPS,
    Archive,
    Instance,
    Step,
    pack_guids,
    unpack_guids,
)

#: The maps in ``EdfProgramContent`` keyed by measurement-step id. Every step
#: must appear in all of them.
STEP_KEYED_MAPS = ("LinksFrom", "LinksTo", "Ranks", "RelationsFrom", "RelationsTo")

#: .NET type moniker Newtonsoft writes on a link between two steps.
LINK_TYPE = "syngo.MR.ExamDataFoundation.Data.EdfProgramLink, syngo.MR.ExamDataFoundation.Data"

#: The all-zero GUID, which these payloads use for "no condition".
NO_GUID = "00000000-0000-0000-0000-000000000000"


def renumber_references(document: Any) -> Any:
    """Renumber Newtonsoft ``$id`` values sequentially, remapping ``$ref``.

    Json.NET numbers references ``1..N`` in the order it writes them, so an
    invented key is legal JSON and not what the console produces. Verified by
    reproducing an untouched console document exactly, which is what shows the
    walk order matches Newtonsoft's serialization order rather than merely
    looking plausible.

    Parameters
    ----------
    document : Any
        A decoded JSON document.

    Returns
    -------
    Any
        The document with ``$id`` renumbered and every ``$ref`` following it.
    """
    mapping: dict[str, str] = {}
    counter = 0

    def collect(node: Any) -> None:
        nonlocal counter
        if isinstance(node, dict):
            if "$id" in node:
                counter += 1
                mapping[str(node["$id"])] = str(counter)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    def rewrite(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key == "$id":
                    out[key] = mapping[str(value)]
                elif key == "$ref":
                    out[key] = mapping.get(str(value), str(value))
                else:
                    out[key] = rewrite(value)
            return out
        if isinstance(node, list):
            return [rewrite(value) for value in node]
        return node

    collect(document)
    return rewrite(document)


def duplicate_step(
    archive: Archive,
    step: Step,
    name: str,
    source: Archive | None = None,
    program: Instance | None = None,
) -> str:
    """Append a copy of ``step`` to the archive's running order.

    The copy gets its own identity in all three GUID spaces and its own label.
    Its protocol keeps the source's ``ContentHash``, which is correct rather
    than lazy: the content is identical and the store is addressed by content,
    exactly as the console shares one protocol between identical scans. Patch
    the copy afterwards to give it content of its own.

    Pass ``source`` to import a step out of a *different* archive. The store
    being content-addressed is what makes that little more than carrying the
    step's and protocol's ``Content`` rows across, and because an unmodified
    :class:`~.envelope.Envelope` keeps the bytes it was read as, an imported
    protocol is copied rather than recompressed.

    Parameters
    ----------
    archive : Archive
        The archive to extend. Modified in place; call
        :meth:`Archive.write` to save, and re-read before using the new step,
        since the live instance map is rebuilt on read.
    step : Step
        The step to copy.
    name : str
        Displayed name for the copy.
    source : Archive or None
        The archive ``step`` was read from, when that is not ``archive``.
        ``None`` means the step is already in ``archive``.
    program : Instance or None
        Which program to append to. ``None`` means the archive's only one,
        which raises if it holds several -- a backup carries one program per
        protocol, and appending to whichever came first would put the scan in
        an unrelated protocol.

    Returns
    -------
    str
        The new step's object id.

    Raises
    ------
    ValueError
        If the archive has no program node to attach the step to, if it has
        several and none was named, or if an imported step comes from a
        different release. The baseline is the compatibility key the scanner
        checks, so importing across it produces an archive that is
        well-formed and will not load.
    """
    if program is None:
        program = archive.program
    if program is None:
        raise ValueError("archive has no program to append a step to")
    origin = archive if source is None else source
    if origin is not archive and origin.major_version != archive.major_version:
        raise ValueError(
            f"cannot import a {origin.major_version or '?'} step "
            f"into a {archive.major_version or '?'} archive"
        )

    source_label = origin.by_element[step.instance.label_element_id]
    fresh = {
        tag: (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
        for tag in ("step", "protocol", "label")
    }

    label_hash = _store_label(archive, origin, source_label, name)
    _add_instances(archive, origin, step, source_label, fresh, label_hash, program)
    _extend_map(archive, fresh)
    _attach_to_program(archive, program, fresh["step"])
    return fresh["step"][2]


def _adopt(archive: Archive, content: Any) -> str:
    """Ensure ``content`` has a row in ``archive``, and return its hash.

    Parameters
    ----------
    archive : Archive
        The archive to store into.
    content : Envelope
        The content to store. Written back as it was read when unmodified, so
        an imported blob keeps the console's own DEFLATE stream.

    Returns
    -------
    str
        The content hash.
    """
    if content.hash not in archive.contents:
        archive.contents[content.hash] = content
        archive.container.tables["Content"].append(
            {"Hash": content.hash, "Data": content.to_stored(), "Format": envelope.STORED_FORMAT}
        )
    return content.hash


def _store_label(archive: Archive, origin: Archive, source: Any, name: str) -> str:
    """Write a locale table holding ``name`` and return its content hash.

    Parameters
    ----------
    archive : Archive
        The archive to store the content in.
    origin : Archive
        The archive ``source`` was read from, which is where its existing
        content lives. The same archive as ``archive`` for a plain duplicate.
    source : Instance
        The label node being copied, whose locale keys are reused. Which key
        that is varies by export -- most write ``""`` and some write ``"en"``
        -- so the copy follows its source rather than assuming one.
    name : str
        The displayed name.

    Returns
    -------
    str
        Content hash of the stored label.
    """
    document = dict(origin.document(source))
    texts = document.get("Texts", {})
    document["Texts"] = {k: (v if k == "$id" else name) for k, v in texts.items()}
    return _adopt(archive, origin.contents[source.content_hash].replace(document))


def _add_instances(
    archive: Archive,
    origin: Archive,
    step: Step,
    source_label: Any,
    fresh: dict[str, tuple[str, str, str]],
    label_hash: str,
    program: Any,
) -> None:
    """Add the three instance rows, their elements and their changeset rows.

    Rows are read out of ``origin`` and written into ``archive``, which are
    the same object for a plain duplicate. Any content the copy still points
    at is adopted on the way, since the target has never seen it when the two
    differ.

    Parameters
    ----------
    archive : Archive
        The archive being extended.
    origin : Archive
        The archive the step is being copied out of.
    step : Step
        The step being copied.
    source_label : Instance
        The label node being copied.
    fresh : dict
        New ``(Id, Element_id, ObjectId)`` per node.
    label_hash : str
        Content hash of the copy's label.
    program : Instance
        The target's program node, which the new step parents to.

    Returns
    -------
    None
    """
    tables = archive.container.tables
    instances, elements = tables["Instance"], tables["Element"]
    changes = tables["InstanceChangeSet"]
    read_instances = origin.container.tables["Instance"]
    read_elements = origin.container.tables["Element"]
    sources = {"step": step.instance, "protocol": step.protocol.instance, "label": source_label}
    for tag, source in sources.items():
        identifier, element, obj = fresh[tag]
        position = read_instances.find("Id", source.id)[0]
        row = dict(zip(read_instances.columns, read_instances.rows[position]))
        row["Id"], row["Element_id"], row["ObjectId"] = identifier, element, obj
        if tag == "step":
            row["Children"] = pack_guids([fresh["protocol"][1]])
            row["LabelElement_id"] = fresh["label"][1]
            # A step parents to the program. Inheriting the source's pointer
            # is right only while the two archives are one file; name the
            # target's program instead, which is the same value in that case.
            row["ParentElementId"] = program.element_id
        else:
            # A protocol and a label both parent to their own step. Keeping the
            # source's pointer makes the console serve this copy the source's
            # protocol, and any edit to it silently disappears.
            row["ParentElementId"] = fresh["step"][1]
            if tag == "label":
                row["ContentHash"] = label_hash
        if tag != "label" and row.get("ContentHash"):
            _adopt(archive, origin.contents[str(row["ContentHash"])])
        instances.append(row)
        source_element = read_elements.rows[read_elements.find("Id", source.element_id)[0]]
        elements.append(dict(zip(elements.columns, (element, source_element[1], None))))
        changes.append(
            {
                "InstanceId": identifier,
                "ChangeSetId": archive.head,
                "ElementId": element,
                "State": 0,
            }
        )


def _extend_map(archive: Archive, fresh: dict[str, tuple[str, str, str]]) -> None:
    """Add the new nodes to the head changeset's checkout index.

    Parameters
    ----------
    archive : Archive
        The archive being extended.
    fresh : dict
        New ``(Id, Element_id, ObjectId)`` per node.

    Returns
    -------
    None
    """
    maps = archive.container.tables["ElementToInstanceMap"]
    wanted = _head_map_id(archive)
    position = maps.find("Id", wanted)[0]
    blob = maps.rows[position][maps.index_of("Data")]
    for identifier, element, _obj in fresh.values():
        blob += uuid.UUID(element).bytes_le + uuid.UUID(identifier).bytes_le
    maps.set(position, "Data", blob)


def _head_map_id(archive: Archive) -> str:
    """Return the element map the head changeset resolves through.

    Parameters
    ----------
    archive : Archive
        The archive to inspect.

    Returns
    -------
    str
        The map's id.
    """
    for row in archive.container.rows("ChangeSet"):
        if str(row["Id"]) != archive.head:
            continue
        delta = str(row["DeltaElementMapId"])
        return delta if delta != NO_GUID else str(row["BaseElementMapId"])
    raise ValueError(f"no changeset {archive.head}")


def _attach_to_program(archive: Archive, program: Any, step_ids: tuple[str, str, str]) -> None:
    """Put the new step in the program's children and in all five maps.

    Parameters
    ----------
    archive : Archive
        The archive being extended.
    program : Instance
        The program node.
    step_ids : tuple
        The new step's ``(Id, Element_id, ObjectId)``.

    Returns
    -------
    None
    """
    instances = archive.container.tables["Instance"]
    position = instances.find("Id", program.id)[0]
    children = unpack_guids(instances.rows[position][instances.index_of("Children")])
    instances.set(position, "Children", pack_guids(children + [step_ids[1]]))

    document = archive.document(program)
    last, new = document["LastStepId"], step_ids[2]
    link = f"link-{new}"
    document["LinksFrom"].setdefault(last, {"$id": f"lf-{last}", "$values": []})
    document["LinksFrom"][last]["$values"].append(
        {
            "$id": link,
            "$type": LINK_TYPE,
            "ConditionId": NO_GUID,
            "SelectionId": NO_GUID,
            "SourceId": last,
            "TargetId": new,
        }
    )
    document["LinksFrom"][new] = {"$id": f"lf-{new}", "$values": []}
    # The incoming edge is the same link object, referenced rather than repeated.
    document["LinksTo"][new] = {"$id": f"lt-{new}", "$values": [{"$ref": link}]}
    rank = max(v["Rank"] for k, v in document["Ranks"].items() if k != "$id") + 1
    document["Ranks"][new] = {"$id": f"rk-{new}", "Rank": rank, "StepId": new}
    document["RelationsFrom"][new] = {"$id": f"rf-{new}", "$values": []}
    document["RelationsTo"][new] = {"$id": f"rt-{new}", "$values": []}
    document["LastStepId"] = new
    archive.replace_content(program, renumber_references(document))


#: .NET type moniker Newtonsoft writes on a relation between two steps.
RELATION_TYPE = (
    "syngo.MR.ExamDataFoundation.Data.EdfProgramRelation, syngo.MR.ExamDataFoundation.Data"
)

#: The namespace the copy-reference payload declares. Written verbatim: it is
#: part of the element the console emits, and a payload without it is not the
#: same document.
COPY_REFERENCE_NS = "http://syngo.MR.ExamDataFoundation.Data"


def link_steps(
    archive: Archive,
    source: Step,
    target: Step,
    group: str = "Slices",
    program: Instance | None = None,
    copy_phase_encoding: bool = False,
    copy_steps: bool = False,
    ignore_last_step: bool = False,
    ignore_measurements: bool = False,
) -> None:
    """Slave ``target`` to ``source``, the way the console's copy dialog does.

    A prescription link is a property of the *program*, not of either
    protocol: nothing in a scan's own XProtocol says it is linked, and the PDF
    export does not record it at all. So it lives in ``RelationsFrom`` keyed
    by the source step, mirrored into ``RelationsTo`` on the target as a
    ``$ref`` back-pointer to the same relation object rather than a second
    copy of it.

    Parameters
    ----------
    archive : Archive
        The archive to edit, in place.
    source : Step
        The step being copied *from*.
    target : Step
        The step slaved to it.
    group : str
        Which menu item the link represents, one of
        :data:`~.archive.COPY_REFERENCE_GROUPS`.
    program : Instance or None
        The program holding both steps. ``None`` means the archive's only
        one, which raises if it holds several.
    copy_phase_encoding : bool
        ``CopyPhaseEncodingDirection``. Orthogonal to ``group`` rather than a
        further value of it.
    copy_steps : bool
        ``CopySteps``.
    ignore_last_step : bool
        ``IgnoreLastStep``. ``False`` on every relation in the corpus, so its
        spelling is established and its effect is not.
    ignore_measurements : bool
        ``IgnoreMeasurements``. The same.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If the archive has no program, if ``group`` is not a known menu item,
        or if either step is missing from the program's relation maps -- a
        step absent from one of the five leaves the console unable to build
        the program, which it shows as a folder tree with no protocols in it.
    """
    if group not in COPY_REFERENCE_GROUPS:
        offered = ", ".join(COPY_REFERENCE_GROUPS)
        raise ValueError(f"{group!r} is not a copy-reference group; expected one of: {offered}")
    if program is None:
        program = archive.program
    if program is None:
        raise ValueError("archive has no program to link within")

    document = archive.document(program)
    outgoing, incoming = document.get("RelationsFrom", {}), document.get("RelationsTo", {})
    for name, table, step in (
        ("RelationsFrom", outgoing, source),
        ("RelationsTo", incoming, target),
    ):
        if step.instance.object_id not in table:
            raise ValueError(f"{step.name!r} is missing from the program's {name}")

    flags = {
        "CopyPhaseEncodingDirection": copy_phase_encoding,
        "CopySteps": copy_steps,
        "IgnoreLastStep": ignore_last_step,
        "IgnoreMeasurements": ignore_measurements,
    }
    # Attribute order is the console's: the flags around Group alphabetically,
    # then the namespace last.
    written = {"Group": group} | {k: str(v) for k, v in flags.items()}
    attributes = " ".join(f'{k}="{written[k]}"' for k in sorted(written))
    payload = f'<EdfCopyReferenceParameters {attributes} xmlns="{COPY_REFERENCE_NS}" />'

    marker = f"rel-{source.instance.object_id}-{target.instance.object_id}"
    outgoing[source.instance.object_id]["$values"].append(
        {
            "$id": marker,
            "$type": RELATION_TYPE,
            "Constraint": 1,
            "Data": payload,
            "Kind": COPY_REFERENCE,
            "SourceId": source.instance.object_id,
            "State": "",
            "TargetId": target.instance.object_id,
        }
    )
    # The incoming side references the same object. Renumbering rewrites the
    # marker on both, which is what keeps the $ref resolving.
    incoming[target.instance.object_id]["$values"].append({"$ref": marker})
    archive.replace_content(program, renumber_references(document))


def rename(archive: Archive, instance: Instance, name: str) -> str:
    """Change the displayed name of a program, step or directory.

    Names are not in the protocol. They hang off ``Instance.LabelElement_id``
    on an ``EdfString`` node holding a locale table, which is what lets a scan
    or a protocol be renamed without re-hashing the content -- so this rewrites
    that table and nothing else.

    An archive built from a template inherits the template's program name, and
    the console disambiguates on import rather than complaining: a copy of
    ``Potpourri_P1`` arrives as ``Potpourri_P1 (2)``. That is tidy and it is
    also how a generated protocol ends up named after whichever export it was
    seeded from.

    Parameters
    ----------
    archive : Archive
        The archive to edit, in place.
    instance : Instance
        The node to rename -- a program, a step, or a directory.
    name : str
        The new displayed name. The console keeps 35 characters of a scan
        name; this does not truncate, since the limit is the console's and
        applies where it applies.

    Returns
    -------
    str
        The label's new content hash.

    Raises
    ------
    ValueError
        If the node carries no label, or if its label is shared with another
        node -- renaming would then silently rename both. No corpus archive
        shares one, but the store is content-addressed and nothing in the
        format forbids it.
    """
    if not instance.label_element_id:
        raise ValueError(f"{instance.kind} {instance.id[:8]} carries no label to change")
    label = archive.by_element.get(instance.label_element_id)
    if label is None:
        raise ValueError(f"{instance.kind} {instance.id[:8]} names a label that is not live")
    sharing = [
        one
        for one in archive.instances.values()
        if one.label_element_id == instance.label_element_id and one.id != instance.id
    ]
    if sharing:
        kinds = ", ".join(sorted({one.kind for one in sharing}))
        raise ValueError(
            f"that label is shared with {len(sharing)} other node(s) ({kinds}); "
            "renaming it would rename them too"
        )

    document = dict(archive.document(label))
    texts = document.get("Texts", {})
    # Which locale key an export uses varies -- most write "" and some write
    # "en" -- so every entry is set rather than assuming one.
    document["Texts"] = {k: (v if k == "$id" else name) for k, v in texts.items()}
    return archive.replace_content(label, document)
