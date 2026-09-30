"""Transactional edits, donor dependency graphs, and review/publication contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import find_exar, requires_exar
from siemens_protocol.analysis.edit import EditSession
from siemens_protocol.analysis.query import Predicate, Query
from siemens_protocol.cli import main
from siemens_protocol.exar import read, validate
from siemens_protocol.exar.archive import Archive, from_container
from siemens_protocol.exar.inspect import ascconv_table
from siemens_protocol.gui.commands import build_argv

pytestmark = requires_exar
CMRR = "Minn_CMRR_2.3mm_S8_rest_6min"


@pytest.fixture(scope="module")
def base() -> Archive:
    """Load the small donor once; sessions clone it before editing.

    Returns
    -------
    Archive
        Immutable test donor.
    """
    return read(find_exar("Potpourri_P1.exar1"))


@pytest.fixture(scope="module")
def linked() -> Archive:
    """Load the copy-reference corpus with an add-in-bearing scout.

    Returns
    -------
    Archive
        Immutable linked donor.
    """
    return read(find_exar("copyparametertest.exar1"))


def test_patch_updates_both_stores_without_touching_source(base: Archive, tmp_path: Path) -> None:
    """A unitful canonical edit is isolated and survives serialization.

    Parameters
    ----------
    base : Archive
        Source fixture.
    tmp_path : Path
        Output directory.

    Returns
    -------
    None
    """
    rows = copy.deepcopy(base.container.tables["Instance"].rows)
    session = EditSession(base)
    event = session.patch(Query(scan=CMRR), {"tr": "2 s"})
    parameter = event["scans"][0]["parameters"][0]
    assert parameter["previous"] == 650.0
    assert parameter["value"] == 2000.0
    assert parameter["ascconv_value"] == "2000000"
    assert base.container.tables["Instance"].rows == rows
    assert session.archive.head != base.head
    output = tmp_path / "edited.exar1"
    assert session.write(output)["serialized_validation"] == []
    reread = read(str(output))
    scan = next(s for s in reread.steps if s.name == CMRR)
    assert scan.protocol.preview["sub.0.msr.tr.0"].value == 2000.0
    assert ascconv_table(scan.protocol.xprotocol)["alTR[0]"] == "2000000"
    # The previous head still resolves precisely the source instance versions.
    historical = copy.deepcopy(reread.container)
    branch = historical.tables["Branch"]
    for i, row in enumerate(branch.dicts()):
        if row["Head"] == reread.head:
            branch.set(i, "Head", base.head)
    old = from_container(historical)
    assert {n.element_id: n.content_hash for n in old.instances.values()} == {
        n.element_id: n.content_hash for n in base.instances.values()
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"TR": 2000, "unmapped field": 1},
        {"raw:alTR[0]": 2000},
        {"tr": 2000, "TR": 1000},
        {"TR": "2 mm"},
        {"TR": float("nan")},
        {"TR": float("inf")},
        {"TR": "NaN"},
        {"TR": "Infinity"},
        {"TR": "wrong"},
        {"TA": 1000},
        {},
    ],
)
def test_refused_patch_has_no_partial_changes(base: Archive, changes: dict[str, Any]) -> None:
    """Every requested control must succeed, including aliases and unit checks.

    Parameters
    ----------
    base : Archive
        Source fixture.
    changes : dict
        Invalid or partially applicable batch.

    Returns
    -------
    None
    """
    session = EditSession(base)
    archive = session.archive
    with pytest.raises(ValueError):
        session.patch(Query(scan=CMRR), changes)
    assert session.archive is archive
    assert session.events == []


@pytest.mark.parametrize(
    "query", [Query(scan="missing"), Query(scan=CMRR, where=Predicate("absent", "=", "On"))]
)
def test_unresolved_selector_cannot_edit(base: Archive, query: Query) -> None:
    """Empty and undecidable selectors refuse instead of applying a subset.

    Parameters
    ----------
    base : Archive
        Source fixture.
    query : Query
        Unresolved selector.

    Returns
    -------
    None
    """
    session = EditSession(base)
    with pytest.raises(ValueError):
        session.patch(query, {"TR": 2000})
    assert not session.events


def test_checkout_and_deleted_head_records_match_console_conventions(base: Archive) -> None:
    """Existing versions are changed records; removed staged versions leave the head.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    import re

    session = EditSession(base)
    change = next(
        r for r in session.archive.container.rows("ChangeSet") if r["Id"] == session.archive.head
    )
    assert change["Parent2ChangeSet_id"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{7}", change["DateTime"])
    records = [
        r
        for r in session.archive.container.rows("InstanceChangeSet")
        if r["ChangeSetId"] == session.archive.head
    ]
    assert {r["State"] for r in records} == {1}
    element = session.archive.steps[0].instance.element_id
    session.apply_plan(
        {"operations": [{"op": "delete", "steps": [element], "external_links": "drop"}]}
    )
    assert not [
        r
        for r in session.archive.container.rows("InstanceChangeSet")
        if r["ChangeSetId"] == session.archive.head and r["ElementId"] == element
    ]


def test_copy_protocol_preserves_relations_and_opaque_dependencies(
    linked: Archive, tmp_path: Path
) -> None:
    """Whole-program copying preserves content bytes, add-ins, and link flags.

    Parameters
    ----------
    linked : Archive
        Copy-link fixture.
    tmp_path : Path
        Output directory.

    Returns
    -------
    None
    """
    session = EditSession(linked)
    session.apply_plan(
        {"operations": [{"op": "copy_protocol", "parent": "Research/New exam", "name": "Copied"}]}
    )
    original, copied = session.archive.programs
    assert [s.name for s in original.steps] == [s.name for s in copied.steps]
    source_parent = next(
        r["ParentElementId"]
        for r in linked.container.rows("Instance")
        if r["Id"] == linked.program.id
    )
    assert (
        next(
            r["ParentElementId"]
            for r in session.archive.container.rows("Instance")
            if r["Id"] == copied.instance.id
        )
        == source_parent
    )
    for node in session.archive.instances.values():
        if node.kind == "EdfDirectory":
            assert (
                next(
                    r["ParentElementId"]
                    for r in session.archive.container.rows("Instance")
                    if r["Id"] == node.id
                )
                == session.archive.tree_root.element_id
            )
    assert [
        (l.group, l.copies_phase_encoding_direction, l.copies_steps) for l in original.links
    ] == [(l.group, l.copies_phase_encoding_direction, l.copies_steps) for l in copied.links]
    assert {s.instance.object_id for s in original.steps}.isdisjoint(
        s.instance.object_id for s in copied.steps
    )
    for before, after in zip(linked.programs[0].steps, copied.steps):
        assert before.protocol.instance.content_hash == after.protocol.instance.content_hash
        before_children = [linked.by_element[e] for e in before.instance.children]
        after_children = [session.archive.by_element[e] for e in after.instance.children]
        assert [n.kind for n in before_children] == [n.kind for n in after_children]
        for b, a in zip(before_children, after_children):
            assert (
                linked.contents[b.content_hash].to_stored()
                == session.archive.contents[a.content_hash].to_stored()
            )
    output = tmp_path / "copy.exar1"
    session.write(output)
    assert validate.problems(read(str(output))) == []


def test_assembly_preserves_group_links_and_donor_order(base: Archive, linked: Archive) -> None:
    """An assembled program can mix donor archives and preserve internal links.

    Parameters
    ----------
    base : Archive
        Base fixture.
    linked : Archive
        Linked donor.

    Returns
    -------
    None
    """
    session = EditSession(base)
    donor_names = [s.name for s in linked.steps]
    session.apply_plan(
        {
            "operations": [
                {
                    "op": "assemble",
                    "name": "Mixed",
                    "parent": ["Research", "Study"],
                    "donors": [{"scans": [CMRR]}, {"source": "links", "scans": donor_names}],
                }
            ]
        },
        sources={"links": linked},
    )
    program = next(p for p in session.archive.programs if p.name == "Mixed")
    assert [s.name for s in program.steps] == [CMRR, *donor_names]
    assert [
        (l.group, l.copies_phase_encoding_direction, l.copies_steps) for l in program.links
    ] == [
        (l.group, l.copies_phase_encoding_direction, l.copies_steps)
        for l in linked.programs[0].links
    ]
    assert validate.problems(session.archive) == []


def test_boundary_relations_require_explicit_drop(linked: Archive) -> None:
    """Partial donor groups cannot quietly lose copy dependencies.

    Parameters
    ----------
    linked : Archive
        Linked donor.

    Returns
    -------
    None
    """
    session = EditSession(linked)
    operation = {
        "op": "assemble",
        "parent": "Research/New",
        "name": "Partial",
        "donors": [{"scans": [linked.steps[3].name]}],
    }
    archive = session.archive
    with pytest.raises(ValueError, match="crosses a relation"):
        session.apply_plan({"operations": [operation]})
    assert session.archive is archive
    operation["external_links"] = "drop"
    event = session.apply_plan({"operations": [operation]})[0]
    assert len(event["dropped_relations"]) == 1
    assert not session.archive.programs[-1].links


def test_failed_plan_rolls_back_earlier_operations(base: Archive) -> None:
    """A later error cannot publish an earlier successful copy or patch.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    session = EditSession(base)
    before = session.archive
    with pytest.raises(ValueError, match="operation 2"):
        session.apply_plan(
            {
                "operations": [
                    {"op": "copy_protocol", "name": "Copy", "parent": "Research/Study"},
                    {
                        "op": "patch",
                        "query": {"protocol": "Copy", "scan": CMRR},
                        "changes": {"TR": 2000, "unknown": 1},
                    },
                ]
            }
        )
    assert session.archive is before
    assert not session.events


def test_live_copy_can_be_patched_without_rereading(base: Archive) -> None:
    """Fresh graph objects are queryable and writable within the same plan.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    session = EditSession(base)
    session.apply_plan(
        {
            "operations": [
                {"op": "copy_protocol", "name": "Copy", "parent": "Research/Study"},
                {"op": "pause", "program": "Copy", "name": "Wait", "position": 0},
                {
                    "op": "patch",
                    "query": {
                        "protocol": "Copy",
                        "scan": CMRR,
                        "where": {"parameter": "TR", "op": "=", "value": "650 ms"},
                    },
                    "changes": {"TR": 1200},
                },
            ]
        }
    )
    original = next(p for p in session.archive.programs if p.name == base.programs[0].name)
    copied = next(p for p in session.archive.programs if p.name == "Copy")
    assert copied.steps[0].is_pause
    assert (
        next(s for s in original.steps if s.name == CMRR).protocol.preview["sub.0.msr.tr.0"].value
        == 650
    )
    assert (
        next(s for s in copied.steps if s.name == CMRR).protocol.preview["sub.0.msr.tr.0"].value
        == 1200
    )


def test_pause_move_rename_delete_keep_mirrors_valid(base: Archive, tmp_path: Path) -> None:
    """Instruction steps are editable members of the execution order.

    Parameters
    ----------
    base : Archive
        Source fixture.
    tmp_path : Path
        Output directory.

    Returns
    -------
    None
    """
    session = EditSession(base)
    session.apply_plan(
        {
            "operations": [
                {"op": "pause", "name": "Wait", "position": 0},
                {"op": "rename", "step": "Wait", "name": "Check positioning"},
                {"op": "move", "step": "Check positioning", "position": 2},
            ]
        }
    )
    assert session.archive.steps[2].is_pause
    session.apply_plan({"operations": [{"op": "delete", "steps": ["Check positioning"]}]})
    assert [s.name for s in session.archive.steps] == [s.name for s in base.steps]
    session.write(tmp_path / "order.exar1")


def test_link_update_remove_and_backwards_guard(base: Archive) -> None:
    """Repeated link updates replace one relation and preserve flag values.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    session = EditSession(base)
    source, target = [s.name for s in base.steps[:2]]
    operation = {
        "op": "link",
        "source": source,
        "target": target,
        "group": "Slices",
        "copy_phase_encoding": True,
    }
    for _ in range(2):
        session.apply_plan({"operations": [operation]})
    matches = [
        l
        for l in session.archive.programs[0].links
        if l.group == "Slices"
        and l.source == base.steps[0].instance.object_id
        and l.target == base.steps[1].instance.object_id
    ]
    assert len(matches) == 1
    assert matches[0].copies_phase_encoding_direction
    before = session.archive
    with pytest.raises(ValueError, match="precede"):
        session.apply_plan({"operations": [{"op": "move", "step": target, "position": 0}]})
    assert session.archive is before
    session.apply_plan(
        {"operations": [{"op": "unlink", "source": source, "target": target, "group": "Slices"}]}
    )
    assert not [
        l
        for l in session.archive.programs[0].links
        if l.source == base.steps[0].instance.object_id
        and l.target == base.steps[1].instance.object_id
    ]


def test_delete_linked_scan_requires_explicit_policy(linked: Archive) -> None:
    """Deleting one endpoint records link loss instead of silently removing it.

    Parameters
    ----------
    linked : Archive
        Linked donor.

    Returns
    -------
    None
    """
    session = EditSession(linked)
    operation = {"op": "delete", "steps": [linked.steps[3].name]}
    with pytest.raises(ValueError, match="crosses a relation"):
        session.apply_plan({"operations": [operation]})
    operation["external_links"] = "drop"
    assert session.apply_plan({"operations": [operation]})[0]["dropped_relations"]
    assert validate.problems(session.archive) == []


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "unknown"},
        {"op": "pause", "name": "Wait", "position": -1},
        {"op": "move", "step": "Localizer", "position": True},
        {"op": "delete", "steps": []},
        {"op": "rename", "name": ""},
        {"op": "copy_protocol", "name": "Copy", "parent": "New", "typo": True},
    ],
)
def test_bad_operations_refuse_without_changes(base: Archive, operation: dict) -> None:
    """Invalid plan fields and positions must not corrupt the working archive.

    Parameters
    ----------
    base : Archive
        Source fixture.
    operation : dict
        Invalid operation.

    Returns
    -------
    None
    """
    session = EditSession(base)
    with pytest.raises(ValueError):
        session.apply_plan({"operations": [operation]})
    assert not session.events


def test_conditional_order_refuses_but_whole_copy_preserves(base: Archive) -> None:
    """Unsupported branching metadata is preserved on copy and refused on reorder.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    session = EditSession(base)
    document = session.archive.document(session.archive.program)
    document["Conditions"]["condition"] = {"$id": "9999", "Enabled": True}
    session.archive.replace_content(session.archive.program, document)
    with pytest.raises(ValueError, match="nonempty Conditions"):
        session.apply_plan({"operations": [{"op": "pause", "name": "Wait", "position": 0}]})
    assert not session.events


def test_output_protection_includes_aliases_and_hard_links(tmp_path: Path) -> None:
    """Force may replace an unrelated output but never the input or its aliases.

    Parameters
    ----------
    tmp_path : Path
        Temporary source and outputs.

    Returns
    -------
    None
    """
    import os

    original = Path(find_exar("Potpourri_P1.exar1")).read_bytes()
    source = tmp_path / "source.exar1"
    source.write_bytes(original)
    session = EditSession(source)
    session.patch(Query(scan=CMRR), {"TR": 1200})
    alias = tmp_path / "alias.exar1"
    alias.symlink_to(source)
    hard = tmp_path / "hard.exar1"
    os.link(source, hard)
    for target in (source, alias, hard):
        with pytest.raises(ValueError, match="source or donor"):
            session.write(target, force=True)
    output = tmp_path / "output.exar1"
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="already exists"):
        session.write(output)
    session.write(output, force=True)
    assert source.read_bytes() == original
    assert validate.problems(read(str(output))) == []


