"""Dependency-preserving XA60 archive graph edits.

These primitives operate on an isolated edit head. They retain prior changesets,
copy opaque protocol/add-in content, and rebuild all mirrored execution maps.
The analysis edit session supplies transactional rollback and mapped parameters.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any, Sequence

from . import generate
from .archive import (
    DIRECTORY,
    PROGRAM,
    STEP_KINDS,
    Archive,
    Instance,
    Step,
    from_container,
    pack_guids,
)
from .envelope import Envelope
from .extract import _closure


def refresh(archive: Archive) -> Archive:
    """Rebuild the live model without changing content provenance.

    Parameters
    ----------
    archive : Archive
        Tables whose instances or head map changed.

    Returns
    -------
    Archive
        A fresh model over the same tables, retaining pruning bookkeeping.
    """
    result = from_container(archive.container)
    result.as_read = archive.as_read
    result.displaced = set(archive.displaced)
    return result


def clone(archive: Archive) -> Archive:
    """Copy an archive in memory, leaving the caller's tables untouched.

    Parameters
    ----------
    archive : Archive
        Source archive, including any unsaved edits.

    Returns
    -------
    Archive
        Independent tables and live objects.
    """
    result = from_container(copy.deepcopy(archive.container))
    result.as_read = archive.as_read
    result.displaced = set(archive.displaced)
    return result


def checkout(archive: Archive) -> Archive:
    """Create an isolated child changeset and instance versions for editing.

    Parameters
    ----------
    archive : Archive
        Source to copy. Its existing changesets remain intact.

    Returns
    -------
    Archive
        New head whose live instance rows can be modified independently.
    """
    result = clone(archive)
    tables = result.container.tables
    previous = result.head
    head, map_id = str(uuid.uuid4()), str(uuid.uuid4())
    old_change = next(r for r in result.container.rows("ChangeSet") if r["Id"] == previous)
    change = dict(old_change)
    change.update(
        Id=head,
        Parent1ChangeSet_id=previous,
        Parent2ChangeSet_id=None,
        BaseElementMapId=map_id,
        DeltaElementMapId=generate.NO_GUID,
        Comment="spt edit session",
        DateTime=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f") + "0",
    )
    tables["ChangeSet"].append(change)
    pairs = bytearray()
    rows = {str(r["Id"]): r for r in result.container.rows("Instance")}
    for node in result.instances.values():
        row = dict(rows[node.id])
        row["Id"] = str(uuid.uuid4())
        tables["Instance"].append(row)
        pairs.extend(uuid.UUID(node.element_id).bytes_le + uuid.UUID(row["Id"]).bytes_le)
        tables["InstanceChangeSet"].append(
            {
                "InstanceId": row["Id"],
                "ChangeSetId": head,
                "ElementId": node.element_id,
                "State": 1,
            }
        )
    tables["ElementToInstanceMap"].append({"Id": map_id, "Data": bytes(pairs)})
    branches = tables["Branch"]
    for position, row in enumerate(list(branches.dicts())):
        if row["Head"] == previous and row["Baseline"] == result.baseline:
            branches.set(position, "Head", head)
            break
    return refresh(result)


def _remap(value: Any, ids: dict[str, str]) -> Any:
    """Replace graph GUIDs in structured documents, preserving opaque strings.

    Parameters
    ----------
    value : Any
        JSON value to copy.
    ids : dict of str to str
        Old-to-new graph identities.

    Returns
    -------
    Any
        Copied value with exact GUID keys and values remapped.
    """
    if isinstance(value, dict):
        return {ids.get(k, k): _remap(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap(v, ids) for v in value]
    return ids.get(value, value) if isinstance(value, str) else value


def copy_graph(
    archive: Archive, origin: Archive, root: Instance, parent: Instance
) -> tuple[Archive, str, dict[str, str]]:
    """Import a node and its owned dependencies with fresh graph identities.

    Parameters
    ----------
    archive : Archive
        Destination, modified in place.
    origin : Archive
        Archive holding the source node.
    root : Instance
        Program, step, or directory to copy.
    parent : Instance
        Destination parent for a step or owned dependency. Directory instance
        parents remain the structure root; program placement is registered
        separately in the directory tree without an instance parent.

    Returns
    -------
    tuple
        Refreshed archive, new root element id, and identity remapping.

    Raises
    ------
    ValueError
        If releases differ or a dependency cannot be resolved.
    """
    if origin.major_version != archive.major_version:
        raise ValueError("donor and destination archive releases differ")
    elements = _closure(origin, [root], stop=set())
    missing = elements - set(origin.by_element)
    if missing:
        raise ValueError(f"unresolved donor dependencies: {', '.join(sorted(missing))}")
    nodes = [origin.by_element[e] for e in sorted(elements)]
    fresh = {
        node.element_id: (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
        for node in nodes
    }
    ids = {}
    for node in nodes:
        version, element, obj = fresh[node.element_id]
        ids.update({node.id: version, node.element_id: element, node.object_id: obj})
    instance_rows = {str(r["Id"]): r for r in origin.container.rows("Instance")}
    element_rows = {str(r["Id"]): r for r in origin.container.rows("Element")}
    for node in nodes:
        version, element, obj = fresh[node.element_id]
        row = dict(instance_rows[node.id])
        row.update(Id=version, Element_id=element, ObjectId=obj)
        row["Children"] = pack_guids([ids[c] for c in node.children])
        for key in ("LabelElement_id", "DescriptionElement_id", "ParentElementId"):
            if row.get(key) in ids:
                row[key] = ids[row[key]]
        if node.element_id == root.element_id:
            # Directory hierarchy is encoded in EdfStructure, not through
            # Instance parents. Directories are structure children; programs
            # are standalone roots whose placement lives only in that tree.
            if root.kind == DIRECTORY:
                row["ParentElementId"] = archive.tree_root.element_id
            elif root.kind != PROGRAM:
                row["ParentElementId"] = parent.element_id
        elif root.kind == PROGRAM and node.kind in STEP_KINDS and node.element_id in root.children:
            # A console program may reuse a step belonging to another program.
            # Every copied step is now owned by the newly copied program.
            row["ParentElementId"] = ids[root.element_id]
        if node.content_hash:
            generate._adopt(archive, origin.contents[node.content_hash])
        archive.container.tables["Instance"].append(row)
        declaration = dict(element_rows[node.element_id])
        declaration["Id"] = element
        if declaration.get("CommentElement_id") in ids:
            declaration["CommentElement_id"] = ids[declaration["CommentElement_id"]]
        archive.container.tables["Element"].append(declaration)
        archive.container.tables["InstanceChangeSet"].append(
            {"InstanceId": version, "ChangeSetId": archive.head, "ElementId": element, "State": 0}
        )
    generate._extend_map(archive, fresh)
    archive = refresh(archive)
    for node in nodes:
        if not node.content_hash:
            continue
        document = origin.document(node)
        remapped = _remap(document, ids)
        if remapped != document:
            if node.kind == PROGRAM:
                remapped = generate.renumber_references(generate.sort_step_maps(remapped))
            archive.replace_content(archive.by_element[ids[node.element_id]], remapped)
    return refresh(archive), ids[root.element_id], ids


def _children(archive: Archive, node: Instance, children: Sequence[str]) -> None:
    """Update both the stored and decoded children of one live node.

    Parameters
    ----------
    archive : Archive
        Archive to edit.
    node : Instance
        Node holding the children.
    children : sequence of str
        New child element ids.

    Returns
    -------
    None
    """
    table = archive.container.tables["Instance"]
    table.set(table.find("Id", node.id)[0], "Children", pack_guids(list(children)))
    node.children = list(children)


def _tree(archive: Archive, node: Instance, parent: Instance) -> None:
    """Register a new directory or program in every mirrored tree map.

    Parameters
    ----------
    archive : Archive
        Destination archive.
    node : Instance
        New directory or program.
    parent : Instance
        Parent directory.

    Returns
    -------
    None
    """
    root = archive.tree_root
    if root is None:
        raise ValueError("archive has no folder structure")
    document = archive.document(root)
    key = node.object_id if node.kind == DIRECTORY else node.element_id
    document["ParentDirectoryId"][key] = parent.object_id
    if node.kind == DIRECTORY:
        for table in ("SubdirectoryIds", "SubprogramElementIds"):
            document[table][key] = {"$id": f"new-{table}-{key}", "$values": []}
        document["SubdirectoryIds"][parent.object_id]["$values"].append(key)
        _children(archive, root, root.children + [node.element_id])
    else:
        document["SubprogramElementIds"][parent.object_id]["$values"].append(key)
        document["ProgramElementIds"]["$values"].append(key)
    archive.replace_content(root, generate.renumber_references(document))


def ensure_directory(archive: Archive, path: Sequence[str]) -> tuple[Archive, str]:
    """Resolve or create a directory path beneath the archive's root.

    Parameters
    ----------
    archive : Archive
        Destination archive.
    path : sequence of str
        Directory names, optionally beginning with the existing root name.

    Returns
    -------
    tuple
        Refreshed archive and destination directory element id.

    Raises
    ------
    ValueError
        If the tree is absent, a name is empty, or a directory is ambiguous.
    """
    parent = archive.by_object.get(archive.declared_root)
    if parent is None or parent.kind != DIRECTORY:
        raise ValueError("archive has no root directory")
    names = list(path)
    if names and names[0].casefold() == archive.label_of(parent).casefold():
        names.pop(0)
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("directory names must be nonempty strings")
        children = archive.directory_children.get(parent.object_id, [])
        matches = [
            archive.by_object[child]
            for child in children
            if child in archive.by_object
            and archive.label_of(archive.by_object[child]).casefold() == name.casefold()
        ]
        if len(matches) > 1:
            raise ValueError(f"ambiguous directory {name!r}")
        if matches:
            parent = matches[0]
            continue
        template = next(
            n for n in archive.instances.values() if n.kind == DIRECTORY and not n.children
        )
        parent_id = parent.element_id
        archive, new, _ = copy_graph(archive, archive, template, parent)
        parent = archive.by_element[parent_id]
        node = archive.by_element[new]
        generate.rename(archive, node, name)
        _tree(archive, node, parent)
        archive = refresh(archive)
        parent = archive.by_element[new]
    return archive, parent.element_id


def copy_program(
    archive: Archive,
    origin: Archive,
    program: Instance,
    parent: Instance,
    name: str,
    *,
    include_steps: bool = True,
) -> tuple[Archive, str]:
    """Copy an entire program with its pauses, relations, and add-ins.

    Parameters
    ----------
    archive, origin : Archive
        Destination and donor archives.
    program : Instance
        Donor program.
    parent : Instance
        Destination directory.
    name : str
        Name of the copied program.
    include_steps : bool, optional
        Copy the complete running order by default. False copies only program
        metadata and non-step dependencies, for linear donor assembly.

    Returns
    -------
    tuple
        Refreshed archive and new program element id.
    """
    if not include_steps:
        origin = checkout(origin)
        program = origin.by_element[program.element_id]
        origin = delete_steps(origin, program, origin.steps_of(program))
        program = origin.by_element[program.element_id]
    archive, new, _ = copy_graph(archive, origin, program, parent)
    node = archive.by_element[new]
    generate.rename(archive, node, name)
    _tree(archive, node, archive.by_element[parent.element_id])
    return refresh(archive), new


def relations(archive: Archive, program: Instance) -> list[dict[str, Any]]:
    """Read full relation records in their stored creation order.

    Parameters
    ----------
    archive : Archive
        Archive to inspect.
    program : Instance
        Program holding the relation graph.

    Returns
    -------
    list of dict
        Independent copies of all outgoing relations.
    """
    document = archive.document(program)
    return [
        copy.deepcopy(relation)
        for key, entry in document.get("RelationsFrom", {}).items()
        if key != "$id"
        for relation in entry.get("$values", [])
    ]


def set_relations(archive: Archive, program: Instance, records: Sequence[dict[str, Any]]) -> None:
    """Rebuild both relation maps, retaining every supplied relation payload.

    Parameters
    ----------
    archive : Archive
        Destination archive.
    program : Instance
        Program to edit.
    records : sequence of dict
        Full relation records, whose endpoints must belong to this program.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If a relation names a step outside the program.
    """
    document = archive.document(program)
    step_ids = archive.step_order(program)
    for table, prefix in (("RelationsFrom", "rf"), ("RelationsTo", "rt")):
        document[table] = {"$id": table}
        document[table].update({s: {"$id": f"{prefix}-{s}", "$values": []} for s in step_ids})
    for index, supplied in enumerate(records):
        relation = copy.deepcopy(supplied)
        source, target = relation["SourceId"], relation["TargetId"]
        if source not in step_ids or target not in step_ids:
            raise ValueError("relation endpoint is outside the destination program")
        marker = f"relation-{index}"
        relation["$id"] = marker
        document["RelationsFrom"][source]["$values"].append(relation)
        document["RelationsTo"][target]["$values"].append({"$ref": marker})
    archive.replace_content(
        program, generate.renumber_references(generate.sort_step_maps(document))
    )


def _linear(archive: Archive, program: Instance) -> None:
    """Reject execution metadata that a linear-order editor cannot preserve.

    Parameters
    ----------
    archive : Archive
        Archive to inspect.
    program : Instance
        Program whose running order will change.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If the program has conditions, groups, or conditional edges.
    """
    document = archive.document(program)
    for key in ("Conditions", "Groups", "Selections"):
        if any(k != "$id" and v for k, v in document.get(key, {}).items()):
            raise ValueError(f"cannot reorder a program with nonempty {key}")
    for key, entry in document.get("LinksFrom", {}).items():
        if key == "$id":
            continue
        edges = entry.get("$values", [])
        if len(edges) > 1 or any(
            edge.get(flag) not in (None, generate.NO_GUID)
            for edge in edges
            for flag in ("ConditionId", "SelectionId")
        ):
            raise ValueError("cannot rewrite a conditional or branching running order")


def set_order(archive: Archive, program: Instance, steps: Sequence[Instance]) -> None:
    """Set a linear running order and rebuild all five step-keyed maps.

    Parameters
    ----------
    archive : Archive
        Archive to edit.
    program : Instance
        Program holding the steps.
    steps : sequence of Instance
        Desired ordered step nodes, including pauses.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If steps repeat or the program has unsupported execution metadata.
    """
    _linear(archive, program)
    ids = [s.object_id for s in steps]
    if len(ids) != len(set(ids)):
        raise ValueError("a step cannot occur twice in a program's running order")
    old_relations = [
        r for r in relations(archive, program) if r["SourceId"] in ids and r["TargetId"] in ids
    ]
    document = archive.document(program)
    for table in generate.STEP_KEYED_MAPS:
        document[table] = {"$id": table}
    for index, step_id in enumerate(ids):
        for table in ("LinksFrom", "LinksTo", "RelationsFrom", "RelationsTo"):
            document[table][step_id] = {"$id": f"{table}-{step_id}", "$values": []}
        document["Ranks"][step_id] = {"$id": f"rank-{step_id}", "Rank": index, "StepId": step_id}
        if index + 1 < len(ids):
            marker = f"edge-{index}"
            document["LinksFrom"][step_id]["$values"].append(
                {
                    "$id": marker,
                    "$type": generate.LINK_TYPE,
                    "ConditionId": generate.NO_GUID,
                    "SelectionId": generate.NO_GUID,
                    "SourceId": step_id,
                    "TargetId": ids[index + 1],
                }
            )
            # The target's entry is initialized by the next iteration.
    for index, step_id in enumerate(ids[1:]):
        document["LinksTo"][step_id]["$values"].append({"$ref": f"edge-{index}"})
    document["FirstStepId"] = ids[0] if ids else None
    document["LastStepId"] = ids[-1] if ids else None
    nonsteps = [e for e in program.children if archive.by_element[e].kind not in STEP_KINDS]
    _children(archive, program, nonsteps + [s.element_id for s in steps])
    archive.replace_content(
        program, generate.renumber_references(generate.sort_step_maps(document))
    )
    set_relations(archive, program, old_relations)


def _reparent_shared(archive: Archive, elements: set[str]) -> None:
    """Keep shared steps parented to a program that still runs them.

    Parameters
    ----------
    archive : Archive
        Archive after program membership changes.
    elements : set of str
        Step elements whose ownership may have changed.

    Returns
    -------
    None
    """
    programs = [n for n in archive.instances.values() if n.kind == PROGRAM]
    table = archive.container.tables["Instance"]
    for element in elements:
        node = archive.by_element[element]
        owners = [p.element_id for p in programs if element in p.children]
        if not owners:
            continue
        at = table.find("Id", node.id)[0]
        parent = table.rows[at][table.index_of("ParentElementId")]
        if parent not in owners:
            table.set(at, "ParentElementId", owners[0])


def delete_steps(archive: Archive, program: Instance, steps: Sequence[Step]) -> Archive:
    """Remove steps and unshared dependencies from the edit head only.

    Parameters
    ----------
    archive : Archive
        Archive over an isolated edit head.
    program : Instance
        Program to edit.
    steps : sequence of Step
        Steps to remove. Callers must decide how external links are handled.

    Returns
    -------
    Archive
        Refreshed archive, retaining historical versions of removed nodes.
    """
    gone = {s.instance.element_id for s in steps}
    owned = _closure(archive, [s.instance for s in steps], stop=set())
    kept = [s.instance for s in archive.steps_of(program) if s.instance.element_id not in gone]
    set_order(archive, program, kept)
    roots = [
        n for n in archive.instances.values() if n.kind in (PROGRAM, DIRECTORY, "EdfStructure")
    ]
    retained = _closure(archive, roots, stop=set())
    _reparent_shared(archive, gone & retained)
    dropped = owned - retained
    table = archive.container.tables["ElementToInstanceMap"]
    position = table.find("Id", generate._head_map_id(archive))[0]
    blob = bytes(table.rows[position][table.index_of("Data")])
    table.set(
        position,
        "Data",
        b"".join(
            blob[i : i + 32]
            for i in range(0, len(blob), 32)
            if str(uuid.UUID(bytes_le=blob[i : i + 16])) not in dropped
        ),
    )
    # These isolated-head versions were staged by this session and never
    # committed at another head. They must no longer be reported as touched
    # by the new head; historical records and versions remain intact.
    versions = {archive.by_element[e].id for e in dropped}
    archive.container.tables["InstanceChangeSet"].discard("InstanceId", versions)
    return refresh(archive)


def copy_steps(
    archive: Archive,
    program: Instance,
    donors: Sequence[tuple[Archive, Instance, Step]],
    *,
    position: int | None = None,
    external_links: str = "reject",
) -> tuple[Archive, list[str], list[dict[str, Any]]]:
    """Insert donor steps, preserving dependencies and links internal to the group.

    Parameters
    ----------
    archive : Archive
        Destination archive.
    program : Instance
        Destination program.
    donors : sequence of tuple
        Donor archive, owning program, and step, in desired order.
    position : int or None, optional
        Insertion index in the complete running order. None appends.
    external_links : str, optional
        ``reject`` refuses boundary relations; ``drop`` records their omission.

    Returns
    -------
    tuple
        Refreshed archive, copied element ids, and deliberately dropped relations.

    Raises
    ------
    ValueError
        If selection is empty, repeated, incompatible, or loses unapproved links.
    """
    if not donors or external_links not in {"reject", "drop"}:
        raise ValueError("choose donor steps and an external-links policy of reject or drop")
    originals = archive.steps_of(program)
    at = len(originals) if position is None else position
    if not isinstance(at, int) or isinstance(at, bool) or not 0 <= at <= len(originals):
        raise ValueError("insertion position is outside the running order")
    groups: dict[tuple[int, str], dict[str, Any]] = {}
    for origin, owner, step in donors:
        group = groups.setdefault(
            (id(origin), owner.element_id), {"archive": origin, "program": owner, "steps": {}}
        )
        if step.instance.object_id in group["steps"]:
            raise ValueError("donor steps must be unique within one copy group")
        if step.instance.element_id not in {s.instance.element_id for s in origin.steps_of(owner)}:
            raise ValueError("donor step does not belong to its stated program")
        group["steps"][step.instance.object_id] = None
    dropped = []
    for group in groups.values():
        selected = set(group["steps"])
        group["relations"] = []
        for record in relations(group["archive"], group["program"]):
            endpoints = {record["SourceId"], record["TargetId"]}
            if not endpoints & selected:
                continue
            if endpoints <= selected:
                group["relations"].append(record)
            elif external_links == "reject":
                raise ValueError(
                    "donor selection crosses a relation; include its endpoints or explicitly drop external links"
                )
            else:
                dropped.append(copy.deepcopy(record))
    new = []
    program_id = program.element_id
    for origin, owner, step in donors:
        archive, element, _ = copy_graph(
            archive, origin, step.instance, archive.by_element[program_id]
        )
        new.append(element)
        groups[(id(origin), owner.element_id)]["steps"][step.instance.object_id] = (
            archive.by_element[element].object_id
        )
    program = archive.by_element[program_id]
    desired = [archive.by_element[s.instance.element_id] for s in originals]
    desired[at:at] = [archive.by_element[e] for e in new]
    set_order(archive, program, desired)
    records = relations(archive, program)
    for group in groups.values():
        for record in group["relations"]:
            remapped = copy.deepcopy(record)
            for key in ("SourceId", "TargetId"):
                remapped[key] = group["steps"][record[key]]
            records.append(remapped)
    set_relations(archive, program, records)
    return refresh(archive), new, dropped


def detach_step(archive: Archive, program: Instance, step: Step) -> tuple[Archive, str]:
    """Give a program its own copy of a shared step without changing its order.

    Parameters
    ----------
    archive : Archive
        Isolated edit archive.
    program : Instance
        Program that will own the independent step.
    step : Step
        Shared step to replace in that program alone.

    Returns
    -------
    tuple
        Refreshed archive and replacement element id. All execution and
        relation metadata is preserved, including conditional edges.
    """
    document = archive.document(program)
    children = list(program.children)
    archive, element, ids = copy_graph(archive, archive, step.instance, program)
    program = archive.by_element[program.element_id]
    _children(
        archive, program, [element if c == step.instance.element_id else c for c in children]
    )
    archive.replace_content(
        program, generate.renumber_references(generate.sort_step_maps(_remap(document, ids)))
    )
    _reparent_shared(archive, {step.instance.element_id})
    return refresh(archive), element


def add_pause(
    archive: Archive, program: Instance, name: str, position: int
) -> tuple[Archive, str]:
    """Insert a plain instruction pause without contrast-agent actions.

    Parameters
    ----------
    archive : Archive
        Destination archive with at least one labelled step.
    program : Instance
        Destination program.
    name : str
        Instruction displayed at the scanner.
    position : int
        Zero-based position, counting scans and pauses.

    Returns
    -------
    tuple
        Refreshed archive and pause element id.

    Raises
    ------
    ValueError
        If the instruction, position, or template is unavailable.
    """
    steps = archive.steps_of(program)
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(position, int)
        or isinstance(position, bool)
        or not 0 <= position <= len(steps)
    ):
        raise ValueError("pause needs a nonempty instruction and valid insertion position")
    template = next(
        (n for n in archive.instances.values() if n.kind in STEP_KINDS and n.label_element_id),
        None,
    )
    if template is None:
        raise ValueError("archive has no labelled step to supply the pause label format")
    version, element, obj = (str(uuid.uuid4()) for _ in range(3))
    seed = Envelope(
        content_type="syngo.MR.ExamDataFoundation.Data.EdfPauseStepContent", payload=b"{}"
    )
    generate._adopt(archive, seed)
    row = dict(next(r for r in archive.container.rows("Instance") if r["Id"] == template.id))
    row.update(
        Id=version,
        Element_id=element,
        ObjectId=obj,
        InstanceType="EdfPauseStep",
        Children=None,
        ContentHash=seed.hash,
        ParentElementId=program.element_id,
        DescriptionElement_id=None,
    )
    archive.container.tables["Instance"].append(row)
    archive.container.tables["Element"].append(
        {"Id": element, "Type": 5, "CommentElement_id": None}
    )
    archive.container.tables["InstanceChangeSet"].append(
        {"InstanceId": version, "ChangeSetId": archive.head, "ElementId": element, "State": 0}
    )
    generate._extend_map(archive, {element: (version, element, obj)})
    archive = refresh(archive)
    archive, label, _ = copy_graph(
        archive,
        archive,
        archive.by_element[template.label_element_id],
        archive.by_element[element],
    )
    table = archive.container.tables["Instance"]
    table.set(table.find("Id", version)[0], "LabelElement_id", label)
    archive = refresh(archive)
    node = archive.by_element[element]
    archive.replace_content(
        node,
        {
            "$id": "1",
            "ApplyContrastAgent": False,
            "ContrastAgent": {
                "$id": "2",
                "ActiveIngredient": "",
                "CodeValue": "",
                "CodingScheme": "",
                "Comment": "",
                "Concentration": "",
                "ConcentrationUnit": "mg/ml",
                "Dilution": "",
                "IsGiven": False,
                "Name": "",
                "TotalDose": "",
                "Volume": "",
            },
            "ContrastAgentName": "",
            "OpenInjectorControl": False,
        },
    )
    generate.rename(archive, node, name)
    order = [archive.by_element[s.instance.element_id] for s in steps]
    order.insert(position, node)
    set_order(archive, archive.by_element[program.element_id], order)
    return refresh(archive), element
