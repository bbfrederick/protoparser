"""The folder tree an archive carries, drawn the way ``tree`` draws one.

An ``.exar1`` file is not always one protocol. A backup taken at the exam or
region level holds several -- the XA30 backup in the corpus holds seven, and a
whole-scanner export holds hundreds -- and every command that reads one needs
``--program`` to say which. This module answers the question that raises: what
is in the file, and by what path.

Nothing here establishes anything about the format. The hierarchy is read from
:attr:`..archive.Archive.directory_parents`, which is the same upward map
:meth:`..archive.Archive.path_of` walks, so a path printed here is exactly the
address ``--program`` and ``--scan`` accept. That is the reason for reading the
tree upwards and inverting it rather than reading
:attr:`..archive.Archive.directory_children` downwards: the two agree on every
corpus archive, but only one of them is the map the rest of the tool addresses
by, and a tree that disagreed with ``path_of`` would be a tree whose output
could not be pasted back into a command line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .archive import DIRECTORY, Archive, Instance, Program, Step

#: A folder in the archive's tree, an ``EdfDirectory``.
DIRECTORY_NODE = "directory"

#: A protocol, an ``EdfProgram``. What the scanner calls a Program and what a
#: printout prints as one export.
PROTOCOL_NODE = "protocol"

#: A step that runs a protocol -- a scan, and what the PDF lists as one.
SCAN_NODE = "scan"

#: A step in the running order that acquires nothing: a pause an operator put
#: between scans, an interaction, or the split/join pair bracketing a branch.
STEP_NODE = "step"

#: Drawing characters. ``use_utf8_output`` makes these safe on a redirected
#: Windows stdout, which is where the legacy code page would otherwise refuse
#: them exactly as it refuses the ``×`` these protocols print.
BRANCH = "├── "
LAST_BRANCH = "└── "
TRUNK = "│   "
BLANK = "    "

#: Shown for a node whose label node carries no text. No corpus archive has
#: one, but dropping such a node would orphan everything beneath it, so it is
#: rendered rather than skipped.
UNNAMED = "(unnamed)"


@dataclass(frozen=True)
class Node:
    """One entry in the rendered tree.

    Attributes
    ----------
    name : str
        The node's own label, as the archive stores it.
    kind : str
        One of :data:`DIRECTORY_NODE`, :data:`PROTOCOL_NODE`,
        :data:`SCAN_NODE` or :data:`STEP_NODE`.
    children : tuple of Node
        What sits beneath it. Directories and protocols are sorted by name;
        a protocol's steps keep their running order.
    scans : int or None, optional
        For a protocol, how many of its steps run a protocol. ``None`` on
        every other kind. A protocol's step count and its scan count differ
        wherever an operator put pauses in the running order -- eleven of
        ``CHR-MDD``'s thirty-four steps are pauses -- so the count is worth
        printing beside the children rather than being read off them.
    step_kind : str or None, optional
        For a step that acquires nothing, what it is: ``"pause"``,
        ``"interaction"``, ``"split"``. ``None`` on every other kind and on a
        step that runs a protocol.
    """

    name: str
    kind: str
    children: tuple["Node", ...] = ()
    scans: int | None = None
    step_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this node and everything under it.

        Returns
        -------
        dict
            ``name`` and ``kind`` always; ``scans`` and ``step_kind`` where
            they apply; ``children`` where there are any.
        """
        out: dict[str, Any] = {"name": self.name, "kind": self.kind}
        if self.scans is not None:
            out["scans"] = self.scans
        if self.step_kind is not None:
            out["step_kind"] = self.step_kind
        if self.children:
            out["children"] = [child.to_dict() for child in self.children]
        return out


def short_kind(kind: str) -> str:
    """Name an instance kind the way a reader rather than the file spells it.

    ``EdfPauseStep`` becomes ``pause``. The affixes are stripped rather than
    the six known kinds being enumerated, so a seventh -- ``EdfDecisionStep``
    is in :data:`..archive.STEP_KINDS` and in no corpus archive -- is named
    sensibly instead of being reported as unknown.

    Parameters
    ----------
    kind : str
        An instance kind, such as ``"EdfPauseStep"``.

    Returns
    -------
    str
        The lower-cased middle of it, or the kind unchanged when stripping
        would leave nothing.
    """
    middle = kind
    if middle.startswith("Edf"):
        middle = middle[len("Edf") :]
    if middle.endswith("Step"):
        middle = middle[: -len("Step")]
    return middle.lower() or kind.lower()


