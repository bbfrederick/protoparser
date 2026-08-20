"""The flattened view and its conflict detection."""

from __future__ import annotations

from collections import OrderedDict

import pytest

from conftest import EXAMPLE_FILES, EXAMPLE_IDS, ParseFixture, find_example, requires_examples
from siemens_protocol.flatten import conflicts, flatten_sections


def test_agreeing_occurrences_collapse_to_one_value() -> None:
    """A key printed twice with the same value yields one entry, no conflict.

    Returns
    -------
    None
    """
    flat = flatten_sections(
        OrderedDict(
            [
                ("Routine", OrderedDict([("TR", "2530.0 ms"), ("Slices", "176")])),
                ("Contrast - Common", OrderedDict([("TR", "2530.0 ms")])),
            ]
        )
    )
    assert flat["TR"] == {
        "value": "2530.0 ms",
        "sections": ["Routine", "Contrast - Common"],
        "conflict": False,
    }
    assert flat["Slices"]["sections"] == ["Routine"]
    assert not conflicts(flat)


def test_disagreeing_occurrences_are_flagged_and_both_kept() -> None:
    """A constructed disagreement: nothing is chosen at random.

    Returns
    -------
    None
    """
    flat = flatten_sections(
        OrderedDict(
            [
                ("Routine", OrderedDict([("TR", "2530.0 ms")])),
                ("Contrast - Common", OrderedDict([("TR", "2500.0 ms")])),
            ]
        )
    )
    entry = flat["TR"]
    assert entry["conflict"] is True
    assert "value" not in entry, "a conflicting key must not claim a single value"
    assert entry["values"] == {"Routine": "2530.0 ms", "Contrast - Common": "2500.0 ms"}
    assert list(conflicts(flat)) == ["TR"]


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_flat_view_covers_every_key(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """Flattening drops no key from the hierarchy.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    for scan in parsed(pdf).protocol.scans:
        sections = scan.sections()
        flat = flatten_sections(sections)
        every_key = {key for entries in sections.values() for key in entries}
        assert set(flat) == every_key


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_flat_entries_are_well_formed(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """Every entry names its sections and carries exactly one value shape.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    for scan in parsed(pdf).protocol.scans:
        for key, entry in scan.to_dict()["flat"].items():
            assert entry["sections"], key
            if entry["conflict"]:
                assert len(set(entry["values"].values())) > 1
            else:
                assert "value" in entry


@requires_examples
def test_a_multi_section_parameter_is_recorded_from_every_section(parsed: ParseFixture) -> None:
    """TR is printed in several sections of the same scan, and all agree here.

    The hierarchy still holds each occurrence separately.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    pdf = find_example("R01StressDyn.pdf")
    scan = next(s for s in parsed(pdf).protocol.scans if s.name == "localizer")
    entry = scan.to_dict()["flat"]["TR"]
    assert entry["conflict"] is False
    assert entry["value"] == "20.0 ms"
    assert {"Routine", "Contrast - Common", "Geometry - Common"} <= set(entry["sections"])
    sections = scan.sections()
    for name in ("Routine", "Contrast - Common", "Geometry - Common"):
        assert sections[name]["TR"] == "20.0 ms"
