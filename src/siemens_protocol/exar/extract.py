"""Copy chosen protocols out of an ``.exar1`` archive into a new one.

A backup taken at the exam or region level holds many protocols, and the
usual reason to open one is to take a few of them somewhere else. This builds
a *new* archive holding only those: a fresh :class:`.store.Container` with
the source's schema, into which the rows the chosen protocols need are
copied. The source is only read, so it is left exactly as it was.

What a protocol needs is a subgraph in **element** space -- the program node,
its steps through ``Children``, each step's protocol and ``EdfAddInConfig``,
and every node's label -- plus the directories above it and the root
``EdfStructure``. Element space rather than object space because a copied
program keeps its source's step ``ObjectId``s, so two programs of one backup
can share an object id and resolving through it takes the other program's
step.

Everything is copied verbatim except the one node whose content *describes*
the whole file: the root structure's document, which states the folder tree
four ways (``ParentDirectoryId`` upwards, ``SubdirectoryIds`` and
``SubprogramElementIds`` downwards, and a flat ``ProgramElementIds``), and its
``Children`` blob, which lists the directories. Those five are filtered
alike, so the two directions of the tree still agree, which is what
:func:`.validate.problems` checks.

History is kept for what is kept. Every console archive carries a superseded
version of its structure node in an earlier changeset, and every version of
each kept element is copied, with each changeset's element map filtered to
kept elements, so each changeset still resolves -- to its own view of the
chosen part of the tree.

Nothing here has yet been shown to a scanner. The protocols travel as
byte-identical content rows, which is the part the console judges, and an
earlier import of a 49-scan generated archive came back byte-identical. But
an archive assembled by filtering has not been imported yet, and only an
import can say whether one loads.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable

from . import store
from .archive import Archive, Instance, from_container, pack_guids, unpack_guids
from .generate import renumber_references

#: The structure document's maps keyed by a directory or program id.
KEYED_TREE_MAPS = ("ParentDirectoryId", "SubdirectoryIds", "SubprogramElementIds")

#: The structure document's flat list of every program's element id.
PROGRAM_LIST = "ProgramElementIds"


def extract(source: Archive, programs: Iterable[Instance]) -> Archive:
    """Build a new archive holding only some of a source's protocols.

    Parameters
    ----------
    source : Archive
        The archive to copy from. It is read and never modified.
    programs : iterable of Instance
        The program nodes to keep, each one of ``source.program_nodes``.

    Returns
    -------
    Archive
        A new archive over a new container, ready for :meth:`Archive.write`.
        Its folder tree is the directories above the chosen protocols, with
        the same names and ids as in the source.

    Raises
    ------
    ValueError
        If no program is given, or one is not a program of ``source``.
    """
    chosen = _chosen(source, programs)
    dirs = _ancestor_directories(source, chosen)
    dropped = _dropped_roots(source, chosen, dirs)
    keep = _kept_elements(source, chosen, dirs, dropped)
    container = _copy_rows(source, keep)
    result = from_container(container)
    _prune_tree(result, dropped)
    return result


def _chosen(source: Archive, programs: Iterable[Instance]) -> list[Instance]:
    """Check the requested programs belong to the source.

    Parameters
    ----------
    source : Archive
        The archive to copy from.
    programs : iterable of Instance
        The program nodes requested.

    Returns
    -------
    list of Instance
        The requested programs, deduplicated, in the source's order.

    Raises
    ------
    ValueError
        If none is given, or one is not a live program node of ``source``.
    """
    wanted = {one.element_id for one in programs}
    if not wanted:
        raise ValueError("no protocol was chosen to extract")
    nodes = source.program_nodes
    known = {one.element_id for one in nodes}
    stray = sorted(wanted - known)
    if stray:
        raise ValueError(f"not a protocol of this archive: {', '.join(stray)}")
    return [one for one in nodes if one.element_id in wanted]


def _ancestor_directories(source: Archive, chosen: list[Instance]) -> list[Instance]:
    """Return every directory above a chosen program, root included.

    Parameters
    ----------
    source : Archive
        The archive to copy from.
    chosen : list of Instance
        The programs being kept.

    Returns
    -------
    list of Instance
        Each ancestor directory once, in the order first reached.
    """
    parents = source.directory_parents
    found: dict[str, Instance] = {}
    for program in chosen:
        current = source.parent_of(program, parents)
        while current is not None and current.element_id not in found:
            found[current.element_id] = current
            current = source.parent_of(current, parents)
    return list(found.values())


def _dropped_roots(
    source: Archive, chosen: list[Instance], dirs: list[Instance]
) -> list[Instance]:
    """Return the programs and directories the new archive leaves out.

    Parameters
    ----------
    source : Archive
        The archive to copy from.
    chosen : list of Instance
        The programs being kept.
    dirs : list of Instance
        The directories being kept.

    Returns
    -------
    list of Instance
        Every other live program and directory node.
    """
    kept = {one.element_id for one in chosen} | {one.element_id for one in dirs}
    tree_kinds = {"EdfProgram", "EdfDirectory"}
    return [
        one
        for one in source.instances.values()
        if one.kind in tree_kinds and one.element_id not in kept
    ]


def _kept_elements(
    source: Archive, chosen: list[Instance], dirs: list[Instance], dropped: list[Instance]
) -> set[str]:
    """Return the element ids the new archive holds.

    Everything live is kept except what only a dropped program or directory
    owns. Framing it that way round keeps any node that belongs to no program
    -- the structure's own label, say -- rather than dropping it for not
    having been named, and a node reachable from both sides stays.

    Parameters
    ----------
    source : Archive
        The archive to copy from.
    chosen : list of Instance
        The programs being kept.
    dirs : list of Instance
        The directories being kept.
    dropped : list of Instance
        The programs and directories being left out.

    Returns
    -------
    set of str
        Element ids.
    """
    boundary_out = {one.element_id for one in dropped}
    kept_roots = list(chosen) + list(dirs)
    boundary_in = {one.element_id for one in kept_roots}
    reached = _closure(source, kept_roots, stop=boundary_out)
    owned_elsewhere = _closure(source, dropped, stop=boundary_in) - reached
    live = {one.element_id for one in source.instances.values()}
    return live - owned_elsewhere


def _closure(source: Archive, roots: list[Instance], stop: set[str]) -> set[str]:
    """Return every element reachable from ``roots`` without entering ``stop``.

    A node owns its ``Children``, its label, its description and its comment.
    Upward pointers (``ParentElementId``) are not followed, since they would
    lead from any step to its program and from there to everything.

    Parameters
    ----------
    source : Archive
        The archive to walk.
    roots : list of Instance
        Where to start.
    stop : set of str
        Element ids never entered, which is what keeps a walk from one
        program out of another's nodes.

    Returns
    -------
    set of str
        Element ids, the roots included.
    """
    by_element = source.by_element
    rows = {str(row["Id"]): row for row in source.container.rows("Instance")}
    comments = {
        str(row["Id"]): row.get("CommentElement_id") for row in source.container.rows("Element")
    }
    seen: set[str] = set()
    pending = [one.element_id for one in roots]
    while pending:
        element = pending.pop()
        if element in seen or element in stop:
            continue
        seen.add(element)
        node = by_element.get(element)
        if node is None:
            continue
        row = rows.get(node.id, {})
        linked = [row.get("LabelElement_id"), row.get("DescriptionElement_id")]
        linked.append(comments.get(element))
        pending.extend(node.children)
        pending.extend(str(one) for one in linked if one)
    return seen


def _copy_rows(source: Archive, keep: set[str]) -> store.Container:
    """Copy the rows a set of elements needs into a new container.

    Parameters
    ----------
    source : Archive
        The archive to copy from.
    keep : set of str
        The element ids to carry across.

    Returns
    -------
    store.Container
        A new container with the source's schema and indexes. ``Element``,
        ``Instance`` and ``InstanceChangeSet`` are filtered to ``keep``; each
        ``ElementToInstanceMap`` record likewise; ``Content`` to what a kept
        instance references plus anything no instance references at all;
        every other table -- ``ChangeSet``, ``Branch`` -- is copied whole.
    """
    tables = source.container.tables
    instance_table = tables["Instance"]
    at_element = instance_table.index_of("Element_id")
    at_hash = instance_table.index_of("ContentHash")
    kept_instances = [row for row in instance_table.rows if str(row[at_element]) in keep]
    referenced = {str(row[at_hash]) for row in kept_instances if row[at_hash] is not None}
    anyone = {str(row[at_hash]) for row in instance_table.rows if row[at_hash] is not None}

    fresh = store.Container(indexes=list(source.container.indexes))
    for name, table in tables.items():
        if name == "Instance":
            rows = kept_instances
        elif name == "Element":
            rows = _rows_where(table, "Id", keep)
        elif name == "InstanceChangeSet":
            rows = _rows_where(table, "ElementId", keep)
        elif name == "ElementToInstanceMap":
            rows = [_filtered_map_row(table, row, keep) for row in table.rows]
        elif name == "Content":
            at = table.index_of("Hash")
            rows = [r for r in table.rows if str(r[at]) in referenced or str(r[at]) not in anyone]
        else:
            rows = list(table.rows)
        fresh.tables[name] = store.Table(
            name=table.name, sql=table.sql, columns=list(table.columns), rows=rows
        )
    return fresh


def _rows_where(table: store.Table, column: str, keep: set[str]) -> list[tuple[Any, ...]]:
    """Return the rows whose ``column`` holds one of ``keep``.

    Parameters
    ----------
    table : store.Table
        The table to filter.
    column : str
        The column holding an element id.
    keep : set of str
        The ids to keep.

    Returns
    -------
    list of tuple
        The matching rows, in stored order.
    """
    at = table.index_of(column)
    return [row for row in table.rows if str(row[at]) in keep]


def _filtered_map_row(table: store.Table, row: tuple[Any, ...], keep: set[str]) -> tuple[Any, ...]:
    """Drop the records of one element map whose element is not kept.

    A map is a flat run of 32-byte records, element id then instance id, both
    .NET mixed-endian GUIDs; see :func:`.archive._element_map`.

    Parameters
    ----------
    table : store.Table
        The ``ElementToInstanceMap`` table, for its column positions.
    row : tuple
        One of its rows.
    keep : set of str
        The element ids to keep.

    Returns
    -------
    tuple
        The row with its ``Data`` filtered, record order preserved.
    """
    at = table.index_of("Data")
    raw = bytes(row[at])
    records = [raw[n : n + 32] for n in range(0, len(raw) - 31, 32)]
    kept = b"".join(one for one in records if str(uuid.UUID(bytes_le=one[:16])) in keep)
    out = list(row)
    out[at] = kept
    return tuple(out)


def _prune_tree(result: Archive, dropped: list[Instance]) -> None:
    """Remove the left-out directories and programs from the folder tree.

    Parameters
    ----------
    result : Archive
        The new archive, whose structure node is rewritten in place.
    dropped : list of Instance
        The programs and directories left out, as instances of the *source*;
        only their ids are used.

    Returns
    -------
    None
    """
    root = result.tree_root
    if root is None or not root.content_hash:
        return
    gone = {one.element_id for one in dropped} | {one.object_id for one in dropped}
    document = result.document(root)
    pruned = renumber_references(_pruned_structure(document, gone))
    if pruned != document:
        result.replace_content(root, pruned)
    children = [one for one in root.children if one not in gone]
    if children != root.children:
        rows = result.container.tables["Instance"]
        for position in rows.find("Id", root.id):
            rows.set(position, "Children", pack_guids(children))
        root.children = unpack_guids(pack_guids(children))


def _pruned_structure(document: dict[str, Any], gone: set[str]) -> dict[str, Any]:
    """Filter the structure document's four statements of the tree.

    Parameters
    ----------
    document : dict
        The decoded ``EdfStructureContent``.
    gone : set of str
        Element and object ids of everything left out. The maps key a
        directory by object id and a program by element id, so both are
        needed; GUIDs do not collide across the two spaces.

    Returns
    -------
    dict
        A new document; ``document`` is not modified. ``$id`` numbering is
        left for the caller to renumber.
    """
    out = dict(document)
    for name in KEYED_TREE_MAPS:
        table = document.get(name)
        if not isinstance(table, dict):
            continue
        out[name] = {
            key: _without(value, gone)
            for key, value in table.items()
            if key not in gone and not (isinstance(value, str) and value in gone)
        }
    listed = document.get(PROGRAM_LIST)
    if isinstance(listed, dict):
        out[PROGRAM_LIST] = _without(listed, gone)
    return out


def _without(value: Any, gone: set[str]) -> Any:
    """Drop left-out ids from a Newtonsoft ``{"$values": [...]}`` list.

    Parameters
    ----------
    value : Any
        A map entry: a ``$values`` wrapper, or a scalar passed through.
    gone : set of str
        Ids to drop.

    Returns
    -------
    Any
        The filtered wrapper, or ``value`` unchanged when it is not one.
    """
    if not isinstance(value, dict) or "$values" not in value:
        return value
    return {**value, "$values": [one for one in value["$values"] if one not in gone]}
