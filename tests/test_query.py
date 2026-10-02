"""Collection search semantics, source fidelity and CLI integration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

from conftest import find_example, find_exar
from siemens_protocol.analysis.query import (
    All,
    AnyOf,
    Expression,
    Not,
    Predicate,
    Query,
    QueryReport,
    expression_from_dict,
    load_protocols,
    parameter_reading,
    parse_predicate,
    search,
    search_files,
)
from siemens_protocol.cli import build_parser, main
from siemens_protocol.exar import ascconv, read
from siemens_protocol.gui.commands import build_argv


def protocol(
    values: Any = None,
    *,
    version: str = "XA60",
    name: str = "bold",
    index: int = 2,
    path: str | None = None,
) -> dict:
    """Build a normalized protocol fixture.

    Parameters
    ----------
    values : Any
        Test input or fixture.
    version : str
        Test input or fixture.
    name : str
        Test input or fixture.
    index : int
        Test input or fixture.
    path : str | None
        Test input or fixture.

    Returns
    -------
    dict
        Test result or fixture data.
    """
    return {
        "source_file": "source.pdf",
        "software_version": version,
        "scans": [
            {
                "name": name,
                "index": index,
                "path": path or f"\\\\Research\\Brain\\Study\\Rest\\{name}",
                "header": {"sequence": "cmrr_mbep2d_bold"},
                "provenance": {"vendor": "CMRR", "family": "multiband EPI, BOLD"},
                "sections": {"Routine": values or {}},
            }
        ],
    }


def result(value: Any, condition: Expression) -> QueryReport:
    """Evaluate a parameter condition against one fixture.

    Parameters
    ----------
    value : Any
        Test input or fixture.
    condition : Expression
        Test input or fixture.

    Returns
    -------
    QueryReport
        Test result or fixture data.
    """
    return search([protocol({"TR": value})], Query(where=condition), include_unknown=True)


@pytest.mark.parametrize(
    "actual,op,wanted,matched",
    [
        ("2000 ms", "=", "2 s", True),
        ("2000 ms", ">", "1.9 s", True),
        ("2000 ms", "<", "2 s", False),
        ("2000 ms", "!=", "2 s", False),
        ("2e3 us", "=", "2 ms", True),
        ("0.1 ms", ">=", "100 us", True),
        ("0.1 ms", "<=", "100 us", True),
        ("0.1 ms", ">", "100 us", False),
        ("0.1 ms", "<", "100 us", False),
        ("2000 µs", "=", "0.002 sec", True),
        ("20 mm", "=", "2 cm", True),
        ("1 kHz", "=", "1000 Hz", True),
        ("50 %", ">=", "49 %", True),
        ("2 ms", "=", "2 mm", None),
        ("2 ms", ">", "1", None),
        ("Off", "=", "off", True),
        ("Off", "!=", "On", True),
        ("Off", ">", "1", None),
        ("Off", "~", "^off$", True),
    ],
)
def test_parameter_comparisons_and_units(
    actual: Any, op: str, wanted: Any, matched: bool | None
) -> None:
    """Confirm parameter comparisons and units.

    Parameters
    ----------
    actual : Any
        Test input or fixture.
    op : str
        Test input or fixture.
    wanted : Any
        Test input or fixture.
    matched : bool | None
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    report = result(actual, Predicate("TR", op, wanted))
    if matched is False:
        assert not report.matches and report.unknown == 0
    else:
        assert report.matches[0]["matched"] is matched
        assert report.unknown == (matched is None)


