"""Tests for generating probe archives and decoding their returns.

A probe archive asks a scanner a question this library cannot answer on its
own: it writes a stored field whose printed meaning is unknown, and the
returned printout says which label -- if any -- that field drives. Nothing
here can establish that a probed value is valid; that remains what only a
scanner says. What these tests hold is that a probe run writes what it
claims to write, that a refusal is never reported as a write, and that the
decode joins a return back to the questions it answers.

The anchor miner is tested by calibration rather than by example. A curated
table of anchors already exists, read off the console's own output, so a
miner that cannot recover those cannot be trusted about a key nobody has
curated -- which is the whole reason to have one.
"""

from __future__ import annotations

import pathlib

import pytest

from conftest import find_exar, requires_exar  # noqa: F401
from siemens_protocol.analysis.generate import probe
from siemens_protocol.exar import ascconv, inspect, read, validate

#: A template known to hold a CMRR multiband scan, which is the sequence the
#: corpus has most evidence about and so the one a probe run starts from.
TEMPLATE = "Potpourri_P1.exar1"

#: The known-good scan every probe in these tests is a copy of.
SOURCE = "Minn_CMRR_2.3mm_S8_rest_6min"

#: A one-scan export, used as the seed a probe run grows from so the archive
#: carries the probes and almost nothing else.
SEED = "VASO test.exar1"


def _donor_text() -> str:
    """Return the XProtocol text of the scan the probes copy.

    Returns
    -------
    str
        The template scan's XProtocol, ASCCONV block included.
    """
    archive = read(find_exar(TEMPLATE))
    for step in archive.steps:
        if step.name == SOURCE:
            return step.protocol.xprotocol
    pytest.skip(f"{TEMPLATE} no longer holds {SOURCE}")


@requires_exar
def test_writing_an_existing_assignment_replaces_it() -> None:
    """A key the protocol already carries is overwritten in place.

    Returns
    -------
    None
    """
    text = _donor_text()
    assert ascconv.read_ascconv(text, "alTR[0]") is not None
    after, how = probe.write_key(text, "alTR[0]", "660000")
    assert how == "set"
    assert ascconv.read_ascconv(after, "alTR[0]") == "660000"


@requires_exar
def test_removing_an_assignment_is_how_a_sparse_array_spells_zero() -> None:
    """Deleting a key is a probe in its own right and is reported as one.

    Returns
    -------
    None
    """
    text = _donor_text()
    after, how = probe.write_key(text, "alTR[0]", None)
    assert how == "removed"
    assert ascconv.read_ascconv(after, "alTR[0]") is None
    again, how = probe.write_key(after, "alTR[0]", None)
    assert how == "unchanged"
    assert again == after


@requires_exar
def test_a_refusal_is_never_reported_as_a_write() -> None:
    """An assignment with nowhere to go leaves the text alone and says so.

    This is the failure the shape invites: :func:`ascconv.insert_ascconv`
    reports "nowhere to put it" by returning the text unchanged, so a caller
    that does not check records a write that wrote nothing.

    Returns
    -------
    None
    """
    text = _donor_text()
    key = "sNothing.ucInvented"
    assert ascconv.read_ascconv(text, key) is None
    after, how = probe.write_key(text, key, "1", anchors=("sAlsoNotThere.ucMissing",))
    assert how == "refused"
    assert after == text


@requires_exar
def test_the_anchor_miner_recovers_the_curated_anchors() -> None:
    """Calibration: a miner that misses a known answer proves nothing.

    Every entry in :data:`ascconv.SPARSE_ANCHORS` was read off the console's
    own output, so each is a question whose answer is already known. The
    miner is only worth pointing at an uncurated key if it reproduces these.

    The claim is membership rather than equality, and the distinction is the
    curated table's own: its values are tuples because a predecessor can be
    sparse and absent from the protocol at hand. ``sAdjData.uiAdjWithBC`` is
    that case here -- the template omits the curated first choice, so the
    miner reads the second, which is the table agreeing with itself rather
    than the miner disagreeing with it.

    Returns
    -------
    None
    """
    protocols = probe.acquiring_protocols([find_exar(TEMPLATE)])
    checked = 0
    for key, curated in ascconv.SPARSE_ANCHORS.items():
        mined = probe.mine_anchor(key, protocols)
        if not mined:
            continue
        checked += 1
        assert mined[0] in curated, f"{key} was mined as {mined[0]!r}, curated {curated}"
    assert checked >= 3, "too few curated anchors appear in the template to calibrate against"


