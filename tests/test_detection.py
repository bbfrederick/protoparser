"""Version auto-detection against the ground truth in the folder names."""

from __future__ import annotations

import pytest

from conftest import EXAMPLE_FILES, EXAMPLE_IDS, ParseFixture, requires_examples
from siemens_protocol.pipeline import ParseOptions, parse_document
from siemens_protocol.profiles import REGISTRY


@requires_examples
@pytest.mark.parametrize("pdf,expected", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_auto_detection_matches_folder(parsed: ParseFixture, pdf: str, expected: str) -> None:
    """Auto-detection agrees with the version the folder name declares.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    expected : str
        Version taken from the parent folder name.

    Returns
    -------
    None
    """
    protocol = parsed(pdf).protocol
    assert protocol.software_version == expected
    assert protocol.detection["confidence"] == "high"


@requires_examples
@pytest.mark.parametrize("pdf,expected", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_forcing_the_right_version_agrees_with_auto(
    parsed: ParseFixture, pdf: str, expected: str
) -> None:
    """Forcing the version detection would have chosen warns about nothing.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture. Unused, kept for signature parity.
    pdf : str
        Path to the example.
    expected : str
        Version taken from the parent folder name.

    Returns
    -------
    None
    """
    forced = parse_document(pdf, ParseOptions(version=expected)).protocol
    assert forced.detection["method"] == "forced"
    assert all("version forced" not in w for w in forced.warnings)


@requires_examples
def test_forcing_the_wrong_version_warns() -> None:
    """An override that disagrees with detection is honoured, but flagged.

    Returns
    -------
    None
    """
    pdf, expected = EXAMPLE_FILES[0]
    other = next(n for n in REGISTRY.names() if n != expected)
    protocol = parse_document(pdf, ParseOptions(version=other)).protocol
    assert protocol.software_version == other
    assert any("version forced" in w for w in protocol.warnings)


#: A running page header from each release, as printed. The trailing group is
#: a site build tag and varies between exports of the same release.
RUNNING_HEADERS = {
    "VB17A": "SIEMENS MAGNETOM TrioTim syngo MR B17",
    "VE11C": "SIEMENS MAGNETOM Prisma",
    "XA30": "SIEMENS MAGNETOM 3.0T XR Numaris/X VA30A-03GR",
    "XA60": "SIEMENS MAGNETOM 3.0T XR Numaris/X VA60A-0D4N",
}


@pytest.mark.parametrize("version,header", sorted(RUNNING_HEADERS.items()))
def test_each_running_header_scores_for_exactly_one_release(version: str, header: str) -> None:
    """A page header must match its own release and no other.

    Scoring "well enough" is not sufficient: every profile that scores at all
    is a detection candidate, so an overlapping pattern silently produces a
    confident wrong answer rather than an ambiguous one.

    Parameters
    ----------
    version : str
        The release the header came from.
    header : str
        The running page header as printed.

    Returns
    -------
    None
    """
    scoring = {
        name: REGISTRY.get(name).match_score(header)
        for name in REGISTRY.names()
        if REGISTRY.get(name).match_score(header) > 0
    }
    assert list(scoring) == [version], f"{header!r} also scored for {set(scoring) - {version}}"


@pytest.mark.parametrize("version,header", sorted(RUNNING_HEADERS.items()))
def test_detection_is_confident_about_each_release(version: str, header: str) -> None:
    """Detection returns the right release with high confidence.

    Parameters
    ----------
    version : str
        The release the header came from.
    header : str
        The running page header as printed.

    Returns
    -------
    None
    """
    profile, info = REGISTRY.detect(header)
    assert profile is not None and profile.name == version
    assert info["confidence"] == "high"


@pytest.mark.parametrize("build", ["VA30A-03GR", "VA30A-03MV", "VA30A-03DZ"])
def test_xa30_site_build_tags_all_detect(build: str) -> None:
    """The build suffix varies by site and must not affect detection.

    Parameters
    ----------
    build : str
        A build tag observed in the example exports.

    Returns
    -------
    None
    """
    profile, _info = REGISTRY.detect(f"SIEMENS MAGNETOM 3.0T XR Numaris/X {build}")
    assert profile is not None and profile.name == "XA30"


def test_a_release_number_is_not_matched_by_prefix() -> None:
    r"""XA60's pattern must not match VA30, and vice versa.

    This is the regression: XA60 originally required only ``VA\d\d``, so
    every XA30 export detected as XA60 at high confidence. Nothing failed
    loudly -- the grammars are identical -- but the version was wrong in the
    output and selected the wrong parameter vocabulary.

    Returns
    -------
    None
    """
    assert REGISTRY.get("XA60").match_score(RUNNING_HEADERS["XA30"]) == 0
    assert REGISTRY.get("XA30").match_score(RUNNING_HEADERS["XA60"]) == 0


def test_ve11c_is_identified_by_what_it_is_not() -> None:
    """VE11C prints no version string, so every other release must be rejected.

    Its header names only the scanner model, which is why the profile carries
    rejections rather than a positive pattern. Each new release therefore has
    to be added to that list, and this is what fails when it is not: VB17A
    exports otherwise detect as VE11C at high confidence.

    Returns
    -------
    None
    """
    ve11c = REGISTRY.get("VE11C")
    for version, header in RUNNING_HEADERS.items():
        expected = version == "VE11C"
        assert bool(ve11c.match_score(header)) is expected, f"VE11C mis-scores {version}"


def test_unknown_document_is_reported_not_guessed() -> None:
    """An unrecognized document yields no profile rather than a bad guess.

    Returns
    -------
    None
    """
    profile, info = REGISTRY.detect("some unrelated PDF text")
    assert profile is None
    assert info["confidence"] == "none"
