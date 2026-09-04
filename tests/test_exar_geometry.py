"""Tests for recomputing a slice array from the quantities it derives from.

The geometry cards move six to twelve ``sSliceArray.asSlice[]`` fields per
console option, and none of those fields is independent. Writing one of them
alone leaves the rest describing the old geometry, which is an inconsistent
parameter set -- and the console answers those by greying the scan out, so it
cannot be opened, edited or printed. Recomputing is therefore the safe
operation rather than merely the tidy one, and these tests are what say the
recomputation matches what the console writes.
"""

from __future__ import annotations

import math
import os

import pytest

from conftest import (  # noqa: F401
    EXAR_PROTOCOL_FILES,
    PARAMCHECK_PAIRS,
    find_exar,
    requires_exar,
    requires_paramcheck,
)
from siemens_protocol.exar import geometry, patch, read

#: The one array in the corpus that disagrees with its own inputs, and the
#: only one this library is responsible for. ``driver_loadtest`` is the
#: driver's output after a scanner loaded and re-exported it, and it was built
#: before :func:`build.recentre` existed: ``Slice Thickness`` went 2.3 to 2.2
#: on all 64 slices while the positions kept describing the 2.3 mm geometry,
#: putting the outermost slices ``(64 - 1) / 2 * 0.1`` mm from where they
#: belong. It is pinned rather than excluded for what it says about the
#: format -- the scanner accepted the scan, did not grey it out, and returned
#: the array untouched -- so a *second* such array fails here.
KNOWN_INCONSISTENT_ARRAYS = {
    ("driver_loadtest.exar1", "Minn_CMRR_2.3mm_S8_rest_6min"): 3.16,
}


@requires_exar
def test_every_single_group_slice_array_is_reproduced_from_its_inputs() -> None:
    """The stored array must equal the one the formula produces.

    Swept over the whole corpus rather than a chosen scan, because the
    formula multiplies two factors that most protocols cannot tell apart: at
    a distance factor of zero the step and the thickness are the same number.
    ``paramcheck`` varies them separately, and the sweep is what carries that
    into every other export.

    Returns
    -------
    None
    """
    worst, checked, pinned = 0.0, 0, 0
    for path, _version in EXAR_PROTOCOL_FILES:
        for step in read(path).steps:
            if not step.runs_a_protocol:
                continue
            text = step.protocol.xprotocol
            group = geometry.read_group(text)
            if group is None:
                continue
            apart = geometry.agrees(text, group)
            assert apart is not None
            allowed = KNOWN_INCONSISTENT_ARRAYS.get((os.path.basename(path), step.name))
            if allowed is not None:
                assert apart < allowed, f"{step.name}: array drifted further, to {apart} mm"
                pinned += 1
                continue
            assert apart < geometry.TOLERANCE, f"{step.name}: array is {apart} mm out"
            worst = max(worst, apart)
            checked += 1
    assert pinned == len(KNOWN_INCONSISTENT_ARRAYS), (
        f"{len(KNOWN_INCONSISTENT_ARRAYS) - pinned} pinned array(s) went missing; "
        "remove the entry rather than leaving it to excuse the next one"
    )
    assert checked > 200, f"only {checked} slice arrays exercised"
    # Far tighter than the tolerance, and stated so a regression that merely
    # squeaks under the bar still shows up as a change here.
    assert worst < 1e-6, f"worst deviation grew to {worst}"


@requires_paramcheck
def test_the_step_is_the_product_and_not_either_factor() -> None:
    """Thickness and distance factor are both varied, so neither alone fits.

    Returns
    -------
    None
    """
    seen = set()
    for archive_path, _pdf in PARAMCHECK_PAIRS:
        for step in read(archive_path).steps:
            if not step.runs_a_protocol:
                continue
            group = geometry.read_group(step.protocol.xprotocol)
            if group is not None:
                seen.add((round(group.thickness, 3), round(group.distance_factor, 3)))
    thicknesses = {t for t, _f in seen}
    factors = {f for _t, f in seen}
    assert len(thicknesses) > 1, f"thickness never varies: {sorted(thicknesses)}"
    assert factors >= {0.0, 0.2, 0.5}, f"distance factor barely varies: {sorted(factors)}"


@requires_paramcheck
def test_a_multi_group_slice_array_is_refused_rather_than_misread() -> None:
    """Several slice groups interleave in one array, so the single-group read
    must decline rather than describe the first group's spacing.

    ``extravals`` carries localizers with one, two and three groups. The
    array is ordered by acquisition, not by group: with three groups the
    first slice of each comes first, so group 0 occupies indices 0, 3 and 4.
    Reading it as one progression would take the step between indices 0 and 1
    -- two different groups -- and get a number belonging to neither.

    Returns
    -------
    None
    """
    found = [a for a, _p in PARAMCHECK_PAIRS if a.endswith("extravals_FIX.exar1")]
    assert found, "the extravals option scan is missing from paramcheck/"
    steps = {s.name: s for s in read(found[0]).steps}
    single = geometry.read_group(steps["localizer_1slicegroup"].protocol.xprotocol)
    assert single is not None and single.count == 3
    assert math.isclose(single.step, single.thickness * 1.2, rel_tol=1e-9)

    for name in ("localizer_2slicegroup", "localizer_3slicegroup"):
        text = steps[name].protocol.xprotocol
        assert geometry.read_group(text) is None, f"{name} was read as one group"
        # It is a real multi-group array, not merely an unreadable one.
        assert patch.read_ascconv(text, "sGroupArray.asGroup[1].nSize") is not None


