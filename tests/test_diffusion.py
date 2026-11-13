"""Sparse ASCCONV recovery, DVS export and real-archive extraction."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from siemens_protocol.cli import main
from siemens_protocol.exar import extract_diffusion, find_diffusion, read

ROOT = "sDiffusion.sFreeDiffusionData."
EXAMPLE = Path(__file__).resolve().parents[1] / "examples/XA60/CHR-MDD.exar1"


def protocol(extra="", *, size=3, count=3):
    # Deliberately asymmetric components; index 1 is an entirely omitted zero vector.
    lines = [
        "### ASCCONV BEGIN ###",
        f"{ROOT}asDiffDirVector.__attribute__.size = {size}",
        f"{ROOT}lDiffDirections = {count}",
        f"{ROOT}ulCoordinateSystem = 2",
        f"{ROOT}ulNormalization = 1",
        f"{ROOT}asDiffDirVector[0].dSag = -0.5",
        f"{ROOT}asDiffDirVector[2].dCor = 0.25",
        f"{ROOT}asDiffDirVector[2].dTra = 1e-3",
        f"{ROOT}sComment.0 = 0x41 # 'A'",
        f"{ROOT}sComment.1 = 0x00",
        "sDiffusion.ulMode = 128",
        extra,
        "### ASCCONV END ###",
    ]
    return SimpleNamespace(xprotocol="\n".join(lines))


def test_sparse_vectors_keep_order_zeros_sign_and_magnitude(tmp_path):
    table = extract_diffusion(protocol())
    assert table.vectors == ((-0.5, 0, 0), (0, 0, 0), (0, 0.25, 0.001))
    assert table.comment == "A"
    assert (table.coordinate_system_code, table.normalization_code) == (2, 1)
    output = tmp_path / "vectors.dvs"
    table.write_dvs(output, coordinate_system="prs", normalization="none")
    data = output.read_bytes()
    assert b"\r\n" in data
    text = data.decode()
    assert "[directions=3]" in text
    assert "CoordinateSystem = prs" in text
    assert "Normalisation = none" in text
    vectors = [
        tuple(map(float, row.split(",")))
        for row in re.findall(r"Vector\[\d+\] = \(([^)]+)\)", text)
    ]
    assert tuple(vectors) == table.vectors
    with pytest.raises(FileExistsError):
        table.write_dvs(output, coordinate_system="prs", normalization="none")


@pytest.mark.parametrize(
    "extra,size,count",
    [
        ("", 3, 2),
        ("", 0, 3),
        (f"{ROOT}asDiffDirVector[3].dSag = 1", 3, 3),
        (f"{ROOT}asDiffDirVector[0].dCor = nan", 3, 3),
        (f"{ROOT}asDiffDirVector[0].dCor = invalid", 3, 3),
        (f"{ROOT}sComment.2 = 0x100", 3, 3),
    ],
)
def test_invalid_tables_refused(extra, size, count):
    with pytest.raises(ValueError):
        extract_diffusion(protocol(extra, size=size, count=count))


def test_known_and_unknown_conventions():
    assert extract_diffusion(SimpleNamespace(xprotocol="")) is None
    table = extract_diffusion(protocol())
    assert table.coordinate_system == "prs"
    assert table.normalization == "none"
    assert "CoordinateSystem = prs" in table.to_dvs()
    with pytest.raises(ValueError, match="conflicts"):
        table.to_dvs(coordinate_system="xyz")
    with pytest.raises(ValueError, match="conflicts"):
        table.to_dvs(normalization="unity")
    unknown = replace(table, coordinate_system_code=999, normalization_code=999)
    assert unknown.coordinate_system is None
    assert unknown.normalization is None
    with pytest.raises(ValueError, match="unknown coordinate"):
        unknown.to_dvs()
    with pytest.raises(ValueError, match="unknown normalization"):
        unknown.to_dvs(coordinate_system="xyz")
    assert "Normalisation = unity" in unknown.to_dvs(
        coordinate_system="xyz", normalization="unity"
    )
    with pytest.raises(ValueError):
        unknown.to_dvs(coordinate_system="unknown", normalization="none")
    with pytest.raises(ValueError):
        unknown.to_dvs(coordinate_system="xyz", normalization="unknown")


@pytest.mark.skipif(not EXAMPLE.exists(), reason="CHR-MDD archive unavailable")
def test_real_107_vector_table_and_cli(tmp_path, capsys):
    archive = read(str(EXAMPLE))
    matches = find_diffusion(archive)
    assert len(matches) == 1
    scan = matches[0]
    assert scan.path[-1] == "dMRI_dir107_AP"
    table = scan.table
    assert len(table.vectors) == 107
    assert table.vectors[0] == (0, 0, 0)
    assert table.vectors[1] == (-0.395, 0.158, 0.565)
    assert table.comment.strip() == "DiffusionVectors_107_99dir_twoLowBshells.dvs"
    assert table.diffusion_mode_code == 128
    original = EXAMPLE.read_bytes()
    output = tmp_path / "recovered.dvs"
    assert main(["diffusion", str(EXAMPLE), "--json"]) == 0
    inventory = json.loads(capsys.readouterr().out)
    assert inventory[0]["table"]["vectors"][1] == [-0.395, 0.158, 0.565]
    assert inventory[0]["table"]["coordinate_system"] == "xyz"
    assert inventory[0]["table"]["normalization"] == "none"
    args = [
        "diffusion",
        str(EXAMPLE),
        "--scan",
        "dMRI_dir107_AP",
        "--out",
        str(output),
    ]
    assert main(args) == 0
    assert len(re.findall(r"^Vector\[", output.read_text(encoding="utf-8"), re.M)) == 107
    assert main(args) == 1
    assert main([*args, "--force"]) == 0
    assert main(["diffusion", str(EXAMPLE), "--scan", "Localizer"]) == 1
    assert EXAMPLE.read_bytes() == original


def read_original_dvs(path):
    """Independent parser for the supplied evidence, including multiple sections."""
    sections = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if match := re.fullmatch(r"\[directions\s*=\s*(\d+)\]", line, re.I):
            current = {"vectors": {}}
            sections[int(match[1])] = current
        elif match := re.fullmatch(r"vector\[\s*(\d+)\s*\]\s*=\s*\(([^)]+)\)", line, re.I):
            index = int(match[1])
            assert index not in current["vectors"]
            current["vectors"][index] = tuple(map(float, match[2].split(",")))
        else:
            key, value = map(str.strip, line.split("=", 1))
            current[key.lower()] = value.lower()
    for count, section in sections.items():
        assert sorted(section["vectors"]) == list(range(count))
    return sections


@pytest.mark.parametrize(
    "filename,archive_name,scan_name,count,code,coordinate",
    [
        (
            "DiffusionVectors_ABCD_102dir.dvs",
            "Potpourri_P2.exar1",
            "cmrr_mbep2d_diff",
            102,
            2,
            "prs",
        ),
        (
            "DiffusionVectors_107_99dir_twoLowBshells.dvs",
            "CHR-MDD.exar1",
            "dMRI_dir107_AP",
            107,
            1,
            "xyz",
        ),
    ],
)
def test_original_dvs_matches_archive_and_automatic_export(
    filename, archive_name, scan_name, count, code, coordinate, tmp_path
):
    fixture = Path(__file__).parent / "fixtures/diffusion" / filename
    sections = read_original_dvs(fixture)
    original = sections[count]
    assert original["coordinatesystem"] == coordinate
    assert original["normalisation"] == "none"
    if count == 107:
        assert set(sections) == {104, 107, 99}
    archive_path = EXAMPLE.parent / archive_name
    if not archive_path.exists():
        pytest.skip("paired archive unavailable")
    scan = next(s for s in find_diffusion(read(str(archive_path))) if s.path[-1] == scan_name)
    table = scan.table
    assert table.coordinate_system_code == code
    assert table.normalization_code == 1
    assert table.coordinate_system == coordinate
    assert table.normalization == "none"
    assert table.vectors == tuple(original["vectors"][i] for i in range(count))
    output = tmp_path / filename
    table.write_dvs(output)
    exported = read_original_dvs(output)
    assert set(exported) == {count}
    assert exported[count] == original
