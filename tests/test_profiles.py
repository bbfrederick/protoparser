"""Version profiles: header-summary grammars and the registry."""

from __future__ import annotations

import pytest

from siemens_protocol.profiles import REGISTRY
from siemens_protocol.profiles.base import LayoutConfig, ProfileRegistry, VersionProfile

VE11C_LINE = "TA: 6:02 PM: REF Voxel size: 1.0×1.0×1.0 mmPAT: 2 Rel. SNR: 1.00 : tfl_me"
XA60_LINE = (
    "TA: 6:02 min Coil Selection: Manual Voxel Size: 1.0×1.0×1.0 mm³ Acc:: 2 Rel. SNR: 1.00"
)


def test_ve11c_header_grammar() -> None:
    """VE11C runs "mm" straight into "PAT:" and appends the sequence binary.

    Returns
    -------
    None
    """
    fields = REGISTRY.get("VE11C").parse_header_summary(VE11C_LINE)
    assert fields == {
        "ta": "6:02",
        "pm": "REF",
        "voxel_size_mm": "1.0×1.0×1.0",
        "pat": "2",
        "rel_snr": "1.00",
        "sequence": "tfl_me",
    }


def test_xa60_header_grammar() -> None:
    """XA60 doubles the colon in "Acc::" and omits the sequence entirely.

    Which is why the profile recovers the sequence from a parameter instead.

    Returns
    -------
    None
    """
    fields = REGISTRY.get("XA60").parse_header_summary(XA60_LINE)
    assert fields == {
        "ta": "6:02 min",
        "coil_selection": "Manual",
        "voxel_size_mm": "1.0×1.0×1.0",
        "pat": "2",
        "rel_snr": "1.00",
    }
    assert REGISTRY.get("XA60").param_fallbacks == {"sequence": "Sequence Name"}


@pytest.mark.parametrize("name,line", [("VE11C", VE11C_LINE), ("XA60", XA60_LINE)])
def test_header_grammars_survive_ocr_spacing(name: str, line: str) -> None:
    """OCR drops and doubles spaces; the label-splitting parser tolerates it.

    Parameters
    ----------
    name : str
        Profile name.
    line : str
        That release's header summary line.

    Returns
    -------
    None
    """
    mangled = line.replace(" ", "  ").replace("Rel.  SNR", "Rel. SNR")
    fields = REGISTRY.get(name).parse_header_summary(mangled)
    assert fields["ta"].startswith("6:02")
    assert fields["rel_snr"].startswith("1.00")


def test_missing_fields_are_omitted_not_guessed() -> None:
    """A truncated header yields only the fields actually present.

    Returns
    -------
    None
    """
    fields = REGISTRY.get("VE11C").parse_header_summary("TA: 0:19")
    assert list(fields) == ["ta"]


def test_empty_header_yields_nothing() -> None:
    """An empty summary line parses to an empty mapping.

    Returns
    -------
    None
    """
    assert REGISTRY.get("XA60").parse_header_summary("") == {}


def test_profiles_expose_a_layout_config() -> None:
    """Every registered profile carries geometry thresholds.

    Returns
    -------
    None
    """
    for name in REGISTRY.names():
        assert isinstance(REGISTRY.get(name).layout, LayoutConfig)


def test_unknown_profile_names_are_reported() -> None:
    """Asking for an unknown release names the ones that do exist.

    Returns
    -------
    None
    """
    with pytest.raises(KeyError) as exc:
        REGISTRY.get("VE11E")
    assert "VE11E" in str(exc.value)
    for name in REGISTRY.names():
        assert name in str(exc.value)


def test_a_new_release_needs_only_a_profile() -> None:
    """Adding a version is registration plus a grammar, not new core code.

    Returns
    -------
    None
    """
    registry = ProfileRegistry()
    registry.register(
        VersionProfile(
            name="XA31",
            require=[r"Numaris/X", r"\bVA31"],
            header_labels=[("ta", r"\bTA\s*:"), ("rel_snr", r"\bRel\.?\s*SNR\s*:")],
        )
    )
    profile, info = registry.detect("SIEMENS MAGNETOM Numaris/X VA31A-1")
    assert profile is not None and profile.name == "XA31"
    assert info["confidence"] == "high"
    assert profile.parse_header_summary("TA: 1:00 Rel. SNR: 1.00") == {
        "ta": "1:00",
        "rel_snr": "1.00",
    }