@requires_paramcheck
def test_rebuilding_an_untouched_protocol_changes_nothing() -> None:
    """Writing the array back as it stands must be a no-op.

    The one check that exercises reading, computing and formatting together:
    anything that rounds differently, spells a literal differently, or places
    a created assignment wrongly shows up here as a diff against a protocol
    nobody asked to change.

    Returns
    -------
    None
    """
    checked = 0
    for archive_path, _pdf in PARAMCHECK_PAIRS:
        for step in read(archive_path).steps:
            if not step.runs_a_protocol:
                continue
            text = step.protocol.xprotocol
            group = geometry.read_group(text)
            if group is None:
                continue
            assert geometry.rebuild(text, group) == text, f"{step.name} was rewritten"
            checked += 1
    assert checked > 50, f"only {checked} protocols rebuilt"


@requires_exar
def test_driving_a_protocol_leaves_every_slice_array_consistent() -> None:
    """A write that changes the spacing must recompute the positions.

    ``Slice Thickness`` and ``Distance Factor`` both set the step between
    slices, and every position is a function of it, so writing either alone
    leaves the array describing the geometry it replaced. That is not
    hypothetical: the driver did exactly this to a 64-slice EPI, and the
    scanner accepted the scan, declined to grey it out, and returned an array
    3.15 mm out -- which is why the pinned entry above exists and why nothing
    offline but this check would have caught it.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import build
    from siemens_protocol.pipeline import parse_document

    template = find_exar("Potpourri_P1.exar1")
    pdf = os.path.join(os.path.dirname(template), "Potpourri_P1_changed.pdf")
    if not os.path.exists(pdf):
        pytest.skip("Potpourri_P1_changed.pdf is not beside the template")

    archive = read(template)
    report = build.apply_protocol(archive, parse_document(pdf).protocol.to_dict(include_flat=True))
    respaced = {
        one.step for one in report.applied if one.label in ("Slice Thickness", "Distance Factor")
    }
    assert respaced, "this pair no longer changes any spacing, so it proves nothing"

    checked = set()
    for step in archive.steps:
        if not step.runs_a_protocol:
            continue
        text = step.protocol.xprotocol
        group = geometry.read_group(text)
        if group is None:
            continue
        apart = geometry.agrees(text, group)
        assert (
            apart is not None and apart < geometry.TOLERANCE
        ), f"{step.name}: the driver left the array {apart} mm out"
        checked.add(step.name)
    # Counting arrays would pass on the four this drive never touches, so the
    # claim is about the ones whose spacing moved. Not all of them can be
    # checked: `localizer_64ch_uncombined` is a three-plane scout, three groups
    # of one slice each, and a one-slice group puts its slice at the centre
    # with the step never entering -- so a thickness write leaves nothing to
    # recompute and `read_group` rightly declines to describe it.
    assert respaced & checked, (
        "no scan whose spacing moved has a readable array, so this proves "
        f"nothing; spacing moved on {sorted(respaced)}"
    )


def test_a_slice_array_that_arrived_broken_is_left_alone() -> None:
    """Only an array this write invalidated may be rebuilt.

    Repairing one that was already inconsistent would be a change nobody
    asked for, and it would hide the state the pinned entry above exists to
    keep visible.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import build

    intact = "\n".join(
        [
            "### ASCCONV BEGIN ###",
            "sSliceArray.lSize\t = \t2",
            "sSliceArray.asSlice[0].dThickness\t = \t2.0",
            "sSliceArray.asSlice[0].sNormal.dTra\t = \t1.0",
            "sSliceArray.asSlice[0].sPosition.dTra\t = \t-1.0",
            "sSliceArray.asSlice[1].dThickness\t = \t2.0",
            "sSliceArray.asSlice[1].sNormal.dTra\t = \t1.0",
            "sSliceArray.asSlice[1].sPosition.dTra\t = \t1.0",
            "### ASCCONV END ###",
        ]
    )
    assert geometry.agrees(intact) is not None
    assert geometry.agrees(intact) < geometry.TOLERANCE

    broken = intact.replace(
        "sSliceArray.asSlice[1].sPosition.dTra\t = \t1.0",
        "sSliceArray.asSlice[1].sPosition.dTra\t = \t9.0",
    )
    assert geometry.agrees(broken) > geometry.TOLERANCE
    # Broken before the write and after it: not ours to repair.
    assert build.recentre(broken, broken) == broken
    # Broken only by the write: rebuilt.
    assert build.recentre(intact, broken) != broken
    assert geometry.agrees(build.recentre(intact, broken)) < geometry.TOLERANCE
    # Untouched: a no-op, which is what keeps a diff of an unedited protocol
    # readable.
    assert build.recentre(intact, intact) == intact
