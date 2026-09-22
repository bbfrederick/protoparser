"""The folder tree: does the drawing describe the archive it came from?

An ``.exar1`` file is not always one protocol, and until this command there
was no way to ask what one holds without reading its whole JSON document. The
tree is therefore what a user meets first on an unfamiliar archive, and its
paths are what they will paste into ``--program`` and ``--scan``. That is the
invariant worth pinning: not that the drawing looks a certain way, but that it
names every node the archive holds, exactly once, by the path the rest of the
tool addresses it with.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import pytest

from conftest import find_exar, requires_exar
from siemens_protocol.exar import read
from siemens_protocol.exar import tree as exar_tree
from siemens_protocol.exar.archive import DIRECTORY
from siemens_protocol.exar.tree import Node


def _walk(nodes: Sequence[Node], path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Node]]:
    """Every node in a tree, with the names walked to reach it.

    Parameters
    ----------
    nodes : sequence of Node
        The roots, or any node's children.
    path : tuple of str, optional
        The names above them. Default empty.

    Returns
    -------
    list of tuple
        ``(path including this node's own name, node)``, depth first.
    """
    found: list[tuple[tuple[str, ...], Node]] = []
    for node in nodes:
        here = path + (node.name,)
        found.append((here, node))
        found.extend(_walk(node.children, here))
    return found


@requires_exar
def test_every_node_the_archive_holds_is_drawn_exactly_once(archive_path: str) -> None:
    """Nothing is lost from the tree and nothing is invented in it.

    Swept over every archive rather than checked against a recorded drawing,
    which would pin the renderer to itself. Counted by ``id`` on the archive
    side and by position in the tree, so a node drawn twice fails as loudly as
    one dropped -- and a directory sharing a name with its sibling, which
    ``NAV_optionscan_P1_loadtest`` has, cannot mask either.

    Parameters
    ----------
    archive_path : str
        One archive from the corpus.

    Returns
    -------
    None
    """
    archive = read(archive_path)
    roots = exar_tree.build(archive)
    drawn = _walk(roots)

    directories = [one for one in archive.instances.values() if one.kind == DIRECTORY]
    expected = len(directories) + len(archive.program_nodes)
    assert len(drawn) == expected, (
        f"::error::{archive_path} holds {len(directories)} directories and "
        f"{len(archive.program_nodes)} protocols but the tree drew {len(drawn)} nodes"
    )

    counts = exar_tree.tally(roots)
    assert counts["directories"] == len(directories)
    assert counts["protocols"] == len(archive.program_nodes)
    assert counts["scans"] == sum(
        1 for program in archive.programs for step in program.steps if step.runs_a_protocol
    )


@requires_exar
def test_a_drawn_path_is_the_address_the_other_commands_take(archive_path: str) -> None:
    """The path to a node in the tree is the one ``path_of`` states.

    This is the whole reason the tree is built from the upward
    ``ParentDirectoryId`` map rather than the downward ``SubdirectoryIds``
    one. The two agree on every corpus archive, but only the upward map is
    what ``--program`` and ``--scan`` resolve against, so a tree built from
    the other could print a path that no command would accept -- and it would
    do so on an archive where nothing else disagreed.

    Parameters
    ----------
    archive_path : str
        One archive from the corpus.

    Returns
    -------
    None
    """
    archive = read(archive_path)
    parents = archive.directory_parents
    drawn = {path for path, _node in _walk(exar_tree.build(archive))}

    for program in archive.programs:
        stated = tuple(archive.path_of(program.instance, parents))
        assert stated in drawn, (
            f"::error::{archive_path}: the tree does not reach {'/'.join(stated)}, "
            "which is the path --program takes"
        )
    for instance in archive.instances.values():
        if instance.kind != DIRECTORY:
            continue
        stated = tuple(archive.path_of(instance, parents))
        assert stated in drawn, f"::error::{archive_path}: {'/'.join(stated)} is not in the tree"


@requires_exar
def test_a_protocols_scans_are_drawn_in_running_order(archive_path: str) -> None:
    """``--scans`` shows the running order, not an alphabetical one.

    Directories and protocols are sorted, because the archive's own row order
    is not known to match the console's display order and a sorted listing is
    reproducible. A step's place in the running order *is* meaningful, so
    sorting there would be a lie about the acquisition -- which is the one
    asymmetry in this renderer and the one worth a test.

    Parameters
    ----------
    archive_path : str
        One archive from the corpus.

    Returns
    -------
    None
    """
    archive = read(archive_path)
    by_name: dict[tuple, list] = {}
    for program in archive.programs:
        by_name.setdefault(tuple(archive.path_of(program.instance)), []).append(program)

    for path, node in _walk(exar_tree.build(archive, scans=True)):
        if node.kind != exar_tree.PROTOCOL_NODE:
            continue
        candidates = by_name.get(path, [])
        # Two protocols can share a path only if they share a name inside one
        # directory, which no corpus archive does; pairing is by path alone.
        assert len(candidates) == 1, f"::error::{archive_path}: {'/'.join(path)} is not unique"
        expected = [step.name for step in candidates[0].steps]
        assert [
            child.name for child in node.children
        ] == expected, f"::error::{archive_path}: {'/'.join(path)} is drawn out of running order"


@requires_exar
def test_a_step_that_acquires_nothing_is_marked_and_is_not_a_scan(archive_path: str) -> None:
    """A pause is in the running order and is not a scan.

    Eleven of ``CHR-MDD``'s thirty-four steps are operator instructions --
    "Pause for saliva collection" -- and the PDF does not print them as scans.
    Drawing them unmarked would make the tree disagree with every printout of
    the same protocol, and counting them as scans would make it disagree with
    ``list``.

    Parameters
    ----------
    archive_path : str
        One archive from the corpus.

    Returns
    -------
    None
    """
    archive = read(archive_path)
    roots = exar_tree.build(archive, scans=True)
    for _path, node in _walk(roots):
        if node.kind == exar_tree.SCAN_NODE:
            assert node.step_kind is None, f"::error::{archive_path}: a scan carries a step kind"
        elif node.kind == exar_tree.STEP_NODE:
            assert node.step_kind, f"::error::{archive_path}: {node.name} is unmarked"

    counts = exar_tree.tally(roots)
    non_acquiring = sum(
        1 for program in archive.programs for step in program.steps if not step.runs_a_protocol
    )
    assert counts["steps"] == non_acquiring, (
        f"::error::{archive_path}: {counts['steps']} steps counted against "
        f"{non_acquiring} that acquire nothing"
    )


def test_the_pauses_of_a_real_protocol_are_named_as_pauses() -> None:
    """One archive pinned by name, so the marking is checked and not just typed.

    The sweeps above hold whatever the corpus contains; this fails if
    ``CHR-MDD``'s operator instructions stop being recognized as pauses at
    all, which a sweep asserting "every non-acquiring step is marked" would
    pass vacuously on an archive that had none.

    Returns
    -------
    None
    """
    archive = read(find_exar("CHR-MDD.exar1"))
    roots = exar_tree.build(archive, scans=True)
    kinds = [node.step_kind for _path, node in _walk(roots) if node.kind == exar_tree.STEP_NODE]
    assert kinds, "::error::CHR-MDD draws no non-acquiring steps"
    assert set(kinds) == {"pause"}, f"::error::unexpected step kinds {sorted(set(kinds))}"
    assert exar_tree.tally(roots) == {
        "directories": 4,
        "protocols": 1,
        "scans": 23,
        "steps": len(kinds),
    }


def test_restricting_to_one_protocol_drops_the_folders_it_left_behind() -> None:
    """A restricted tree shows the way to the protocol and nothing else.

    ``NAV_optionscan_P1_loadtest`` holds two protocols of one name under
    sibling directories -- the console disambiguates a repeated directory name
    exactly as it does a program name -- so restricting to one left the
    other's folders standing and empty. That reads as something hidden rather
    than as something excluded, which is the opposite of what the restriction
    was asked for.

    Returns
    -------
    None
    """
    archive = read(find_exar("NAV_optionscan_P1_loadtest.exar1"))
    whole = exar_tree.build(archive)
    assert exar_tree.tally(whole)["protocols"] == 2, "::error::this archive should hold two"

    wanted = next(
        program
        for program in archive.programs
        if archive.path_of(program.instance)[2] == "Investigators (2)"
    )
    restricted = exar_tree.build(archive, program=wanted)
    counts = exar_tree.tally(restricted)
    assert counts["protocols"] == 1
    drawn = {"/".join(path) for path, _node in _walk(restricted)}
    assert "Root/Export/Investigators (2)/Frederick/NAV_optionscan_P1 (2)" in drawn
    assert not any(
        one.startswith("Root/Export/Investigators/") for one in drawn
    ), f"::error::the other protocol's folders are still drawn: {sorted(drawn)}"


def _parse_drawing(drawing: str) -> list[tuple[int, str, tuple[str, ...], str]]:
    """Read a drawing back into one record per line.

    Parameters
    ----------
    drawing : str
        The rendered tree.

    Returns
    -------
    list of tuple
        ``(depth, connector, prefix groups, text)`` per line. The prefix is
        split into four-character groups, which is the only way it can be
        composed -- a group that is neither a trunk nor a blank fails here.
    """
    parsed: list[tuple[int, str, tuple[str, ...], str]] = []
    for line in drawing.splitlines():
        connector = ""
        head, body = "", line
        for candidate in (exar_tree.BRANCH, exar_tree.LAST_BRANCH):
            if candidate in line:
                head, _sep, body = line.partition(candidate)
                connector = candidate
                break
        width = len(exar_tree.TRUNK)
        assert len(head) % width == 0, f"::error::ragged prefix in {line!r}"
        groups = tuple(head[i : i + width] for i in range(0, len(head), width))
        assert set(groups) <= {exar_tree.TRUNK, exar_tree.BLANK}, f"::error::bad prefix {head!r}"
        # A root carries no connector and no prefix; every other node carries a
        # connector and one group per ancestor below the root, which is what
        # makes the depth one more than the number of groups.
        parsed.append((len(groups) + bool(connector), connector, groups, body))
    return parsed


def _has_following_sibling(
    parsed: Sequence[tuple[int, str, tuple[str, ...], str]], at: int
) -> bool:
    """Whether the line at ``at`` is followed by another child of its parent.

    Parameters
    ----------
    parsed : sequence of tuple
        Records from :func:`_parse_drawing`.
    at : int
        Index of the line to ask about.

    Returns
    -------
    bool
        ``True`` when the next line at or above this depth is at exactly this
        depth, which makes it a sibling.
    """
    depth = parsed[at][0]
    for later in parsed[at + 1 :]:
        if later[0] <= depth:
            return later[0] == depth
    return False


@requires_exar
def test_the_drawing_can_be_read_back_as_the_tree_it_drew(archive_path: str) -> None:
    """The connectors and trunks describe the nesting they are meant to.

    Re-parsing the drawing rather than comparing it to a recorded one is what
    makes this a test of the renderer: depth is recovered from the prefix
    width alone, so a subtree hung off the wrong parent comes back as a
    different tree.

    The depth check alone is not enough, and that was found by breaking the
    renderer rather than by reading it: giving every child the branching
    connector instead of the closing one leaves the same indentation, so the
    tree read back identically while the drawing grew a trunk running past
    its own last child. The second half therefore asks what the connectors
    *mean* -- a closing connector says there is no sibling below, and a trunk
    at some depth says an ancestor there still has one -- against the drawing
    itself rather than against the tree, so it cannot agree by mirroring the
    code it is checking.

    Parameters
    ----------
    archive_path : str
        One archive from the corpus.

    Returns
    -------
    None
    """
    archive = read(archive_path)
    roots = exar_tree.build(archive, scans=True)
    if not roots:
        pytest.skip(f"{archive_path} holds no directories and no protocols")

    parsed = _parse_drawing(exar_tree.render(roots, counts=False))
    expected = [(len(path) - 1, exar_tree.label(node)) for path, node in _walk(roots)]
    assert [
        (depth, text) for depth, _c, _g, text in parsed
    ] == expected, "::error::the drawing does not read back as the tree"

    ancestry: list[int] = []
    for index, (depth, connector, groups, text) in enumerate(parsed):
        del ancestry[depth:]
        more = _has_following_sibling(parsed, index)
        if depth == 0:
            assert connector == "", f"::error::a root carries a connector: {text!r}"
        else:
            wanted = exar_tree.BRANCH if more else exar_tree.LAST_BRANCH
            assert connector == wanted, (
                f"::error::{archive_path}: {text!r} has "
                f"{'a' if more else 'no'} sibling below it and is drawn as if it had "
                f"{'none' if more else 'one'}"
            )
            # groups[i] is the trunk drawn for the ancestor at depth i + 1: the
            # root draws none, its children being separate trees.
            for level, group in enumerate(groups):
                above = ancestry[level + 1]
                wanted_group = (
                    exar_tree.TRUNK if _has_following_sibling(parsed, above) else exar_tree.BLANK
                )
                assert group == wanted_group, (
                    f"::error::{archive_path}: the trunk under {parsed[above][3]!r} "
                    f"is drawn wrongly beside {text!r}"
                )
        ancestry.append(index)


def test_an_archive_with_nothing_in_it_says_so() -> None:
    """An empty tree renders as a sentence rather than as no output at all.

    Exporting an empty folder node rather than the protocol tree yields a
    valid, readable archive holding the directory scaffolding and nothing
    else. No shipped archive is one, so the case is constructed here: silence
    would read as a command that failed.

    Returns
    -------
    None
    """
    assert exar_tree.render([]) == "this archive holds no directories and no protocols"
    assert exar_tree.tally([]) == {"directories": 0, "protocols": 0, "scans": 0, "steps": 0}


def test_a_step_kind_is_named_the_way_a_reader_spells_it() -> None:
    """``EdfPauseStep`` reads as ``pause``, and an unseen kind still reads.

    The affixes are stripped rather than the known kinds enumerated, so
    ``EdfDecisionStep`` -- in ``STEP_KINDS`` and in no corpus archive -- is
    named sensibly instead of being reported as unknown.

    Returns
    -------
    None
    """
    assert exar_tree.short_kind("EdfPauseStep") == "pause"
    assert exar_tree.short_kind("EdfInteractionStep") == "interaction"
    assert exar_tree.short_kind("EdfDecisionStep") == "decision"
    assert exar_tree.short_kind("EdfStep") == "edfstep"


def test_the_subcommand_runs_and_its_json_carries_the_same_counts(tmp_path: Path) -> None:
    """The command runs end to end, in both of its output shapes.

    Returns
    -------
    None

    Parameters
    ----------
    tmp_path : pathlib.Path
        Pytest's temporary directory.
    """
    source = find_exar("CHR-MDD.exar1")
    drawn = subprocess.run(
        [sys.executable, "-m", "siemens_protocol.cli", "tree", source, "--scans"],
        capture_output=True,
        text=True,
    )
    assert drawn.returncode == 0, drawn.stderr
    assert "CHR-MDD (23 scans)" in drawn.stdout
    assert "[pause]" in drawn.stdout
    assert drawn.stdout.rstrip().endswith("4 directories, 1 protocol, 23 scans, 10 other steps")

    written = tmp_path / "tree.json"
    emitted = subprocess.run(
        [
            sys.executable,
            "-m",
            "siemens_protocol.cli",
            "tree",
            source,
            "--scans",
            "--json",
            "--out",
            str(written),
        ],
        capture_output=True,
        text=True,
    )
    assert emitted.returncode == 0, emitted.stderr
    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["format"] == "exar1"
    assert document["counts"] == {
        "directories": 4,
        "protocols": 1,
        "scans": 23,
        "steps": 10,
    }
    assert document["roots"][0]["name"] == "Root"


def test_pointing_the_command_at_a_pdf_is_a_message_and_not_a_traceback() -> None:
    """An .exar1 is a SQLite database, and a PDF is not one.

    Several corpus directories hold an archive and its printout under one
    stem, so this is an easy mistake to make, and ``sqlite3.DatabaseError``
    was reaching the terminal as a stack trace saying "file is not a
    database".

    Returns
    -------
    None
    """
    from conftest import find_pdf

    result = subprocess.run(
        [sys.executable, "-m", "siemens_protocol.cli", "tree", find_pdf("CHR-MDD.pdf")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "not a readable .exar1 archive" in result.stderr
