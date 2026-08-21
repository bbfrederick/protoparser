"""Grouping a comparison by section, and restricting it to one.

Two properties matter here and neither is obvious from reading the code.

The first is that filtering happens *after* the two sides are paired. Siemens
moves parameters between cards across releases, so restricting each side's
keys before the comparison would leave a moved parameter matched against
nothing and report it as an addition that never happened. The tests below
build exactly that case.

The second is that a filter partitions the report rather than sampling it:
run every section in turn and you get back the unfiltered comparison, once
each, unchanged counts included.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from conftest import ParseFixture, find_example, requires_examples
from siemens_protocol.cli import main
from siemens_protocol.diff import (
    HEADER_SECTION,
    ONLY_LEFT,
    ONLY_RIGHT,
    ProtocolDiff,
    diff_parameters,
    diff_protocols,
    diff_scans,
    merge_section_order,
    normalize_section,
    section_groups,
)
from siemens_protocol.flatten import flatten_sections
from siemens_protocol.report import render_protocol, render_scan


def sectioned(sections: dict[str, dict[str, str]]) -> dict[str, dict]:
    """Build a flattened view through the production flattening step.

    Going through :func:`~siemens_protocol.flatten.flatten_sections` rather
    than hand-writing the entries is deliberate: it is the step that records
    which sections printed a key, and that provenance is what is under test.

    Parameters
    ----------
    sections : dict
        Section name to its ordered key/value mapping, in printed order.

    Returns
    -------
    dict
        A flattened view in the shape the comparison expects.
    """
    return flatten_sections(sections)


def scan(sections: dict[str, dict[str, str]], name: str = "scan") -> dict:
    """Build a serialized scan around a section mapping.

    Parameters
    ----------
    sections : dict
        Section name to its ordered key/value mapping, in printed order.
    name : str, optional
        The scan's name. Default ``"scan"``.

    Returns
    -------
    dict
        A scan carrying ``sections`` and the matching ``flat`` view, which is
        what :func:`~siemens_protocol.diff.diff_scans` reads.
    """
    return {
        "name": name,
        "index": 0,
        "header": {},
        "sections": sections,
        "flat": flatten_sections(sections),
    }


# -- section names ----------------------------------------------------------


def test_a_tab_folds_to_its_card() -> None:
    """``Contrast - Common`` and ``Contrast - Dynamic`` are one filter name.

    Returns
    -------
    None
    """
    assert normalize_section("Contrast - Common") == "contrast"
    assert normalize_section("Contrast - Dynamic") == "contrast"
    assert normalize_section("Contrast") == "contrast"
    assert normalize_section("System - pTx Volumes") == "system"


def test_folding_stops_at_the_first_separator() -> None:
    """Only the leading card is kept, however many dashes follow.

    Returns
    -------
    None
    """
    assert normalize_section("Inline - Soft Tissue") == "inline"
    assert normalize_section("") == ""


def test_a_hyphenated_word_is_not_a_separator() -> None:
    """The split is on `` - ``, so a hyphen inside a word survives.

    Returns
    -------
    None
    """
    assert normalize_section("Tim-Planning") == "tim-planning"


# -- section order ----------------------------------------------------------


def test_the_right_hand_order_is_the_report_order() -> None:
    """The protocol being edited decides where a reader will look.

    Returns
    -------
    None
    """
    order = merge_section_order(["Routine", "Contrast - Common"], ["Contrast - Common", "Routine"])
    assert order == ["Routine", "Contrast - Common"]


def test_a_left_only_section_lands_beside_its_card() -> None:
    """VE11C's split tabs must not drift away from VB17A's single card.

    Returns
    -------
    None
    """
    order = merge_section_order(
        ["Routine", "Contrast", "System"],
        ["Contrast - Common", "Contrast - Dynamic"],
    )
    assert order == ["Routine", "Contrast", "Contrast - Common", "Contrast - Dynamic", "System"]


def test_a_left_only_section_with_no_kin_goes_last() -> None:
    """A card the right-hand release dropped entirely has nowhere to sit.

    Returns
    -------
    None
    """
    assert merge_section_order(["Routine"], ["Angio"]) == ["Routine", "Angio"]


def test_section_groups_lists_the_header_first() -> None:
    """The header answers to a filter too, so it has to be offered.

    Returns
    -------
    None
    """
    protocol = {"scans": [scan({"Routine": {"TR": "20 ms"}, "Contrast - Common": {"TE": "3 ms"}})]}
    assert section_groups(protocol) == [HEADER_SECTION, "contrast", "routine"]


# -- grouping ---------------------------------------------------------------


def test_a_difference_carries_the_right_hand_section() -> None:
    """The section reported is where the edit will be made.

    Returns
    -------
    None
    """
    diffs, _ = diff_parameters(
        sectioned({"Routine": {"TR": "20 ms"}}),
        sectioned({"Contrast - Common": {"TR": "30 ms"}}),
    )
    assert len(diffs) == 1
    assert diffs[0].sections_left == ["Routine"]
    assert diffs[0].sections_right == ["Contrast - Common"]
    assert diffs[0].section == "Contrast - Common"


def test_a_left_only_parameter_falls_back_to_its_own_section() -> None:
    """A dropped parameter has no right-hand section to be filed under.

    Returns
    -------
    None
    """
    diffs, _ = diff_parameters(
        sectioned({"Routine": {"Gain": "High"}}), sectioned({"Routine": {}})
    )
    assert len(diffs) == 1
    assert diffs[0].status == ONLY_LEFT
    assert diffs[0].section == "Routine"


def test_a_parameter_printed_twice_records_both_sections() -> None:
    """Siemens prints TR on several cards; the report keeps all of them.

    Returns
    -------
    None
    """
    diffs, _ = diff_parameters(
        sectioned({"Routine": {"TR": "20 ms"}, "Contrast - Common": {"TR": "20 ms"}}),
        sectioned({"Routine": {"TR": "30 ms"}, "Contrast - Common": {"TR": "30 ms"}}),
    )
    assert diffs[0].sections_right == ["Routine", "Contrast - Common"]
    assert diffs[0].section == "Routine", "the first printed section is the one to edit"


def test_parameters_are_reported_in_printed_order_within_a_section() -> None:
    """Findability is the point, so the scanner's own order wins over ABC.

    Returns
    -------
    None
    """
    left = sectioned({"Routine": {"Slices": "1", "Averages": "1", "TR": "20 ms"}})
    right = sectioned({"Routine": {"Slices": "2", "Averages": "2", "TR": "30 ms"}})
    diffs, _ = diff_parameters(left, right, section_order=["Routine"])
    assert [d.key for d in diffs] == ["Slices", "Averages", "TR"]


def test_sections_are_reported_in_the_given_order() -> None:
    """A section's differences form one block, ordered by the right file.

    Returns
    -------
    None
    """
    left = sectioned(
        {"Routine": {"TR": "20 ms"}, "Contrast": {"TE": "3 ms"}, "System": {"Gain": "High"}}
    )
    right = sectioned(
        {"Routine": {"TR": "30 ms"}, "Contrast": {"TE": "4 ms"}, "System": {"Gain": "Low"}}
    )
    order = ["System", "Contrast", "Routine"]
    diffs, _ = diff_parameters(left, right, section_order=order)
    assert [d.section for d in diffs] == order


def test_the_report_indents_parameters_under_a_section_label() -> None:
    """The label is what makes the list navigable while editing.

    Returns
    -------
    None
    """
    left = scan({"Routine": {"TR": "20 ms"}, "Contrast - Common": {"TE": "3 ms"}})
    right = scan({"Routine": {"TR": "30 ms"}, "Contrast - Common": {"TE": "4 ms"}})
    lines = render_scan(diff_scans(left, right))
    assert "    Routine" in lines
    assert "    Contrast - Common" in lines
    assert "      ~ TR: 20 ms  |  30 ms" in lines
    assert lines.index("    Routine") < lines.index("    Contrast - Common")


# -- filtering --------------------------------------------------------------


def test_filtering_keeps_only_the_named_card() -> None:
    """Both tabs of a card come through together; other cards do not.

    Returns
    -------
    None
    """
    left = sectioned(
        {
            "Routine": {"TR": "20 ms"},
            "Contrast - Common": {"TE": "3 ms"},
            "Contrast - Dynamic": {"Mode": "Short"},
        }
    )
    right = sectioned(
        {
            "Routine": {"TR": "30 ms"},
            "Contrast - Common": {"TE": "4 ms"},
            "Contrast - Dynamic": {"Mode": "Long"},
        }
    )
    diffs, _ = diff_parameters(left, right, sections=["contrast"])
    assert sorted(d.key for d in diffs) == ["Mode", "TE"]


def test_a_parameter_that_moved_card_is_not_invented_as_a_change() -> None:
    """The regression this feature could most easily have introduced.

    Filtering each side's keys before pairing would leave the right-hand
    ``TR`` matched against nothing under ``--filter contrast``, and the
    report would claim a parameter had been added that was only moved.

    Returns
    -------
    None
    """
    left = sectioned({"Routine": {"TR": "20 ms"}})
    right = sectioned({"Contrast": {"TR": "20 ms"}})
    diffs, unchanged = diff_parameters(left, right, sections=["contrast"])
    assert diffs == [], "a moved parameter with an unchanged value is not a difference"
    assert unchanged == 1

    changed, _ = diff_parameters(
        sectioned({"Routine": {"TR": "20 ms"}}),
        sectioned({"Contrast": {"TR": "30 ms"}}),
        sections=["contrast"],
    )
    assert len(changed) == 1
    assert changed[0].status not in (ONLY_LEFT, ONLY_RIGHT)


def test_a_moved_parameter_is_filed_under_the_card_it_moved_to() -> None:
    """Asking for the card it left behind must not surface it.

    Returns
    -------
    None
    """
    left = sectioned({"Routine": {"TR": "20 ms"}})
    right = sectioned({"Contrast": {"TR": "30 ms"}})
    diffs, unchanged = diff_parameters(left, right, sections=["routine"])
    assert diffs == []
    assert unchanged == 0, "it did not match here either; it is simply not in this card"


def test_the_unchanged_count_is_scoped_to_the_filter() -> None:
    """Otherwise "no differences (N match)" would describe the whole scan.

    Returns
    -------
    None
    """
    left = sectioned({"Routine": {"TR": "20 ms", "Slices": "5"}, "Contrast": {"TE": "3 ms"}})
    right = sectioned({"Routine": {"TR": "20 ms", "Slices": "5"}, "Contrast": {"TE": "3 ms"}})
    _, everything = diff_parameters(left, right)
    _, routine = diff_parameters(left, right, sections=["routine"])
    assert everything == 3
    assert routine == 2


def test_the_header_answers_to_its_own_filter_name() -> None:
    """``--filter header`` is the only way to see header differences alone.

    Returns
    -------
    None
    """
    left = {"name": "s", "header": {"ta": "0:19"}, "sections": {}, "flat": {}}
    right = {"name": "s", "header": {"ta": "0:22"}, "sections": {}, "flat": {}}
    assert diff_scans(left, right, sections=[HEADER_SECTION]).header
    assert diff_scans(left, right, sections=["routine"]).header == []


def test_a_filter_excludes_the_header_unless_it_is_named() -> None:
    """A parameter filter is about cards, and the header is not one.

    Returns
    -------
    None
    """
    left = scan({"Routine": {"TR": "20 ms"}})
    right = scan({"Routine": {"30": "30 ms"}})
    left["header"] = {"ta": "0:19"}
    right["header"] = {"ta": "0:22"}
    assert diff_scans(left, right, sections=["routine"]).header == []
    assert diff_scans(left, right).header


# -- the partition property, on real protocols ------------------------------


@requires_examples
def test_filtering_partitions_a_real_comparison(parsed: ParseFixture) -> None:
    """Every difference lands in exactly one section, and none is invented.

    Run the comparison once per section and the pieces reassemble into the
    unfiltered report -- same differences, same count, nothing duplicated and
    nothing lost. The unchanged tallies have to add up too, or the "N
    parameters match" line would contradict what was printed.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01_Mindfulness.pdf", "VE11C")).protocol.to_dict()
    right = parsed(find_example("R01_Mindfulness.pdf", "XA60")).protocol.to_dict()

    def signature(result: ProtocolDiff) -> collections.Counter:
        """Identify every difference a comparison reported.

        Parameters
        ----------
        result : ProtocolDiff
            The comparison to fingerprint.

        Returns
        -------
        collections.Counter
            One entry per reported difference.
        """
        return collections.Counter(
            (i, kind, d.key_left, d.key_right, d.status, tuple(d.values_left), d.section)
            for i, s in enumerate(result.scans)
            for kind, group in (("header", s.header), ("parameters", s.parameters))
            for d in group
        )

    whole = diff_protocols(left, right)
    assert whole.substantive_count > 0, "the pair must actually differ"

    pieces: collections.Counter = collections.Counter()
    unchanged: collections.Counter = collections.Counter()
    for name in sorted(set(section_groups(left)) | set(section_groups(right))):
        part = diff_protocols(left, right, sections=[name])
        pieces.update(signature(part))
        for i, s in enumerate(part.scans):
            unchanged[i] += s.unchanged

    assert pieces == signature(whole)
    assert {i: s.unchanged for i, s in enumerate(whole.scans)} == dict(unchanged)


