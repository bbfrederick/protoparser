"""Check an archive against the structure a console-authored one has.

Self-consistency is not enough, and this module exists because of two defects
that proved it. Both times the archive was internally coherent -- every
reference resolved, every id was unique -- and both times the console rejected
or silently corrupted it. A field can be populated, well-formed and wrong,
because what it means is relational: ``ParentElementId`` on a copied protocol
pointed at a real step that simply was not its own.

So the checks here are stated as *relationships that hold in every archive a
console wrote*, and are re-derived from the archive under test rather than
assumed. :func:`problems` returns what does not hold, most specific first, and
an empty list is the only passing result.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from . import envelope
from .archive import MEASUREMENT_STEP, Archive
from .generate import NO_GUID, STEP_KEYED_MAPS

#: Matches a GUID as these payloads spell one.
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def problems(archive: Archive) -> list[str]:
    """Return every structural rule the archive breaks.

    Parameters
    ----------
    archive : Archive
        The archive to check.

    Returns
    -------
    list of str
        One line per broken rule, empty when the archive is sound.
    """
    found: list[str] = []
    program = archive.program
    if program is None:
        return ["archive has no program node"]
    document = archive.document(program)
    found += _program_maps(archive, document)
    found += _running_order(archive, document)
    found += _references(document)
    found += _parents(archive)
    found += _identity(archive)
    return found


def _program_maps(archive: Archive, document: dict[str, Any]) -> list[str]:
    """Every step must appear in every step-keyed map.

    Parameters
    ----------
    archive : Archive
        The archive under test.
    document : dict
        The decoded program content.

    Returns
    -------
    list of str
        Broken rules.
    """
    steps = {step.instance.object_id for step in archive.steps}
    found = []
    for name in STEP_KEYED_MAPS:
        table = document.get(name)
        if not isinstance(table, dict):
            found.append(f"program content has no {name} map")
            continue
        keys = {k for k in table if k != "$id"}
        missing = steps - keys
        if missing:
            found.append(f"{name} is missing {len(missing)} step(s): {sorted(missing)[:3]}")
        stray = {k for k in keys if GUID.match(k)} - steps
        if stray:
            found.append(f"{name} names {len(stray)} step(s) that do not exist")
    return found


def _running_order(archive: Archive, document: dict[str, Any]) -> list[str]:
    """Ranks number the steps 0..N, and the chain spans them all.

    Parameters
    ----------
    archive : Archive
        The archive under test.
    document : dict
        The decoded program content.

    Returns
    -------
    list of str
        Broken rules.
    """
    found = []
    order = archive.step_order()
    # Count the step nodes independently of the chain. ``archive.steps`` is
    # derived by walking the chain, so comparing the two would be circular: a
    # chain that stops early would agree with itself and the break would show
    # up only as unrelated complaints about the maps.
    existing = sum(1 for i in archive.instances.values() if i.kind == MEASUREMENT_STEP)
    if len(order) != existing:
        found.append(f"link chain covers {len(order)} steps but {existing} exist")
    if order and document.get("FirstStepId") != order[0]:
        found.append("FirstStepId is not where the chain starts")
    if order and document.get("LastStepId") != order[-1]:
        found.append("LastStepId is not where the chain ends")
    ranks = sorted(v["Rank"] for k, v in document.get("Ranks", {}).items() if k != "$id")
    if ranks and ranks != list(range(len(ranks))):
        found.append(f"Ranks are not 0..{len(ranks) - 1} without gaps")
    if len(archive.program.children) != len(archive.steps):
        found.append(
            f"program lists {len(archive.program.children)} children "
            f"for {len(archive.steps)} steps"
        )
    return found


def _references(document: dict[str, Any]) -> list[str]:
    """Newtonsoft ``$ref`` values resolve, and ``$id`` values are unique.

    Parameters
    ----------
    document : dict
        The decoded program content.

    Returns
    -------
    list of str
        Broken rules.
    """
    ids: list[str] = []
    refs: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "$id" in node:
                ids.append(str(node["$id"]))
            if "$ref" in node:
                refs.append(str(node["$ref"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    found = []
    if len(ids) != len(set(ids)):
        found.append("program content repeats a $id")
    dangling = set(refs) - set(ids)
    if dangling:
        found.append(f"program content has {len(dangling)} unresolved $ref")
    return found


def _parents(archive: Archive) -> list[str]:
    """A protocol and a label parent to their step; a step to the program.

    This is the rule a duplicated scan breaks by inheriting its source's
    pointer, and the console resolves a step's protocol through it -- so the
    copy is served the original's protocol and any edit to it disappears.

    Parameters
    ----------
    archive : Archive
        The archive under test.

    Returns
    -------
    list of str
        Broken rules.
    """
    rows = {str(row["Id"]): row for row in archive.container.rows("Instance")}
    program = archive.program
    found = []
    for step in archive.steps:
        expected = {
            "protocol": (step.protocol.instance, step.instance.element_id),
            "step": (step.instance, program.element_id),
        }
        holder = archive.by_element.get(step.instance.label_element_id)
        if holder is not None:
            expected["label"] = (holder, step.instance.element_id)
        for tag, (node, wanted) in expected.items():
            actual = rows[node.id]["ParentElementId"]
            if actual is not None and str(actual) != wanted:
                found.append(f"{step.name}: its {tag} parents to another node, not {tag}'s own")
    return found


def _identity(archive: Archive) -> list[str]:
    """Ids are unique, the checkout index resolves, content hashes hold.

    Parameters
    ----------
    archive : Archive
        The archive under test.

    Returns
    -------
    list of str
        Broken rules.
    """
    rows = archive.container.rows("Instance")
    ids = [str(row["Id"]) for row in rows]
    elements = {str(row["Id"]) for row in archive.container.rows("Element")}
    found = []
    if len(ids) != len(set(ids)):
        found.append("Instance.Id is not unique")
    absent = {str(row["Element_id"]) for row in rows} - elements
    if absent:
        found.append(f"{len(absent)} instance(s) reference a missing Element row")
    for row in rows:
        digest = row["ContentHash"]
        if digest is not None and str(digest) not in archive.contents:
            found.append(f"instance {str(row['Id'])[:8]} points at missing content")
            break
    for stored, content in archive.contents.items():
        rebuilt = envelope.Envelope(
            content_type=content.content_type, payload=envelope.dumps(content.decode())
        )
        if rebuilt.hash != stored:
            found.append(f"{content.kind} does not re-encode to its own address")
            break
    return found
