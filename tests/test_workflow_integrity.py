"""Regressions for misleading empty comparisons and inconsistent edit routes."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conftest import find_exar, requires_exar
from siemens_protocol.analysis import archive_view
from siemens_protocol.analysis.diff import diff_protocols
from siemens_protocol.analysis.flatten import flatten_sections
from siemens_protocol.analysis.generate import build, mappings
from siemens_protocol.analysis.report import render_protocol
from siemens_protocol.cli import _load_protocol, main
from siemens_protocol.exar import geometry, read
from siemens_protocol.model import Protocol, ScanLink


def document(*names: str) -> dict:
    """A protocol with known execution metadata and no parameter differences."""
    return {
        "source_file": "test.exar1",
        "scans": [
            {"index": i, "name": name, "header": {}, "flat": {}} for i, name in enumerate(names)
        ],
        "links": [],
        "pauses": [],
    }


@pytest.mark.parametrize("payload", [{"format": "exar1", "programs": []}, {"programs": []}])
@pytest.mark.parametrize("command", ["list", "summary", "check", "diff", "exar"])
def test_archive_json_is_rejected_by_protocol_commands(tmp_path, capsys, payload, command):
    path = tmp_path / "archive.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    if command == "exar":
        # The writer loads its template before the JSON input.
        args = [command, find_exar("Potpourri_P1_loadtest.exar1"), str(path)]
    else:
        args = [command, str(path)] + ([str(path)] if command == "diff" else [])
    assert main(args) == 1
    output = capsys.readouterr()
    assert "Use the original .exar1" in output.err
    assert "no substantive differences" not in output.out


@pytest.mark.parametrize("payload", [[], {}, {"scans": {}}, {"scans": [None]}])
def test_malformed_protocol_json_is_rejected(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="scan"):
        _load_protocol(str(path), "auto")


def test_valid_empty_protocol_json_is_still_accepted(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    assert _load_protocol(str(path), "auto")["scans"] == []


@pytest.mark.parametrize("side", ["left", "right"])
def test_library_diff_also_refuses_archive_json(side):
    inputs = {"left": document(), "right": document()}
    inputs[side] = {"format": "exar1", "programs": []}
    with pytest.raises(ValueError, match=f"{side}: archive JSON"):
        diff_protocols(**inputs)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("links", {"source": 0, "target": 1, "group": "Slices"}),
        ("pauses", {"before": 1, "name": "Wait for operator"}),
    ],
)
def test_execution_only_change_affects_cli_status_and_both_reports(tmp_path, capsys, kind, value):
    left, right = document("A", "B"), document("A", "B")
    right[kind].append(value)
    paths = [tmp_path / "left.json", tmp_path / "right.json"]
    for path, data in zip(paths, (left, right)):
        path.write_text(json.dumps(data), encoding="utf-8")
    args = ["diff", *map(str, paths)]
    assert main(args) == 1
    text = capsys.readouterr().out
    assert "1 execution changes" in text
    assert "no substantive differences" not in text
    assert main([*args, "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_count"] == 1
    assert payload["substantive_count"] == 0
    assert payload["execution_differences"][0]["value"] == value
    # A parameter-card filter must not hide execution changes.
    assert diff_protocols(left, right, sections=["geometry"]).differs


@pytest.mark.parametrize(
    "option,value",
    [
        ("group", "Everything"),
        ("copies_phase_encoding_direction", True),
        ("copies_steps", True),
        ("ignores_last_step", True),
        ("ignores_measurements", True),
        ("extra", {"FutureOption": "True"}),
    ],
)
def test_copy_link_options_are_compared(option, value):
    left = document("A", "B")
    left["links"] = [ScanLink(0, 1, "Slices").to_dict()]
    right = copy.deepcopy(left)
    right["links"][0][option] = value
    result = diff_protocols(left, right)
    assert result.differs and result.execution_count == 2


def test_legacy_link_options_are_unknown_rather_than_false():
    left, right = document("A", "B"), document("A", "B")
    left["links"] = [{"source": 0, "target": 1, "group": "Slices"}]
    right["links"] = [ScanLink(0, 1, "Slices", copies_steps=True).to_dict()]
    result = diff_protocols(left, right)
    assert not result.differs
    assert "options not compared" in " ".join(result.warnings)


def test_inserting_a_scan_does_not_move_existing_link_or_pause_anchors():
    left, right = document("A", "B"), document("new", "A", "B")
    left["links"] = [{"source": 0, "target": 1, "group": "Slices"}]
    right["links"] = [{"source": 1, "target": 2, "group": "Slices"}]
    left["pauses"] = [{"before": 1, "name": "wait"}, {"before": 2, "name": "done"}]
    right["pauses"] = [{"before": 2, "name": "wait"}, {"before": 3, "name": "done"}]
    result = diff_protocols(left, right)
    assert result.unmatched_count == 1
    assert result.execution_count == 0


def test_link_order_is_ignored_but_multiplicity_and_pause_order_are_not():
    left = document("A", "B", "C")
    left["links"] = [{"source": 0, "target": t, "group": "Slices"} for t in (1, 2)]
    right = copy.deepcopy(left)
    right["links"].reverse()
    assert not diff_protocols(left, right).differs
    right["links"].append(right["links"][0])
    assert diff_protocols(left, right).execution_count == 1
    right = copy.deepcopy(left)
    left["pauses"] = [{"before": 1, "name": name} for name in ("first", "second")]
    right["pauses"] = list(reversed(left["pauses"]))
    assert diff_protocols(left, right).execution_count == 2


@pytest.mark.parametrize("before,name", [(0, "wait"), (2, "wait"), (1, "different")])
def test_moving_or_renaming_a_pause_is_detected(before, name):
    left, right = document("A", "B"), document("A", "B")
    left["pauses"] = [{"before": 1, "name": "wait"}]
    right["pauses"] = [{"before": before, "name": name}]
    assert diff_protocols(left, right).differs


def test_pdf_unknown_execution_metadata_is_not_treated_as_absence():
    pdf = Protocol(source_file="test.pdf").to_dict()
    archive = document()
    archive["pauses"] = [{"before": 0, "name": "wait"}]
    result = diff_protocols(pdf, archive)
    assert not result.differs
    assert len(result.warnings) == 2
    assert "metadata unavailable" in render_protocol(result)
    assert "in comparable data" in render_protocol(result)


def test_archive_model_keeps_known_empty_execution_metadata():
    archive = Protocol(source_file="test.exar1", execution_metadata_available=True).to_dict()
    assert archive["links"] == archive["pauses"] == []
    assert not diff_protocols(archive, archive).warnings


@requires_exar
def test_archive_adapter_keeps_every_copy_option():
    archive = read(find_exar("copyparametertest.exar1"))
    program = archive.programs[0]
    data = archive_view.as_protocol(archive, program, "test.exar1")
    for actual, stored in zip(data["links"], program.copy_references):
        for key in (
            "copies_phase_encoding_direction",
            "copies_steps",
            "ignores_last_step",
            "ignores_measurements",
            "extra",
        ):
            assert actual[key] == getattr(stored, key)


@requires_exar
def test_direct_and_pdf_driven_geometry_edits_refuse_calculated_positions(tmp_path: Path) -> None:
    """All supported edit routes preserve a scan needing calculated position writes.

    Parameters
    ----------
    tmp_path : Path
        Destination for checking serialization of the unchanged scan.

    Returns
    -------
    None
    """
    source = find_exar("Potpourri_P1_loadtest.exar1")
    name = "CTRL05_unchanged_cmrr_mbep2d_bold"
    archive = read(source)
    step = next(s for s in archive.steps if s.name == name)
    before = step.protocol.xprotocol
    group = geometry.read_group(before)
    assert group is not None and geometry.agrees(before) < geometry.TOLERANCE
    value = group.thickness + 0.2
    requested = {"Slice Thickness": value, "TR": 777.0}
    patched, applied, skipped = mappings.patch_document(step.protocol, requested)
    assert not applied and {s.label for s in skipped} == set(requested)
    assert step.protocol.xprotocol == before  # The document API is non-mutating.
    assert patched == step.protocol.document

    manifest = mappings.apply(archive, {name: requested})
    assert not manifest.applied and {s.label for s in manifest.skipped} == set(requested)
    destination = tmp_path / "patched.exar1"
    archive.write(str(destination))
    reloaded = next(s for s in read(str(destination)).steps if s.name == name)
    assert reloaded.protocol.xprotocol == before
    assert geometry.agrees(reloaded.protocol.xprotocol) < geometry.TOLERANCE

    driven = read(source)
    report = build.apply_protocol(
        driven,
        {
            "scans": [
                {
                    "name": name,
                    "flat": flatten_sections(
                        {
                            "Geometry - Common": {"Slice Thickness": str(value)},
                            "Contrast - Common": {"TR": "777.0 ms"},
                        }
                    ),
                }
            ]
        },
    )
    assert not report.applied and {s.label for s in report.skipped} == set(requested)
    assert next(s for s in driven.steps if s.name == name).protocol.xprotocol == before
