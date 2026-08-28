"""Tests for creating scans in an ``.exar1`` archive, and for validating one.

A scanner has accepted archives built this way, which is the only authority
that counts. Getting there cost two defects, and both are represented here as
negative tests: a validator that never fires would be worse than none, since
it would carry the appearance of assurance.

The positive tests assert that generation produces an archive indistinguishable
from a console-authored one on every rule :mod:`validate` knows. The negative
tests reintroduce each defect and require it to be reported.
"""

from __future__ import annotations

import pathlib

import pytest

from conftest import find_exar, protocol_archive_path, requires_exar  # noqa: F401
from siemens_protocol.exar import generate, patch, read, validate


@requires_exar
def test_every_console_archive_passes_the_structural_rules(protocol_archive_path: str) -> None:
    """The rules are stated from what console archives do, so they must hold.

    Parameters
    ----------
    protocol_archive_path : str
        One archive from the corpus that carries protocols.

    Returns
    -------
    None
    """
    assert validate.problems(read(protocol_archive_path)) == []


@requires_exar
def test_a_duplicated_scan_produces_a_sound_archive(tmp_path: pathlib.Path) -> None:
    """Adding a scan leaves every structural rule intact.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the written archive.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    before = [step.name for step in archive.steps]
    generate.duplicate_step(archive, archive.steps[0], "COPY_of_first")

    written = tmp_path / "grown.exar1"
    archive.write(str(written))
    grown = read(str(written))

    assert validate.problems(grown) == []
    names = [step.name for step in grown.steps]
    assert names == before + ["COPY_of_first"], "the copy is not last in running order"
    assert len(grown.program.children) == len(before) + 1


@requires_exar
def test_a_copy_can_be_given_content_of_its_own(tmp_path: pathlib.Path) -> None:
    """A duplicated scan patched afterwards keeps its own protocol.

    This is the case that matters and the one that failed on a scanner: while
    a copy is identical to its source, being served the source's protocol is
    indistinguishable from success. Only an edited copy can tell the two
    apart, so this test edits one.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the written archive.

    Returns
    -------
    None
    """
    source = read(find_exar("Potpourri_P1.exar1"))
    original = {s.name: s for s in source.steps}["Minn_CMRR_2.3mm_S8_rest_6min"]
    generate.duplicate_step(source, original, "COPY_with_its_own_TR")
    first = tmp_path / "grown.exar1"
    source.write(str(first))

    # Re-read: the live instance map is rebuilt on read, so the new step is
    # only addressable afterwards.
    grown = read(str(first))
    copy = {s.name: s for s in grown.steps}["COPY_with_its_own_TR"]
    document, applied, skipped = patch.patch_document(copy.protocol, {"TR": 652.0})
    assert applied and not skipped
    grown.replace_content(copy.protocol.instance, document)
    second = tmp_path / "patched.exar1"
    grown.write(str(second))

    final = read(str(second))
    assert validate.problems(final) == []
    steps = {s.name: s for s in final.steps}
    assert steps["COPY_with_its_own_TR"].protocol.preview["sub.0.msr.tr.0"].value == 652.0
    assert steps["Minn_CMRR_2.3mm_S8_rest_6min"].protocol.preview["sub.0.msr.tr.0"].value == 650.0
    assert (
        steps["COPY_with_its_own_TR"].protocol.instance.content_hash
        != steps["Minn_CMRR_2.3mm_S8_rest_6min"].protocol.instance.content_hash
    ), "the copy is sharing its source's protocol, so an edit to it would be lost"


@requires_exar
def test_renumbering_reproduces_an_untouched_console_document() -> None:
    """The walk order matches Newtonsoft's, which is what makes it canonical.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    document = archive.document(archive.program)
    assert generate.renumber_references(document) == document


# --------------------------------------------------------------------------
# The defects that motivated the validator. Each must be reported.
# --------------------------------------------------------------------------


@requires_exar
def test_a_step_missing_from_the_other_maps_is_reported() -> None:
    """The console rejects such an archive outright; the checker must not.

    ``EdfProgramContent`` describes the running order in five maps keyed by
    step id. A step in only some of them leaves the console unable to build
    the program, which it reports by showing the folder and no protocols --
    the same symptom as an archive exported off an empty folder node.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    document = archive.document(archive.program)
    victim = archive.steps[-1].instance.object_id
    for name in ("LinksTo", "Ranks", "RelationsFrom", "RelationsTo"):
        document[name].pop(victim, None)
    archive.replace_content(archive.program, document)

    reported = validate.problems(archive)
    assert reported, "a step missing from four maps was not reported"
    for name in ("LinksTo", "Ranks", "RelationsFrom", "RelationsTo"):
        assert any(line.startswith(f"{name} is missing") for line in reported), name


@requires_exar
def test_a_protocol_parenting_to_another_step_is_reported() -> None:
    """The failure this catches is silent, which is why it is worth catching.

    The console resolves a step's protocol through ``ParentElementId``, so a
    copy that keeps its source's pointer is served the source's protocol. On a
    scanner that showed up as an edited copy returning its source's TR.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    rows = archive.container.tables["Instance"]
    target = archive.steps[3]
    elsewhere = archive.steps[0].instance.element_id
    rows.set(rows.find("Id", target.protocol.instance.id)[0], "ParentElementId", elsewhere)

    reported = validate.problems(archive)
    assert any("its protocol parents to another node" in line for line in reported)


@requires_exar
def test_a_dangling_reference_is_reported() -> None:
    """Newtonsoft resolves ``$ref`` against ``$id``; an unresolved one is broken.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    document = archive.document(archive.program)
    document["LinksTo"][document["LastStepId"]]["$values"] = [{"$ref": "nonexistent"}]
    archive.replace_content(archive.program, document)
    assert any("unresolved $ref" in line for line in validate.problems(archive))


@requires_exar
def test_a_broken_running_order_is_reported() -> None:
    """A chain that does not span the steps means scans would go unseen.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    document = archive.document(archive.program)
    document["LinksFrom"][document["FirstStepId"]]["$values"] = []
    archive.replace_content(archive.program, document)
    reported = validate.problems(archive)
    assert any("link chain covers" in line for line in reported)
