"""Pause steps in the listing: shown in running order, never numbered.

A pause is an operator instruction the archive keeps in the running order --
"Pause for saliva collection" -- and a printout does not print. ``list
--pauses`` shows them between the scans; the scans keep the numbers they have
without it, which is the property worth guarding, since a pause counted as a
scan would shift every index after it and every ``--scan`` address with it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import EXAR_FILES, requires_exar
from siemens_protocol import exar
from siemens_protocol.analysis import archive_view
from siemens_protocol.analysis.listing import PAUSE, build_listing, render_listing
from siemens_protocol.cli import main


def _protocol() -> dict:
    """Build three scans with a pause before, between and after them.

    Returns
    -------
    dict
        A serialized protocol carrying ``pauses``.
    """
    return {
        "source_file": "test.exar1",
        "software_version": "XA60",
        "scans": [
            {"index": i, "name": f"s{i}", "header": {"ta": "0:10", "sequence": "gre"}}
            for i in range(3)
        ],
        "pauses": [
            {"before": 0, "name": "first"},
            {"before": 2, "name": "middle"},
            {"before": 3, "name": "last"},
        ],
    }


def _body(text: str) -> list[list[str]]:
    """Split the table rows of a listing into whitespace-separated fields.

    Parameters
    ----------
    text : str
        A rendered listing.

    Returns
    -------
    list of list of str
        The fields of every line between the header rule and the closing rule,
        without the leading verdict mark a third-party scan carries.
    """
    lines = text.splitlines()
    rules = [at for at, line in enumerate(lines) if line.strip() and not line.strip(" -")]
    body = [line.split() for line in lines[rules[0] + 1 : rules[1]]]
    return [fields[1:] if fields[0] in ("*", "?") else fields for fields in body]


def test_pauses_are_shown_in_running_order_without_numbers() -> None:
    """Each pause sits above the scan it precedes and carries no index.

    Returns
    -------
    None
    """
    protocol = _protocol()
    body = _body(render_listing(protocol, build_listing(protocol), pauses=True))
    assert body == [
        ["first", PAUSE],
        ["0", "s0", "gre", "0:10"],
        ["1", "s1", "gre", "0:10"],
        ["middle", PAUSE],
        ["2", "s2", "gre", "0:10"],
        ["last", PAUSE],
    ]


def test_pauses_are_hidden_unless_asked_for() -> None:
    """Without the switch the listing is what it always was.

    Returns
    -------
    None
    """
    protocol = _protocol()
    text = render_listing(protocol, build_listing(protocol))
    assert PAUSE not in text
    assert "total (3 scans)" in text


@requires_exar
def test_every_corpus_pause_step_is_placed_before_the_right_scan() -> None:
    """The adapter's pauses match the archive's pause steps, one for one.

    Returns
    -------
    None
    """
    seen = 0
    for path, _version in EXAR_FILES:
        try:
            archive = exar.read(path)
        except Exception:  # an unreadable corpus file is another test's concern
            continue
        for program in archive.programs:
            protocol = archive_view.as_protocol(archive, program, path, include_flat=False)
            expected, scans = [], 0
            for step in program.steps:
                if step.is_pause:
                    expected.append({"before": scans, "name": step.name})
                if step.runs_a_protocol:
                    scans += 1
            assert protocol.get("pauses", []) == expected, (path, program.name)
            assert [s["index"] for s in protocol["scans"]] == list(range(scans))
            seen += len(expected)
    assert seen, "no corpus archive carries a pause step"


@requires_exar
def test_cli_list_pauses_leaves_the_scan_numbers_alone(tmp_path: Path) -> None:
    """``CHR-MDD`` interleaves pauses with its scans; the scans keep 0 to N-1.

    The counts are read off the archive rather than written in, so a
    re-export of the file does not edit this test.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    path = next((p for p, _ in EXAR_FILES if os.path.basename(p) == "CHR-MDD.exar1"), None)
    if path is None:
        pytest.skip("CHR-MDD.exar1 not available")
    steps = exar.read(path).programs[0].steps
    n_pauses = sum(1 for step in steps if step.is_pause)
    n_scans = sum(1 for step in steps if step.runs_a_protocol)
    assert n_pauses and n_scans

    text = tmp_path / "list.txt"
    assert main(["list", path, "--pauses", "--out", str(text)]) == 0
    body = _body(text.read_text(encoding="utf-8"))
    pauses = [fields for fields in body if fields[-1] == PAUSE]
    numbered = [int(fields[0]) for fields in body if fields[-1] != PAUSE]
    assert len(pauses) == n_pauses
    assert numbered == list(range(n_scans))
    assert f"total ({n_scans} scans)" in text.read_text(encoding="utf-8")

    out = tmp_path / "list.json"
    assert main(["list", path, "--pauses", "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["pauses"]) == n_pauses
    assert len(payload["scans"]) == n_scans

    narrowed = tmp_path / "one.txt"
    assert main(["list", path, "--pauses", "--scan", "reward1", "--out", str(narrowed)]) == 0
    assert PAUSE not in narrowed.read_text(encoding="utf-8")