@requires_exar
def test_a_mined_ladder_falls_back_past_an_absent_anchor() -> None:
    """The nearest predecessor is often sparse itself, so more are offered.

    Returns
    -------
    None
    """
    protocols = probe.acquiring_protocols([find_exar(TEMPLATE)])
    ladder = probe.mine_anchor("sRawFilter.ucMode", protocols)
    if not ladder:
        pytest.skip("no protocol in the template carries sRawFilter.ucMode")
    assert len(ladder) > 1, "a single anchor leaves an absent one with no fallback"
    assert len(set(ladder)) == len(ladder), "the ladder repeats a rung"


@requires_exar
def test_ranking_candidates_skips_what_is_mapped_or_never_probed() -> None:
    """The candidate list is the settable surface, not every assignment.

    Returns
    -------
    None
    """
    protocols = probe.acquiring_protocols([find_exar(TEMPLATE)], "cmrr_mbep2d")
    if not protocols:
        pytest.skip("the template holds no CMRR multiband scan")
    ranked = probe.settable_keys(protocols)
    assert not [key for key in ranked if key.startswith(probe.NEVER_PROBE)]
    assert not [key for key in ranked if key in probe.DERIVED_KEYS]
    assert "alTR[0]" not in ranked, "TR is mapped and should not be offered as a candidate"
    assert all(count > 1 for count in ranked.values()), "a constant key is not a candidate"


def _pilot(destination: pathlib.Path, literals: dict[str, str]) -> probe.ProbeManifest:
    """Build a small probe archive for the tests to read.

    Parameters
    ----------
    destination : pathlib.Path
        Where to write the archive.
    literals : dict
        ASCCONV key to the literal to probe it with.

    Returns
    -------
    probe.ProbeManifest
        The manifest describing what was written.
    """
    template = find_exar(TEMPLATE)
    protocols = probe.acquiring_protocols([template])
    probes = [
        probe.Probe(key, literal, key.split(".")[-1], anchors=probe.mine_anchor(key, protocols))
        for key, literal in literals.items()
    ]
    return probe.build_probes(
        probes,
        template,
        SOURCE,
        str(destination),
        seed_path=find_exar(SEED),
        program="PROBE_TEST",
        controls=2,
    )


@requires_exar
def test_a_probe_archive_validates_and_holds_every_value_it_wrote(
    tmp_path: pathlib.Path,
) -> None:
    """The assembled archive is sound and each probe reads back.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive.

    Returns
    -------
    None
    """
    out = tmp_path / "probe.exar1"
    manifest = _pilot(out, {"alTR[0]": "660000", "sKSpace.lBaseResolution": "96"})
    grown = read(str(out))
    assert validate.problems(grown) == []
    steps = {step.name: step for step in grown.steps}
    for placed in manifest.placed:
        assert placed.how in ("set", "created"), f"{placed.name} was {placed.how}"
        table = inspect.ascconv_table(steps[placed.name].protocol.xprotocol)
        assert table.get(placed.probe.key) == placed.probe.literal


@requires_exar
def test_every_probe_gets_a_protocol_of_its_own(tmp_path: pathlib.Path) -> None:
    """A copy served its source's protocol once, and silently.

    A duplicated step keeps a ``ParentElementId`` pointing at its own step,
    and a copy that kept the source's was handed the source's protocol --
    invisible while the copy is identical and fatal once it is not. Distinct
    content hashes are what say each probe carries its own edit.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive.

    Returns
    -------
    None
    """
    out = tmp_path / "probe.exar1"
    manifest = _pilot(out, {"alTR[0]": "660000", "sKSpace.lBaseResolution": "96"})
    steps = {step.name: step for step in read(str(out)).steps}
    hashes = {name: steps[name].protocol.instance.content_hash for name in steps}
    probed = [placed.name for placed in manifest.placed]
    assert len(set(hashes[name] for name in probed)) == len(probed)
    for name in probed:
        assert hashes[name] != hashes[manifest.controls[0]]


@requires_exar
def test_the_controls_are_byte_identical_to_the_scan_they_copy(
    tmp_path: pathlib.Path,
) -> None:
    """A control has to be the template untouched or it cannot be a baseline.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive.

    Returns
    -------
    None
    """
    out = tmp_path / "probe.exar1"
    manifest = _pilot(out, {"alTR[0]": "660000"})
    steps = {step.name: step for step in read(str(out)).steps}
    assert manifest.controls, "a probe run without a control cannot be decoded"
    for name in manifest.controls:
        assert steps[name].protocol.xprotocol == _donor_text()


