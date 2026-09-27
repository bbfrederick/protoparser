"""Extracting protocols: does the new archive hold them, and only them?

``extract`` builds a new archive by copying rows out of a source, so there
are two ways to be wrong -- leaving out something a protocol needs, and
carrying across something that belongs to a protocol left behind -- and a
third that matters more than either: touching the source.

The strongest check is an identity. Extracting *every* protocol must
reproduce the source row for row, which exercises every table filter and
the structure rewrite at once, and can only pass if each rule keeps exactly
what a console-written file holds. The per-protocol checks then say that a
subset is the same protocols, byte for byte, under the same folder path.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from conftest import find_exar
from siemens_protocol import cli
from siemens_protocol.exar import Archive, read, validate
from siemens_protocol.exar.archive import DIRECTORY, STRUCTURE, Program
from siemens_protocol.exar.extract import extract

#: Corpus archives holding more than one protocol. The second holds two
#: protocols of one name under sibling directories, so extracting one must
#: drop a whole directory branch, not just a program.
MULTI_PROGRAM = ("Frederick_P2.exar1", "NAV_optionscan_P1_loadtest.exar1")


def _digest(path: str) -> str:
    """Return a file's SHA-1.

    Parameters
    ----------
    path : str
        The file.

    Returns
    -------
    str
        Hex digest of its bytes.
    """
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def _fingerprint(program: Program, archive: Archive) -> tuple:
    """Describe a protocol by everything a copy must preserve.

    Parameters
    ----------
    program : Program
        The protocol.
    archive : Archive
        The archive holding it.

    Returns
    -------
    tuple
        Its path, its program document, and each step's name, kind, content
        hash and protocols' content hashes in running order.
    """
    steps = tuple(
        (
            step.name,
            step.instance.kind,
            step.instance.content_hash,
            tuple(p.instance.content_hash for p in step.protocols),
        )
        for step in program.steps
    )
    return (tuple(archive.path_of(program.instance)), archive.document(program.instance), steps)


def test_extracting_every_protocol_reproduces_the_source(protocol_archive_path: str) -> None:
    """Keeping everything must change nothing, table by table and row by row.

    Parameters
    ----------
    protocol_archive_path : str
        One corpus archive holding protocols.

    Returns
    -------
    None
    """
    source = read(protocol_archive_path)
    result = extract(source, source.program_nodes)
    assert result.container is not source.container
    assert set(result.container.tables) == set(source.container.tables)
    for name, table in source.container.tables.items():
        assert result.container.tables[name].rows == table.rows, name
    assert result.container.indexes == source.container.indexes


@pytest.mark.parametrize("name", MULTI_PROGRAM)
def test_each_protocol_extracts_alone_intact(name: str) -> None:
    """One protocol out of many: the same protocol, nothing else, still valid.

    Parameters
    ----------
    name : str
        A corpus archive holding several protocols.

    Returns
    -------
    None
    """
    source = read(find_exar(name))
    programs = source.programs
    assert len(programs) > 1
    for program in programs:
        result = extract(source, [program.instance])
        assert validate.problems(result) == [], program.name
        (only,) = result.programs
        assert _fingerprint(only, result) == _fingerprint(program, source)
        kept_dirs = {
            tuple(result.path_of(d)) for d in result.instances.values() if d.kind == DIRECTORY
        }
        path = tuple(source.path_of(program.instance))
        assert kept_dirs == {path[:n] for n in range(1, len(path))}


def _tree_scaffolding(archive: Archive) -> set[str]:
    """Return the elements describing the folder tree rather than a protocol.

    Parameters
    ----------
    archive : Archive
        The archive.

    Returns
    -------
    set of str
        Element ids of the structure node, every directory, and their labels.
    """
    rows = {str(r["Id"]): r for r in archive.container.rows("Instance")}
    found: set[str] = set()
    for one in archive.instances.values():
        if one.kind in (DIRECTORY, STRUCTURE):
            found.add(one.element_id)
            label = rows[one.id].get("LabelElement_id")
            if label:
                found.add(str(label))
    return found


@pytest.mark.parametrize("name", MULTI_PROGRAM)
def test_every_protocol_node_lands_in_exactly_one_extract(name: str) -> None:
    """Extracting each protocol alone partitions the source's protocol nodes.

    A node a left-out protocol owns -- its label, a step's add-in config --
    must not ride along. Leaking one leaves every check on the *kept*
    protocols passing, so the question has to be asked of the whole set: each
    node outside the folder tree belongs to one extract and one only.

    Parameters
    ----------
    name : str
        A corpus archive holding several protocols.

    Returns
    -------
    None
    """
    source = read(find_exar(name))
    seen: dict[str, str] = {}
    for program in source.programs:
        result = extract(source, [program.instance])
        own = {one.element_id for one in result.instances.values()} - _tree_scaffolding(result)
        clash = sorted(own & set(seen))
        assert not clash, f"{program.name} shares {len(clash)} node(s) with {seen[clash[0]]}"
        seen.update(dict.fromkeys(own, program.name))
    everything = {one.element_id for one in source.instances.values()}
    assert set(seen) == everything - _tree_scaffolding(source)


def test_a_subset_keeps_its_running_order_and_leaves_the_rest(tmp_path: Path) -> None:
    """Several protocols at once, written out and read back from disk.

    Parameters
    ----------
    tmp_path : Path
        Where to write the extracted archive.

    Returns
    -------
    None
    """
    source = read(find_exar("Frederick_P2.exar1"))
    chosen = source.programs[::5]
    out = str(tmp_path / "subset.exar1")
    extract(source, [p.instance for p in chosen]).write(out)
    back = read(out)
    assert validate.problems(back) == []
    assert [_fingerprint(p, back) for p in back.programs] == [
        _fingerprint(p, source) for p in chosen
    ]
    kept = {one.element_id for one in back.instances.values()}
    chosen_ids = {p.instance.element_id for p in chosen}
    for program in source.programs:
        if program.instance.element_id in chosen_ids:
            continue
        assert program.instance.element_id not in kept
        assert not {step.instance.element_id for step in program.steps} & kept
    assert os.path.getsize(out) < os.path.getsize(find_exar("Frederick_P2.exar1"))


def test_the_source_is_never_modified(tmp_path: Path) -> None:
    """Neither the file nor the archive read from it changes.

    Parameters
    ----------
    tmp_path : Path
        Where to write the extracted archive.

    Returns
    -------
    None
    """
    path = find_exar("Frederick_P2.exar1")
    before = _digest(path)
    source = read(path)
    tables = {name: list(t.rows) for name, t in source.container.tables.items()}
    root_hash = source.tree_root.content_hash
    extract(source, [source.programs[0].instance]).write(str(tmp_path / "one.exar1"))
    assert {name: t.rows for name, t in source.container.tables.items()} == tables
    assert source.tree_root.content_hash == root_hash
    assert _digest(path) == before


def test_a_foreign_or_empty_selection_is_refused() -> None:
    """Nothing chosen, or a program of another archive, is an error.

    Returns
    -------
    None
    """
    source = read(find_exar("Frederick_P2.exar1"))
    other = read(find_exar("Potpourri_P1.exar1"))
    with pytest.raises(ValueError, match="no protocol"):
        extract(source, [])
    with pytest.raises(ValueError, match="not a protocol of this archive"):
        extract(source, [other.program_nodes[0]])


def test_the_command_takes_repeated_and_pattern_protocols(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--protocol`` repeats, accepts ``re:``, and a duplicate is taken once.

    Parameters
    ----------
    tmp_path : Path
        Where to write.
    capsys : pytest.CaptureFixture
        Captures the summary.

    Returns
    -------
    None
    """
    path = find_exar("Frederick_P2.exar1")
    out = str(tmp_path / "x.exar1")
    argv = ["extract", path, "--protocol", "MEMPRAGE", "--protocol", "re:^CMRR", "--out", out]
    assert cli.main(argv + ["--protocol", "MEMPRAGE"]) == 0
    names = sorted(p.name for p in read(out).programs)
    source = read(path)
    expected = sorted(
        {"MEMPRAGE"} | {p.name for p in source.programs if p.name.startswith("CMRR")}
    )
    assert names == expected
    assert f"{len(expected)} protocol(s)" in capsys.readouterr().out


def test_the_command_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    """An existing destination is kept unless ``--force`` says otherwise.

    Parameters
    ----------
    tmp_path : Path
        Where to write.

    Returns
    -------
    None
    """
    path = find_exar("Frederick_P2.exar1")
    out = tmp_path / "x.exar1"
    out.write_bytes(b"not an archive")
    argv = ["extract", path, "--protocol", "MEMPRAGE", "--out", str(out)]
    assert cli.main(argv) == 1
    assert out.read_bytes() == b"not an archive"
    assert cli.main(argv + ["--force"]) == 0
    assert [p.name for p in read(str(out)).programs] == ["MEMPRAGE"]
    assert sorted(os.listdir(tmp_path)) == ["x.exar1"]


def test_the_command_refuses_to_write_over_its_source(tmp_path: Path) -> None:
    """``--out`` naming the source is refused, even with ``--force``.

    Parameters
    ----------
    tmp_path : Path
        Unused beyond isolating the test.

    Returns
    -------
    None
    """
    path = find_exar("Frederick_P2.exar1")
    before = _digest(path)
    assert cli.main(["extract", path, "--protocol", "MEMPRAGE", "--out", path, "--force"]) == 1
    assert _digest(path) == before