@requires_examples
def test_every_difference_is_filed_under_a_printed_section(parsed: ParseFixture) -> None:
    """No parameter falls through to the unsectioned bucket.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01_Mindfulness.pdf", "VE11C")).protocol.to_dict()
    right = parsed(find_example("R01_Mindfulness.pdf", "XA60")).protocol.to_dict()
    result = diff_protocols(left, right)
    total = 0
    for pair in result.scans:
        printed = set(left["scans"][pair.index_left]["sections"]) | set(
            right["scans"][pair.index_right]["sections"]
        )
        for diff in pair.parameters:
            total += 1
            assert diff.section in printed, f"{diff.key} filed under {diff.section!r}"
    assert total > 0


# -- the command line -------------------------------------------------------


@requires_examples
def test_cli_filter_restricts_the_report(tmp_path: Path) -> None:
    """``--filter`` narrows the report and says that it did.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "report.txt"
    code = main(
        [
            "diff",
            find_example("R01_Mindfulness.pdf", "VE11C"),
            find_example("R01_Mindfulness.pdf", "XA60"),
            "--filter",
            "contrast",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    text = out.read_text()
    assert "showing only sections: contrast" in text
    assert "Contrast - Common" in text
    assert "Routine" not in text, "a filtered report must not leak other cards"


@requires_examples
def test_cli_filter_accepts_a_list_and_a_full_section_name(tmp_path: Path) -> None:
    """A comma-separated list works, and a pasted section name is folded.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "report.txt"
    main(
        [
            "diff",
            find_example("R01_Mindfulness.pdf", "VE11C"),
            find_example("R01_Mindfulness.pdf", "XA60"),
            "--filter",
            "header,Contrast - Common",
            "--out",
            str(out),
        ]
    )
    text = out.read_text()
    assert "showing only sections: header, contrast" in text
    assert "  header" in text
    assert "Contrast - Common" in text


@requires_examples
def test_cli_filter_repeats_to_add_sections(tmp_path: Path) -> None:
    """Repeating the option is the other way to name several.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "report.txt"
    main(
        [
            "diff",
            find_example("R01_Mindfulness.pdf", "VE11C"),
            find_example("R01_Mindfulness.pdf", "XA60"),
            "--filter",
            "routine",
            "--filter",
            "contrast",
            "--out",
            str(out),
        ]
    )
    assert "showing only sections: routine, contrast" in out.read_text()


@requires_examples
def test_cli_rejects_an_unknown_section(capsys: pytest.CaptureFixture) -> None:
    """A typo is named back, along with what could have been asked for.

    Reporting nothing would be the silent alternative, and indistinguishable
    from a section that genuinely holds no differences.

    Parameters
    ----------
    capsys : pytest.CaptureFixture
        Pytest's stdout/stderr capture.

    Returns
    -------
    None
    """
    code = main(
        [
            "diff",
            find_example("R01_Mindfulness.pdf", "VE11C"),
            find_example("R01_Mindfulness.pdf", "XA60"),
            "--filter",
            "contrats",
        ]
    )
    assert code == 1
    error = capsys.readouterr().err
    assert "no section named 'contrats'" in error
    assert "contrast" in error and "routine" in error


@requires_examples
def test_cli_filter_reaches_the_json_payload(tmp_path: Path) -> None:
    """The filter travels with the counts it scopes.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "report.json"
    main(
        [
            "diff",
            find_example("R01_Mindfulness.pdf", "VE11C"),
            find_example("R01_Mindfulness.pdf", "XA60"),
            "--filter",
            "contrast",
            "--json",
            "--out",
            str(out),
        ]
    )
    payload = json.loads(out.read_text())
    assert payload["sections"] == ["contrast"]
    reported = {
        d.get("section") for scan_diff in payload["scans"] for d in scan_diff["parameters"]
    }
    assert reported and all(s.startswith("Contrast") for s in reported)


@requires_examples
def test_cli_filter_applies_to_a_single_scan_comparison(tmp_path: Path) -> None:
    """The scan-to-scan mode honours the filter and states it too.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "report.txt"
    main(
        [
            "diff",
            find_example("R01_Mindfulness.pdf", "VE11C"),
            find_example("R01_Mindfulness.pdf", "XA60"),
            "--scan",
            "AAHScout_64ch",
            "--filter",
            "contrast",
            "--out",
            str(out),
        ]
    )
    text = out.read_text()
    assert "showing only sections: contrast" in text
    assert "Routine" not in text
