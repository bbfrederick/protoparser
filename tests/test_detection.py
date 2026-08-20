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


def test_detection_scoring_separates_the_two_releases() -> None:
    """The Numaris/X build string is what tells the two releases apart.

    Both name a MAGNETOM scanner, so the rejection pattern carries the
    distinction rather than the requirement.

    Returns
    -------
    None
    """
    ve11c, xa60 = REGISTRY.get("VE11C"), REGISTRY.get("XA60")
    ve_header = "SIEMENS MAGNETOM Prisma"
    xa_header = "SIEMENS MAGNETOM 3.0T XR Numaris/X VA60A-0D4N"
    assert ve11c.match_score(ve_header) > 0
    assert xa60.match_score(ve_header) == 0
    assert ve11c.match_score(xa_header) == 0
    assert xa60.match_score(xa_header) > 0


def test_unknown_document_is_reported_not_guessed() -> None:
    """An unrecognized document yields no profile rather than a bad guess.

    Returns
    -------
    None
    """
    profile, info = REGISTRY.detect("some unrelated PDF text")
    assert profile is None
    assert info["confidence"] == "none"