@pytest.mark.parametrize(
    "expression,expected",
    [
        (Not(Predicate("Absent", "=", "Off")), None),
        (All((Predicate("TR", "=", "2 s"), Predicate("Absent", "=", "Off"))), None),
        (All((Predicate("TR", "=", "3 s"), Predicate("Absent", "=", "Off"))), False),
        (AnyOf((Predicate("TR", "=", "2 s"), Predicate("Absent", "=", "Off"))), True),
        (AnyOf((Predicate("TR", "=", "3 s"), Predicate("Absent", "=", "Off"))), None),
        (Not(Predicate("TR", "=", "2 s")), False),
    ],
)
def test_boolean_expressions_do_not_turn_unknowns_into_matches(
    expression: Expression, expected: bool | None
) -> None:
    """Confirm boolean expressions do not turn unknowns into matches.

    Parameters
    ----------
    expression : Expression
        Test input or fixture.
    expected : bool | None
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    report = result("2000 ms", expression)
    assert (
        report.matches[0]["matched"] is expected if expected is not False else not report.matches
    )


def test_states_and_conflicting_occurrences() -> None:
    """Confirm states and conflicting occurrences.
    Returns
    -------
    None
        Test result or fixture data.
    """
    scan = protocol({"TR": "2000 ms", "TE": None})["scans"][0]
    assert parameter_reading(scan, "TR").state == "known"
    assert parameter_reading(scan, "TE").state == "unknown"
    assert parameter_reading(scan, "missing").state == "missing"
    scan["sections"]["Contrast"] = {"TR": "3000 ms"}
    reading = parameter_reading(scan, "TR")
    assert reading.state == "conflicting" and reading.values == ("2000 ms", "3000 ms")
    doc = {"scans": [scan]}
    compared = search([doc], Query(where=Predicate("TR", "=", "2 s")), include_unknown=True)
    assert compared.matches[0]["matched"] is None
    state = search([doc], Query(where=parse_predicate("TR is conflicting")))
    assert state.matches[0]["parameters"]["TR"]["state"] == "conflicting"


def test_missing_exists_and_negative_tests_are_distinct() -> None:
    """Confirm missing exists and negative tests are distinct.
    Returns
    -------
    None
        Test result or fixture data.
    """
    data = [protocol()]
    assert search(data, Query(where=Predicate("TR", "state", "missing"))).matches
    assert not search(data, Query(where=Predicate("TR", "exists"))).matches
    assert search(data, Query(where=Not(Predicate("TR", "exists")))).matches
    assert search(data, Query(where=Not(Predicate("TR", "=", "2 s")))).unknown == 1


def test_canonical_parameter_names_work_across_releases() -> None:
    """All observed acceleration labels share an identity and preserve values.

    Returns
    -------
    None
    """
    docs = [
        protocol({"PAT mode": "GRAPPA"}, version="VB17A"),
        protocol({"PAT mode": "GRAPPA"}, version="VE11C"),
        protocol({"Accel. mode": "GRAPPA"}, version="VE11C"),
        protocol({"Acceleration mode": "GRAPPA"}, version="XA30"),
        protocol({"Accel. Mode": "GRAPPA"}, version="XA30"),
        protocol({"Acceleration Mode": "GRAPPA"}, version="XA60"),
        protocol({"Accel. Mode": "GRAPPA"}, version="XA60"),
        protocol({"Accel. mode": "Slice accel."}, version="VE11C"),
        protocol({"Acceleration Mode": "SMS"}, version="XA60"),
    ]
    report = search(docs, Query(where=Predicate("acceleration_mode", "=", "GRAPPA")))
    assert len(report.matches) == 7
    for doc in docs[:7]:
        label = next(iter(doc["scans"][0]["sections"]["Routine"]))
        assert search([doc], Query(where=Predicate(label, "=", "GRAPPA"))).matches
    for value in ("Slice accel.", "SMS"):
        report = search(docs, Query(where=Predicate("acceleration_mode", "=", value)))
        assert len(report.matches) == 1


@pytest.mark.parametrize(
    "label",
    ["Suppress DICOM file output", "suppress dicom file output", "suppress_dicom_file_output"],
)
def test_spaced_and_underscore_query_names_are_equivalent(label: str) -> None:
    """Legacy spaced names still find parameters with underscore identifiers.

    Parameters
    ----------
    label : str
        A displayed label, old normalized name, or underscore query name.

    Returns
    -------
    None
    """
    report = search(
        [protocol({"Suppress DICOM file output": "Off"})],
        Query(where=Predicate(label, "=", "Off")),
    )
    assert len(report.matches) == 1
    reading = report.matches[0]["parameters"][label]
    assert reading["labels"] == ["Suppress DICOM file output"]


def test_aliases_are_explicit_and_raw_indices_are_preserved() -> None:
    """Confirm aliases are explicit and raw indices are preserved.
    Returns
    -------
    None
        Test result or fixture data.
    """
    data = protocol({"Suppress 16-bit DICOM": "Off"})
    query = Query(
        aliases={"Suppress DICOM file output": "Suppress 16-bit DICOM"},
        where=Predicate("Suppress DICOM file output", "=", "Off"),
    )
    assert search([data], query).matches
    scan = data["scans"][0]
    scan["raw_parameters"] = {"alTE[0]": "1000", "alTE[1]": "2000"}
    assert parameter_reading(scan, "raw:alTE[1]").values == ("2000",)
    assert parameter_reading(scan, "raw:alTE[2]").state == "missing"


def test_hierarchy_sequence_and_regex_filters_keep_source_and_scan_index() -> None:
    """Confirm hierarchy sequence and regex filters keep source and scan index.
    Returns
    -------
    None
        Test result or fixture data.
    """
    query = Query(
        region="brain",
        exam="study",
        protocol="Rest",
        scan="re:^bold$",
        vendor="CMRR",
        family="re:EPI",
        sequence="cmrr_mbep2d_bold",
    )
    report = search([protocol({"TR": "2000 ms"})], query)
    match = report.matches[0]
    assert match["scan_index"] == 2 and match["source_file"] == "source.pdf"
    assert match["path"] == "Research/Brain/Study/Rest/bold"
    assert not search([protocol()], Query(exam="different")).matches


@pytest.mark.parametrize(
    "selector, matched",
    [
        ("Rest", True),
        ("Study/Rest", True),
        ("brain/study/rest", True),
        ("Research/Brain/Study/Rest", True),
        ("Brain/Rest", False),
        ("Wrong/Study/Rest", False),
        ("re:^study$/^rest$", True),
        ("re:^wrong$/^rest$", False),
        ("re:[x/y]", False),
        ("re:[r/e]", True),
    ],
)
def test_qualified_protocol_selectors(selector: str, matched: bool) -> None:
    """Match contiguous protocol path tails without skipping hierarchy levels.

    Parameters
    ----------
    selector : str
        Bare or qualified protocol selector.
    matched : bool
        Whether the selector should name the fixture's protocol.

    Returns
    -------
    None
    """
    assert bool(search([protocol()], Query(protocol=selector)).matches) is matched


def test_qualified_protocol_selector_preserves_slashes_inside_names() -> None:
    """Escaped slashes remain part of a protocol name.

    Returns
    -------
    None
    """
    data = protocol(path=r"Root/Brain/Study/Rest\/one/bold")
    assert search([data], Query(protocol=r"Study/Rest\/one")).matches
    assert search([data], Query(protocol=r"Rest\/one")).matches
    assert search([data], Query(protocol="Rest/one")).matches
    assert not search([data], Query(protocol="Study/Rest/one")).matches


@pytest.mark.parametrize(
    "selectors",
    [
        {"protocol": "Frederick/UIC tests"},
        {"protocol": "re:^frederick$/^UIC tests$"},
        {"exam": "Frederick"},
        {"region": "Investigators", "protocol": "UIC tests", "exam": "Frederick"},
        {"path": "Root/Investigators/Frederick/UIC tests/bold"},
        {"scan": "bold"},
        {"protocol": "missing"},
    ],
)
def test_archive_hierarchy_filters_skip_unrelated_parameter_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selectors: dict[str, str]
) -> None:
    """Reject protocols before expensive decoding while preserving search counts.

    Parameters
    ----------
    tmp_path : Path
        Directory for the explicit archive input.
    monkeypatch : pytest.MonkeyPatch
        Replace archive IO and decoding with a guarded fixture.
    selectors : dict of str to str
        Hierarchy filters to compare with a fully decoded search.

    Returns
    -------
    None
    """
    source = tmp_path / "backup.exar1"
    folders = [
        ("Root", "Investigators", "Frederick", "UIC tests"),
        ("Root", "Investigators", "Other", "UIC tests"),
    ]
    documents = []
    programs = []
    for position, folder in enumerate(folders):
        name = "bold" if position == 0 else "other"
        data = protocol({"TR": "2000 ms"}, name=name, index=0, path="/".join((*folder, name)))
        data["source_file"] = source.as_posix()
        data["program"] = folder[-1]
        data["scans"][0]["raw_parameters"] = {}
        documents.append(data)
        programs.append(
            SimpleNamespace(
                instance=folder,
                name=folder[-1],
                steps=[
                    SimpleNamespace(runs_a_protocol=False, name="pause"),
                    SimpleNamespace(
                        runs_a_protocol=True, name=name, protocol=SimpleNamespace(xprotocol="")
                    ),
                ],
            )
        )
    query = Query(**selectors, where=Predicate("TR", "=", "2 s"))
    expected = search(documents, query)
    decoded = []

    def path_of(node: tuple[str, ...], parents: dict) -> list[str]:
        """Return the fixture node's existing folder path.

        Parameters
        ----------
        node : tuple of str
            Folder components used as a node identity.
        parents : dict
            Shared archive parent index.

        Returns
        -------
        list of str
            Full folder path.
        """
        return list(node)

    def as_protocol(archive: Any, program: Any, source: str) -> dict:
        """Fail if a rejected program reaches parameter decoding.

        Parameters
        ----------
        archive : Any
            Fixture archive.
        program : Any
            Program to decode.
        source : str
            Input source path.

        Returns
        -------
        dict
            Selected normalized document.
        """
        data = documents[folders.index(program.instance)]
        assert search([data], query).candidates
        decoded.append(program.instance)
        return data

    archive = SimpleNamespace(programs=programs, directory_parents={}, path_of=path_of)
    monkeypatch.setattr("siemens_protocol.analysis.query.read", lambda path: archive)
    monkeypatch.setattr("siemens_protocol.analysis.query.archive_view.as_protocol", as_protocol)
    actual = search_files([source], query)
    assert actual.to_dict() == expected.to_dict()
    assert actual.scanned == 2
    assert len(decoded) == expected.candidates


def test_missing_hierarchy_and_escaped_slashes_are_retained() -> None:
    """Confirm missing hierarchy and escaped slashes are retained.
    Returns
    -------
    None
        Test result or fixture data.
    """
    data = protocol(name="scan/one", path=r"Root/Brain/Study/Rest/scan\/one")
    assert search([data]).matches[0]["scan"] == "scan/one"
    bare = protocol(path="bad", name="bold")
    match = search([bare]).matches[0]
    assert match["region"] == match["exam"] == match["protocol"] == ""


def test_json_expressions_support_nested_and_or_not() -> None:
    """Confirm json expressions support nested and or not.
    Returns
    -------
    None
        Test result or fixture data.
    """
    query = Query.from_dict(
        {
            "where": {
                "all": [
                    {"parameter": "TR", "op": ">=", "value": "2 s"},
                    {"not": {"any": [{"parameter": "TE", "op": "state", "value": "known"}]}},
                ]
            }
        }
    )
    assert result("2000 ms", query.where).matches


@pytest.mark.parametrize(
    "payload",
    [
        {"all": {}},
        {"not": {}, "parameter": "TR"},
        {"oops": []},
        {"parameter": "TR", "op": "execute", "value": "2"},
        {"parameter": "TR", "op": "exists", "value": False},
    ],
)
def test_invalid_expressions_are_rejected(payload: Any) -> None:
    """Confirm invalid expressions are rejected.

    Parameters
    ----------
    payload : Any
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    with pytest.raises(ValueError):
        expression_from_dict(payload)


