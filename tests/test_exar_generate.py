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

import copy
import pathlib
import re
import uuid

import pytest

from conftest import (  # noqa: F401
    EXAR_PROTOCOL_FILES,
    UNTRUSTWORTHY_SCANS,
    find_exar,
    find_pdf,
    protocol_archive_path,
    requires_exar,
)
from siemens_protocol.exar import generate, patch, read, validate
from siemens_protocol.exar.archive import STEP_KINDS, Instance, pack_guids

#: An assignment into the ``sWipMemBlock`` scratch block, which is what a
#: sequence with a Special card writes. The block itself is declared by every
#: protocol, so the index is the part that carries the meaning.
WIP_ELEMENT = re.compile(r"sWipMemBlock\.\w+\[\d+\]\s*=")


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


def _foreign_scan(donor: object, target: object, needs_preview: str | None = None) -> object:
    """Return a donor scan running a sequence the target does not have.

    Chosen from the corpus rather than named, because a named scan ties the
    test to one export: the archive these two used was replaced when it turned
    out to be a different protocol under the wrong file name, and both tests
    broke on the scan names rather than on anything they were testing.

    Parameters
    ----------
    donor : Archive
        The archive to import from.
    target : Archive
        The archive that must not already run the sequence.
    needs_preview : str or None
        A preview path the chosen scan must carry, when the caller intends to
        patch that parameter afterwards.

    Returns
    -------
    Step
        The first suitable scan, in running order.
    """
    here = {patch.sequence_of(s.protocol) for s in target.steps if s.runs_a_protocol}
    for step in donor.steps:
        if not step.runs_a_protocol or step.name in UNTRUSTWORTHY_SCANS:
            continue
        if patch.sequence_of(step.protocol) in here:
            continue
        if needs_preview is not None and needs_preview not in step.protocol.preview:
            continue
        return step
    raise AssertionError("no donor scan runs a sequence the target lacks")


@requires_exar
def test_a_step_imported_from_another_archive_brings_its_own_protocol(
    tmp_path: pathlib.Path,
) -> None:
    """An import carries content the target has never seen.

    Within one archive a copy can lean on content that is already stored.
    Across two it cannot, and the store is addressed by hash, so the blob has
    to travel with the step. Asserting the imported XProtocol is *identical*
    to the donor's is what separates a copy from something reconstructed:
    our DEFLATE does not reproduce the console's, so a recompressed protocol
    would still read back correctly while rewriting the stored bytes.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the written archive.

    Returns
    -------
    None
    """
    target = read(find_exar("Potpourri_P1.exar1"))
    donor = read(find_exar("31P CSI 20230503 NOE.exar1"))
    wanted = _foreign_scan(donor, target)

    before = [step.name for step in target.steps]
    generate.duplicate_step(target, wanted, "IMPORTED_multiecho", source=donor)
    written = tmp_path / "imported.exar1"
    target.write(str(written))

    grown = read(str(written))
    assert validate.problems(grown) == []
    assert [s.name for s in grown.steps] == before + ["IMPORTED_multiecho"]
    copy = {s.name: s for s in grown.steps}["IMPORTED_multiecho"]
    assert copy.protocol.xprotocol == wanted.protocol.xprotocol
    assert copy.protocol.instance.content_hash == wanted.protocol.instance.content_hash


@requires_exar
def test_an_imported_step_can_be_given_content_of_its_own(tmp_path: pathlib.Path) -> None:
    """Editing an imported scan leaves the archive sound and the edit in place.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the written archive.

    Returns
    -------
    None
    """
    target = read(find_exar("Potpourri_P1.exar1"))
    donor = read(find_exar("31P CSI 20230503 NOE.exar1"))
    original = _foreign_scan(donor, target, needs_preview="sub.0.msr.tr.0")
    generate.duplicate_step(target, original, "IMPORTED_FOREIGN", source=donor)
    first = tmp_path / "imported.exar1"
    target.write(str(first))

    grown = read(str(first))
    manifest = patch.apply(grown, {"IMPORTED_FOREIGN": {"TR": 810.0}})
    assert manifest.applied and not manifest.skipped
    second = tmp_path / "patched.exar1"
    grown.write(str(second))

    final = read(str(second))
    assert validate.problems(final) == []
    copy = {s.name: s for s in final.steps}["IMPORTED_FOREIGN"]
    assert copy.protocol.preview["sub.0.msr.tr.0"].value == 810.0
    assert copy.protocol.instance.content_hash != original.protocol.instance.content_hash


