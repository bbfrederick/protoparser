"""Observed-variable inventories and guarded PDF-to-ASCCONV writer support."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conftest import find_exar
from siemens_protocol.analysis.glossary import (
    glossary_files,
    modification_support,
    render,
    sequence_glossary,
)
from siemens_protocol.analysis.glossary_catalog import build_catalog
from siemens_protocol.analysis.query import Predicate, Query, search_files
from siemens_protocol.cli import build_parser, main
from siemens_protocol.exar import ascconv, read
from siemens_protocol.exar.archive import Protocol
from siemens_protocol.gui.commands import build_argv


@pytest.fixture(scope="module")
def cmrr_scan() -> Protocol:
    """Read a concrete CMRR scan with its verified build stamp.

    Returns
    -------
    Protocol
        The first BOLD scan from the XA60 copy-reference example.
    """
    archive = read(find_exar("copyparametertest.exar1"))
    return next(
        s.protocol
        for s in archive.steps
        if s.runs_a_protocol and ascconv.sequence_of(s.protocol) == "cmrr_mbep2d_bold"
    )


def test_modification_support_obeys_mapping_build_and_read_only_gates(cmrr_scan: Protocol) -> None:
    """Only verified displayed mappings qualify for user editing.

    Parameters
    ----------
    cmrr_scan : Protocol
        A concrete archive scan whose Special-card mapping is known.

    Returns
    -------
    None
    """
    support = modification_support(cmrr_scan, "Suppress 16-bit DICOM")
    assert support["modifiable"] and support["bit"] == 25
    assert support["choices"] == ["Off", "On"] and support["evidence"]
    changed = copy.deepcopy(cmrr_scan)
    changed.document["Data"] = changed.xprotocol.replace("R017 nxva60a", "R999 nxva60a")
    gated = modification_support(changed, "Suppress 16-bit DICOM")
    assert not gated["modifiable"] and gated["characterization_needed"]
    assert "build" in gated["reason"]
    assert modification_support(changed, "TR")["modifiable"]
    derived = modification_support(cmrr_scan, "Scan Res. A >> P")
    assert not derived["modifiable"] and not derived["characterization_needed"]
    absent = modification_support(cmrr_scan, "Uncharacterized control")
    assert not absent["modifiable"] and absent["characterization_needed"]
    raw = modification_support(cmrr_scan, "alTR[0]", raw=True)
    assert not raw["modifiable"] and raw["status"] == "raw_only"


def test_real_archive_glossary_names_round_trip_through_search() -> None:
    """The glossary exposes usable predicate names and checked writer evidence.

    Returns
    -------
    None
    """
    source = find_exar("copyparametertest.exar1")
    query = Query(sequence="cmrr_mbep2d_bold")
    report = glossary_files([source], query)
    assert not report.errors and report.selected >= 10
    entries = {e["name"]: e for e in report.parameters}
    assert "sub.0.msr.seq_path" not in entries
    assert "sub.0.HEADER.SubProtocolCount" not in entries
    entry = entries["Suppress 16-bit DICOM"]
    assert entry["query_name"] == "suppress_16_bit_dicom"
    assert entry["modifiable_occurrences"] == entry["occurrences"] == report.selected
    assert entry["examples"][0]["modification"]["raw_key"] == "sWipMemBlock.alFree[0]"
    assert not any(e["query_name"].startswith("raw:") for e in report.parameters)
    matched = search_files(
        [source], Query(sequence=query.sequence, where=Predicate(entry["query_name"], "=", "Off"))
    )
    assert len(matched.matches) == report.selected
    assert "Suppress 16-bit DICOM" in render(report)
    raw = glossary_files([source], Query(scan="rfMRI REST ME PA XA60"), include_raw=True)
    raw_entry = next(e for e in raw.parameters if e["query_name"] == "raw:alTR[0]")
    assert raw_entry["modifiable_occurrences"] == 0
    assert raw_entry["examples"][0]["reading"]["state"] == "known"


def test_parsed_pdf_glossary_preserves_conflicts_and_does_not_assert_write_support(
    tmp_path: Path,
) -> None:
    """Displayed names remain searchable when archive write gates are unavailable.

    Parameters
    ----------
    tmp_path : Path
        Temporary protocol JSON location.

    Returns
    -------
    None
    """
    source = tmp_path / "parsed.json"
    source.write_text(
        json.dumps(
            {
                "software_version": "XA60",
                "scans": [
                    {
                        "index": 4,
                        "name": "rest",
                        "path": "Brain/Study/Rest/rest",
                        "header": {"sequence": "epfid"},
                        "sections": {
                            "Routine": {"TR": "2000 ms", "Unmapped control": "Off"},
                            "Contrast": {"TR": "3000 ms"},
                            "ASCCONV": {"alTE[1]": "2000"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()
    report = glossary_files([source], Query(scan="rest"), include_raw=True)
    assert not report.errors and report.selected == 1
    entries = {e["name"]: e for e in report.parameters}
    assert entries["TR"]["states"] == {"conflicting": 1}
    assert entries["TR"]["modifiable_occurrences"] == 0
    assert entries["TR"]["modification_counts"] == {"unverified": 1}
    assert entries["Unmapped control"]["characterization_needed"]
    assert entries["Unmapped control"]["query_name"] == "unmapped_control"
    assert entries["alTE[1]"]["query_name"] == "raw:alTE[1]"
    assert source.read_bytes() == before


def test_glossary_cli_gui_and_batch_errors(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """CLI and GUI selectors share inventory behavior and explicit input errors.

    Parameters
    ----------
    tmp_path : Path
        Input and output locations.
    capsys : CaptureFixture
        Captured command-line output.

    Returns
    -------
    None
    """
    source, out = tmp_path / "scan.json", tmp_path / "out.json"
    source.write_text(
        json.dumps(
            {
                "scans": [
                    {
                        "name": "one",
                        "sections": {"Routine": {"TR": "2 s"}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["glossary", str(source), "--scan", "one", "--json", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["parameter_count"] == 1
    assert main(["glossary", str(source), str(tmp_path / "missing.pdf"), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"] == 1 and len(payload["errors"]) == 1
    assert main(["glossary", str(source), "--scan", "re:["]) == 2
    assert "glossary:" in capsys.readouterr().err
    args = build_argv("glossary", {"input": str(source), "sequence": "epfid", "raw": True})
    parsed = build_parser().parse_args(args)
    assert parsed.command == "glossary" and parsed.sequence == "epfid" and parsed.raw


def test_glossary_aggregation_counts_multiple_files(tmp_path: Path) -> None:
    """Matching sequences aggregate values while retaining concrete source examples.

    Parameters
    ----------
    tmp_path : Path
        Temporary parsed protocol files.

    Returns
    -------
    None
    """
    inputs = []
    for name, value in (("a", "2 s"), ("b", "3 s")):
        path = tmp_path / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "scans": [
                        {
                            "name": name,
                            "header": {"sequence": "epfid"},
                            "sections": {"Routine": {"TR": value}},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        inputs.append(path)
    report = glossary_files(inputs)
    assert not report.errors and report.selected == 2
    entry = report.parameters[0]
    assert entry["occurrences"] == 2 and set(entry["values"]) == {"2 s", "3 s"}
    assert entry["modification_counts"] == {"unverified": 2}
    assert {e["source_file"] for e in entry["examples"]} == {p.as_posix() for p in inputs}


def test_sequence_glossary_requires_no_input_or_working_directory_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """The user's fileless sequence command uses packaged knowledge alone.

    Parameters
    ----------
    tmp_path : Path
        Empty working directory, with no example or protocol files.
    monkeypatch : MonkeyPatch
        Changes the working directory.
    capsys : CaptureFixture
        Captured command output.

    Returns
    -------
    None
    """
    monkeypatch.chdir(tmp_path)
    assert main(["glossary", "--sequence", "cmrr_mbep2d_bold", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "catalog" and report["parameter_count"] > 100
    entries = {e["name"]: e for e in report["parameters"]}
    assert entries["Suppress 16-bit DICOM"]["modifiable"]
    mapping = entries["Suppress 16-bit DICOM"]["modification"]["mappings"][0]
    assert mapping["bit"] == 25 and mapping["builds"] and mapping["evidence"]
    assert entries["Slices"]["characterization_needed"]
    assert not entries["Slices"]["modifiable"]
    assert not report["selected"] and not report["scanned"]
    assert "mapped_conditional" in render(sequence_glossary(Query(sequence="cmrr_mbep2d_bold")))
    assert main(["glossary", "--sequence", "cmrr_mbep2d_bold", "--raw"]) == 2
    assert "input file" in capsys.readouterr().err


def test_sequence_catalog_release_scan_alias_and_sequence_listing() -> None:
    """Catalog names can be selected by release, known scan alias or signature.

    Returns
    -------
    None
    """
    xa60 = sequence_glossary(Query(sequence="cmrr-mb-epi-bold"))
    older = sequence_glossary(Query(sequence="cmrr_mbep2d_bold"), release="VE11C")
    assert xa60.parameters and older.parameters
    assert {e["software_version"] for e in older.parameters} == {"VE11C"}
    alias = xa60.sequences[0]["scans"][0]
    assert sequence_glossary(Query(scan=alias)).parameters
    listed = sequence_glossary()
    assert listed.sequences and not listed.parameters
    assert "cmrr_mbep2d_bold" in render(listed)
    assert not sequence_glossary(Query(sequence="Unknown sequence")).parameters
    with pytest.raises(ValueError, match="input file"):
        sequence_glossary(Query(exam="study"))
    with pytest.raises(ValueError, match="input file"):
        sequence_glossary(Query(where=Predicate("TR", "=", "2 s")))


def test_catalog_builder_uses_signature_identity_without_recording_scan_values() -> None:
    """A shared kernel is indexed under its identified sequence rather than guessed.

    Returns
    -------
    None
    """
    doc = {
        "source_file": "/private/path/scan.pdf",
        "software_version": "XA60",
        "scans": [
            {
                "name": "rest",
                "header": {"sequence": "epfid"},
                "sections": {
                    "Sequence - Special": {
                        "MB LeakBlock kernel": "On",
                        "Online multi-band recon.": "On",
                    },
                    "Routine": {"TR": "2 s"},
                },
            }
        ],
    }
    payload = build_catalog([doc])
    sequence = payload["sequences"][0]
    assert sequence["sequence"] == "cmrr_mbep2d_bold"
    assert "cmrr-mb-epi-bold" in sequence["aliases"] and sequence["sources"] == ["scan.pdf"]
    assert all("value" not in p for p in sequence["parameters"])
    assert "/private/path" not in json.dumps(payload)
