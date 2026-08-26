"""Tests for writing a printed parameter back into an ``.exar1`` protocol.

The acceptance test this file is built around is not a round-trip: it is
whether the patcher, told to make the change the console made, produces the
values the console produced. ``Potpourri_changed.exar1`` is the same export
re-saved with TR altered on five scans, so it is a recorded answer key for one
parameter, and reproducing it end to end exercises the preview map, the
ASCCONV block, the unit scale, the re-hashing and the container rewiring at
once.

The rest of the file guards the ways a patcher fails quietly: writing one of
the two locations, writing a value into a protocol that has no such field,
guessing at an ambiguous label, or re-addressing content that it never
actually changed.
"""

from __future__ import annotations

import pathlib

import pytest

from conftest import find_exar, requires_exar
from siemens_protocol.exar import patch, read

#: The parameter the reference pair records a controlled edit for.
CONTROLLED = "TR"


def _changed_steps(source: str, target: str) -> dict[str, dict[str, float]]:
    """Derive the console's own edit from the reference pair.

    Parameters
    ----------
    source : str
        Path to the unmodified archive.
    target : str
        Path to the re-saved, modified archive.

    Returns
    -------
    dict
        Step name to the requested change, for every step whose TR moved.
    """
    before, after = read(source), read(target)
    wanted: dict[str, dict[str, float]] = {}
    for one, other in zip(before.steps, after.steps):
        was = one.protocol.by_label(CONTROLLED)[0].value
        now = other.protocol.by_label(CONTROLLED)[0].value
        if was != now:
            wanted[one.name] = {CONTROLLED: now}
    return wanted


# --------------------------------------------------------------------------
# The answer key: reproducing the console's own edit
# --------------------------------------------------------------------------


@requires_exar
def test_patching_reproduces_the_console_edit(tmp_path: pathlib.Path) -> None:
    """Told to make the console's change, the patcher writes what it wrote.

    Both storage locations are compared for every scan, changed and unchanged
    alike, so a patch that edited the wrong protocol fails here too.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the patched archive.

    Returns
    -------
    None
    """
    source = find_exar("Potpourri.exar1")
    target = find_exar("Potpourri_changed.exar1")
    wanted = _changed_steps(source, target)
    assert wanted, "the reference pair records no TR change to reproduce"

    archive = read(source)
    manifest = patch.apply(archive, wanted)
    assert manifest.complete, manifest.report()
    assert len(manifest.applied) == len(wanted)

    written = tmp_path / "patched.exar1"
    archive.write(str(written))

    ours, theirs = read(str(written)), read(target)
    for one, other in zip(ours.steps, theirs.steps):
        assert one.name == other.name
        assert (
            one.protocol.by_label(CONTROLLED)[0].value
            == other.protocol.by_label(CONTROLLED)[0].value
        ), f"preview TR differs on {one.name}"
        assert patch.read_ascconv(one.protocol.xprotocol, "alTR[0]") == patch.read_ascconv(
            other.protocol.xprotocol, "alTR[0]"
        ), f"ASCCONV alTR[0] differs on {one.name}"


@requires_exar
def test_a_patch_writes_both_locations_not_just_the_preview() -> None:
    """The preview and the ASCCONV block move together.

    Writing only the preview is the failure that looks most like success: the
    console lists the new number and the scan runs the old one.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    step = archive.steps[0]
    before = patch.read_ascconv(step.protocol.xprotocol, "alTR[0]")
    document, applied, skipped = patch.patch_document(step.protocol, {"TR": 1234.0})
    assert not skipped and len(applied) == 1
    assert document["Preview"]["sub.0.msr.tr.0"]["Value"] == 1234.0
    assert patch.read_ascconv(document["Data"], "alTR[0]") == "1234000"
    assert before != "1234000", "the fixture already held the value under test"


# --------------------------------------------------------------------------
# Units, formatting and layout
# --------------------------------------------------------------------------


@requires_exar
def test_the_millisecond_to_microsecond_scale_is_applied() -> None:
    """A preview value in ms becomes an ASCCONV value in us.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    document, applied, _ = patch.patch_document(archive.steps[0].protocol, {"TR": 2000.0})
    assert applied[0].value == 2000.0
    assert patch.read_ascconv(document["Data"], "alTR[0]") == "2000000"