def _step_node(step: Step) -> Node:
    """Turn one step of a program's running order into a node.

    Parameters
    ----------
    step : Step
        The step to render.

    Returns
    -------
    Node
        A :data:`SCAN_NODE` when the step runs a protocol, else a
        :data:`STEP_NODE` naming what it is instead. The content is asked
        rather than the instance kind, for the reason
        :attr:`..archive.Step.runs_a_protocol` gives: a measurement step need
        not carry a protocol.
    """
    if step.runs_a_protocol:
        return Node(name=step.name or UNNAMED, kind=SCAN_NODE)
    return Node(
        name=step.name or UNNAMED,
        kind=STEP_NODE,
        step_kind=short_kind(step.instance.kind),
    )


def _protocol_node(program: Program, *, scans: bool) -> Node:
    """Turn one protocol into a node, optionally carrying its running order.

    Parameters
    ----------
    program : Program
        The protocol to render.
    scans : bool
        Whether to descend into its steps.

    Returns
    -------
    Node
        The protocol, with its scan count and its steps when asked for.
    """
    return Node(
        name=program.name or UNNAMED,
        kind=PROTOCOL_NODE,
        children=tuple(_step_node(step) for step in program.steps) if scans else (),
        scans=sum(1 for step in program.steps if step.runs_a_protocol),
    )


def _by_parent(
    archive: Archive, nodes: Sequence[Instance], parents: dict[str, str]
) -> tuple[dict[str, list[Instance]], list[Instance]]:
    """Group directories and protocols under the directory holding each.

    Parameters
    ----------
    archive : Archive
        The archive being read.
    nodes : sequence of Instance
        Every directory and program instance to place.
    parents : dict of str to str
        A prebuilt :attr:`..archive.Archive.directory_parents`. Passed in
        because building it decodes and parses the root document, which is a
        megabyte-scale JSON object on a whole-scanner export.

    Returns
    -------
    tuple
        ``(children by parent instance id, roots)``. A node whose parent does
        not resolve is a root rather than being dropped, which is what keeps
        this agreeing with :meth:`..archive.Archive.path_of`: that walk stops
        at an unresolvable parent too, so such a node really is at the top of
        the only path the archive can state for it.
    """
    placeable = {one.id for one in nodes}
    children: dict[str, list[Instance]] = {}
    roots: list[Instance] = []
    for instance in nodes:
        parent = archive.parent_of(instance, parents)
        if parent is None or parent.id not in placeable:
            roots.append(instance)
        else:
            children.setdefault(parent.id, []).append(instance)
    return children, roots


def _leading_somewhere(node: Node) -> Node | None:
    """Drop a directory that no longer holds a protocol.

    Only ever applied to a tree restricted to one protocol. There an empty
    folder is an artefact of the restriction rather than a fact about the
    archive -- ``NAV_optionscan_P1_loadtest`` holds two protocols of one name
    under sibling directories, so asking for one of them left the other's
    folders standing and empty, which reads as something hidden rather than
    as something excluded. An unrestricted tree keeps its empty directories,
    because there the emptiness is the archive's own.

    Parameters
    ----------
    node : Node
        The node to keep or drop.

    Returns
    -------
    Node or None
        The node with its pruned children, or ``None`` when it is a directory
        with nothing left beneath it.
    """
    if node.kind != DIRECTORY_NODE:
        return node
    kept = tuple(one for one in map(_leading_somewhere, node.children) if one is not None)
    if not kept:
        return None
    return Node(name=node.name, kind=node.kind, children=kept)


def build(archive: Archive, *, program: Program | None = None, scans: bool = False) -> list[Node]:
    """Read an archive's folder tree.

    Parameters
    ----------
    archive : Archive
        The archive to read.
    program : Program or None, optional
        One protocol to restrict the tree to, keeping the directories above
        it so the path it is addressed by still reads, and dropping the ones
        that no longer lead anywhere. ``None``, the default, shows every
        protocol and every directory, empty ones included.
    scans : bool, optional
        Whether each protocol carries its running order. Default ``False``,
        which stops the tree at the protocols.

    Returns
    -------
    list of Node
        The roots, outermost first. A list rather than one node because
        nothing in the format promises a single root: every corpus archive
        has exactly one, and a node whose parent does not resolve would
        otherwise be lost.
    """
    parents = archive.directory_parents
    programs = archive.programs
    if program is not None:
        programs = [one for one in programs if one.instance.id == program.instance.id]
    by_id = {one.instance.id: one for one in programs}
    directories = [one for one in archive.instances.values() if one.kind == DIRECTORY]
    instances = directories + [one.instance for one in programs]
    children, roots = _by_parent(archive, instances, parents)

    def node_of(instance: Instance) -> Node:
        """Render one instance and everything the tree puts beneath it."""
        found = by_id.get(instance.id)
        if found is not None:
            return _protocol_node(found, scans=scans)
        below = sorted(children.get(instance.id, []), key=lambda one: archive.label_of(one))
        return Node(
            name=archive.label_of(instance) or UNNAMED,
            kind=DIRECTORY_NODE,
            children=tuple(node_of(one) for one in below),
        )

    drawn = [node_of(one) for one in sorted(roots, key=lambda one: archive.label_of(one))]
    if program is None:
        return drawn
    return [one for one in map(_leading_somewhere, drawn) if one is not None]


