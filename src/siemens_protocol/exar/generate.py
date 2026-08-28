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
from .archive import Archive, Step, pack_guids, unpack_guids

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


def duplicate_step(archive: Archive, step: Step, name: str) -> str:
    """Append a copy of ``step`` to the archive's running order.

    The copy gets its own identity in all three GUID spaces and its own label.
    Its protocol keeps the source's ``ContentHash``, which is correct rather
    than lazy: the content is identical and the store is addressed by content,
    exactly as the console shares one protocol between identical scans. Patch
    the copy afterwards to give it content of its own.

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

    Returns
    -------
    str
        The new step's object id.

    Raises
    ------
    ValueError
        If the archive has no program node to attach the step to.
    """
    program = archive.program
    if program is None:
        raise ValueError("archive has no program to append a step to")

    tables = archive.container.tables
    instances = tables["Instance"]
    source_label = archive.by_element[step.instance.label_element_id]
    fresh = {
        tag: (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
        for tag in ("step", "protocol", "label")
    }

    label_hash = _store_label(archive, source_label, name)
    _add_instances(archive, step, source_label, fresh, label_hash)
    _extend_map(archive, fresh)
    _attach_to_program(archive, program, fresh["step"])
    return fresh["step"][2]


def _store_label(archive: Archive, source: Any, name: str) -> str:
    """Write a locale table holding ``name`` and return its content hash.

    Parameters
    ----------
    archive : Archive
        The archive to store the content in.
    source : Instance
        The label node being copied, whose locale keys are reused.
    name : str
        The displayed name.

    Returns
    -------
    str
        Content hash of the stored label.
    """
    document = dict(archive.document(source))
    texts = document.get("Texts", {})
    document["Texts"] = {k: (v if k == "$id" else name) for k, v in texts.items()}
    content = archive.contents[source.content_hash].replace(document)
    if content.hash not in archive.contents:
        archive.contents[content.hash] = content
        archive.container.tables["Content"].append(
            {"Hash": content.hash, "Data": content.to_stored(), "Format": envelope.STORED_FORMAT}
        )
    return content.hash


def _add_instances(
    archive: Archive,
    step: Step,
    source_label: Any,
    fresh: dict[str, tuple[str, str, str]],
    label_hash: str,
) -> None:
    """Add the three instance rows, their elements and their changeset rows.

    Parameters
    ----------
    archive : Archive
        The archive being extended.
    step : Step
        The step being copied.
    source_label : Instance
        The label node being copied.
    fresh : dict
        New ``(Id, Element_id, ObjectId)`` per node.
    label_hash : str
        Content hash of the copy's label.

    Returns
    -------
    None
    """
    tables = archive.container.tables
    instances, elements = tables["Instance"], tables["Element"]
    changes = tables["InstanceChangeSet"]
    sources = {"step": step.instance, "protocol": step.protocol.instance, "label": source_label}
    for tag, source in sources.items():
        identifier, element, obj = fresh[tag]
        row = dict(zip(instances.columns, instances.rows[instances.find("Id", source.id)[0]]))
        row["Id"], row["Element_id"], row["ObjectId"] = identifier, element, obj
        if tag == "step":
            row["Children"] = pack_guids([fresh["protocol"][1]])
            row["LabelElement_id"] = fresh["label"][1]
        else:
            # A protocol and a label both parent to their own step. Keeping the
            # source's pointer makes the console serve this copy the source's
            # protocol, and any edit to it silently disappears.
            row["ParentElementId"] = fresh["step"][1]
            if tag == "label":
                row["ContentHash"] = label_hash
        instances.append(row)
        source_element = elements.rows[elements.find("Id", source.element_id)[0]]
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