def test_serialized_validation_failure_never_publishes(
    base: Archive, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-back failure keeps an existing output and removes the temporary file.

    Parameters
    ----------
    base : Archive
        Source fixture.
    tmp_path : Path
        Output directory.
    monkeypatch : pytest.MonkeyPatch
        Read-back validator override.

    Returns
    -------
    None
    """
    session = EditSession(base)
    output = tmp_path / "output.exar1"
    output.write_bytes(b"keep this")
    monkeypatch.setattr("siemens_protocol.analysis.edit.read", lambda _: base)
    check = validate.problems
    monkeypatch.setattr(
        validate, "problems", lambda a: ["bad serialized archive"] if a is base else check(a)
    )
    with pytest.raises(ValueError, match="serialized"):
        session.write(output, force=True)
    assert output.read_bytes() == b"keep this"
    assert list(tmp_path.iterdir()) == [output]


def test_cli_patch_dry_run_and_strict_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The CLI shares canonical edits, evidence, and all-or-nothing refusal.

    Parameters
    ----------
    tmp_path : Path
        Output directory.
    capsys : pytest.CaptureFixture
        CLI output capture.

    Returns
    -------
    None
    """
    source = find_exar("Potpourri_P1.exar1")
    args = ["patch", source, "--scan", CMRR, "--set", "tr=2 s", "--json"]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"]
    assert report["operations"][0]["scans"][0]["parameters"][0]["ascconv_value"] == "2000000"
    output = tmp_path / "patch.exar1"
    assert main([*args, "--set", "unknown=On", "--out", str(output)]) == 2
    assert not output.exists()
    assert "mapping" in capsys.readouterr().err
    assert main([*args, "--out", str(output)]) == 0
    assert not json.loads(capsys.readouterr().out)["dry_run"]


def test_cli_plan_relative_donors_and_report_protection(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Plan-relative sources are protected along with the archive and manifest.

    Parameters
    ----------
    tmp_path : Path
        Plan and output directory.
    capsys : pytest.CaptureFixture
        CLI output capture.

    Returns
    -------
    None
    """
    base = find_exar("Potpourri_P1.exar1")
    donor = tmp_path / "donor.exar1"
    donor.write_bytes(Path(base).read_bytes())
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "sources": {"donor": "donor.exar1"},
                "operations": [
                    {
                        "op": "assemble",
                        "parent": "Research/New",
                        "name": "New",
                        "donors": [{"source": "donor", "scans": [CMRR]}],
                    },
                    {"op": "patch", "query": {"protocol": "New"}, "changes": {"TR": 1200}},
                ],
            }
        )
    )
    output = tmp_path / "assembled.exar1"
    report = tmp_path / "manifest.json"
    args = ["assemble", base, str(plan), "--out", str(output), "--manifest", str(report)]
    assert main([*args, "--manifest", str(donor), "--force"]) == 2
    assert not output.exists()
    assert main(args) == 0
    assert json.loads(report.read_text())["serialized_validation"] == []
    assert next(p for p in read(str(output)).programs if p.name == "New").steps[0].name == CMRR
    assert "Wrote" in capsys.readouterr().out


def test_gui_edit_forms_build_the_same_commands() -> None:
    """GUI forms emit strict CLI commands, including multiline changes.

    Returns
    -------
    None
    """
    assert build_argv(
        "patch", {"input": "base.exar1", "scan": CMRR, "changes": "tr=2 s\nte=30 ms"}
    ) == [
        "patch",
        "base.exar1",
        "--scan",
        CMRR,
        "--set",
        "tr=2 s",
        "--set",
        "te=30 ms",
    ]
    assert build_argv("assemble", {"input": "base.exar1", "plan": "plan.json"}) == [
        "assemble",
        "base.exar1",
        "plan.json",
    ]


def test_assembly_cli_selects_programs_inside_the_plan(
    base: Archive, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A multi-program input is selected by the plan, without a global program flag.

    Parameters
    ----------
    base : Archive
        Source fixture.
    tmp_path : Path
        Plan and output directory.
    capsys : pytest.CaptureFixture
        CLI output capture.

    Returns
    -------
    None
    """
    prepared = EditSession(base)
    prepared.apply_plan(
        {"operations": [{"op": "copy_protocol", "parent": "Research/Other", "name": "Second"}]}
    )
    prepared.patch(Query(protocol="Second", scan=CMRR), {"tr": 1200})
    multiple = tmp_path / "multiple.exar1"
    prepared.write(multiple)
    plan = tmp_path / "plan.json"
    operation = {
        "op": "copy_protocol",
        "protocol": "Second",
        "parent": "Research/Selected",
        "name": "Chosen",
    }
    plan.write_text(json.dumps({"operations": [operation]}))
    output = tmp_path / "selected.exar1"
    assert main(["assemble", str(multiple), str(plan), "--out", str(output), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["operations"][0]["before"]["path"].endswith("/Second")
    chosen = next(p for p in read(str(output)).programs if p.name == "Chosen")
    assert (
        next(s for s in chosen.steps if s.name == CMRR).protocol.preview["sub.0.msr.tr.0"].value
        == 1200
    )
    del operation["protocol"]
    plan.write_text(json.dumps({"operations": [operation]}))
    refused = tmp_path / "refused.exar1"
    assert main(["assemble", str(multiple), str(plan), "--out", str(refused)]) == 2
    assert not refused.exists()


def test_program_scoped_repeated_object_ids_remain_editable(base: Archive) -> None:
    """Console-style repeated identities cannot redirect a patch to another program.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import edit as graph
    from siemens_protocol.exar import generate

    session = EditSession(base)
    session.apply_plan(
        {"operations": [{"op": "copy_protocol", "parent": "Research/New", "name": "Repeated"}]}
    )
    original, copied = session.archive.programs
    remapping = {
        b.instance.object_id: a.instance.object_id for a, b in zip(original.steps, copied.steps)
    }
    table = session.archive.container.tables["Instance"]
    for step in copied.steps:
        table.set(
            table.find("Id", step.instance.id)[0], "ObjectId", remapping[step.instance.object_id]
        )
    session.archive.replace_content(
        copied.instance,
        generate.renumber_references(
            generate.sort_step_maps(
                graph._remap(session.archive.document(copied.instance), remapping)
            )
        ),
    )
    session.archive = graph.refresh(session.archive)
    assert validate.problems(session.archive) == []
    session.patch(Query(protocol="Repeated", scan=CMRR), {"tr": 1200})
    first, second = session.archive.programs
    assert (
        next(s for s in first.steps if s.name == CMRR).protocol.preview["sub.0.msr.tr.0"].value
        == 650
    )
    assert (
        next(s for s in second.steps if s.name == CMRR).protocol.preview["sub.0.msr.tr.0"].value
        == 1200
    )


def test_insert_uses_same_session_donors_and_manifest_order(base: Archive) -> None:
    """A freshly created and patched scan can be copied again in memory.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    session = EditSession(base)
    session.apply_plan(
        {
            "operations": [
                {
                    "op": "assemble",
                    "name": "New",
                    "parent": "Research/Study",
                    "donors": [{"query": {"scan": CMRR}}],
                },
                {"op": "patch", "query": {"protocol": "New"}, "changes": {"tr": 1400}},
                {
                    "op": "insert",
                    "program": "New",
                    "position": 0,
                    "donors": [{"source": "session", "program": "New", "scans": [CMRR]}],
                },
            ]
        }
    )
    result = session.archive.programs[-1]
    assert len(result.steps) == 2
    assert all(s.protocol.preview["sub.0.msr.tr.0"].value == 1400 for s in result.steps)
    assert len(session.events[-1]["before"]["steps"]) == 1
    assert len(session.events[-1]["after"]["steps"]) == 2
    assert len({s.instance.element_id for s in result.steps}) == 2
    assert validate.problems(session.archive) == []


def test_delete_all_steps_and_duplicate_destinations_refuse(base: Archive) -> None:
    """A final empty program or colliding destination name cannot be published.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    session = EditSession(base)
    with pytest.raises(ValueError, match="empty running order"):
        session.apply_plan(
            {
                "operations": [
                    {
                        "op": "delete",
                        "steps": [s.instance.element_id for s in base.steps],
                        "external_links": "drop",
                    }
                ]
            }
        )
    operation = {"op": "copy_protocol", "parent": "Research/New", "name": "Copy"}
    session.apply_plan({"operations": [operation]})
    before = session.archive
    with pytest.raises(ValueError, match="already contains"):
        session.apply_plan({"operations": [operation]})
    assert session.archive is before


def test_other_releases_and_mismatched_donors_refuse(base: Archive) -> None:
    """XA60 editing cannot silently import a differently versioned archive.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    None
    """
    from siemens_protocol.exar import edit as graph

    other = graph.clone(base)
    other.baseline = other.baseline.replace("VA60A", "VA30A")
    with pytest.raises(ValueError, match="XA60"):
        EditSession(other)
    session = EditSession(base)
    with pytest.raises(ValueError, match="incompatible"):
        session.apply_plan(
            {
                "operations": [
                    {"op": "copy_protocol", "source": "other", "name": "Other", "parent": "New"}
                ]
            },
            sources={"other": other},
        )
    assert not session.events


@pytest.fixture
def shared(base: Archive) -> Archive:
    """Construct two programs that reuse one console step instance.

    Parameters
    ----------
    base : Archive
        Source fixture.

    Returns
    -------
    Archive
        Valid archive with a shared CMRR acquisition.
    """
    from siemens_protocol.exar import edit as graph

    session = EditSession(base)
    session.apply_plan(
        {"operations": [{"op": "copy_protocol", "name": "Shared", "parent": "Research/New"}]}
    )
    archive = session.archive
    original, copied = archive.programs
    target = next(s for s in copied.steps if s.name == CMRR)
    archive = graph.delete_steps(archive, copied.instance, [target])
    owner = archive.by_element[copied.instance.element_id]
    order = [s.instance for s in archive.steps_of(owner)]
    order.insert(0, next(s.instance for s in original.steps if s.name == CMRR))
    graph.set_order(archive, owner, order)
    archive = graph.refresh(archive)
    assert validate.problems(archive) == []
    return archive


@pytest.mark.parametrize("program", ["Shared", "Potpourri_P1"])
def test_shared_patch_copies_on_write(shared: Archive, program: str, tmp_path: Path) -> None:
    """A program-scoped patch must leave every unselected program unchanged.

    Parameters
    ----------
    shared : Archive
        Shared-step fixture.
    program : str
        Program to edit, including the original instance owner.
    tmp_path : Path
        Output directory.

    Returns
    -------
    None
    """
    session = EditSession(shared)
    event = session.patch(Query(protocol=program, scan=CMRR), {"tr": 1400})
    assert event["scans"][0]["detached_from"]
    for p in session.archive.programs:
        assert next(s for s in p.steps if s.name == CMRR).protocol.preview[
            "sub.0.msr.tr.0"
        ].value == (1400 if p.name == program else 650)
    session.write(tmp_path / "shared.exar1")


@pytest.mark.parametrize("op", ["delete", "rename", "copy_protocol"])
def test_shared_step_graph_changes_preserve_other_programs(shared: Archive, op: str) -> None:
    """Deleting, renaming, or copying a shared step must retain valid ownership.

    Parameters
    ----------
    shared : Archive
        Shared-step fixture.
    op : str
        Operation to check.

    Returns
    -------
    None
    """
    session = EditSession(shared)
    if op == "delete":
        operation = {
            "op": op,
            "program": "Potpourri_P1",
            "steps": [CMRR],
            "external_links": "drop",
        }
    elif op == "rename":
        operation = {"op": op, "program": "Potpourri_P1", "step": CMRR, "name": "Renamed"}
    else:
        operation = {
            "op": op,
            "source": "session",
            "protocol": "Shared",
            "parent": "Research/More",
            "name": "Independent",
        }
    session.apply_plan({"operations": [operation]})
    untouched = next(p for p in session.archive.programs if p.name == "Shared")
    assert CMRR in [s.name for s in untouched.steps]
    assert validate.problems(session.archive) == []