@requires_exar
def test_importing_a_step_across_releases_is_refused() -> None:
    """The baseline is the compatibility key, so an import may not cross it.

    Every archive in the corpus is ``VA60A``, so the mismatch is staged rather
    than found. That is the point: the guard exists for the first XA30 archive
    to arrive, and an archive built across releases is well-formed and will
    not load, which is the hardest kind of defect to attribute.

    Returns
    -------
    None
    """
    target = read(find_exar("Potpourri_P1.exar1"))
    donor = read(find_exar("31P CSI 20230503 NOE.exar1"))
    donor.baseline = donor.baseline.replace(target.major_version, "VA30A")
    assert donor.major_version != target.major_version

    with pytest.raises(ValueError, match="VA30A"):
        generate.duplicate_step(target, donor.steps[0], "WRONG_RELEASE", source=donor)


@requires_exar
def test_every_customer_sequence_in_the_corpus_assembles_into_one_archive(
    tmp_path: pathlib.Path,
) -> None:
    """One archive can hold every sequence the corpus knows a Special card for.

    The list of sequences is re-derived from the corpus rather than written
    down, so a new export carrying a sequence this cannot assemble fails here
    instead of being discovered on a scanner.

    An *indexed* ``sWipMemBlock`` assignment is the test for a Special card:
    the block is scratch memory a sequence binary reads as it likes, and a
    sequence with no card leaves it empty. Matching the block's name alone is
    not enough -- every protocol declares it, so ``gre`` and ``tse`` match a
    substring test and this would then assert nothing.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the written archive.

    Returns
    -------
    None
    """
    wanted: dict[str, tuple[str, str]] = {}
    for path, _version in EXAR_PROTOCOL_FILES:
        for step in read(path).steps:
            if not step.runs_a_protocol:
                continue
            if not WIP_ELEMENT.search(step.protocol.xprotocol):
                continue
            wanted.setdefault(patch.sequence_of(step.protocol), (path, step.name))
    assert len(wanted) > 10, "the corpus sweep found almost nothing, so this proves little"

    target = read(find_exar("Potpourri_P1.exar1"))
    opened: dict[str, object] = {}
    for number, (sequence, (path, name)) in enumerate(sorted(wanted.items()), start=1):
        donor = opened.setdefault(path, read(path))
        step = {s.name: s for s in donor.steps if s.runs_a_protocol}[name]
        generate.duplicate_step(
            target, step, f"SEQ{number:02d}_{sequence}"[:35], source=donor  # type: ignore[arg-type]
        )
    written = tmp_path / "every-sequence.exar1"
    target.write(str(written))

    built = read(str(written))
    assert validate.problems(built) == []
    assembled = {patch.sequence_of(s.protocol) for s in built.steps if s.name.startswith("SEQ")}
    assert assembled == set(wanted)


