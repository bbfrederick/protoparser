"""Version profiles: header-summary grammars and the registry."""

from __future__ import annotations

import re

import pytest

from siemens_protocol.profiles import REGISTRY
from siemens_protocol.profiles.base import (
    SIZE_FIELDS,
    LayoutConfig,
    ProfileRegistry,
    VersionProfile,
)

VE11C_LINE = "TA: 6:02 PM: REF Voxel size: 1.0×1.0×1.0 mmPAT: 2 Rel. SNR: 1.00 : tfl_me"
#: VB17A pads its fields with runs of spaces, has no PM, puts PAT before the
#: voxel size, and names the sequence's owner rather than using a bare colon.
VB17A_LINE = (
    "TA: 1:08       PAT: Off      Voxel size: 2.2×1.1×10.0 mm     "
    "Rel. SNR: 1.00       SIEMENS: gre  "
)
XA60_LINE = (
    "TA: 6:02 min Coil Selection: Manual Voxel Size: 1.0×1.0×1.0 mm³ Acc:: 2 Rel. SNR: 1.00"
)
XA30_LINE = "TA: 9 sec Coil Selection: Auto Voxel Size: 1.2×1.2×5.0 mm³ Acc:: None Rel. SNR: 1.00"
#: Spectroscopy substitutes a volume of interest for the voxel size. VE11C
#: additionally omits PAT, which runs "mm" straight into "Rel.".
VE11C_VOI_LINE = "TA: 0:36 PM: REF VoI: 25 ×25 ×40 mmRel. SNR: 1.00 : svs_se"
XA60_VOI_LINE = "TA: 12 sec Coil Selection: Manual VoI: 25×25×22 mm³ Rel. SNR: 1.00"


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


def test_xa30_header_grammar() -> None:
    """XA30 shares the Numaris/X grammar with XA60 exactly.

    Returns
    -------
    None
    """
    fields = REGISTRY.get("XA30").parse_header_summary(XA30_LINE)
    assert fields == {
        "ta": "9 sec",
        "coil_selection": "Auto",
        "voxel_size_mm": "1.2×1.2×5.0",
        "pat": "None",
        "rel_snr": "1.00",
    }
    assert REGISTRY.get("XA30").param_fallbacks == {"sequence": "Sequence Name"}


def test_numaris_x_releases_agree_on_the_header_grammar() -> None:
    """XA30 and XA60 parse each other's summary lines identically.

    The grammar is shared source, so this guards the sharing rather than the
    regexes: if a future release needs its own, it must say so explicitly.

    Returns
    -------
    None
    """
    xa30, xa60 = REGISTRY.get("XA30"), REGISTRY.get("XA60")
    for line in (XA30_LINE, XA60_LINE):
        assert xa30.parse_header_summary(line) == xa60.parse_header_summary(line)


@pytest.mark.parametrize(
    "name,line",
    [
        ("VB17A", VB17A_LINE),
        ("VE11C", VE11C_LINE),
        ("XA30", XA30_LINE),
        ("XA60", XA60_LINE),
    ],
)
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
    profile = REGISTRY.get(name)
    mangled = line.replace(" ", "  ").replace("Rel.  SNR", "Rel. SNR")
    fields = profile.parse_header_summary(mangled)
    clean = profile.parse_header_summary(line)
    assert set(fields) == set(clean), "a field was lost to the respacing"
    # Doubling the spaces is allowed to show up inside a value, but nothing
    # else about the parse may change.
    assert {k: re.sub(r"\s+", " ", v) for k, v in fields.items()} == clean


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


# -- spectroscopy header lines ----------------------------------------------


def test_ve11c_spectroscopy_header_grammar() -> None:
    """A VE11C spectroscopy line yields a VoI, an SNR and a sequence.

    Before ``VoI:`` was declared, the whole tail was absorbed into ``pm``,
    which also swallowed the SNR and the sequence binary.

    Returns
    -------
    None
    """
    fields = REGISTRY.get("VE11C").parse_header_summary(VE11C_VOI_LINE)
    assert fields == {
        "ta": "0:36",
        "pm": "REF",
        "voi_mm": "25 ×25 ×40",
        "rel_snr": "1.00",
        "sequence": "svs_se",
    }


def test_xa60_spectroscopy_header_grammar() -> None:
    """A Numaris/X spectroscopy line yields a VoI rather than a voxel size.

    Returns
    -------
    None
    """
    fields = REGISTRY.get("XA60").parse_header_summary(XA60_VOI_LINE)
    assert fields == {
        "ta": "12 sec",
        "coil_selection": "Manual",
        "voi_mm": "25×25×22",
        "rel_snr": "1.00",
    }


def test_a_volume_of_interest_is_not_reported_as_a_voxel_size() -> None:
    """VoI and voxel size stay distinct fields.

    They are different quantities -- one acquisition volume versus an imaging
    resolution -- so folding them together would report a changed voxel size
    whenever the acquisition type changed.

    Returns
    -------
    None
    """
    for name, line in (("VE11C", VE11C_VOI_LINE), ("XA60", XA60_VOI_LINE)):
        fields = REGISTRY.get(name).parse_header_summary(line)
        assert "voi_mm" in fields
        assert "voxel_size_mm" not in fields


