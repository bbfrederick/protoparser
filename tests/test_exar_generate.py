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

from conftest import (  # noqa: F401
    find_exar,
    find_pdf,
    protocol_archive_path,
    requires_exar,
)
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


# --------------------------------------------------------------------------
# The driver: a template archive plus a parsed PDF
# --------------------------------------------------------------------------


def _parse(pdf: str) -> dict:
    """Parse one example PDF through the CLI, as the driver's callers do.

    Parameters
    ----------
    pdf : str
        Path to the PDF.

    Returns
    -------
    dict
        The parsed protocol.
    """
    import json
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-m", "siemens_protocol.cli", "parse", pdf, "--stdout"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


@requires_exar
def test_driving_an_archive_from_its_own_pdf_writes_nothing() -> None:
    """A protocol told what it already says must not change.

    This is the sharpest cheap check on the whole chain: units, scales, the
    derived basis, sparse arrays and change detection all have to be right or
    something reports a spurious write. An earlier version wrote two values
    here, both a printed ``0.00`` against an assignment a sparse array omits.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import build

    archive = read(find_exar("Potpourri_P1.exar1"))
    report = build.apply_protocol(archive, _parse(find_pdf("Potpourri_P1.pdf")))
    assert report.applied == [], [f"{a.step}: {a.label}" for a in report.applied]
    assert report.unchanged > 100, "nothing was compared, so this proves nothing"
    assert report.unmatched == []


@requires_exar
def test_driving_an_archive_reproduces_the_console_edit(tmp_path: pathlib.Path) -> None:
    """Given the changed PDF, the driver writes what the console wrote.

    ``Potpourri_P1_changed`` is the same protocol after the console changed
    many parameters across five scans. Driving the unmodified archive from
    that PDF must land on the same values in every mapped field.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the built archive.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import build

    archive = read(find_exar("Potpourri_P1.exar1"))
    report = build.apply_protocol(archive, _parse(find_pdf("Potpourri_P1_changed.pdf")))
    assert report.applied, "the changed PDF should have moved something"
    assert validate.problems(archive) == []

    written = tmp_path / "built.exar1"
    archive.write(str(written))
    ours = {s.name: s for s in read(str(written)).steps}
    theirs = read(find_exar("Potpourri_P1_changed.exar1"))

    compared = 0
    for step in theirs.steps:
        mine = ours[step.name]
        for mapping in patch.MAPPINGS:
            if not patch.applies_to(mapping, step.protocol):
                continue
            for key, _index in patch.expand(mapping.ascconv_key, step.protocol.xprotocol):
                got = patch.read_ascconv(mine.protocol.xprotocol, key)
                want = patch.read_ascconv(step.protocol.xprotocol, key)
                if got is None and want is None:
                    continue
                compared += 1
                if mapping.bit is not None:
                    assert (int(got or 0) >> mapping.bit & 1) == (
                        int(want or 0) >> mapping.bit & 1
                    ), f"{step.name}: {mapping.label}"
                elif mapping.basis is not None:
                    # FOV Phase is quantised by the console; see patch.Manifest.
                    assert abs(float(got) - float(want)) <= 5e-4 * max(1.0, abs(float(want)))
                else:
                    assert got == want, f"{step.name}: {mapping.label}"
    assert compared > 1000, f"only {compared} fields compared"


@requires_exar
def test_the_report_counts_what_it_could_not_write() -> None:
    """Coverage is stated, not implied.

    Most of what a protocol prints has no mapping, so a manifest that listed
    only successes would describe a small part of the result as if it were the
    whole. The unmapped parameters are named and counted.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import build

    archive = read(find_exar("Potpourri_P1.exar1"))
    report = build.apply_protocol(archive, _parse(find_pdf("Potpourri_P1.pdf")))
    written, total = report.coverage
    assert 0 < written < total, "coverage should be a real fraction, not all or nothing"
    assert report.inherited, "no unmapped parameters were recorded"
    text = report.report()
    assert "coverage:" in text and "no mapping" in text


@requires_exar
def test_a_scan_the_template_lacks_is_reported_not_invented() -> None:
    """An unmatched scan surfaces rather than being guessed at.

    The PDF names a sequence by kernel and the archive by sequence file, so a
    donor to copy cannot be chosen without guessing. The driver says so.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import build

    archive = read(find_exar("Potpourri_P1.exar1"))
    parsed = _parse(find_pdf("Potpourri_P1.pdf"))
    parsed["scans"] = list(parsed["scans"]) + [
        {"name": "a_scan_no_template_has", "flat": {"TR": {"value": "1000 ms"}}}
    ]
    report = build.apply_protocol(archive, parsed)
    assert report.unmatched == ["a_scan_no_template_has"]