def _second_program(archive: object, keep: int) -> str:
    """Split an archive's steps across a second program node.

    A backup holds one program per protocol. No such archive is readable in
    the corpus at the moment -- the one XA30 backup that arrived is a cloud
    placeholder -- so the shape is staged here out of a real export rather
    than asserted about a file that may not be present. Everything the reader
    keys on is real: a second ``EdfProgram`` instance with its own element and
    object ids, its own ``Children``, its own link chain and ``Ranks``, and
    steps re-parented to it.

    Parameters
    ----------
    archive : Archive
        The archive to split. Modified in place.
    keep : int
        How many steps stay with the original program; the rest move.

    Returns
    -------
    str
        The new program's object id.
    """
    original = archive.program
    moving = archive.steps[keep:]
    rows = archive.container.tables["Instance"]
    fresh = (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))

    document = archive.document(original)
    row = dict(zip(rows.columns, rows.rows[rows.find("Id", original.id)[0]]))
    row["Id"], row["Element_id"], row["ObjectId"] = fresh
    row["Children"] = pack_guids([s.instance.element_id for s in moving])
    rows.append(row)
    elements = archive.container.tables["Element"]
    source = elements.rows[elements.find("Id", original.element_id)[0]]
    elements.append(dict(zip(elements.columns, (fresh[1], source[1], None))))
    archive.container.tables["InstanceChangeSet"].append(
        {"InstanceId": fresh[0], "ChangeSetId": archive.head, "ElementId": fresh[1], "State": 0}
    )
    maps = archive.container.tables["ElementToInstanceMap"]
    at = maps.find("Id", generate._head_map_id(archive))[0]
    blob = maps.rows[at][maps.index_of("Data")]
    maps.set(at, "Data", blob + uuid.UUID(fresh[1]).bytes_le + uuid.UUID(fresh[0]).bytes_le)

    for step in moving:
        rows.set(rows.find("Id", step.instance.id)[0], "ParentElementId", fresh[1])

    ids = [s.instance.object_id for s in moving]
    _rewrite_program(
        archive, original, document, [s.instance.object_id for s in archive.steps[:keep]]
    )
    moved = dict(document)
    _rewrite_program(archive, None, moved, ids)
    archive.container.tables["Instance"].set(
        rows.find("Id", fresh[0])[0],
        "ContentHash",
        archive.replace_content(
            Instance(
                id=fresh[0],
                element_id=fresh[1],
                object_id=fresh[2],
                kind=original.kind,
                content_hash=original.content_hash,
            ),
            generate.renumber_references(moved),
        ),
    )
    keeping = [s.instance.element_id for s in archive.steps[:keep]]
    rows.set(rows.find("Id", original.id)[0], "Children", pack_guids(keeping))
    return fresh[2]


def _rewrite_program(archive: object, node: object, document: dict, ids: list) -> None:
    """Rebuild a program document's chain and maps around ``ids``.

    Parameters
    ----------
    archive : Archive
        The archive being edited.
    node : Instance or None
        The program to store the result on, or ``None`` to only edit
        ``document`` in place.
    document : dict
        The program content to rewrite.
    ids : list of str
        Step object ids, in the running order they should take.

    Returns
    -------
    None
    """
    document["FirstStepId"] = ids[0]
    document["LastStepId"] = ids[-1]
    document["Ranks"] = {"$id": "ranks"} | {
        one: {"$id": f"rk-{one}", "Rank": n, "StepId": one} for n, one in enumerate(ids)
    }
    links = {}
    for n, one in enumerate(ids[:-1]):
        links[one] = {
            "$id": f"lf-{one}",
            "$values": [
                {
                    "$id": f"link-{ids[n + 1]}",
                    "$type": generate.LINK_TYPE,
                    "ConditionId": generate.NO_GUID,
                    "SelectionId": generate.NO_GUID,
                    "SourceId": one,
                    "TargetId": ids[n + 1],
                }
            ],
        }
    links[ids[-1]] = {"$id": f"lf-{ids[-1]}", "$values": []}
    document["LinksFrom"] = {"$id": "lfrom"} | links
    document["LinksTo"] = {"$id": "lto"} | {
        one: {
            "$id": f"lt-{one}",
            "$values": [] if n == 0 else [{"$ref": f"link-{one}"}],
        }
        for n, one in enumerate(ids)
    }
    # Distinct prefixes: both names start "Re", and a shared $id makes the
    # document illegal in a way renumbering cannot repair.
    for name, tag in (("RelationsFrom", "rf"), ("RelationsTo", "rt")):
        document[name] = {"$id": name.lower()} | {
            one: {"$id": f"{tag}-{one}", "$values": []} for one in ids
        }
    if node is not None:
        archive.replace_content(node, generate.renumber_references(document))


