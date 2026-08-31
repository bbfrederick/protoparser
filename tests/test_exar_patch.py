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

import collections
import json
import pathlib
import re
import subprocess
import sys

import pytest

from conftest import (  # noqa: F401
    EXAR_PROTOCOL_FILES,
    PARAMCHECK_IDS,
    PARAMCHECK_PAIRS,
    find_exar,
    find_pdf,
    protocol_archive_path,
    requires_exar,
    requires_paramcheck,
)
from siemens_protocol.exar import build, envelope, patch, read
from siemens_protocol.exar.archive import Protocol
from siemens_protocol.pipeline import parse_document

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
    source = find_exar("Potpourri_P2.exar1")
    target = find_exar("Potpourri_P2_changed.exar1")
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
def test_patching_reproduces_the_multi_parameter_console_edit(tmp_path: pathlib.Path) -> None:
    """The P1 pair is an answer key for every mapping at once.

    Potpourri_P1_changed is the same export re-saved after changing many
    parameters across five scans, Special card included. Asking the patcher for
    exactly those values and comparing every mapped assignment -- at every
    array index -- exercises scope, arrays, units, the derived basis, re-hashing
    and the container rewiring together.

    FOV Phase is compared to within the console's own rounding rather than
    exactly: it is quantised to a ratio the hardware can realise and printed to
    a tenth of a percent, so a percentage cannot reconstruct the millimetres it
    came from. Everything else must match byte for byte.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the patched archive.

    Returns
    -------
    None
    """
    source = find_exar("Potpourri_P1.exar1")
    target = find_exar("Potpourri_P1_changed.exar1")
    before, after = read(source), read(target)

    wanted: dict[str, dict[str, object]] = {}
    for one, other in zip(before.steps, after.steps):
        asked: dict[str, object] = {}
        for mapping in patch.MAPPINGS:
            if not patch.applies_to(mapping, one.protocol):
                continue
            if mapping.preview_path is not None:
                was = one.protocol.preview.get(mapping.preview_path)
                now = other.protocol.preview.get(mapping.preview_path)
                if was is not None and now is not None and was.value != now.value:
                    asked[mapping.label] = now.value
                continue
            was_raw = patch.read_ascconv(one.protocol.xprotocol, mapping.ascconv_key)
            now_raw = patch.read_ascconv(other.protocol.xprotocol, mapping.ascconv_key)
            if mapping.bit is not None:
                was_on = bool(int(was_raw or 0) >> mapping.bit & 1)
                now_on = bool(int(now_raw or 0) >> mapping.bit & 1)
                if was_on != now_on:
                    asked[mapping.label] = now_on
                continue
            if was_raw is not None and now_raw is not None and was_raw != now_raw:
                # A mapping with no preview side is read from the ASCCONV
                # block, which holds the *stored* number. Asking for it
                # verbatim only worked while every such mapping was a Special
                # card flag at scale 1; TE 2 stores microseconds, so the
                # request has to be turned back into what the card displays
                # or the patcher scales an already-scaled value.
                asked[mapping.label] = float(now_raw) / mapping.scale - mapping.offset
        if asked:
            wanted[one.name] = asked
    assert wanted, "the reference pair records no mapped change to reproduce"

    archive = read(source)
    manifest = patch.apply(archive, wanted)
    assert manifest.complete, manifest.report()
    written = tmp_path / "patched.exar1"
    archive.write(str(written))

    ours = read(str(written))
    exact = approximate = 0
    for one, other in zip(ours.steps, after.steps):
        for mapping in patch.MAPPINGS:
            if not patch.applies_to(mapping, other.protocol):
                continue
            for key, _index in patch.expand(mapping.ascconv_key, other.protocol.xprotocol):
                got = patch.read_ascconv(one.protocol.xprotocol, key)
                want = patch.read_ascconv(other.protocol.xprotocol, key)
                if got is None or want is None:
                    continue
                if mapping.bit is not None:
                    # Compare the bit this mapping claims, not the whole word.
                    # The console also sets flags no shipped mapping covers --
                    # this export toggled "Echoes in separate series", which no
                    # option scan has pinned -- and those share the word.
                    mine = int(got) >> mapping.bit & 1
                    theirs = int(want) >> mapping.bit & 1
                    assert mine == theirs, f"{one.name} {key} bit {mapping.bit}"
                    exact += 1
                    continue
                if got == want:
                    exact += 1
                    continue
                assert mapping.basis is not None, f"{one.name} {key}: {got} != {want}"
                assert abs(float(got) - float(want)) <= 5e-4 * max(1.0, abs(float(want)))
                approximate += 1
    assert exact > 1000, f"only {exact} assignments compared"
    assert approximate, "the quantised parameter was not exercised"