def test_cli_query_combines_conditions_and_reports_input_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Confirm cli query combines conditions and reports input errors.

    Parameters
    ----------
    tmp_path : Path
        Test input or fixture.
    capsys : pytest.CaptureFixture
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    source = tmp_path / "input.json"
    source.write_text(json.dumps(protocol({"TR": "2000 ms", "TE": "30 ms"})), encoding="utf-8")
    args = ["query", str(source), "--where", "TR >= 2 s", "--where", "TE < 0.04 s", "--json"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["match_count"] == 1
    assert (
        main(
            [
                "query",
                str(source),
                "--where",
                "TR = 3 s",
                "--where",
                "TE = 30 ms",
                "--any",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["match_count"] == 1
    bad = tmp_path / "bad.json"
    bad.write_text('{"programs": []}', encoding="utf-8")
    assert main(["query", str(source), str(bad), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["match_count"] == 1 and len(payload["errors"]) == 1
    assert "original .exar1" in payload["errors"][0]["error"]
    assert main(["query", str(source), "--where", "bad"]) == 2


def test_query_file_cli_overrides_unknowns_and_output_path(tmp_path: Path) -> None:
    """Confirm query file cli overrides unknowns and output path.

    Parameters
    ----------
    tmp_path : Path
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    source, spec, output = [tmp_path / f"{name}.json" for name in ("source", "spec", "out")]
    source.write_text(json.dumps(protocol()), encoding="utf-8")
    spec.write_text(
        json.dumps({"vendor": "incorrect", "where": {"parameter": "TR", "value": "2 s"}}),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "query",
                str(source),
                "--query",
                str(spec),
                "--vendor",
                "CMRR",
                "--include-unknown",
                "--json",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["unknown"] == 1 and payload["matches"][0]["matched"] is None


def test_json_without_flat_and_inputs_are_not_modified(tmp_path: Path) -> None:
    """Confirm json without flat and inputs are not modified.

    Parameters
    ----------
    tmp_path : Path
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    data = protocol({"TR": "2000 ms"})
    text = json.dumps(data)
    source = tmp_path / "source.json"
    source.write_text(text, encoding="utf-8")
    assert search_files([source], Query(where=Predicate("TR", "=", "2 s"))).matches
    assert source.read_text(encoding="utf-8") == text
    assert json.dumps(data) == text


def test_directory_discovery_and_duplicates_use_actual_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirm directory discovery and duplicates use actual inputs.

    Parameters
    ----------
    tmp_path : Path
        Test input or fixture.
    monkeypatch : pytest.MonkeyPatch
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    nested = tmp_path / "sub"
    nested.mkdir()
    a, b = nested / "a.PDF", nested / "b.exar1"
    a.touch()
    b.touch()
    (nested / "ignore.json").write_text("{}", encoding="utf-8")
    opened = []

    def load(path: Path | None, **kwargs: Any) -> list[dict]:
        """Record directory inputs without decoding fixture files.

        Parameters
        ----------
        path : Path | None
            Test input or fixture.

        Returns
        -------
        list[dict]
            Test result or fixture data.
        """
        opened.append(path)
        return [protocol()]

    monkeypatch.setattr("siemens_protocol.analysis.query.load_protocols", load)
    report = search_files([tmp_path, a, b])
    assert len(opened) == 2 and report.scanned == 2
    assert not report.errors


def test_real_archive_cmrr_query_and_raw_parameters() -> None:
    """Confirm real archive cmrr query and raw parameters.
    Returns
    -------
    None
        Test result or fixture data.
    """
    source = find_exar("copyparametertest.exar1")
    query = Query(
        vendor="re:CMRR", family="re:EPI", where=Predicate("Suppress 16-bit DICOM", "=", "Off")
    )
    report = search_files([source], query)
    assert len(report.matches) >= 10 and not report.errors
    assert {m["exam"] for m in report.matches} == {"Frederick"}
    raw = search_files([source], Query(where=Predicate("raw:alTR[0]", ">", "0")))
    assert raw.matches and not raw.errors
    archive = read(source)
    scans = [s for s in archive.programs[0].steps if s.runs_a_protocol]
    match = raw.matches[0]
    assert match["parameters"]["raw:alTR[0]"]["values"] == [
        ascconv.read_ascconv(scans[match["scan_index"]].protocol.xprotocol, "alTR[0]")
    ]


def test_every_program_of_a_real_archive_is_searchable() -> None:
    """Confirm every program of a real archive is searchable.
    Returns
    -------
    None
        Test result or fixture data.
    """
    source = find_exar("Frederick_P2.exar1")
    archive = read(source)
    report = search_files([source])
    assert not report.errors
    assert report.scanned == sum(s.runs_a_protocol for p in archive.programs for s in p.steps)
    assert {m["protocol"] for m in report.matches} == {p.name for p in archive.programs}


def test_mixed_pdf_and_archive_search_and_source_names() -> None:
    """Confirm mixed pdf and archive search and source names.
    Returns
    -------
    None
        Test result or fixture data.
    """
    pdf, archive = find_example("copyparametertest.pdf", "XA60"), find_exar(
        "copyparametertest.exar1"
    )
    report = search_files([pdf, archive], Query(scan="re:REST"))
    assert not report.errors
    assert {Path(m["source_file"]).suffix for m in report.matches} == {".pdf", ".exar1"}


def test_gui_query_form_builds_valid_cli_arguments() -> None:
    """Confirm gui query form builds valid cli arguments.
    Returns
    -------
    None
        Test result or fixture data.
    """
    args = build_argv(
        "query",
        {
            "input": "examples",
            "vendor": "re:CMRR",
            "where": "TR >= 2 s\nSuppress 16-bit DICOM = Off",
            "json": True,
        },
    )
    parsed = build_parser().parse_args(args)
    assert parsed.command == "query" and len(parsed.where) == 2


def test_gui_preserves_commas_inside_parameter_values() -> None:
    """Confirm gui preserves commas inside parameter values.
    Returns
    -------
    None
        Test result or fixture data.
    """
    args = build_argv("query", {"input": "example.exar1", "where": "Mode = A, B\nTR >= 2 s"})
    assert build_parser().parse_args(args).where == ["Mode = A, B", "TR >= 2 s"]


def test_corrupt_archive_and_invalid_flat_view_are_reported_without_losing_good_inputs(
    tmp_path: Path,
) -> None:
    """Confirm corrupt archive and invalid flat view are reported without losing good inputs.

    Parameters
    ----------
    tmp_path : Path
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    archive = tmp_path / "bad.exar1"
    archive.write_bytes(b"not a SQLite archive")
    invalid, valid = tmp_path / "invalid.json", tmp_path / "valid.json"
    data = protocol()
    data["scans"][0]["flat"] = []
    invalid.write_text(json.dumps(data), encoding="utf-8")
    valid.write_text(json.dumps(protocol({"TR": "2 s"})), encoding="utf-8")
    report = search_files([archive, invalid, valid], Query(where=Predicate("TR", "=", "2 s")))
    assert len(report.errors) == 2 and len(report.matches) == 1


def test_raw_empty_readings_remain_unknown() -> None:
    """Confirm raw empty readings remain unknown.
    Returns
    -------
    None
        Test result or fixture data.
    """
    scan = {"raw_parameters": {"alTR[0]": ""}}
    assert parameter_reading(scan, "raw:alTR[0]").state == "unknown"


def test_direction_markers_in_parameter_names_are_not_comparison_operators() -> None:
    """Direction markers stay part of the printed parameter name.

    Returns
    -------
    None
    """
    parsed = parse_predicate("Scan Res. A >> P >= 100")
    assert parsed == Predicate("Scan Res. A >> P", ">=", "100")
    assert parse_predicate("Protocol filename = A=B") == Predicate("Protocol filename", "=", "A=B")


def test_directory_traversal_failure_does_not_discard_explicit_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirm directory traversal failure does not discard explicit files.

    Parameters
    ----------
    tmp_path : Path
        Test input or fixture.
    monkeypatch : pytest.MonkeyPatch
        Test input or fixture.

    Returns
    -------
    None
        Test result or fixture data.
    """
    source = tmp_path / "good.json"
    source.write_text(json.dumps(protocol({"TR": "2 s"})), encoding="utf-8")

    def refused(self: Path, pattern: str) -> NoReturn:
        """Simulate a failed directory traversal.

        Parameters
        ----------
        self : Path
            Test input or fixture.
        pattern : str
            Test input or fixture.

        Returns
        -------
        NoReturn
            Test result or fixture data.
        """
        raise PermissionError("directory cannot be read")

    monkeypatch.setattr(Path, "rglob", refused)
    report = search_files([tmp_path, source], Query(where=Predicate("TR", "=", "2 s")))
    assert len(report.matches) == 1 and len(report.errors) == 1
    assert "directory cannot be read" in report.errors[0]["error"]