@requires_exar
def test_an_archive_with_several_programs_reads_all_of_them(tmp_path: pathlib.Path) -> None:
    """Every protocol in a backup is read, not just the first.

    The XA30 backup that prompted this holds seven programs and 43 steps; a
    reader taking the first program described eight of them and looked like
    it had read the file. The split is staged here, but the failure it guards
    is the one that archive produced.

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
    _second_program(archive, keep=10)
    written = tmp_path / "backup.exar1"
    archive.write(str(written))

    split = read(str(written))
    assert len(split.program_nodes) == 2
    assert [len(one.steps) for one in split.programs] == [10, len(before) - 10]
    # Every step still readable, and each program keeps its own running order.
    assert [s.name for s in split.steps] == before
    assert validate.problems(split) == []


@requires_exar
def test_asking_for_the_only_program_refuses_when_there_are_several(
    tmp_path: pathlib.Path,
) -> None:
    """``program`` may not answer when the answer would be a guess.

    Returning the first of several is what made the original defect invisible:
    it reads as success while hiding every other protocol in the file.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the written archive.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    assert archive.program is not None
    _second_program(archive, keep=10)
    written = tmp_path / "backup.exar1"
    archive.write(str(written))
    split = read(str(written))

    with pytest.raises(ValueError, match="2 programs"):
        _ = split.program
    with pytest.raises(ValueError, match="2 programs"):
        generate.duplicate_step(split, split.steps[0], "NOWHERE_TO_PUT_IT")

    # Naming one is enough, and the scan lands in that protocol and no other.
    second = split.programs[1]
    generate.duplicate_step(split, split.steps[0], "INTO_THE_SECOND", program=second.instance)
    grown = tmp_path / "grown.exar1"
    split.write(str(grown))
    final = read(str(grown))
    assert validate.problems(final) == []
    assert [len(one.steps) for one in final.programs] == [10, 9]
    assert final.programs[1].steps[-1].name == "INTO_THE_SECOND"


@requires_exar
def test_every_step_in_a_corpus_archive_belongs_to_exactly_one_program(
    protocol_archive_path: str,
) -> None:
    """No step is orphaned, and a shared one really is shared.

    Written archive-wide rather than per-program so that a multi-protocol
    export tightens it rather than needing a new test -- which is what
    happened. "Claimed exactly once" held across every single-protocol export
    and turned out to be a fact about those rather than about the format:
    copying a protocol within a directory reuses the source's step nodes for
    the scans the copy did not change.

    So a step run by several programs is legitimate, and what is checked is
    that it is one node rather than a GUID-space confusion -- listed in the
    ``Children`` of exactly one of its programs, which is the property that
    would break if two distinct steps were being collapsed onto one object id.

    Parameters
    ----------
    protocol_archive_path : str
        One archive from the corpus that carries protocols.

    Returns
    -------
    None
    """
    archive = read(protocol_archive_path)
    claimed = [s.instance.object_id for one in archive.programs for s in one.steps]
    live = {i.object_id for i in archive.instances.values() if i.kind in STEP_KINDS}
    running = {}
    for one in archive.programs:
        for step in one.steps:
            running.setdefault(step.instance.object_id, []).append(one)
    for object_id, programs in running.items():
        if len(programs) == 1:
            continue
        element = archive.by_object[object_id].element_id
        owners = [one for one in programs if element in set(one.instance.children)]
        assert len(owners) == 1, (
            f"a step run by {len(programs)} programs is a child of {len(owners)}; "
            "one node shared is expected, two nodes collapsed is not"
        )
    assert set(claimed) == live, "a step is in no running order"