@requires_exar
def test_running_order_puts_a_control_at_each_end(tmp_path: pathlib.Path) -> None:
    """Probes run between controls, and the order is the one asked for.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive.

    Returns
    -------
    None
    """
    out = tmp_path / "probe.exar1"
    manifest = _pilot(out, {"alTR[0]": "660000", "sKSpace.lBaseResolution": "96"})
    names = [step.name for step in read(str(out)).steps]
    probed = [name for name in names if name.startswith("P")]
    controls = [name for name in names if name in manifest.controls]
    assert len(controls) == 2
    assert names.index(controls[0]) < min(names.index(n) for n in probed)
    assert names.index(controls[-1]) > max(names.index(n) for n in probed)


@requires_exar
def test_a_manifest_survives_a_round_trip_through_json(tmp_path: pathlib.Path) -> None:
    """The manifest is what decodes a return weeks later, so it must reload.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive and manifest.

    Returns
    -------
    None
    """
    out = tmp_path / "probe.exar1"
    manifest = _pilot(out, {"alTR[0]": "660000"})
    written = tmp_path / "probe.json"
    manifest.to_json(str(written))
    again = probe.ProbeManifest.from_json(str(written))
    assert again.placed == manifest.placed
    assert again.controls == manifest.controls
    assert isinstance(again.placed[0].probe.anchors, tuple)


@requires_exar
def test_decoding_a_run_against_itself_reports_every_value_held(
    tmp_path: pathlib.Path,
) -> None:
    """A return identical to what was sent is the no-op the decode must see.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive.

    Returns
    -------
    None
    """
    out = tmp_path / "probe.exar1"
    manifest = _pilot(out, {"alTR[0]": "660000", "sKSpace.lBaseResolution": "96"})
    findings = probe.decode(manifest, str(out), [])
    assert findings
    assert {found.verdict for found in findings} == {"held"}
    assert not any(found.recomputed for found in findings)


@requires_exar
def test_a_scan_missing_from_the_return_reads_as_inconsistent(
    tmp_path: pathlib.Path,
) -> None:
    """A deleted scan is the console's verdict, not a decode failure.

    The workflow this module serves has the operator delete a scan the
    console greys out, because an inconsistent scan cannot be saved or
    printed at all. So a probe absent from the return is the informative
    outcome and has to be reported as one.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive.

    Returns
    -------
    None
    """
    out = tmp_path / "probe.exar1"
    manifest = _pilot(out, {"alTR[0]": "660000", "sKSpace.lBaseResolution": "96"})
    dropped = manifest.placed[0].name
    trimmed = tmp_path / "returned.exar1"
    _write_without(str(out), str(trimmed), dropped)
    findings = {found.name: found for found in probe.decode(manifest, str(trimmed), [])}
    assert findings[dropped].verdict == "inconsistent"
    assert findings[manifest.placed[1].name].verdict == "held"


