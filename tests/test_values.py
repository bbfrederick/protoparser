"""Parameter values, including the layout cases that are easy to get wrong.

Every expectation here was read off the printout and confirmed against the
raw PDF text, so a failure means the parser drifted, not the fixture.
"""

from __future__ import annotations

import pytest

from conftest import EXAMPLE_FILES, EXAMPLE_IDS, ParseFixture, find_example, requires_examples
from siemens_protocol.model import Scan

#: The protocol exported from two releases. Both exports carry the same file
#: name, so everything below names the release rather than the file.
R01_STRESS_DYN = "R01StressDyn.pdf"

#: ``(release, scan, section, key, value)`` -- the same acquisition in both
#: releases, which also checks that a rebuild across versions is comparable.
KNOWN_VALUES = [
    ("VE11C", "T1_MEMPRAGE_64ch", "Routine", "Slices per slab", "176"),
    ("VE11C", "T1_MEMPRAGE_64ch", "Contrast - Common", "TR", "2530.0 ms"),
    ("VE11C", "T1_MEMPRAGE_64ch", "Contrast - Common", "TI", "1100 ms"),
    ("VE11C", "localizer", "Contrast - Common", "TE", "5.00 ms"),
    ("VE11C", "localizer", "Routine", "Concatenations", "7"),
    ("XA60", "T1_MEMPRAGE_64ch", "Routine", "Slices per Slab", "176"),
    ("XA60", "T1_MEMPRAGE_64ch", "Contrast - Common", "TR", "2530.0 ms"),
    ("XA60", "T1_MEMPRAGE_64ch", "Contrast - Common", "TI", "1100 ms"),
    ("XA60", "localizer", "Contrast - Common", "TE", "5.00 ms"),
    ("XA60", "localizer", "Routine", "Concatenations", "7"),
]


def _scan(parsed: ParseFixture, release: str, scan_name: str) -> Scan:
    """Locate one scan of the R01StressDyn export from one release.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    release : str
        Release folder the export is taken from, such as ``"XA60"``. Both
        exports share a file name, so the release is what picks one.
    scan_name : str
        Protocol name of the scan.

    Returns
    -------
    Scan
        The matching scan. Fails the test if there is none.
    """
    for scan in parsed(find_example(R01_STRESS_DYN, release)).protocol.scans:
        if scan.name == scan_name:
            return scan
    pytest.fail(f"{scan_name} not found in the {release} export")


@requires_examples
@pytest.mark.parametrize("release,scan_name,section,key,value", KNOWN_VALUES)
def test_known_values(
    parsed: ParseFixture, release: str, scan_name: str, section: str, key: str, value: str
) -> None:
    """A hand-checked reading comes through exactly, under the right section.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    release : str
        Release the export is taken from.
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
    sections = _scan(parsed, release, scan_name).sections()
    assert section in sections, f"missing section {section}"
    assert sections[section].get(key) == value


@requires_examples
@pytest.mark.parametrize("release,expected", [("VE11C", "On"), ("XA60", "Off")])
def test_wrapped_label_is_rejoined(parsed: ParseFixture, release: str, expected: str) -> None:
    """The label wraps onto a second line while its value stays on the first.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    release : str
        Release the export is taken from.
    expected : str
        The value printed beside the wrapped label.

    Returns
    -------
    None
    """
    properties = _scan(parsed, release, "localizer").sections()["Properties"]
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
    routine = _scan(parsed, "VE11C", "localizer").sections()["Routine"]
    assert routine["Filter"] == "Prescan Normalize, Elliptical filter"


@requires_examples
@pytest.mark.parametrize(
    "release,key",
    [("VE11C", "Slice group"), ("XA60", "Slice Group")],
)
def test_repeated_keys_are_kept_not_overwritten(
    parsed: ParseFixture, release: str, key: str
) -> None:
    """Three slice groups print the same labels three times over.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    release : str
        Release the export is taken from.
    key : str
        The repeating label, spelled as that release prints it.

    Returns
    -------
    None
    """
    geometry = _scan(parsed, release, "localizer").sections()["Geometry - Common"]
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


#: A collapsed value column shows up as a flood of parameters printed with no
#: value at all. Across the examples the worst healthy scan sits under 5%,
#: while the spectroscopy scans that first exposed the fault ran to 21-30%.
MAX_VALUELESS_SHARE = 0.15


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_a_scan_is_not_mostly_parameters_without_a_value(
    parsed: ParseFixture, pdf: str, _version: str
) -> None:
    """A scan whose values mostly vanished has lost its label/value boundary.

    Siemens does print the occasional parameter with nothing beside it, so
    the count cannot be zero. What cannot happen is a whole page of them:
    that is a column whose boundary landed right of the value cell, which
    leaves every reading glued onto its own key and unqueryable.

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
        entries = [v for section in scan.sections().values() for v in section.values()]
        if not entries:
            continue
        valueless = sum(1 for v in entries if not v)
        assert (
            valueless <= len(entries) * MAX_VALUELESS_SHARE
        ), f"{scan.name}: {valueless} of {len(entries)} parameters have no value"
