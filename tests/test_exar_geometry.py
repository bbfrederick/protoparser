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

import pytest

from conftest import (  # noqa: F401
    EXAR_PROTOCOL_FILES,
    PARAMCHECK_PAIRS,
    requires_exar,
    requires_paramcheck,
)
from siemens_protocol.exar import geometry, patch, read


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
    worst, checked = 0.0, 0
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
            assert apart < geometry.TOLERANCE, f"{step.name}: array is {apart} mm out"
            worst = max(worst, apart)
            checked += 1
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