def _write_without(source: str, destination: str, name: str) -> None:
    """Copy an archive with one scan's label changed out of recognition.

    Deleting a step properly is not something this library does, and the
    decode only asks whether a probe's *name* came back. Renaming the scan
    reproduces what the console leaves behind after an operator deletes it,
    as far as the join is concerned.

    Parameters
    ----------
    source : str
        Archive to copy.
    destination : str
        Where to write the copy.
    name : str
        Scan name to make unrecognisable.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import generate

    archive = read(source)
    for step in archive.steps:
        if step.name == name:
            generate.rename(archive, step.instance, "DELETED_ON_THE_CONSOLE")
            break
    archive.write(destination)


@requires_exar
def test_a_printed_difference_is_read_per_section(tmp_path: pathlib.Path) -> None:
    """Flattening a scan first is the trap the Position note describes.

    A label printed on several cards collapses to whichever won, so the
    comparison has to be keyed by the section that printed it.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Unused; present so the test reads like its neighbours.

    Returns
    -------
    None
    """
    scan = {
        "name": "one",
        "sections": {
            "Routine": {"Position": "L1.2 P3.4 H5.6"},
            "System - Adjust Volume": {"Position": "Isocentre"},
        },
    }
    indexed = probe.printed_by_section(scan)
    assert indexed[("Routine", "Position")] != indexed[("System - Adjust Volume", "Position")]
    assert len(indexed) == 2


@requires_exar
def test_a_label_printed_on_only_one_side_is_still_a_difference() -> None:
    """A probe that makes the console print a *new* row must not read as inert.

    Restricting the comparison to labels common to both scans is the quiet
    version of this bug, and it hid a real result: turning a physio signal on
    made the console add an ``Average Cycle`` row the control does not have,
    and the probe was reported as having changed nothing at all.

    Returns
    -------
    None
    """
    control = {"name": "c", "sections": {"Physio - Signal": {"1st Signal/Mode": "None"}}}
    probed = {
        "name": "p",
        "sections": {"Physio - Signal": {"1st Signal/Mode": "None", "Average Cycle": "No Signal"}},
    }
    reference = probe.printed_by_section(control)
    mine = probe.printed_by_section(probed)
    appeared = {
        where: (reference.get(where, probe.NOT_PRINTED), value)
        for where, value in mine.items()
        if reference.get(where, probe.NOT_PRINTED) != value
    }
    assert appeared == {("Physio - Signal", "Average Cycle"): (probe.NOT_PRINTED, "No Signal")}
    assert probe.NOT_PRINTED != "", "the sentinel must differ from a blanked value"


@requires_exar
def test_a_manifest_finds_its_archive_after_both_are_moved(
    tmp_path: pathlib.Path,
) -> None:
    """A manifest and its archive travel together, so the pair must reconnect.

    The manifest records the absolute path its archive was written to, and
    that path stops resolving the moment the two are moved out of the build
    directory -- which is every real use, since the archive goes to a scanner
    and comes back somewhere else.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive and manifest.

    Returns
    -------
    None
    """
    built = tmp_path / "built"
    built.mkdir()
    manifest = _pilot(built / "probe.exar1", {"alTR[0]": "660000"})
    manifest.to_json(str(built / "probe.json"))

    moved = tmp_path / "moved"
    moved.mkdir()
    for name in ("probe.exar1", "probe.json"):
        (moved / name).write_bytes((built / name).read_bytes())
    for name in ("probe.exar1", "probe.json"):
        (built / name).unlink()

    again = probe.ProbeManifest.from_json(str(moved / "probe.json"))
    assert again.outbound == str(moved / "probe.exar1")
    assert probe.decode(again, str(moved / "probe.exar1"), [])[0].verdict == "held"


@requires_exar
def test_context_is_written_before_the_question_it_enables(
    tmp_path: pathlib.Path,
) -> None:
    """A sub-option beneath an off switch cannot answer anything.

    Run 1 wrote ``sRawFilter.ucMode`` faithfully into a protocol whose
    ``ucOn`` was absent, and the scanner kept it and printed nothing --
    a probe that cost a scan and settled nothing. The context is what puts
    the question somewhere it can be seen.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the archive.

    Returns
    -------
    None
    """
    template = find_exar(TEMPLATE)
    protocols = probe.acquiring_protocols([template])
    switch = probe.Probe(
        "sRawFilter.ucOn",
        "1",
        "RawFilterOn",
        anchors=probe.mine_anchor("sRawFilter.ucOn", protocols),
    )
    asked = probe.Probe(
        "sRawFilter.ucMode",
        "4",
        "RawFilterMode4",
        anchors=probe.mine_anchor("sRawFilter.ucMode", protocols),
        context=(switch,),
    )
    out = tmp_path / "probe.exar1"
    manifest = probe.build_probes(
        [asked], template, SOURCE, str(out), seed_path=find_exar(SEED), program="CTX", controls=1
    )
    placed = manifest.placed[0]
    assert placed.how in ("set", "created")
    assert placed.context == (("sRawFilter.ucOn", "1"),)

    steps = {step.name: step for step in read(str(out)).steps}
    table = inspect.ascconv_table(steps[placed.name].protocol.xprotocol)
    assert table.get("sRawFilter.ucMode") == "4", "the question was not written"
    assert table.get("sRawFilter.ucOn") == "1", "the context that makes it visible was not written"

    control = inspect.ascconv_table(steps[manifest.controls[0]].protocol.xprotocol)
    assert "sRawFilter.ucOn" not in control, "the context leaked into the control"


@requires_exar
def test_a_probe_whose_context_cannot_be_written_asks_nothing() -> None:
    """A question posed in the wrong state is not a result, so it is not one.

    Returns
    -------
    None
    """
    text = _donor_text()
    switch = probe.Probe("sNothing.ucInvented", "1", "ghost", anchors=("sAlsoNotThere.ucMissing",))
    asked = probe.Probe("alTR[0]", "660000", "TR", context=(switch,))

    class _Stub:
        document = {"Data": text}

    _, how, _, _, _ = probe.apply_probe(_Stub(), asked)
    assert how.startswith("context refused")
    assert "sNothing.ucInvented" in how


def test_a_respelled_number_is_the_value_that_was_written() -> None:
    """The console respells what it stores, and that is not a refusal.

    A ``uc`` flag written as ``1`` comes back as ``0x1``. Compared as strings
    that reads as the console having revised the value, which would make
    every flag probe -- the commonest kind on a Special card -- look like it
    had been rejected.

    Returns
    -------
    None
    """
    assert probe.same_value("0x1", "1")
    assert probe.same_value("650000", "650000")
    assert probe.same_value("2.50", "2.5")
    assert not probe.same_value("650000", "50000")
    assert not probe.same_value("0x2", "1")
    assert not probe.same_value('"text"', "1")
    assert not probe.same_value("1", None), "a deletion is not satisfied by a value"


def test_a_rejected_value_that_drags_other_fields_is_not_a_result() -> None:
    """Three outcomes share the shape "something else moved".

    They are separated by whether the probed value itself survived, which is
    what makes the distinction threshold-free: a value that held beside other
    movement is a parameter the sequence derives from it; a value reverted
    with nothing else moving is a field the sequence owns; a value reverted
    that took other fields with it is the console refusing the write and
    rebuilding the protocol around it.

    Returns
    -------
    None
    """
    asked = probe.Probe("alTR[0]", "50000", "TR")
    derived = probe.Finding(
        name="a", probe=asked, verdict="held", recomputed={"lTotalScanTimeSec": ("369", "374")}
    )
    owned = probe.Finding(name="b", probe=asked, verdict="revised", stored="650000")
    rebuilt = probe.Finding(
        name="c",
        probe=asked,
        verdict="revised",
        stored="650000",
        recomputed={
            "sCoilSelectMeas.aRxCoilSelectData[0].asList[5].lElementSelected": ("1", None)
        },
    )
    assert not derived.reconciled, "a value that held is not a refusal"
    assert not owned.reconciled, "a field the sequence owns is not a reconciliation"
    assert rebuilt.reconciled
    assert not rebuilt.clean


def test_a_switch_is_credited_with_what_the_switch_prints() -> None:
    """A probe under a switch must not inherit the switch's printed change.

    Run 2 wrote ``sHammingFilter.lWidthPercent`` beneath ``ucOn``, and the
    printed ``Hamming`` line went Off to On -- which is the switch's doing,
    already pinned by a probe in the same run that asked ``ucOn`` alone.
    Crediting it to the width would have invented a mapping.

    Returns
    -------
    None
    """
    switch = probe.Probe("sHammingFilter.ucOn", "1", "on")
    width = probe.Probe("sHammingFilter.lWidthPercent", "60", "w", context=(switch,))
    moved = {("Resolution - Filter", "Hamming"): ("Off", "On")}
    findings = [
        probe.Finding(name="a", probe=switch, verdict="held", printed=dict(moved)),
        probe.Finding(name="b", probe=width, verdict="held", printed=dict(moved)),
    ]
    probe._attribute_context(findings)

    assert findings[0].clean, "the switch itself names one label and is clean"
    assert findings[1].from_context == moved
    assert findings[1].own_printed == {}
    assert not findings[1].clean, "the width explains nothing of its own"
    assert not findings[1].confounded, "the control was present, so this is resolved"


def test_a_context_with_no_control_leaves_the_attribution_open() -> None:
    """Absence of a control is not evidence the probe caused what moved.

    Run 2 wrote ``sRawFilter.ucMode`` beneath ``ucOn`` but asked ``ucOn``
    alone only in run 1, so within run 2 nothing separates them. Reporting
    that probe as clean would credit the switch's line to the mode.

    Returns
    -------
    None
    """
    switch = probe.Probe("sRawFilter.ucOn", "1", "on")
    mode = probe.Probe("sRawFilter.ucMode", "4", "m", context=(switch,))
    findings = [
        probe.Finding(
            name="b",
            probe=mode,
            verdict="held",
            printed={("Resolution - Filter", "Raw Filter"): ("Off", "On")},
        )
    ]
    probe._attribute_context(findings)

    assert findings[0].uncontrolled == ("sRawFilter.ucOn",)
    assert findings[0].confounded
    assert not findings[0].clean