#: Archives whose scans are one sequence repeated with a single Special-card
#: option changed each time. They are what pins the WIP indices, the enum
#: values and the flag bits, none of which can be read off a single export.
OPTION_SCANS = (
    "CMRR_optionscan_P1",
    "MEMPRAGE_optionscan_P1",
    "NAV_optionscan_P1",
)

#: Units the card prints beside a value and the protocol does not store.
UNIT_SUFFIX = re.compile(r"\s*(ms|TRs|deg|Hz|%)$")


def _printed(scan: dict) -> dict[str, object]:
    """Flatten one parsed scan to ``{parameter: printed value}``.

    Parameters
    ----------
    scan : dict
        One entry of a parsed protocol's ``scans``.

    Returns
    -------
    dict
        Every parameter the scan prints, keyed by name.
    """
    return {
        key: (item.get("value") if isinstance(item, dict) else item)
        for key, item in (scan.get("flat") or {}).items()
    }


@requires_exar
@pytest.mark.parametrize("name", OPTION_SCANS)
def test_replaying_every_single_option_toggle_matches_the_console(name: str) -> None:
    """Each option scan is dozens of one-change answer keys in one file.

    Every scan after the first in a sequence group differs from the group's
    baseline by one Special-card option, so asking the patcher for that one
    label and comparing what it wrote against what the console wrote tests the
    index, the encoding and the sparse-array behaviour at once. A flag is
    compared at its own bit rather than by the whole word, since the console
    also sets flags no shipped mapping covers and they share that word.

    Parameters
    ----------
    name : str
        Base name of the option-scan pair.

    Returns
    -------
    None
    """
    archive = read(find_exar(f"{name}.exar1"))
    printed = json.loads(
        subprocess.run(
            [
                sys.executable,
                "-m",
                "siemens_protocol.cli",
                "parse",
                find_pdf(f"{name}.pdf"),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )["scans"]
    assert len(printed) == len(archive.steps)

    groups: dict[str, list[int]] = {}
    for position, step in enumerate(archive.steps):
        groups.setdefault(patch.sequence_of(step.protocol), []).append(position)

    replayed = 0
    for positions in groups.values():
        first = positions[0]
        baseline = archive.steps[first].protocol
        was = _printed(printed[first])
        for position in positions[1:]:
            now = _printed(printed[position])
            asked = {}
            for label, value in now.items():
                if was.get(label) == value:
                    continue
                mapping, _reason = patch.resolve(baseline, label)
                if mapping is not None:
                    asked[label] = UNIT_SUFFIX.sub("", str(value)).strip()
            if not asked:
                continue
            replayed += 1
            document, _applied, skipped = patch.patch_document(baseline, asked)
            assert not skipped, [s.reason for s in skipped]
            target = archive.steps[position].protocol.xprotocol
            for label in asked:
                mapping, _reason = patch.resolve(baseline, label)
                assert mapping is not None
                for key, _index in patch.expand(mapping.ascconv_key, target):
                    ours = patch.read_ascconv(document["Data"], key)
                    theirs = patch.read_ascconv(target, key)
                    if mapping.bit is not None:
                        assert (int(ours or 0) >> mapping.bit & 1) == (
                            int(theirs or 0) >> mapping.bit & 1
                        ), f"scan {position}: {label} at {key} bit {mapping.bit}"
                    else:
                        assert ours == theirs, f"scan {position}: {label} at {key}"
    assert replayed >= 5, f"{name} exercised only {replayed} toggles"


@requires_exar
def test_a_patch_writes_both_locations_not_just_the_preview() -> None:
    """The preview and the ASCCONV block move together.

    Writing only the preview is the failure that looks most like success: the
    console lists the new number and the scan runs the old one.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P2.exar1"))
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
    archive = read(find_exar("Potpourri_P2.exar1"))
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

    ``Bandwidth`` is the live example: the PDF prints it on every scan and no
    controlled edit has pinned where it is stored, so the request must surface
    rather than be guessed at.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P2.exar1"))
    _, applied, skipped = patch.patch_document(archive.steps[0].protocol, {"Bandwidth": 2000.0})
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
    archive = read(find_exar("Potpourri_P2.exar1"))
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
    archive = read(find_exar("Potpourri_P2.exar1"))
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
    path = find_exar("Potpourri_P2.exar1")
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
    archive = read(find_exar("Potpourri_P2.exar1"))
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
    archive = read(find_exar("Potpourri_P2.exar1"))
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
    archive = read(find_exar("Potpourri_P2.exar1"))
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
    archive = read(find_exar("Potpourri_P2.exar1"))
    manifest = patch.apply(archive, {archive.steps[0].name: {"TR": 900.0}})
    assert "lScanTimeSec" in manifest.stale
    assert "not recomputed" in manifest.report()


# --------------------------------------------------------------------------
# The mapping table itself
# --------------------------------------------------------------------------


#: Archives a scanner returned after loading a patched copy of their source.
#: Each ``Tnn`` scan carries one value this package wrote; the scanner kept
#: every one, which is the only evidence that the write path is acceptable to
#: a loader rather than merely consistent with other exports.
ROUND_TRIPPED = (
    ("CMRR_optionscan_P1.exar1", "CMRR_optionscan_P1_loadtest.exar1"),
    ("NAV_optionscan_P1.exar1", "NAV_optionscan_P1_loadtest.exar1"),
    ("MEMPRAGE_optionscan_P1.exar1", "MEMPRAGE_optionscan_P1_loadtest.exar1"),
)


@requires_exar
@pytest.mark.parametrize("source,returned", ROUND_TRIPPED, ids=[r for _s, r in ROUND_TRIPPED])
def test_a_patched_protocol_survives_a_real_scanner_load(source: str, returned: str) -> None:
    """Nothing was dropped, and what changed is only what a mapping writes.

    The returned archive is the source after this package changed one mapped
    parameter per scan and a scanner loaded and re-exported it. A scan the
    loader rejected would be missing; a value it overrode or normalised would
    show up as an ASCCONV difference no mapping accounts for.

    Parameters
    ----------
    source : str
        The archive the test scans were built from.
    returned : str
        What the scanner wrote back.

    Returns
    -------
    None
    """
    before, after = read(find_exar(source)), read(find_exar(returned))
    assert len(after.steps) == len(before.steps), "the loader dropped a scan"

    changed = touched = 0
    for original, result in zip(before.steps, after.steps):
        writable = {
            key
            for mapping in patch.MAPPINGS
            if patch.applies_to(mapping, result.protocol)
            for key, _index in patch.expand(mapping.ascconv_key, result.protocol.xprotocol)
        }
        differing = _ascconv_differences(original.protocol.xprotocol, result.protocol.xprotocol)
        if not differing:
            continue
        touched += 1
        stray = differing - writable
        assert not stray, f"{result.name}: the scanner changed {sorted(stray)[:4]}"
        changed += len(differing)
    assert touched, f"{returned} is identical to its source; nothing was exercised"
    assert changed >= touched, "expected at least one field per changed scan"


def _ascconv_differences(one: str, other: str) -> set[str]:
    """Return the ASCCONV keys whose values differ between two protocols.

    Parameters
    ----------
    one, other : str
        XProtocol texts to compare.

    Returns
    -------
    set of str
        Keys present in one and not the other, or holding different literals.
    """

    def table(text: str) -> dict[str, str]:
        start, end = patch.ascconv_bounds(text)
        if start < 0:
            return {}
        found = re.finditer(
            r"^[ \t]*([A-Za-z_][\w\[\].]*)[ \t]*=[ \t]*(.*?)[ \t]*$", text[start:end], re.M
        )
        return {m.group(1): m.group(2) for m in found}

    a, b = table(one), table(other)
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


@requires_exar
def test_re_encoding_a_returned_archive_reproduces_the_scanner_bytes() -> None:
    """The scanner's own output round-trips through our serializer exactly.

    These archives were written by the console after loading protocols this
    package had edited, so they are the closest thing to an authoritative
    sample of what our writer must produce. Every content blob must re-encode
    to its stored bytes and hash back to its stored address.

    Returns
    -------
    None
    """
    archive = read(find_exar("CMRR_optionscan_P1_loadtest.exar1"))
    checked = 0
    for digest, content in archive.contents.items():
        again = envelope.dumps(content.decode())
        assert again == content.payload, f"{content.kind} does not re-encode byte for byte"
        rebuilt = envelope.Envelope(content_type=content.content_type, payload=again)
        assert rebuilt.hash == digest
        checked += 1
    assert checked > 20, f"only {checked} blobs compared"


@requires_exar
def test_every_shipped_mapping_agrees_with_the_corpus(protocol_archive_path: str) -> None:
    """Each mapping's stored value matches its displayed one, in every protocol.

    This re-derives the evidence the table rests on rather than trusting it: a
    wrong scale, key or basis shows up as a disagreement. Array targets are
    checked at every index, not just the first, since a mapping that wrote one
    slice and left the rest is exactly the bug this catches.

    Parameters
    ----------
    protocol_archive_path : str
        One archive from the corpus that carries protocols.

    Returns
    -------
    None
    """
    archive = read(protocol_archive_path)
    checked = 0
    for step in archive.steps:
        if not step.runs_a_protocol:
            continue
        protocol = step.protocol
        for mapping in patch.MAPPINGS:
            if mapping.preview_path is None or not patch.applies_to(mapping, protocol):
                continue
            entry = protocol.preview.get(mapping.preview_path)
            if entry is None or not isinstance(entry.value, (int, float)):
                continue
            for key, index in patch.expand(mapping.ascconv_key, protocol.xprotocol):
                literal = patch.read_ascconv(protocol.xprotocol, key)
                if literal is None:
                    continue
                want = float(entry.value) * mapping.scale
                if mapping.basis is not None:
                    basis = patch.read_ascconv(
                        protocol.xprotocol, mapping.basis.replace("[*]", f"[{index}]")
                    )
                    if basis is None:
                        continue
                    want *= float(basis)
                checked += 1
                # FOV Phase is quantised by the console and printed rounded to a
                # tenth of a percent, so it agrees to within that rounding and
                # not exactly. Everything else is exact.
                tolerance = 2e-3 if mapping.basis is not None else 1e-6
                assert abs(float(literal) - want) <= tolerance * max(1.0, abs(want)), (
                    f"{step.name}: {mapping.label} shows {entry.value} but "
                    f"{key}={literal}, wanted {want}"
                )
    assert checked, "no mapping was exercised by this archive"


@requires_exar
def test_a_sequence_specific_mapping_is_refused_on_another_sequence() -> None:
    """``sWipMemBlock`` indices mean different things per sequence.

    ``alFree[0]`` is MT Flip Angle on ``can_neuromelanin`` and a packed word of
    checkbox flags on CMRR's multiband sequences, so a table that wrote it
    without regard to the sequence would put a flip angle into CMRR's flags.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    steps = {s.name: s for s in archive.steps}
    cmrr = steps["Minn_CMRR_2.3mm_S8_rest_6min"].protocol
    neuro = steps["can_neuromelanin"].protocol
    assert patch.sequence_of(cmrr) == "cmrr_mbep2d_bold"
    assert patch.sequence_of(neuro) == "can_neuromelanin"

    found, _ = patch.resolve(neuro, "MT Flip Angle")
    assert found is not None and found.ascconv_key == "sWipMemBlock.alFree[0]"

    refused, reason = patch.resolve(cmrr, "MT Flip Angle")
    assert refused is None
    assert "cmrr_mbep2d_bold" in reason


@requires_exar
def test_an_array_target_writes_every_slice_not_only_the_first() -> None:
    """FOV Read and Slice Thickness are replicated across the slice array.

    Writing element zero alone leaves the others at the old value, which loads,
    lists correctly in the console, and is wrong.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    step = {s.name: s for s in archive.steps}["Minn_CMRR_2.3mm_S8_rest_6min"]
    targets = patch.expand("sSliceArray.asSlice[*].dReadoutFOV", step.protocol.xprotocol)
    assert len(targets) > 1, "this fixture should have a multi-slice array"

    document, applied, skipped = patch.patch_document(step.protocol, {"FOV Read": 199.0})
    assert not skipped and len(applied) == 1
    written = {patch.read_ascconv(document["Data"], key) for key, _ in targets}
    assert written == {"199.0"}, f"not every slice was written: {sorted(written)}"


@requires_exar
def test_a_derived_target_is_scaled_by_its_basis() -> None:
    """FOV Phase is stored as millimetres, not as the percentage displayed.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    step = {s.name: s for s in archive.steps}["Minn_CMRR_2.3mm_S8_rest_6min"]
    document, applied, skipped = patch.patch_document(
        step.protocol, {"FOV Read": 200.0, "FOV Phase": 50.0}
    )
    assert not skipped and len(applied) == 2
    assert patch.read_ascconv(document["Data"], "sSliceArray.asSlice[0].dReadoutFOV") == "200.0"
    assert patch.read_ascconv(document["Data"], "sSliceArray.asSlice[0].dPhaseFOV") == "100.0"


@requires_exar
def test_a_special_card_mapping_writes_ascconv_with_no_preview_side() -> None:
    """Nothing on the Special card appears in ``Preview``.

    The console lists only common parameters, so these mappings have no preview
    entry to keep in sync and must not invent one.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    step = {s.name: s for s in archive.steps}["can_neuromelanin"]
    assert not step.protocol.by_label("MT Flip Angle")

    before = dict(step.protocol.document["Preview"])
    document, applied, skipped = patch.patch_document(step.protocol, {"MT Flip Angle": 355})
    assert not skipped and len(applied) == 1
    assert patch.read_ascconv(document["Data"], "sWipMemBlock.alFree[0]") == "355"
    assert document["Preview"] == before, "an ASCCONV-only mapping touched the preview"


def test_a_float_is_written_the_way_the_console_writes_one() -> None:
    """Doubles carry twelve significant figures, not Python's full repr.

    A freshly computed value otherwise arrives with its binary tail attached --
    ``201.26200000000003`` where the console would write ``201.262``.

    Returns
    -------
    None
    """
    assert patch.format_like(201.26200000000003, "207.0") == "201.262"
    assert patch.format_like(1.0, "2.0") == "1.0"
    assert patch.format_like(2000000.0, "650000") == "2000000"


@requires_exar
def test_the_sequence_build_stamp_is_stable_where_the_guid_beside_it_is_not() -> None:
    """CMRR records which binary wrote a protocol; the GUID in front does not.

    ``sWipMemBlock.tFree`` was first taken for pure per-export churn because
    its leading GUID is regenerated on every save. The rest of it is not churn:
    it names the sequence build, and normalising the field away would discard
    the only thing in the protocol that says which binary produced it.

    Returns
    -------
    None
    """
    archive = read(find_exar("CMRR_optionscan_P1.exar1"))
    stamps, guids = set(), set()
    for step in archive.steps:
        if not step.runs_a_protocol:
            continue
        if not patch.sequence_of(step.protocol).startswith("cmrr_"):
            continue
        raw = patch.read_ascconv(step.protocol.xprotocol, "sWipMemBlock.tFree")
        guids.add(raw.strip('"').split("||")[0])
        stamps.add(patch.sequence_stamp(step.protocol))
    assert len(guids) > 1, "the GUID prefix should differ between saves"
    # One stamp per binary: the BOLD, SE and diffusion sequences were built
    # minutes apart from one commit.
    assert 1 <= len(stamps) <= 3, sorted(stamps)
    assert all(s.startswith("Sequence: R") for s in stamps), sorted(stamps)


@requires_exar
def test_the_build_stamp_survives_an_edit_and_a_round_trip(tmp_path: pathlib.Path) -> None:
    """Patching a protocol must not disturb which build is recorded.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Destination for the patched archive.

    Returns
    -------
    None
    """
    archive = read(find_exar("CMRR_optionscan_P1.exar1"))
    step = next(s for s in archive.steps if patch.sequence_of(s.protocol).startswith("cmrr_"))
    before = patch.sequence_stamp(step.protocol)
    assert before

    document, applied, skipped = patch.patch_document(step.protocol, {"MB dual kernel": True})
    assert not skipped and applied
    archive.replace_content(step.protocol.instance, document)
    written = tmp_path / "patched.exar1"
    archive.write(str(written))

    back = {s.name: s for s in read(str(written)).steps}[step.name]
    assert patch.sequence_stamp(back.protocol) == before


@requires_exar
@pytest.mark.parametrize(
    "sequence,shape",
    [("cmrr_mbep2d_bold", "Sequence: R"), ("tfl_mgh_epinav_ABCD", ".prot")],
)
def test_what_tfree_holds_depends_on_the_sequence(sequence: str, shape: str) -> None:
    """``tFree`` is sequence-private free text, like the rest of sWipMemBlock.

    CMRR writes a build stamp behind a GUID; the ABCD navigators write a
    protocol file name with no GUID; ``tfl_mgh_multiecho`` writes nothing.
    Reading it as one kind of value across sequences would be wrong the same
    way reading ``alFree[0]`` as one parameter is.

    Parameters
    ----------
    sequence : str
        The sequence to look at.
    shape : str
        Text the stamp must contain for that sequence.

    Returns
    -------
    None
    """
    for name in ("CMRR_optionscan_P1.exar1", "NAV_optionscan_P1.exar1"):
        archive = read(find_exar(name))
        for step in archive.steps:
            if patch.sequence_of(step.protocol) == sequence:
                assert shape in patch.sequence_stamp(step.protocol)
                return
    pytest.skip(f"no {sequence} scan available")


def test_a_sequence_that_writes_no_stamp_yields_an_empty_string() -> None:
    """An absent ``tFree`` is a legitimate state, not an error.

    Returns
    -------
    None
    """

    class Bare:
        xprotocol = "### ASCCONV BEGIN ###\nalTR[0]\t = \t100\n### ASCCONV END ###\n"

    assert patch.sequence_stamp(Bare()) == ""


def test_every_mapping_records_how_it_was_established() -> None:
    """No mapping ships without stating its evidence.

    The table was derived from a corpus, not from documentation, and the
    difference between a controlled edit and mere agreement is what a later
    reader needs in order to know which entries to trust.

    Returns
    -------
    None
    """
    for mapping in patch.MAPPINGS:
        assert mapping.evidence.strip()
        assert mapping.scale > 0
        assert mapping.label.strip()
        # sWipMemBlock is scratch memory with no global meaning, so anything
        # reaching into it must say which sequences it is true of.
        if "sWipMemBlock" in mapping.ascconv_key:
            assert mapping.is_sequence_specific, f"{mapping.label} must name its sequences"


def test_no_two_mappings_claim_the_same_ascconv_key() -> None:
    """Distinct entries are distinguished by more than their target.

    Two mappings may share a key when their conditions are disjoint -- slice
    thickness writes ``dThickness`` on both 2D and 3D acquisitions, meaning
    different things -- so the identity of an entry is the target together with
    the scope it applies in.

    Returns
    -------
    None
    """
    identity = [(m.ascconv_key, m.sequences, m.when, m.bit) for m in patch.MAPPINGS]
    assert len(identity) == len(set(identity))


@requires_exar
def test_no_protocol_has_two_mappings_writing_one_key(protocol_archive_path: str) -> None:
    """The safety property the entry-level check only approximates.

    Sharing a key is fine while the conditions are disjoint, and fine for
    checkboxes packed into one word -- fourteen CMRR options live in
    ``alFree[0]`` and touch a bit each. What would race is two mappings in
    scope for one protocol writing the same *whole* assignment.

    Parameters
    ----------
    protocol_archive_path : str
        One archive from the corpus that carries protocols.

    Returns
    -------
    None
    """
    archive = read(protocol_archive_path)
    for step in archive.steps:
        if not step.runs_a_protocol:
            continue
        protocol = step.protocol
        keys = [(m.ascconv_key, m.bit) for m in patch.MAPPINGS if patch.applies_to(m, protocol)]
        assert len(keys) == len(set(keys)), (
            f"{step.name} ({patch.sequence_of(protocol)}) has two mappings "
            f"writing one key: {sorted(k for k in keys if keys.count(k) > 1)}"
        )


@requires_exar
def test_slice_thickness_follows_the_acquisition_dimension() -> None:
    """On a 3D acquisition ``dThickness`` is the slab, not the slice.

    Writing the displayed thickness straight into it would put 1.0 where the
    protocol holds 176.0 -- a protocol that loads and is badly wrong.

    Returns
    -------
    None
    """
    archive = read(find_exar("Potpourri_P1.exar1"))
    steps = {s.name: s for s in archive.steps}
    flat = steps["Minn_CMRR_2.3mm_S8_rest_6min"].protocol
    slab = steps["ABCD_T1w_MPR_vNav"].protocol
    assert patch.read_ascconv(flat.xprotocol, "sKSpace.ucDimension") == "2"
    assert patch.read_ascconv(slab.xprotocol, "sKSpace.ucDimension") == "4"

    two_d, _ = patch.resolve(flat, "Slice Thickness")
    three_d, _ = patch.resolve(slab, "Slice Thickness")
    assert two_d is not None and two_d.basis is None
    assert three_d is not None and three_d.basis == "sKSpace.lImagesPerSlab"

    partitions = float(patch.read_ascconv(slab.xprotocol, "sKSpace.lImagesPerSlab"))
    document, applied, skipped = patch.patch_document(slab, {"Slice Thickness": 1.25})
    assert not skipped and len(applied) == 1
    written = float(patch.read_ascconv(document["Data"], "sSliceArray.asSlice[0].dThickness"))
    assert written == 1.25 * partitions


@requires_exar
def test_every_build_gated_mapping_still_applies_across_the_corpus() -> None:
    """The gate must be a no-op on what is already verified.

    A guard that silently stops writing the parameters the option scans
    pinned would be worse than none, so this asserts the corpus still
    resolves every gated mapping. It is the half that keeps the gate honest;
    the next test is the half that proves it fires.

    Returns
    -------
    None
    """
    gated = [m for m in patch.MAPPINGS if m.builds]
    assert gated, "nothing is build-gated, so this test asserts nothing"
    checked = 0
    for path, _version in EXAR_PROTOCOL_FILES:
        for step in read(path).steps:
            if not step.runs_a_protocol:
                continue
            for mapping in gated:
                if patch.sequence_of(step.protocol) not in mapping.sequences:
                    continue
                found, why = patch.resolve(step.protocol, mapping.label)
                assert found is mapping, f"{step.name}: {mapping.label} refused -- {why}"
                checked += 1
    assert checked > 100, f"only {checked} gated resolutions exercised"


@requires_exar
def test_a_later_sequence_build_refuses_a_bit_mapping() -> None:
    """A renumbered flags word must refuse rather than write the wrong option.

    ``sWipMemBlock`` is sequence-private, so a later release may pack the
    same card differently and nothing in the protocol announces it -- the
    value would simply land in another option. The corpus holds one CMRR
    build for the mapped sequences, so the mismatch is staged by rewriting
    the stamp and nothing else, which is what isolates the gate from every
    other reason a mapping can be out of scope.

    Returns
    -------
    None
    """
    archive = read(find_exar("CMRR_optionscan_P1.exar1"))
    step = next(
        s
        for s in archive.steps
        if s.runs_a_protocol and patch.sequence_of(s.protocol) == "cmrr_mbep2d_bold"
    )
    assert patch.resolve(step.protocol, "Single-band images")[0] is not None

    document = dict(step.protocol.document)
    document["Data"] = step.protocol.xprotocol.replace(
        "R017 nxva60a/main r/91b106c1e", "R018 nxva60a/main r/0000000"
    )
    later = Protocol(instance=step.protocol.instance, document=document)
    assert patch.sequence_of(later) == patch.sequence_of(step.protocol)

    found, why = patch.resolve(later, "Single-band images")
    assert found is None
    assert "R018" in why and "R017" in why, why
    # The reason must not blame the sequence, which matches perfectly.
    assert "but this protocol runs" not in why, why
    # Only the packed card is gated; a parameter in its own field is not.
    assert patch.resolve(later, "TR")[0] is not None


# --------------------------------------------------------------------------
# The option scans: one console option varied per copy
# --------------------------------------------------------------------------

#: Values the console recomputes from whatever actually changed, so a copy
#: that moves one of these has not necessarily changed it.
FOLLOWS = {
    "TA",
    "Rel. SNR",
    "SAR",
    "Scan Time",
    "Total Scan Time",
    "Delay in TR",
    "Voxel size",
    "PAT",
    "Reference lines PE",
    "Slices per Slab",
}

#: Fields the console rewrites on every save, or recomputes.
OPTION_CHURN = re.compile(
    r"tCheckUUID|tFree|lFinalMatrixSize|ScanTimeSec|dRefSNR|"
    r"dOverallImageScaleFactor|sIR\.adFree"
)


def _ascconv(text: str) -> dict[str, str]:
    """Return the ASCCONV block as ``{key: literal}``.

    Parameters
    ----------
    text : str
        XProtocol text.

    Returns
    -------
    dict
        One entry per assignment.
    """
    low, high = patch.ascconv_bounds(text)
    pairs = (line.partition("=") for line in text[low:high].splitlines()[1:])
    return {k.strip(): v.strip() for k, sep, v in pairs if sep}


def _printed(scan: dict) -> dict[str, object]:
    """Flatten one parsed scan to ``{label: printed value}``.

    Parameters
    ----------
    scan : dict
        One entry of a parsed protocol's ``scans``.

    Returns
    -------
    dict
        The first printing of each label.
    """
    out: dict[str, object] = {}
    for _title, params in scan.get("sections", {}).items():
        for key, value in params.items():
            out.setdefault(key.strip(), value)
    return out


def _modal(records: list[dict]) -> dict:
    """Return the most common value of every key across ``records``.

    Parameters
    ----------
    records : list of dict
        The copies to summarise.

    Returns
    -------
    dict
        The baseline each copy is compared against.
    """
    keys = set().union(*records)
    return {k: collections.Counter(r.get(k) for r in records).most_common(1)[0][0] for k in keys}


def _option_scan(archive_path: str, pdf_path: str) -> tuple:
    """Pair a scan's printed parameters with its protocol, and find the baseline.

    Copies are paired by name when names are unique on both sides and by
    position otherwise: one export still carries 24 scans sharing a name, and
    resolving that by name silently addresses the wrong scan.

    Parameters
    ----------
    archive_path : str
        The option-scan archive.
    pdf_path : str
        Its PDF export.

    Returns
    -------
    tuple
        ``(pairs, prints, blocks, baseline index)``.
    """
    scans = parse_document(pdf_path).protocol.to_dict()["scans"]
    steps = read(archive_path).steps
    counts = collections.Counter(s["name"] for s in scans)
    if max(counts.values()) == 1 and len({s.name for s in steps}) == len(steps):
        by_name = {s["name"]: s for s in scans}
        pairs = [(by_name[s.name], s) for s in steps if s.name in by_name]
    else:
        pairs = list(zip(scans, steps))
    pairs = [
        (p, s)
        for p, s in pairs
        if s.runs_a_protocol and patch.sequence_of(s.protocol) == "cmrr_mbep2d_bold"
    ]
    prints = [_printed(p) for p, _ in pairs]
    blocks = [_ascconv(s.protocol.xprotocol) for _, s in pairs]
    keys = set().union(*blocks)
    modal = _modal(blocks)
    baseline = min(
        range(len(pairs)),
        key=lambda i: sum(1 for k in keys if blocks[i].get(k) != modal.get(k)),
    )
    return pairs, prints, blocks, baseline


@requires_paramcheck
@pytest.mark.parametrize("archive_path,pdf_path", PARAMCHECK_PAIRS, ids=PARAMCHECK_IDS)
def test_each_option_scan_pairs_with_its_own_export(archive_path: str, pdf_path: str) -> None:
    """The PDF beside an archive must be an export *of* it.

    Names agreeing is not enough. An earlier round of these files had the
    archives shifted one place against the PDFs while every name still
    matched its own file, and deriving a mapping through that would attach a
    real label to the wrong field -- well-formed, plausible and wrong. Every
    scan's printed values are therefore checked against the archive's own
    ``Preview``, which is the console listing the same protocol.

    Parameters
    ----------
    archive_path : str
        The option-scan archive.
    pdf_path : str
        Its PDF export.

    Returns
    -------
    None
    """
    pairs, _prints, _blocks, _baseline = _option_scan(archive_path, pdf_path)
    assert pairs, "no copies paired at all"
    checked = 0
    for scan, step in pairs:
        printed_values = _printed(scan)
        for entry in step.protocol.preview.values():
            label = entry.label.strip()
            if label not in printed_values or entry.value is None:
                continue
            shown = build.printed_value(printed_values[label])
            try:
                same = abs(float(shown) - float(entry.value)) < 1e-3
            except (TypeError, ValueError):
                continue
            checked += 1
            assert same, f"{step.name}: {label} prints {shown!r}, preview holds {entry.value!r}"
    assert checked > 50, f"only {checked} values compared; the pairing is barely checked"


@requires_paramcheck
def test_every_derived_option_replays_into_the_console_result() -> None:
    """Applying a printed change must reproduce the copy the console wrote.

    This is what the derived mappings rest on. Each option scan holds one
    baseline and a copy per option, so applying the copy's printed value to
    the baseline should produce that copy's protocol -- every field, not just
    the one aimed at. A mapping with the wrong scale, the wrong spelling or a
    missing second location fails here.

    Returns
    -------
    None
    """
    labels = {m.label for m in patch.MAPPINGS}
    replayed, refused, differing = 0, [], []
    unclaimed: set[str] = set()
    for archive_path, pdf_path in PARAMCHECK_PAIRS:
        pairs, prints, blocks, baseline = _option_scan(archive_path, pdf_path)
        base = pairs[baseline][1].protocol
        printed_base, ascconv_base = _modal(prints), _modal(blocks)
        for index, (printed_now, block) in enumerate(zip(prints, blocks)):
            if index == baseline:
                continue
            moved = {
                k
                for k in set(printed_now) | set(printed_base)
                if printed_now.get(k) != printed_base.get(k) and k not in FOLLOWS
            }
            if len(moved) != 1:
                continue
            label = next(iter(moved))
            if label not in labels:
                continue
            value = build.printed_value(printed_now.get(label))
            document, applied, skipped = patch.patch_document(base, {label: value})
            if skipped:
                refused.append((label, skipped[0].reason))
                continue
            assert applied
            got = _ascconv(document["Data"])
            diff = {
                k
                for k in set(got) | set(block)
                if got.get(k) != block.get(k) and not OPTION_CHURN.search(k)
            }
            replayed += 1
            # A mapping answers for the fields it writes. One copy varies a
            # second option beyond the one it prints -- executionopts E05
            # moves sAngio.ucUseTimingDelay as well as the workflow flag --
            # so a residual difference is only a failure when it lands on a
            # field some mapping claims.
            claimed = {
                key
                for m in patch.MAPPINGS
                for key, _index in patch.expand(m.ascconv_key, base.xprotocol)
            }
            blamed = diff & claimed
            if blamed:
                differing.append((label, sorted(blamed)[:3]))
            unclaimed.update(diff - claimed)
    assert replayed > 25, f"only {replayed} options replayed; this proves little"
    assert not differing, f"replay wrote the wrong value into a mapped field: {differing}"
    # Whatever is left must be small and nameable, or the replay is passing
    # by declaring its failures out of scope.
    assert (
        len(unclaimed) <= 1
    ), f"replay left {len(unclaimed)} unmapped fields: {sorted(unclaimed)}"
    # One refusal is expected and correct: AutoAlign printed as "---" moves
    # ucAARegionMode as well as ucAARefMode, and writing half of a coupled
    # pair is worse than declining.
    assert len(refused) <= 1, refused