def test_ve11c_finds_the_snr_when_no_pat_field_separates_it() -> None:
    """``mmRel. SNR:`` must still resolve, exactly as ``mmPAT:`` does.

    VE11C omits ``PAT:`` on some imaging scans too, not only spectroscopy, so
    this is not a spectroscopy-only concern: the field map in two of the
    example protocols hit it.

    Returns
    -------
    None
    """
    line = "TA: 1:07 PM: FIX Voxel size: 3.2×3.2×2.0 mmRel. SNR: 1.00 : fm_r"
    fields = REGISTRY.get("VE11C").parse_header_summary(line)
    assert fields["voxel_size_mm"] == "3.2×3.2×2.0"
    assert fields["rel_snr"] == "1.00"
    assert fields["sequence"] == "fm_r"


#: Each profile paired with summary lines that release actually prints. A
#: profile is only expected to parse its own release's lines.
OWN_LINES = [
    ("VB17A", VB17A_LINE),
    ("VE11C", VE11C_LINE),
    ("VE11C", VE11C_VOI_LINE),
    ("XA30", XA30_LINE),
    ("XA60", XA60_LINE),
    ("XA60", XA60_VOI_LINE),
]


@pytest.mark.parametrize("name,line", OWN_LINES)
def test_size_fields_carry_no_unit(name: str, line: str) -> None:
    """Spatial-extent fields are stored bare, so mm3 and mm³ compare equal.

    Parameters
    ----------
    name : str
        Profile name.
    line : str
        A summary line that release prints.

    Returns
    -------
    None
    """
    fields = REGISTRY.get(name).parse_header_summary(line)
    assert any(key in fields for key in SIZE_FIELDS), "no spatial extent was parsed at all"
    for key in SIZE_FIELDS:
        value = fields.get(key, "")
        assert "mm" not in value, f"{name} left a unit in {key}: {value!r}"


@pytest.mark.parametrize("name,line", OWN_LINES)
def test_no_header_value_swallows_an_undeclared_label(name: str, line: str) -> None:
    """A colon left inside a parsed value means a label was not declared.

    This is the signature of the spectroscopy bug: an unknown ``VoI:`` was
    absorbed by whichever field preceded it, taking the SNR and the sequence
    binary with it.

    Parameters
    ----------
    name : str
        Profile name.
    line : str
        A summary line that release prints.

    Returns
    -------
    None
    """
    fields = REGISTRY.get(name).parse_header_summary(line)
    leaked = {k: v for k, v in fields.items() if ":" in v and k != "ta"}
    assert not leaked, f"{name} absorbed an undeclared label: {leaked}"


# -- VB17A ------------------------------------------------------------------


def test_vb17a_header_grammar() -> None:
    """VB17A names the sequence's owner and omits PM entirely.

    Returns
    -------
    None
    """
    fields = REGISTRY.get("VB17A").parse_header_summary(VB17A_LINE)
    assert fields == {
        "ta": "1:08",
        "pat": "Off",
        "voxel_size_mm": "2.2×1.1×10.0",
        "rel_snr": "1.00",
        "sequence": "gre",
        "sequence_owner": "SIEMENS",
    }


def test_vb17a_records_a_user_built_sequence() -> None:
    """``USER:`` marks a sequence built at the site rather than by Siemens.

    This is the one place an export says so, so the provenance is kept rather
    than discarded along with the label.

    Returns
    -------
    None
    """
    line = (
        "TA: 8:07       PAT: Off      Voxel size: 1.3×1.0×1.3 mm     "
        "Rel. SNR: 1.00       USER: tfl_mgh_multiecho  "
    )
    fields = REGISTRY.get("VB17A").parse_header_summary(line)
    assert fields["sequence"] == "tfl_mgh_multiecho"
    assert fields["sequence_owner"] == "USER"


def test_vb17a_tolerates_a_missing_pat_field() -> None:
    """Some VB17A scans print no PAT at all.

    Returns
    -------
    None
    """
    line = (
        "TA: 4:20            Voxel size: 2.3×2.3×5.0 mm     "
        "Rel. SNR: 1.00       SIEMENS: gre_field_mapping  "
    )
    fields = REGISTRY.get("VB17A").parse_header_summary(line)
    assert "pat" not in fields
    assert fields["sequence"] == "gre_field_mapping"


def test_vb17a_strips_the_marker_between_snr_and_sequence() -> None:
    """A "!" is printed before the sequence on some scans.

    Its meaning is not documented in the exports, so it is kept out of the
    SNR value rather than interpreted. The raw line survives in
    ``header_summary`` either way.

    Returns
    -------
    None
    """
    line = (
        "TA: 1.5 s       PAT: Off      Voxel size: 1.2×1.2×1.8 mm     "
        "Rel. SNR: 1.00        ! USER: ep2d_bold_MGH_tb  "
    )
    fields = REGISTRY.get("VB17A").parse_header_summary(line)
    assert fields["rel_snr"] == "1.00"
    assert fields["ta"] == "1.5 s"
    assert fields["sequence"] == "ep2d_bold_MGH_tb"