def test_an_integer_literal_stays_an_integer() -> None:
    """Formatting follows the literal already in place, not the Python type.

    Returns
    -------
    None
    """
    assert patch.format_like(2000000.0, "650000") == "2000000"
    assert patch.format_like(90.0, "80.0") == "90.0"


def test_rewriting_an_assignment_preserves_its_separator() -> None:
    """The tab-space layout around ``=`` survives an edit.

    Numaris/X flips between ``\\t = \\t`` and ``  =  `` on its own between
    saves, so normalizing here would add churn unrelated to the edit and make
    a later diff harder to read.

    Returns
    -------
    None
    """
    text = "### ASCCONV BEGIN ###\nalTR[0]\t = \t650000\n### ASCCONV END ###\n"
    assert patch.write_ascconv(text, "alTR[0]", "651000") == text.replace("650000", "651000")
    spaced = text.replace("\t = \t", "  =  ")
    assert patch.write_ascconv(spaced, "alTR[0]", "651000") == spaced.replace("650000", "651000")


def test_only_the_named_assignment_is_rewritten() -> None:
    """A key that is a prefix of another is not confused with it.

    Returns
    -------
    None
    """
    text = "### ASCCONV BEGIN ###\nalTR[0]\t = \t100\nalTR[01]\t = \t200\n### ASCCONV END ###\n"
    result = patch.write_ascconv(text, "alTR[0]", "999")
    assert "alTR[0]\t = \t999" in result
    assert "alTR[01]\t = \t200" in result


def test_an_assignment_outside_the_ascconv_block_is_left_alone() -> None:
    """Only the ASCCONV block is edited, not the XProtocol tree above it.

    Returns
    -------
    None
    """
    text = "alTR[0]  = 1\n### ASCCONV BEGIN ###\nalTR[0]\t = \t650000\n### ASCCONV END ###\n"
    result = patch.write_ascconv(text, "alTR[0]", "42")
    assert result.startswith("alTR[0]  = 1\n")
    assert "\t = \t42" in result


# --------------------------------------------------------------------------
# Refusing rather than guessing
# --------------------------------------------------------------------------


@requires_exar
def test_an_unmapped_label_is_reported_not_written() -> None:
    """A parameter the PDF prints but no mapping covers is skipped by name.

    ``FOV Read`` is the live example: two ASCCONV keys hold the same number
    throughout the corpus, so no mapping ships and the request must surface
    rather than pick one.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    _, applied, skipped = patch.patch_document(archive.steps[0].protocol, {"FOV Read": 210.0})
    assert not applied
    assert len(skipped) == 1
    assert (
        "no verified mapping" in skipped[0].reason
        or "printed by this protocol" in skipped[0].reason
    )


@requires_exar
def test_a_request_for_a_missing_step_is_skipped_with_a_reason() -> None:
    """Naming a step the archive does not hold does not fail silently.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    manifest = patch.apply(archive, {"no_such_scan": {"TR": 100.0}})
    assert not manifest.complete
    assert manifest.skipped[0].reason == "no such step in archive"


@requires_exar
def test_a_protocol_without_the_field_is_skipped_not_invented() -> None:
    """A mapping whose preview path this protocol lacks is refused.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    lacking = [s for s in archive.steps if "sub.0.msr.ips" not in s.protocol.preview]
    if not lacking:
        pytest.skip("every protocol in this archive carries sub.0.msr.ips")
    _, applied, skipped = patch.patch_document(lacking[0].protocol, {"Slices per Slab": 9})
    assert not applied
    assert "no sub.0.msr.ips" in skipped[0].reason


# --------------------------------------------------------------------------
# The container: re-addressing, and leaving untouched content alone
# --------------------------------------------------------------------------


@requires_exar
def test_a_patch_re_addresses_only_the_protocol_it_touched(tmp_path: pathlib.Path) -> None:
    """Editing one scan leaves every other content blob at its old hash.

    Content is deduplicated by hash, so repointing by hash rather than by
    instance id would edit protocols the caller never named.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the patched archive.

    Returns
    -------
    None
    """
    path = find_exar("Potpourri.exar1")
    archive = read(path)
    name = archive.steps[0].name
    before = {s.name: s.protocol.instance.content_hash for s in archive.steps}

    patch.apply(archive, {name: {"TR": 1234.0}})
    after = {s.name: s.protocol.instance.content_hash for s in archive.steps}

    assert after[name] != before[name]
    moved = [n for n in before if after[n] != before[n]]
    assert moved == [name], f"unrelated protocols were re-addressed: {moved}"


@requires_exar
def test_a_run_that_writes_nothing_leaves_the_archive_alone(tmp_path: pathlib.Path) -> None:
    """A wholly skipped request re-addresses no content at all.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    before = {s.name: s.protocol.instance.content_hash for s in archive.steps}
    manifest = patch.apply(archive, {archive.steps[0].name: {"Nonexistent Parameter": 1}})
    assert not manifest.applied
    after = {s.name: s.protocol.instance.content_hash for s in archive.steps}
    assert after == before


