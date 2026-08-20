"""Parameter values, including the layout cases that are easy to get wrong.

Every expectation here was read off the printout and confirmed against the
raw PDF text, so a failure means the parser drifted, not the fixture.
"""

from __future__ import annotations

import pytest

from conftest import EXAMPLE_FILES, EXAMPLE_IDS, ParseFixture, find_example, requires_examples
from siemens_protocol.model import Scan

#: ``(file, scan, section, key, value)`` -- the same acquisition in both
#: releases, which also checks that a rebuild across versions is comparable.
KNOWN_VALUES = [
    ("R01StressDyn.pdf", "T1_MEMPRAGE_64ch", "Routine", "Slices per slab", "176"),
    ("R01StressDyn.pdf", "T1_MEMPRAGE_64ch", "Contrast - Common", "TR", "2530.0 ms"),
    ("R01StressDyn.pdf", "T1_MEMPRAGE_64ch", "Contrast - Common", "TI", "1100 ms"),
    ("R01StressDyn.pdf", "localizer", "Contrast - Common", "TE", "5.00 ms"),
    ("R01StressDyn.pdf", "localizer", "Routine", "Concatenations", "7"),
    ("R01StressDynXA60.pdf", "T1_MEMPRAGE_64ch", "Routine", "Slices per Slab", "176"),
    ("R01StressDynXA60.pdf", "T1_MEMPRAGE_64ch", "Contrast - Common", "TR", "2530.0 ms"),
    ("R01StressDynXA60.pdf", "T1_MEMPRAGE_64ch", "Contrast - Common", "TI", "1100 ms"),
    ("R01StressDynXA60.pdf", "localizer", "Contrast - Common", "TE", "5.00 ms"),
    ("R01StressDynXA60.pdf", "localizer", "Routine", "Concatenations", "7"),
]


def _scan(parsed: ParseFixture, name: str, scan_name: str) -> Scan:
    """Locate one scan of one example by name.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    name : str
        Base file name of the example.
    scan_name : str
        Protocol name of the scan.

    Returns
    -------
    Scan
        The matching scan. Fails the test if there is none.
    """
    for scan in parsed(find_example(name)).protocol.scans:
        if scan.name == scan_name:
            return scan
    pytest.fail(f"{scan_name} not found in {name}")


@requires_examples
@pytest.mark.parametrize("name,scan_name,section,key,value", KNOWN_VALUES)
def test_known_values(
    parsed: ParseFixture, name: str, scan_name: str, section: str, key: str, value: str
) -> None:
    """A hand-checked reading comes through exactly, under the right section.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    name : str
        Base file name of the example.
    scan_name : str
        Protocol name of the scan.
    section : str
        Section the parameter is printed in.
    key : str
        Parameter label.
    value : str
        Expected raw value, units included.

    Returns
    -------
    None
    """
    sections = _scan(parsed, name, scan_name).sections()
    assert section in sections, f"missing section {section}"
    assert sections[section].get(key) == value


@requires_examples
@pytest.mark.parametrize(
    "name,expected", [("R01StressDyn.pdf", "On"), ("R01StressDynXA60.pdf", "Off")]
)
def test_wrapped_label_is_rejoined(parsed: ParseFixture, name: str, expected: str) -> None:
    """The label wraps onto a second line while its value stays on the first.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    name : str
        Base file name of the example.
    expected : str
        The value printed beside the wrapped label.

    Returns
    -------
    None
    """
    properties = _scan(parsed, name, "localizer").sections()["Properties"]
    key = "Start measurement without further preparation"
    assert key in properties, sorted(properties)
    assert properties[key] == expected


@requires_examples
def test_wrapped_value_is_rejoined(parsed: ParseFixture) -> None:
    """A value that wraps onto a second line rejoins into one reading.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    routine = _scan(parsed, "R01StressDyn.pdf", "localizer").sections()["Routine"]
    assert routine["Filter"] == "Prescan Normalize, Elliptical filter"


@requires_examples
@pytest.mark.parametrize(
    "name,key",
    [("R01StressDyn.pdf", "Slice group"), ("R01StressDynXA60.pdf", "Slice Group")],
)
def test_repeated_keys_are_kept_not_overwritten(parsed: ParseFixture, name: str, key: str) -> None:
    """Three slice groups print the same labels three times over.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    name : str
        Base file name of the example.
    key : str
        The repeating label, spelled as that release prints it.

    Returns
    -------
    None
    """
    geometry = _scan(parsed, name, "localizer").sections()["Geometry - Common"]
    assert geometry[key] == "1"
    assert geometry[f"{key} #2"] == "2"
    assert geometry[f"{key} #3"] == "3"


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_sections_are_never_empty_of_structure(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """Every scan has sections, and every section has a non-blank name.

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
        assert sections, f"{scan.name} has no sections"
        assert all(name.strip() for name in sections), "a section has a blank name"


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_no_column_bleed(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """A value must never end up as a key.

    The failure this guards against is a mis-set label/value boundary, which
    would silently pair the left column's label with the right column's value.

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
        for section, entries in scan.sections().items():
            for key in entries:
                assert not key.startswith("#"), (scan.name, section, key)
