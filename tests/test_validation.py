"""Offline invariants, UI-only writes, and scanner-return evidence contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conftest import EXAR_PROTOCOL_FILES, REPO_ROOT, find_exar, requires_exar
from siemens_protocol.analysis.archive_view import as_protocol
from siemens_protocol.analysis.edit import EditSession
from siemens_protocol.analysis.generate import build, mappings
from siemens_protocol.analysis.query import Predicate, Query, search
from siemens_protocol.analysis.roundtrip import _differences, checklist, compare
from siemens_protocol.analysis.validation import validate
from siemens_protocol.cli import main
from siemens_protocol.exar import ascconv
from siemens_protocol.exar import edit as graph
from siemens_protocol.exar import geometry, read
from siemens_protocol.exar.archive import Archive, Protocol
from siemens_protocol.exar.inspect import ascconv_table

pytestmark = requires_exar
CMRR = "Minn_CMRR_2.3mm_S8_rest_6min"


@pytest.fixture(scope="module")
def source() -> Archive:
    """Read the small source once; each mutation uses a clone.

    Returns
    -------
    Archive
        Immutable donor.
    """
    return read(find_exar("Potpourri_P1.exar1"))


@pytest.fixture(scope="module")
def linked() -> Archive:
    """Read a source with twelve links and a scout add-in.

    Returns
    -------
    Archive
        Immutable linked donor.
    """
    return read(find_exar("copyparametertest.exar1"))


def _protocol(archive: Archive) -> Protocol:
    """Find the multislice CMRR acquisition.

    Parameters
    ----------
    archive : Archive
        Donor or independent candidate.

    Returns
    -------
    Protocol
        Named protocol.
    """
    return next(s.protocol for s in archive.steps if s.name == CMRR)


def _change(archive: Archive, key: str, literal: str) -> Archive:
    """Stage a raw change solely to challenge validation/comparison.

    Parameters
    ----------
    archive : Archive
        Immutable source.
    key, literal : str
        Existing assignment and changed literal.

    Returns
    -------
    Archive
        Independent mutated candidate; this is not a user editing API.
    """
    candidate = graph.clone(archive)
    p = _protocol(candidate)
    document = dict(p.document)
    document["Data"] = ascconv.write_ascconv(p.xprotocol, key, literal)
    assert document["Data"] != p.xprotocol
    candidate.replace_content(p.instance, document)
    return candidate


@pytest.mark.parametrize("path,release", EXAR_PROTOCOL_FILES)
def test_semantic_checks_cover_console_and_scanner_return_corpus(path: str, release: str) -> None:
    """Pin the known spacing defect and explicit scanner conversion markers.

    Parameters
    ----------
    path, release : str
        Corpus file and release discriminator.

    Returns
    -------
    None
    """
    archive = read(path)
    report = validate(archive)
    errors = [f for f in report.findings if f.severity == "error"]
    if Path(path).name == "driver_loadtest.exar1":
        assert len(errors) == 1
        assert errors[0].scan == CMRR and "3.15 mm" in errors[0].message
    else:
        conversions = sum(
            ascconv.baseline_string(p) == ascconv.CONVERSION_NEEDED
            for program in archive.programs
            for s in program.steps
            for p in s.protocols
        )
        assert len(errors) == conversions
        assert all(f.code == "conversion_needed" for f in errors), errors
    if report.release == "VA60A":
        assert report.protocols_checked > 0 and report.mappings_checked > 0
    else:
        assert not report.to_dict()["semantic_supported"]
    assert report.to_dict()["scanner_acceptance"] == "not_observed"


def test_validation_corpus_cannot_become_empty() -> None:
    """Keep parametrized coverage from silently turning into skips.

    Returns
    -------
    None
    """
    assert len(EXAR_PROTOCOL_FILES) >= 10
    assert any(Path(p).name == "driver_loadtest.exar1" for p, _ in EXAR_PROTOCOL_FILES)


def test_conversion_marker_is_preserved_and_refuses_ui_patches(source: Archive) -> None:
    """The sequence's explicit consistency marker cannot be cleared by patching.

    Parameters
    ----------
    source : Archive
        Source fixture.

    Returns
    -------
    None
    """
    p = _protocol(source)
    document = dict(p.document)
    start, end = ascconv.ascconv_bounds(p.xprotocol)
    assert start >= 0
    document["Data"] = (
        p.xprotocol[:end]
        + 'sProtConsistencyInfo.tBaselineString = "ConversionNeeded"\n'
        + p.xprotocol[end:]
    )
    marked = Protocol(p.instance, document)
    result, applied, skipped = mappings.patch_document(marked, {"TR": 1000})
    assert result == document and not applied and skipped
    assert "ConversionNeeded" in skipped[0].reason


def test_older_release_read_checks_do_not_authorize_mapped_writing(source: Archive) -> None:
    """An older archive can be structurally inspected but has no XA60 writer.

    Parameters
    ----------
    source : Archive
        Source fixture.

    Returns
    -------
    None
    """
    older = graph.clone(source)
    older.baseline = "N4_VE11S_LATEST_20170215"
    report = validate(older)
    assert not report.to_dict()["semantic_supported"]
    with pytest.raises(ValueError, match="XA60"):
        mappings.apply(older, {CMRR: {"TR": 1000}})
    with pytest.raises(ValueError, match="XA60"):
        build.apply_protocol(older, {"scans": []})


@pytest.mark.parametrize(
    "key,literal",
    [
        ("sSliceArray.lSize", "63"),
        ("sSliceArray.lSize", "64.5"),
        ("sSliceArray.lSize", "NaN"),
        ("sSliceArray.asSlice[1].dThickness", "-1.0"),
        ("sSliceArray.asSlice[1].sNormal.dTra", "0.5"),
        ("sSliceArray.asSlice[1].dReadoutFOV", "199.0"),
        ("alTR[0]", "Infinity"),
    ],
)
def test_numeric_shape_and_replicated_errors_are_located(
    source: Archive, key: str, literal: str
) -> None:
    """Corrupted shapes and mapped data cannot pass as valid geometry.

    Parameters
    ----------
    source : Archive
        Source fixture.
    key, literal : str
        Fault to inject.

    Returns
    -------
    None
    """
    report = validate(_change(source, key, literal))
    assert not report.valid
    assert any(
        f.scan == CMRR and f.step_index is not None and f.program
        for f in report.findings
        if f.severity == "error"
    )


def test_preview_disagreement_is_warning_and_does_not_rewrite_truth(source: Archive) -> None:
    """Preview is mapping evidence and a regenerable summary.

    Parameters
    ----------
    source : Archive
        Source fixture.

    Returns
    -------
    None
    """
    candidate = graph.clone(source)
    p = _protocol(candidate)
    document = copy.deepcopy(p.document)
    document["Preview"]["sub.0.msr.tr.0"]["Value"] = 1
    candidate.replace_content(p.instance, document)
    report = validate(candidate)
    assert report.valid
    assert any(f.code == "preview_ascconv" and f.severity == "warning" for f in report.findings)
    result = compare(source, candidate)
    assert result["preserved"] and result["preview"] and not result["changes"]
    assert ascconv.read_ascconv(_protocol(candidate).xprotocol, "alTR[0]") == "650000"
    document = as_protocol(candidate, candidate.programs[0], "stale Preview")
    assert search([document], Query(scan=CMRR, where=Predicate("tr", "=", "650 ms"))).matches
    assert not search([document], Query(scan=CMRR, where=Predicate("tr", "=", "1 ms"))).matches


@pytest.mark.parametrize(
    "changes",
    [{"Slice Thickness": 2.2}, {"Distance Factor": 20}, {"TR": 1000, "Slice Thickness": 2.2}],
)
def test_calculated_position_writes_are_refused_atomically(source: Archive, changes: dict) -> None:
    """A UI spacing edit must not silently recalculate individual positions.

    Parameters
    ----------
    source : Archive
        Source fixture.
    changes : dict
        Spacing edit, optionally mixed with another UI edit.

    Returns
    -------
    None
    """
    p = _protocol(source)
    document, applied, skipped = mappings.patch_document(p, changes)
    assert not applied and skipped and document == p.document
    session = EditSession(source)
    with pytest.raises(ValueError):
        session.patch(Query(scan=CMRR), changes)
    assert not session.events
    assert _protocol(session.archive).xprotocol == p.xprotocol


@pytest.mark.parametrize(
    "changes", [{"TR": float("inf")}, {"Base Resolution": 90.5}, {"MB dual kernel": 2}]
)
def test_low_level_writer_refuses_nonfinite_fractional_and_nonboolean_values(
    source: Archive, changes: dict
) -> None:
    """Direct mapped calls have the same scalar safeguards as edit sessions.

    Parameters
    ----------
    source : Archive
        Source fixture.
    changes : dict
        Unrepresentable UI value.

    Returns
    -------
    None
    """
    p = _protocol(source)
    document, applied, skipped = mappings.patch_document(p, changes)
    assert not applied and skipped and document == p.document


def test_mapped_basis_updates_are_order_independent_and_keep_calculated_fields(
    source: Archive,
) -> None:
    """FOV percentage is a UI control; only its characterized storage moves.

    Parameters
    ----------
    source : Archive
        Source fixture.

    Returns
    -------
    None
    """
    p = _protocol(source)
    a, applied, skipped = mappings.patch_document(p, {"FOV Phase": 50, "FOV Read": 200})
    b, _, _ = mappings.patch_document(p, {"FOV Read": 200, "FOV Phase": 50})
    assert not skipped and len(applied) == 2 and a == b
    assert ascconv.read_ascconv(a["Data"], "sSliceArray.asSlice[0].dPhaseFOV") == "100.0"
    c, _, skipped = mappings.patch_document(p, {"FOV Read": 200})
    assert not skipped
    before, after = ascconv_table(p.xprotocol), ascconv_table(c["Data"])
    moved = {k for k in before if before[k] != after[k]}
    assert moved and all(k.endswith((".dReadoutFOV", ".dPhaseFOV")) for k in moved)
    assert not geometry.problems(c["Data"])


def test_invalid_candidate_cannot_be_published(source: Archive, tmp_path: Path) -> None:
    """A caller mutating session internals still meets validation at publication.

    Parameters
    ----------
    source : Archive
        Source fixture.
    tmp_path : Path
        Output directory.

    Returns
    -------
    None
    """
    session = EditSession(source)
    session.archive = _change(session.archive, "sSliceArray.asSlice[1].dThickness", "-1.0")
    output = tmp_path / "invalid.exar1"
    with pytest.raises(ValueError, match="semantic validation"):
        session.write(output)
    assert not output.exists()


def test_roundtrip_checks_links_addins_and_fresh_graph_identities(linked: Archive) -> None:
    """A graph copy compares equal even though every owned GUID changes.

    Parameters
    ----------
    linked : Archive
        Donor with links and a scout add-in.

    Returns
    -------
    None
    """
    session = EditSession(linked)
    session.apply_plan(
        {
            "operations": [
                {"op": "copy_protocol", "parent": ["Checks"], "name": linked.programs[0].name}
            ]
        }
    )
    result = compare(linked, session.archive, returned_program="Checks/" + linked.programs[0].name)
    assert result["preserved"], result["changes"][:4]
    assert result["coverage"]["relations"] == 12
    assert "EdfAddInConfig" in result["coverage"]["owned_kinds"]
    assert result["coverage"]["ascconv_assignments"] > 10000
    assert not result["scanner_confirmed"]


def test_changed_addin_and_link_flags_are_substantive(linked: Archive) -> None:
    """Add-ins and all link payload flags remain in the comparison.

    Parameters
    ----------
    linked : Archive
        Linked source.

    Returns
    -------
    None
    """
    candidate = graph.clone(linked)
    addin = next(n for n in candidate.instances.values() if n.kind == "EdfAddInConfig")
    doc = dict(candidate.document(addin))
    doc["Data"] += " changed"
    candidate.replace_content(addin, doc)
    assert any("EdfAddInConfig" in d["path"] for d in compare(linked, candidate)["changes"])
    candidate = graph.clone(linked)
    program = candidate.programs[0].instance
    records = graph.relations(candidate, program)
    records[0]["Constraint"] += 1
    graph.set_relations(candidate, program, records)
    assert any("Constraint" in d["path"] for d in compare(linked, candidate)["changes"])


def test_pauses_order_and_missing_scans_are_detected(source: Archive) -> None:
    """No numeric equality can hide a changed execution recipe.

    Parameters
    ----------
    source : Archive
        Source fixture.

    Returns
    -------
    None
    """
    session = EditSession(source)
    session.apply_plan({"operations": [{"op": "pause", "name": "Check position", "position": 1}]})
    assert not compare(source, session.archive)["preserved"]
    session = EditSession(source)
    session.apply_plan({"operations": [{"op": "delete", "steps": [CMRR]}]})
    result = compare(source, session.archive)
    assert not result["preserved"] and result["changes"]


@pytest.mark.parametrize(
    "path,before,after,changed",
    [
        ("/order[0]/name", "01", "1", True),
        ("/metadata/flag", True, 1, True),
        ("/ascconv/flag", "0x10", "16.0", False),
        ("/ascconv/flag", "9007199254740992", "9007199254740993", True),
        ("/ascconv/value", "1.00000000001", "1", True),
        ("/embedded_ascconv/value", "1e-3", "0.001", False),
    ],
)
def test_roundtrip_numeric_equivalence_preserves_text_and_large_integer_changes(
    path: str, before: object, after: object, changed: bool
) -> None:
    """Literal normalization must not erase names, flag bits, or real numeric changes.

    Parameters
    ----------
    path : str
        Metadata or ASCCONV comparison address.
    before, after : object
        Values on either side of the scanner comparison.
    changed : bool
        Whether their difference must remain visible.

    Returns
    -------
    None
    """
    assert bool(_differences(before, after, path)) is changed


def test_derived_values_and_build_stamps_cannot_be_hidden_as_churn(source: Archive) -> None:
    """Only two derived times can be explicitly accepted, never other internals.

    Parameters
    ----------
    source : Archive
        Source fixture.

    Returns
    -------
    None
    """
    candidate = _change(source, "lTotalScanTimeSec", "999.0")
    assert compare(source, candidate)["derived"]
    assert not compare(source, candidate)["preserved"]
    assert compare(source, candidate, allow_derived=True)["preserved"]
    original = ascconv.read_ascconv(_protocol(source).xprotocol, "sWipMemBlock.tFree")
    candidate = _change(source, "sWipMemBlock.tFree", original.replace("91b106c1e", "otherbuild"))
    result = compare(source, candidate)
    assert result["churn"] and any("sequence_stamp" in d["path"] for d in result["changes"])
    assert not result["preserved"]


def test_observations_require_actual_scans_and_do_not_infer_runnability(source: Archive) -> None:
    """Faithful archives can represent greyed-out scans; observation is separate.

    Parameters
    ----------
    source : Archive
        Source fixture.

    Returns
    -------
    None
    """
    rows = checklist(source)
    assert rows and all(r["status"] == "not_tested" for r in rows)
    assert not compare(source, source)["scanner_confirmed"]
    for r in rows:
        r["status"] = "runnable"
    assert compare(source, source, observations=rows)["scanner_confirmed"]
    rows[0]["status"] = "greyed_out"
    result = compare(source, source, observations=rows)
    assert result["preserved"] and not result["scanner_confirmed"]
    rows[0]["scan"] = "wrong"
    with pytest.raises(ValueError, match="submitted scan"):
        compare(source, source, observations=rows)


def test_real_scanner_return_preserved_an_invalid_array() -> None:
    """Scanner evidence must retain, rather than excuse, the historical defect.

    Returns
    -------
    None
    """
    submitted = Path(REPO_ROOT) / "tests" / "fixtures" / "driver_submitted.exar1"
    result = compare(submitted, find_exar("driver_loadtest.exar1"))
    assert result["comparison_performed"]
    # The broader comparator also exposes SNR/scale, private sIR, and navigator
    # filename changes that the old acceptance check did not account for.
    expected = {"dOverallImageScaleFactor", "dRefSNR", "dRefSNR_VOI", "sequence_stamp"} | {
        f"sIR.adFree[{i}]" for i in range(1, 8)
    }
    assert {d["path"].rsplit("/", 1)[-1] for d in result["changes"]} == expected
    assert len(result["changes"]) == 17
    assert not result["preserved"]
    assert not result["returned_validation"]["valid"]
    assert any("3.15 mm" in f["message"] for f in result["returned_validation"]["findings"])


def test_cli_reports_truth_and_requires_observations(
    source: Archive, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """CLI JSON carries the same evidence and verdicts as the API.

    Parameters
    ----------
    source : Archive
        Source fixture.
    tmp_path : Path
        Observation output directory.
    capsys : CaptureFixture
        Capture CLI output.

    Returns
    -------
    None
    """
    path = find_exar("Potpourri_P1.exar1")
    assert main(["validate", path, "--checklist", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] and payload["observations"]
    assert main(["roundtrip", path, path, "--require-runnable", "--json"]) == 1
    assert not json.loads(capsys.readouterr().out)["scanner_confirmed"]
    for row in payload["observations"]:
        row["status"] = "runnable"
    observation_file = tmp_path / "observations.json"
    observation_file.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        main(
            [
                "roundtrip",
                path,
                path,
                "--observations",
                str(observation_file),
                "--require-runnable",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["scanner_confirmed"]
    assert main(["validate", find_exar("driver_loadtest.exar1"), "--json"]) == 1
    assert not json.loads(capsys.readouterr().out)["valid"]