@requires_exar
def test_a_step_no_program_runs_is_reported(tmp_path: pathlib.Path) -> None:
    """A step in the file but in no running order must be named.

    This is the archive-wide half of the running-order check, and it exists
    because the per-program half cannot see it: each program is compared
    against its *own* children, so a step dropped from every chain leaves
    each program internally consistent. That was the old check's real
    content, and moving to several programs would have quietly lost it.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the written archive.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    _second_program(archive, keep=10)
    written = tmp_path / "backup.exar1"
    archive.write(str(written))
    split = read(str(written))
    assert validate.problems(split) == []

    # Shorten the second program's chain by one, leaving the step in the file.
    second = split.programs[1]
    document = split.document(second.instance)
    kept = [s.instance.object_id for s in second.steps[:-1]]
    _rewrite_program(split, second.instance, document, kept)
    rows = split.container.tables["Instance"]
    at = rows.find("Id", second.instance.id)[0]
    split.container.tables["Instance"].set(
        at, "Children", pack_guids([s.instance.element_id for s in second.steps[:-1]])
    )
    orphaned = tmp_path / "orphaned.exar1"
    split.write(str(orphaned))

    problems = validate.problems(read(str(orphaned)))
    assert any("no program's running order" in one for one in problems), problems


@requires_exar
def test_a_created_link_reproduces_the_console_byte_for_byte(tmp_path: pathlib.Path) -> None:
    """Stripping an export's links and rewriting them must restore them exactly.

    ``copyparametertest`` slaves eleven scans to a twelfth and exercises all
    ten copy-reference groups plus both flags, so it is the answer key rather
    than a sample. The payload is compared as the stored string, not as
    decoded fields: it is an XML element the console emits, and attribute
    order is a guess until something checks it.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the written archives.

    Returns
    -------
    None
    """
    source = read(find_exar("copyparametertest.exar1"))
    program = source.programs[0]
    wanted = _relations(source, program.instance)
    assert len(wanted) == 10 or len(wanted) >= 10, f"{len(wanted)} relations to reproduce"
    groups = {one.group for one in program.copy_references}
    assert len(groups) >= 10, f"the answer key exercises only {len(groups)} groups"

    document = copy.deepcopy(source.document(program.instance))
    for table in ("RelationsFrom", "RelationsTo"):
        for key in [k for k in document[table] if k != "$id"]:
            document[table][key]["$values"] = []
    source.replace_content(program.instance, generate.renumber_references(document))
    stripped = tmp_path / "stripped.exar1"
    source.write(str(stripped))

    blank = read(str(stripped))
    assert not blank.programs[0].copy_references, "the strip left links behind"
    steps = {s.instance.object_id: s for s in blank.programs[0].steps}
    for link in read(find_exar("copyparametertest.exar1")).programs[0].copy_references:
        generate.link_steps(
            blank,
            steps[link.source],
            steps[link.target],
            group=link.group,
            copy_phase_encoding=link.copies_phase_encoding_direction,
            copy_steps=link.copies_steps,
            ignore_last_step=link.ignores_last_step,
            ignore_measurements=link.ignores_measurements,
        )
    relinked = tmp_path / "relinked.exar1"
    blank.write(str(relinked))

    final = read(str(relinked))
    assert validate.problems(final) == []
    rebuilt = _relations(final, final.programs[0].instance)
    assert set(rebuilt) == set(wanted), "a link went missing or moved between steps"
    for key, console in wanted.items():
        for field in ("Data", "Kind", "Constraint", "State", "$type"):
            assert rebuilt[key][field] == console[field], (
                f"{field} differs from the console: {console[field]!r} "
                f"against {rebuilt[key][field]!r}"
            )


def _relations(archive: object, program: object) -> dict[tuple[str, str], dict]:
    """Return a program's relations keyed by the pair of steps they join.

    Parameters
    ----------
    archive : Archive
        The archive holding the program.
    program : Instance
        The program node.

    Returns
    -------
    dict
        ``(source, target)`` to the raw relation object.
    """
    document = archive.document(program)
    found = {}
    for key in [k for k in document.get("RelationsFrom", {}) if k != "$id"]:
        for relation in document["RelationsFrom"][key].get("$values", []):
            found[(relation["SourceId"], relation["TargetId"])] = relation
    return found


@requires_exar
def test_an_unknown_copy_reference_group_is_refused() -> None:
    """A group the console does not offer must not be written.

    The vocabulary is the console's menu, established by one export that
    exercises every item. Accepting a name outside it would put a payload in
    the archive that no dialog can have produced.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    first, second = archive.steps[0], archive.steps[1]
    with pytest.raises(ValueError, match="not a copy-reference group"):
        generate.link_steps(archive, first, second, group="SlicesAndEverything")