@requires_exar
def test_the_patched_archive_survives_a_write_and_read(tmp_path: pathlib.Path) -> None:
    """A patched archive reads back with the same tree and the new value.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the patched archive.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    name = archive.steps[3].name
    patch.apply(archive, {name: {"TR": 1750.0}})
    written = tmp_path / "patched.exar1"
    archive.write(str(written))

    back = read(str(written))
    assert [s.name for s in back.steps] == [s.name for s in archive.steps]
    found = {s.name: s for s in back.steps}[name]
    assert found.protocol.by_label("TR")[0].value == 1750.0
    assert patch.read_ascconv(found.protocol.xprotocol, "alTR[0]") == "1750000"


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------


@requires_exar
def test_the_manifest_counts_what_it_did_not_write() -> None:
    """Inherited values are counted, because they are the standing risk.

    A patched protocol keeps whatever the source archive said for every
    parameter no request named, and a manifest that reported only successes
    would hide that.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    name = archive.steps[0].name
    manifest = patch.apply(archive, {name: {"TR": 900.0}})
    assert manifest.inherited > 0
    assert "inherited" in manifest.report()


@requires_exar
def test_the_manifest_names_the_values_the_console_would_recompute() -> None:
    """Scan time follows TR on the console and does not here.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri.exar1"))
    manifest = patch.apply(archive, {archive.steps[0].name: {"TR": 900.0}})
    assert "lScanTimeSec" in manifest.stale
    assert "not recomputed" in manifest.report()


# --------------------------------------------------------------------------
# The mapping table itself
# --------------------------------------------------------------------------


@requires_exar
def test_every_shipped_mapping_agrees_with_the_corpus(archive_path: str) -> None:
    """Each mapping's two locations hold the same value in every protocol.

    This is the evidence the table rests on, re-checked rather than trusted:
    a mapping whose scale or key is wrong shows up as a disagreement here.

    Parameters
    ----------
    archive_path : str
        One archive from the corpus.

    Returns
    -------
    None
    """
    archive = read(archive_path)
    checked = 0
    for step in archive.steps:
        protocol = step.protocol
        for mapping in patch.MAPPINGS.values():
            entry = protocol.preview.get(mapping.preview_path)
            literal = patch.read_ascconv(protocol.xprotocol, mapping.ascconv_key)
            if entry is None or literal is None or not isinstance(entry.value, (int, float)):
                continue
            checked += 1
            assert float(literal) == pytest.approx(entry.value * mapping.scale), (
                f"{step.name}: {mapping.label} preview {entry.value} does not match "
                f"{mapping.ascconv_key}={literal} at scale {mapping.scale}"
            )
    assert checked, "no mapping was exercised by this archive"


def test_every_mapping_records_how_it_was_established() -> None:
    """No mapping ships without stating its evidence.

    The table was derived from a corpus, not from documentation, and the
    difference between a controlled edit and mere agreement is what a later
    reader needs in order to know which entries to trust.

    Returns
    -------
    None
    """
    for path, mapping in patch.MAPPINGS.items():
        assert mapping.preview_path == path
        assert mapping.evidence.strip()
        assert mapping.scale > 0


def test_no_two_mappings_claim_the_same_ascconv_key() -> None:
    """Two preview paths writing one assignment would race each other.

    Returns
    -------
    None
    """
    keys = [m.ascconv_key for m in patch.MAPPINGS.values()]
    assert len(keys) == len(set(keys))