def tally(roots: Sequence[Node]) -> dict[str, int]:
    """Count what a tree holds.

    Parameters
    ----------
    roots : sequence of Node
        The tree, as :func:`build` returns it.

    Returns
    -------
    dict of str to int
        ``directories``, ``protocols``, ``scans`` and ``steps``, the last
        being the steps in a running order that acquire nothing. ``scans`` is
        summed from each protocol's own count rather than from the rendered
        children, so it is right whether or not the tree was built with them
        -- and a rendered scan is therefore skipped here rather than counted
        a second time, which is what made a 23-scan protocol with ten pauses
        report 33 other steps.
    """
    counts = {"directories": 0, "protocols": 0, "scans": 0, "steps": 0}
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node.kind == DIRECTORY_NODE:
            counts["directories"] += 1
        elif node.kind == PROTOCOL_NODE:
            counts["protocols"] += 1
            counts["scans"] += node.scans or 0
        elif node.kind == STEP_NODE:
            counts["steps"] += 1
        stack.extend(node.children)
    return counts


def label(node: Node) -> str:
    """The one line a node contributes, name plus what it is.

    Parameters
    ----------
    node : Node
        The node to caption.

    Returns
    -------
    str
        The name, with a protocol's scan count or a non-acquiring step's kind
        after it.
    """
    if node.kind == PROTOCOL_NODE and node.scans is not None:
        return f"{node.name} ({node.scans} scan{'' if node.scans == 1 else 's'})"
    if node.kind == STEP_NODE and node.step_kind:
        return f"{node.name} [{node.step_kind}]"
    return node.name


def _draw(node: Node, prefix: str, connector: str, out: list[str]) -> None:
    """Append a node's line and its subtree's, depth first.

    Parameters
    ----------
    node : Node
        The node to draw.
    prefix : str
        The trunk drawn to the left of this node's own connector.
    connector : str
        :data:`BRANCH`, :data:`LAST_BRANCH`, or empty for a root.
    out : list of str
        Lines accumulated so far, appended to in place.

    Returns
    -------
    None
    """
    out.append(f"{prefix}{connector}{label(node)}")
    if connector == "":
        below = prefix
    else:
        below = prefix + (BLANK if connector == LAST_BRANCH else TRUNK)
    for index, child in enumerate(node.children):
        last = index == len(node.children) - 1
        _draw(child, below, LAST_BRANCH if last else BRANCH, out)


def render(roots: Sequence[Node], *, counts: bool = True) -> str:
    """Draw a tree the way the ``tree`` command draws one.

    Parameters
    ----------
    roots : sequence of Node
        The tree, as :func:`build` returns it.
    counts : bool, optional
        Whether to close with the summary line. Default ``True``.

    Returns
    -------
    str
        The drawing, without a trailing newline. An archive holding nothing
        renders as the one line saying so rather than as empty output.
    """
    lines: list[str] = []
    for root in roots:
        _draw(root, "", "", lines)
    if not lines:
        return "this archive holds no directories and no protocols"
    if not counts:
        return "\n".join(lines)
    found = tally(roots)
    parts = [
        f"{found['directories']} director{'y' if found['directories'] == 1 else 'ies'}",
        f"{found['protocols']} protocol{'' if found['protocols'] == 1 else 's'}",
        f"{found['scans']} scan{'' if found['scans'] == 1 else 's'}",
    ]
    if found["steps"]:
        parts.append(f"{found['steps']} other step{'' if found['steps'] == 1 else 's'}")
    lines.extend(["", ", ".join(parts)])
    return "\n".join(lines)
