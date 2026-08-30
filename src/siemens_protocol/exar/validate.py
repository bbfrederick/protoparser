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
from .archive import STEP_KINDS, Archive, Program
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
    programs = archive.programs
    if not programs:
        return ["archive has no program node"]
    for program in programs:
        # A backup holds one program per protocol, and they are independent
        # running orders. Checking only the first describes a fraction of the
        # file while reporting on all of it.
        document = archive.document(program.instance)
        where = f"{program.name}: " if len(programs) > 1 else ""
        found += [where + line for line in _program_maps(program, document)]
        found += [where + line for line in _running_order(archive, program, document)]
        found += [where + line for line in _references(document)]
    found += _step_coverage(archive, programs)
    found += _parents(archive)
    found += _identity(archive)
    return found


def _step_coverage(archive: Archive, programs: list[Program]) -> list[str]:
    """Every live step belongs to exactly one program's running order.

    Counted independently of the chains, which is the whole point: a chain
    that stops early agrees with itself, so only a tally taken from the
    instance table can notice steps nothing runs.

    Parameters
    ----------
    archive : Archive
        The archive under test.
    programs : list of Program
        Its programs, already walked.

    Returns
    -------
    list of str
        Broken rules.
    """
    existing = {i.object_id for i in archive.instances.values() if i.kind in STEP_KINDS}
    seen: list[str] = [step.instance.object_id for one in programs for step in one.steps]
    found = []
    orphaned = existing - set(seen)
    if orphaned:
        found.append(f"{len(orphaned)} step(s) are in no program's running order")
    if len(seen) != len(set(seen)):
        found.append(f"{len(seen) - len(set(seen))} step(s) are claimed by two programs")
    return found


def _program_maps(program: Program, document: dict[str, Any]) -> list[str]:
    """Every step must appear in every step-keyed map.

    Parameters
    ----------
    program : Program
        The program under test, with its own steps. Comparing against every
        step in the *archive* would report each program as missing the other
        programs' steps.
    document : dict
        The decoded program content.

    Returns
    -------
    list of str
        Broken rules.
    """
    steps = {step.instance.object_id for step in program.steps}
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


def _running_order(archive: Archive, program: Program, document: dict[str, Any]) -> list[str]:
    """Ranks number the steps 0..N, and the chain spans the program's children.

    The chain is compared against the program's own ``Children`` blob rather
    than against a tally of every step in the archive. That tally is the right
    check for a single-protocol export and the wrong one for a backup, where
    each program legitimately runs a fraction of the file's steps; the
    archive-wide version is :func:`_step_coverage`.

    Parameters
    ----------
    archive : Archive
        The archive under test.
    program : Program
        The program under test.
    document : dict
        The decoded program content.

    Returns
    -------
    list of str
        Broken rules.
    """
    found = []
    order = archive.step_order(program.instance)
    if len(order) != len(program.instance.children):
        found.append(
            f"link chain covers {len(order)} steps but the program "
            f"lists {len(program.instance.children)} children"
        )
    if order and document.get("FirstStepId") != order[0]:
        found.append("FirstStepId is not where the chain starts")
    if order and document.get("LastStepId") != order[-1]:
        found.append("LastStepId is not where the chain ends")
    ranks = sorted(v["Rank"] for k, v in document.get("Ranks", {}).items() if k != "$id")
    if ranks and ranks != list(range(len(ranks))):
        found.append(f"Ranks are not 0..{len(ranks) - 1} without gaps")
    if len(ranks) != len(order):
        found.append(f"Ranks number {len(ranks)} steps for a chain of {len(order)}")
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
    found = []
    # Each step parents to the program that runs *it*. In a backup the wrong
    # program is a real node with a real element id, so a check written
    # against a single "the program" would pass on nine steps out of ten and
    # fail on the rest for the wrong reason.
    for one in archive.programs:
        for step in one.steps:
            found += _step_parents(archive, rows, step, one.instance.element_id)
    return found


def _step_parents(
    archive: Archive, rows: dict[str, Any], step: Any, program_element: str
) -> list[str]:
    """Check one step's protocol, label and own parent pointers.

    Parameters
    ----------
    archive : Archive
        The archive under test.
    rows : dict
        Instance rows keyed by id.
    step : Step
        The step to check.
    program_element : str
        Element id of the program that runs this step.

    Returns
    -------
    list of str
        Broken rules.
    """
    expected = {"step": (step.instance, program_element)}
    if step.runs_a_protocol:
        expected["protocol"] = (step.protocol.instance, step.instance.element_id)
    holder = archive.by_element.get(step.instance.label_element_id)
    if holder is not None:
        expected["label"] = (holder, step.instance.element_id)
    found = []
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
