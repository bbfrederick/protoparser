"""Copy-parameter link sets, and how the listing and the summary show them.

A link set is one source scan and every scan copying from it. The sets are
numbered from 1 in the source's running order, and a scan is marked ``(X>)``
as the source of set ``X`` and ``(>X)`` as a scan copying from it.

The corpus test checks the marks against the archive's own relations rather
than against a frozen table, so an archive gaining a link tightens it instead
of editing it. It also requires that some archive has more than one set, so
the numbering cannot pass by every link belonging to set 1.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import EXAR_FILES, requires_exar
from siemens_protocol import exar
from siemens_protocol.analysis import archive_view
from siemens_protocol.analysis.links import link_marks, link_sets
from siemens_protocol.analysis.listing import build_listing, render_listing
from siemens_protocol.analysis.summary import build_summary, render_summary
from siemens_protocol.cli import main


def _protocol(links: list[tuple[int, int, str]], scans: int = 6) -> dict:
    """Build a minimal serialized protocol carrying some links.

    Parameters
    ----------
    links : list of tuple
        ``(source, target, group)`` scan-index links, in stored order.
    scans : int, optional
        How many scans the protocol runs. Default 6.

    Returns
    -------
    dict
        A protocol with scans named ``s0``, ``s1``, ... and those links.
    """
    return {
        "source_file": "test.exar1",
        "software_version": "XA60",
        "scans": [
            {"index": i, "name": f"s{i}", "header": {"ta": "0:10", "sequence": "gre"}}
            for i in range(scans)
        ],
        "links": [{"source": s, "target": t, "group": g} for s, t, g in links],
    }


def _exar(name: str) -> str:
    """Locate one corpus archive by file name, skipping when it is absent.

    Parameters
    ----------
    name : str
        Base file name, such as ``"CHR-MDD.exar1"``.

    Returns
    -------
    str
        The full path to the archive.
    """
    for path, _version in EXAR_FILES:
        if os.path.basename(path) == name:
            return path
    pytest.skip(f"{name} not available")


def test_sets_are_numbered_by_the_running_order_of_their_sources() -> None:
    """Stored order is creation order; the numbering follows the scans.

    Returns
    -------
    None
    """
    sets = link_sets(_protocol([(4, 5, "Slices"), (1, 3, "Slices"), (1, 2, "Everything")]))
    assert [(s.number, s.source) for s in sets] == [(1, 1), (2, 4)]
    assert sets[0].targets == [2, 3]
    assert sets[0].groups == ["Everything", "Slices"]


def test_a_protocol_without_links_has_no_sets_and_no_column() -> None:
    """A printout records no links, so its listing must look as it always did.

    Returns
    -------
    None
    """
    protocol = _protocol([])
    del protocol["links"]
    assert link_sets(protocol) == []
    text = render_listing(protocol, build_listing(protocol))
    assert "links" not in text and "(X>)" not in text
    assert "link set" not in render_summary(build_summary(protocol))


def test_the_listing_and_the_summary_show_the_marks() -> None:
    """Each linked scan carries its mark; the summary names what is copied.

    Returns
    -------
    None
    """
    protocol = _protocol([(1, 3, "Slices"), (1, 4, "TablePosition")])
    rows = build_listing(protocol)
    assert [row.links for row in rows] == ["", "(1>)", "", "(>1)", "(>1)", ""]
    listing = render_listing(protocol, rows).splitlines()
    assert listing[5].rstrip().endswith("(1>)")
    assert listing[7].rstrip().endswith("(>1)")
    assert "TablePosition" not in "\n".join(listing)
    with_options = render_listing(protocol, rows, link_options=True).splitlines()
    assert with_options[5].rstrip().endswith("(1>)")
    assert with_options[7].rstrip().endswith("(>1) Slices")
    assert with_options[8].rstrip().endswith("(>1) TablePosition")
    summary = render_summary(build_summary(protocol))
    assert "1 copy-parameter link set" in summary
    assert any(
        line.split()[:3] == ["(>1)", "4", "s4"] and "TablePosition" in line
        for line in summary.splitlines()
    )


@requires_exar
def test_every_corpus_link_is_marked_on_both_of_its_scans() -> None:
    """The marks agree with the archive's own copy references, archive-wide.

    Returns
    -------
    None
    """
    most_sets = 0
    for path, _version in EXAR_FILES:
        try:
            archive = exar.read(path)
        except Exception:  # an unreadable corpus file is another test's concern
            continue
        for program in archive.programs:
            if not program.copy_references:
                continue
            protocol = archive_view.as_protocol(archive, program, path, include_flat=False)
            names = {s["index"]: s["name"] for s in protocol["scans"]}
            sets = link_sets(protocol)
            marks = link_marks(sets)
            most_sets = max(most_sets, len(sets))
            steps = {step.instance.object_id: step.name for step in program.steps}
            assert len(protocol["links"]) == len(program.copy_references), (path, program.name)
            for entry in sets:
                assert f"({entry.number}>)" in marks[entry.source]
                for target in entry.targets:
                    assert f"(>{entry.number})" in marks[target]
            for link in protocol["links"]:
                pair = (names[link["source"]], names[link["target"]])
                assert pair in {
                    (steps[one.source], steps[one.target]) for one in program.copy_references
                }
    assert most_sets >= 2, "no corpus archive exercises more than one link set"


@requires_exar
def test_cli_list_and_summary_report_the_sets(tmp_path: Path) -> None:
    """``CHR-MDD`` has two sets: fieldmap-to-BOLD and diffusion AP-to-PA.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    path = _exar("CHR-MDD.exar1")
    out = tmp_path / "list.json"
    assert main(["list", path, "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    marked = {row["name"]: row["links"] for row in payload["scans"] if "links" in row}
    assert marked["SpinEchoFieldMap_AP"] == "(1>)"
    assert marked["reward1"] == "(>1)"
    assert marked["dMRI_dir107_AP"] == "(2>)"
    assert marked["dMRI_dir107_PA_FORTOPUP"] == "(>2)"
    assert [entry["number"] for entry in payload["link_sets"]] == [1, 2]
    groups = {row["name"]: row.get("link_group") for row in payload["scans"]}
    assert groups["reward1"] == "CenterOfSlicesAndSaturationRegions"
    assert groups["SpinEchoFieldMap_AP"] is None

    text = tmp_path / "list.txt"
    assert main(["list", path, "--link-options", "--out", str(text)]) == 0
    assert any(
        line.rstrip().endswith("(>1) CenterOfSlicesAndSaturationRegions")
        for line in text.read_text(encoding="utf-8").splitlines()
    )

    summary = tmp_path / "summary.json"
    assert main(["summary", path, "--json", "--out", str(summary)]) == 0
    assert len(json.loads(summary.read_text(encoding="utf-8"))["link_sets"]) == 2
