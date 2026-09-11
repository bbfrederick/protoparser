"""Comparing protocols and scans.

The example tree holds the same three protocols exported from both releases.
Those pairs are matched by *scan sequence*, not by parameter content -- the
parameters genuinely differ, which is the reason the tool exists. So nothing
here asserts that a matched pair agrees. What is asserted is that the
comparison classifies differences correctly and never manufactures agreement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import ParseFixture, find_example, requires_examples
from siemens_protocol import address
from siemens_protocol.cli import main
from siemens_protocol.diff import (
    CHANGED,
    ONLY_LEFT,
    ONLY_RIGHT,
    RECASED,
    REFORMATTED,
    RENAMED,
    align_scans,
    compare_values,
    diff_parameters,
    diff_protocols,
    diff_scans,
    normalize_key,
)
from siemens_protocol.report import name_mismatch_note, render_protocol, render_scan


def flat(**pairs: str) -> dict[str, dict]:
    """Build a flattened view from plain key/value pairs.

    Parameters
    ----------
    **pairs : str
        Parameter names and values. Underscores are not translated, so keys
        needing spaces should be passed through :func:`dict` instead.

    Returns
    -------
    dict
        A flattened view in the shape the comparison expects.
    """
    return {k: {"value": v, "conflict": False} for k, v in pairs.items()}


def as_flat(mapping: dict[str, str]) -> dict[str, dict]:
    """Build a flattened view from a mapping with arbitrary key spellings.

    Parameters
    ----------
    mapping : dict
        Parameter names to values.

    Returns
    -------
    dict
        A flattened view in the shape the comparison expects.
    """
    return {k: {"value": v, "conflict": False} for k, v in mapping.items()}


# -- key normalization ------------------------------------------------------


@pytest.mark.parametrize(
    "left,right",
    [
        ("Dist. factor", "Distance Factor"),
        ("Accel. factor PE", "Acceleration Factor PE"),
        ("Distortion Corr.", "Distortion Correction"),
        ("Phase enc. dir.", "Phase Encoding Dir."),
        ("Ref. lines PE", "Reference Lines PE"),
        ("Base resolution", "Base Resolution"),
        ("Dist. factor #2", "Distance Factor #2"),
        ("Flow comp.", "Flow Compensation"),
        # the numbered variants come along for free, which is why this is an
        # abbreviation rather than one vocabulary entry per spelling
        ("Flow comp. 1", "Flow Compensation 1"),
    ],
)
def test_cosmetic_relabeling_is_matched(left: str, right: str) -> None:
    """Case, punctuation and confirmed abbreviations fold together.

    Parameters
    ----------
    left, right : str
        The same parameter as the two releases spell it.

    Returns
    -------
    None
    """
    assert normalize_key(left) == normalize_key(right)


@pytest.mark.parametrize(
    "left,right",
    [
        # Genuinely different parameters, some with deceptively similar text.
        ("Fat sat. mode", "Fast Mode"),
        ("PAT mode", "Fast Mode"),
        ("Coil Select Mode", "Coil Selection"),
        ("Load images to viewer", "Load Images to MR View&GO"),
        ("Reference scan mode", "Reference Scans"),
        ("TE", "TR"),
        # "comp" expands only as a bare token, so these stay distinct
        ("Inline Composing", "Flow Compensation"),
        ("Compensate T2 Decay", "Flow Compensation"),
    ],
)
def test_semantic_renames_are_not_merged(left: str, right: str) -> None:
    """Normalization must never invent a match between different parameters.

    ``Fat sat. mode`` and ``Fast Mode`` are one letter apart and mean
    entirely different things; a similarity-based matcher would pair them and
    report a fabricated value change.

    Parameters
    ----------
    left, right : str
        Two parameters that merely look alike.

    Returns
    -------
    None
    """
    assert normalize_key(left) != normalize_key(right)


def test_repeat_suffixes_do_not_collide_across_different_keys() -> None:
    """A repeat suffix is stripped for matching but keys stay distinct.

    Returns
    -------
    None
    """
    assert normalize_key("Slice group #2") == normalize_key("Slice Group #3")
    assert normalize_key("Slice group #2") != normalize_key("Slab group #2")


# -- value classification ---------------------------------------------------


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("20.0 ms", "20.0 ms", "equal"),
        ("Single shot", "Single Shot", RECASED),
        ("1", "1.00", REFORMATTED),
        ("7.0 deg", "7 deg", REFORMATTED),
        ("Off", "On", CHANGED),
        ("9.8 ms", "9.84 ms", CHANGED),
        ("20.0 ms", "20.0 s", CHANGED),
        ("", "Off", CHANGED),
    ],
)
def test_value_classification(left: str, right: str, expected: str) -> None:
    """Values are sorted into equal, cosmetic and substantive.

    Parameters
    ----------
    left, right : str
        Raw values to compare.
    expected : str
        The expected classification.

    Returns
    -------
    None
    """
    assert compare_values(left, right) == expected


def test_a_unit_change_is_never_cosmetic() -> None:
    """Same number, different unit, is a real difference.

    Returns
    -------
    None
    """
    assert compare_values("20 ms", "20 s") == CHANGED


# -- parameter comparison ---------------------------------------------------


def test_identical_scans_report_nothing() -> None:
    """Matching parameters are counted, not listed.

    Returns
    -------
    None
    """
    view = flat(TR="2530.0 ms", TE="1.69 ms")
    diffs, unchanged = diff_parameters(view, view)
    assert diffs == []
    assert unchanged == 2


def test_added_and_removed_parameters_are_substantive() -> None:
    """A parameter present on one side only is a real finding.

    Returns
    -------
    None
    """
    diffs, _ = diff_parameters(flat(TR="20 ms", Gain="High"), flat(TR="20 ms", Focus="Flat"))
    by_status = {d.status: d for d in diffs}
    assert by_status[ONLY_LEFT].key_left == "Gain"
    assert by_status[ONLY_RIGHT].key_right == "Focus"
    assert all(d.substantive for d in diffs)


def test_a_relabeled_key_with_the_same_value_is_cosmetic() -> None:
    """The rename is reported, with both spellings, and is not substantive.

    Returns
    -------
    None
    """
    diffs, _ = diff_parameters(
        as_flat({"Dist. factor": "20 %"}), as_flat({"Distance Factor": "20 %"})
    )
    assert len(diffs) == 1
    assert diffs[0].status == RENAMED
    assert diffs[0].renamed
    assert (diffs[0].key_left, diffs[0].key_right) == ("Dist. factor", "Distance Factor")
    assert not diffs[0].substantive


def test_a_relabeled_key_with_a_changed_value_stays_substantive() -> None:
    """Relabeling must not mask a real change underneath it.

    Returns
    -------
    None
    """
    diffs, _ = diff_parameters(
        as_flat({"Distortion Corr.": "Off"}), as_flat({"Distortion Correction": "3D"})
    )
    assert len(diffs) == 1
    assert diffs[0].status == CHANGED
    assert diffs[0].renamed
    assert diffs[0].substantive


def test_exact_key_mode_reports_relabeling_as_add_and_remove() -> None:
    """Turning normalization off shows the raw, unmatched picture.

    Returns
    -------
    None
    """
    diffs, _ = diff_parameters(
        as_flat({"Dist. factor": "20 %"}),
        as_flat({"Distance Factor": "20 %"}),
        normalize=False,
    )
    assert {d.status for d in diffs} == {ONLY_LEFT, ONLY_RIGHT}


def test_repeated_keys_compare_as_a_group() -> None:
    """Repeats are compared whole, not paired up position by position.

    The two releases can print a repeating group in a different order, and
    pairing ``#2`` against ``#2`` would then invent misleading matches.

    Returns
    -------
    None
    """
    left = as_flat({"Table position": "H", "Table position #2": "0 mm"})
    right = as_flat({"Table position": "2 mm", "Table position #2": "H"})
    diffs, _ = diff_parameters(left, right)
    assert len(diffs) == 1, "the group must be reported once, not twice"
    assert diffs[0].values_left == ["H", "0 mm"]
    assert diffs[0].values_right == ["2 mm", "H"]
    assert diffs[0].status == CHANGED


def test_an_unequal_group_length_is_a_change() -> None:
    """Three slice groups against two is a real difference.

    Returns
    -------
    None
    """
    left = as_flat({"Slice group": "1", "Slice group #2": "2", "Slice group #3": "3"})
    right = as_flat({"Slice group": "1", "Slice group #2": "2"})
    diffs, _ = diff_parameters(left, right)
    assert diffs[0].status == CHANGED


def test_a_conflicting_reading_is_carried_through() -> None:
    """A cross-section conflict stays visible in the comparison.

    Returns
    -------
    None
    """
    left = {
        "Orientation": {
            "values": {"Routine": "Sagittal", "Adjust": "Transversal"},
            "conflict": True,
        }
    }
    right = flat(Orientation="Sagittal")
    diffs, _ = diff_parameters(left, right)
    assert diffs[0].conflict_left
    assert "conflict" in diffs[0].values_left[0]


# -- scan alignment ---------------------------------------------------------


def test_alignment_pairs_scans_in_order() -> None:
    """Identical sequences align one to one.

    Returns
    -------
    None
    """
    names = ["a", "b", "c"]
    assert align_scans(names, names) == [(0, 0), (1, 1), (2, 2)]


def test_alignment_survives_a_repeated_scan_name() -> None:
    """A protocol may print the same scan name twice.

    Returns
    -------
    None
    """
    names = ["localizer", "fieldmap", "rest", "fieldmap"]
    assert align_scans(names, names) == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_alignment_treats_a_positional_rename_as_a_pair() -> None:
    """A renamed scan keeps its position rather than shifting the rest.

    Returns
    -------
    None
    """
    left = ["localizer", "rest_ME_PA", "fieldmap"]
    right = ["localizer", "rest1_ME_PA", "fieldmap"]
    assert align_scans(left, right) == [(0, 0), (1, 1), (2, 2)]


def test_alignment_reports_an_inserted_scan() -> None:
    """An extra scan is unmatched, and the rest stay in step.

    Returns
    -------
    None
    """
    pairs = align_scans(["a", "c"], ["a", "b", "c"])
    assert (None, 1) in pairs
    assert (0, 0) in pairs and (1, 2) in pairs


# -- against the real examples ----------------------------------------------


@requires_examples
def test_matched_releases_align_scan_for_scan(parsed: ParseFixture) -> None:
    """The two exports of one protocol pair up, with one scan renamed.

    This asserts alignment only. The parameters genuinely differ between
    releases, which is the point of the tool, so nothing here expects them to
    agree.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf", "VE11C")).protocol.to_dict()
    right = parsed(find_example("R01StressDyn.pdf", "XA60")).protocol.to_dict()
    result = diff_protocols(left, right)
    assert len(result.scans) == 21
    assert not result.only_left and not result.only_right
    renamed = [s for s in result.scans if s.renamed_scan]
    assert [(s.name_left, s.name_right) for s in renamed] == [
        ("rfMRI_REST_ME_PA_distortion", "rfMRI_REST1_ME_PA_distortion")
    ]


@requires_examples
def test_normalization_reduces_noise_without_erasing_findings(parsed: ParseFixture) -> None:
    """Matching relabeled keys cuts the noise but keeps real differences.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf", "VE11C")).protocol.to_dict()
    right = parsed(find_example("R01StressDyn.pdf", "XA60")).protocol.to_dict()
    normalized = diff_protocols(left, right, normalize=True).substantive_count
    literal = diff_protocols(left, right, normalize=False).substantive_count
    assert 0 < normalized < literal


@requires_examples
def test_a_protocol_does_not_differ_from_itself(parsed: ParseFixture) -> None:
    """Comparing a file with itself finds nothing, in either mode.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    protocol = parsed(find_example("SYNCT.pdf")).protocol.to_dict()
    result = diff_protocols(protocol, protocol)
    assert result.substantive_count == 0
    assert all(s.identical for s in result.scans)


@requires_examples
def test_two_scans_within_one_protocol(parsed: ParseFixture) -> None:
    """The AP and PA field maps differ by their readout polarity.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    protocol = parsed(find_example("R01StressDyn.pdf", "VE11C")).protocol.to_dict()
    scans = {s["name"]: s for s in protocol["scans"]}
    result = diff_scans(scans["SpinEchoFieldMap_AP"], scans["SpinEchoFieldMap_PA"])
    changed = {d.key for d in result.substantive}
    assert "Invert RO/PE polarity" in changed


# -- reporting --------------------------------------------------------------


@requires_examples
def test_report_leads_with_substantive_differences(parsed: ParseFixture) -> None:
    """Cosmetic differences are summarized, not listed, by default.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf", "VE11C")).protocol.to_dict()
    right = parsed(find_example("R01StressDyn.pdf", "XA60")).protocol.to_dict()
    result = diff_protocols(left, right)
    brief = render_protocol(result)
    full = render_protocol(result, show_cosmetic=True)
    assert "use --show-cosmetic to list" in brief
    assert len(full) > len(brief)
    assert "substantive differences" in brief


def test_report_names_an_identical_scan() -> None:
    """A scan with no differences says so rather than printing nothing.

    Returns
    -------
    None
    """
    scan = {"name": "localizer", "index": 0, "header": {}, "flat": flat(TR="20 ms")}
    lines = render_scan(diff_scans(scan, scan))
    assert any("no differences" in line for line in lines)


# -- the command line -------------------------------------------------------


@requires_examples
def test_cli_diff_two_protocols(tmp_path: Path) -> None:
    """Two differing protocols exit non-zero and write a report.

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
            find_example("R01StressDyn.pdf", "VE11C"),
            find_example("R01StressDyn.pdf", "XA60"),
            "--out",
            str(out),
        ]
    )
    assert code == 1, "differences must be signalled in the exit status"
    text = out.read_text()
    assert "scans compared" in text
    # Aligned by sequence, so a pair can match with different names; the
    # report must say which name belongs to which side.
    assert "Names do not match exactly" in text
    assert (
        "Names do not match exactly - rfMRI_REST_ME_PA_distortion (left) "
        "corresponds to rfMRI_REST1_ME_PA_distortion (right)"
    ) in text


@requires_examples
def test_cli_diff_one_scan_across_two_files(tmp_path: Path) -> None:
    """``--scan`` given once applies the same name to both sides.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "one.json"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            find_example("R01StressDyn.pdf", "XA60"),
            "--scan",
            "T1_MEMPRAGE_64ch",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    payload = json.loads(out.read_text())
    assert payload["name_left"] == payload["name_right"] == "T1_MEMPRAGE_64ch"


@requires_examples
def test_cli_diff_two_scans_within_one_file(tmp_path: Path) -> None:
    """Two ``--scan`` names and one input compare within that protocol.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "within.txt"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            "--scan",
            "SpinEchoFieldMap_AP#1",
            "--scan",
            "SpinEchoFieldMap_PA#1",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    text = out.read_text()
    assert "Invert RO/PE polarity" in text
    # Two deliberately chosen scans are not a rename, but the report still
    # states which name is which, on the first line rather than in the block.
    assert "(scan renamed)" not in text
    note = (
        "Names do not match exactly - SpinEchoFieldMap_AP (left) "
        "corresponds to SpinEchoFieldMap_PA (right)"
    )
    assert note in text
    assert text.splitlines().index(note) == 2, "the note must lead the report"


@requires_examples
def test_cli_diff_accepts_a_scan_index(tmp_path: Path) -> None:
    """Scans can be selected by zero-based index as well as by name.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "byindex.txt"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            "--scan",
            "0",
            "--scan",
            "0",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert "no differences" in out.read_text()


@requires_examples
def test_cli_diff_reads_previously_parsed_json(tmp_path: Path) -> None:
    """A protocol can be parsed once and compared many times.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    parsed_json = tmp_path / "a.json"
    assert main(["parse", find_example("SYNCT.pdf"), "--out", str(parsed_json), "--quiet"]) == 0
    out = tmp_path / "self.txt"
    assert main(["diff", str(parsed_json), str(parsed_json), "--out", str(out)]) == 0
    assert "no substantive differences" in out.read_text()


@requires_examples
def test_cli_diff_rejects_json_without_the_flat_view(tmp_path: Path) -> None:
    """Diffing needs the flattened view, and says so when it is missing.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    lean = tmp_path / "lean.json"
    main(["parse", find_example("SYNCT.pdf"), "--out", str(lean), "--no-flatten", "--quiet"])
    assert main(["diff", str(lean), str(lean)]) == 1


@requires_examples
def test_cli_diff_needs_two_scans_for_a_single_file() -> None:
    """One input and one ``--scan`` is not a comparison.

    Returns
    -------
    None
    """
    assert main(["diff", find_example("SYNCT.pdf"), "--scan", "localizer"]) == 1


@requires_examples
def test_cli_diff_reports_an_unknown_scan_name() -> None:
    """An unknown scan fails cleanly rather than comparing the wrong thing.

    Returns
    -------
    None
    """
    assert main(["diff", find_example("SYNCT.pdf"), "--scan", "nope", "--scan", "alsonope"]) == 1


# -- naming a scan per side -------------------------------------------------


@requires_examples
def test_cli_diff_names_a_scan_for_each_side(tmp_path: Path) -> None:
    """``--left-scan`` and ``--right-scan`` pick one scan from each file.

    The names need not match: this is what compares a scan against its
    counterpart in another protocol, where the vendor or the site renamed it.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "pair.json"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            find_example("R01StressDyn.pdf", "XA60"),
            "--left-scan",
            "T1_MEMPRAGE_64ch",
            "--right-scan",
            "localizer",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    payload = json.loads(out.read_text())
    assert payload["name_left"] == "T1_MEMPRAGE_64ch"
    assert payload["name_right"] == "localizer"


@requires_examples
@pytest.mark.parametrize("side", ["--left-scan", "--right-scan"])
def test_cli_diff_one_named_side_uses_that_name_on_the_other(tmp_path: Path, side: str) -> None:
    """Naming one side compares against the same name on the other.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.
    side : str
        Which side to name.

    Returns
    -------
    None
    """
    out = tmp_path / "one.json"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            find_example("R01StressDyn.pdf", "XA60"),
            side,
            "T1_MEMPRAGE_64ch",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    payload = json.loads(out.read_text())
    assert payload["name_left"] == payload["name_right"] == "T1_MEMPRAGE_64ch"


@requires_examples
def test_cli_diff_names_two_scans_within_one_file(tmp_path: Path) -> None:
    """One input plus a name per side compares two scans of that protocol.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "within.json"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            "--left-scan",
            "T1_MEMPRAGE_64ch",
            "--right-scan",
            "localizer",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    payload = json.loads(out.read_text())
    assert payload["name_left"] == "T1_MEMPRAGE_64ch"
    assert payload["name_right"] == "localizer"


@requires_examples
def test_cli_diff_accepts_an_index_per_side(tmp_path: Path) -> None:
    """The per-side options take a zero-based index as well as a name.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "byindex.json"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            "--left-scan",
            "0",
            "--right-scan",
            "2",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    payload = json.loads(out.read_text())
    assert payload["name_left"] != payload["name_right"]


def test_cli_diff_refuses_to_mix_the_two_spellings() -> None:
    """``--scan`` and the per-side options select the same thing two ways.

    Combining them has no unambiguous reading, so it is refused rather than
    resolved by a precedence rule nobody would remember.

    Returns
    -------
    None
    """
    assert main(["diff", "a.pdf", "b.pdf", "--scan", "X", "--left-scan", "Y"]) == 1


@requires_examples
def test_cli_diff_within_one_file_needs_both_sides_named() -> None:
    """One input and one named side would compare a scan with itself.

    Returns
    -------
    None
    """
    assert (
        main(["diff", find_example("R01StressDyn.pdf", "VE11C"), "--left-scan", "localizer"]) == 1
    )


@requires_examples
def test_cli_diff_within_one_file_given_twice_matches_giving_it_once(
    tmp_path: Path,
) -> None:
    """Naming one file on both sides is the same request as omitting it.

    Both spellings are natural for "compare two scans of this protocol", so
    they must agree; the two-input form is also the one that generalizes to
    two different files, which is how people arrive at it.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    pdf = find_example("R01StressDyn.pdf", "VE11C")
    names = ["--left-scan", "SpinEchoFieldMap_AP#1", "--right-scan", "SpinEchoFieldMap_PA#1"]

    once = tmp_path / "once.json"
    twice = tmp_path / "twice.json"
    assert main(["diff", pdf, *names, "--json", "--out", str(once)]) == 1
    assert main(["diff", pdf, pdf, *names, "--json", "--out", str(twice)]) == 1

    payload = json.loads(once.read_text())
    assert json.loads(twice.read_text()) == payload
    assert payload["name_left"] == "SpinEchoFieldMap_AP"
    assert payload["name_right"] == "SpinEchoFieldMap_PA"
    assert payload["parameters"], "these two scans do differ"


@requires_examples
def test_cli_diff_recognizes_one_file_spelled_two_ways(tmp_path: Path) -> None:
    """``./a.pdf`` and ``a.pdf`` are one file, not two.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    pdf = find_example("R01StressDyn.pdf", "VE11C")
    out = tmp_path / "spelling.json"
    code = main(
        [
            "diff",
            os.path.join(os.path.dirname(pdf), ".", os.path.basename(pdf)),
            pdf,
            "--left-scan",
            "SpinEchoFieldMap_AP#1",
            "--right-scan",
            "SpinEchoFieldMap_PA#1",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    payload = json.loads(out.read_text())
    assert payload["name_left"] == "SpinEchoFieldMap_AP"
    assert payload["name_right"] == "SpinEchoFieldMap_PA"


@requires_examples
def test_cli_diff_two_scans_of_one_file_finds_the_known_difference(
    tmp_path: Path,
) -> None:
    """The AP and PA field maps of one protocol differ only in polarity.

    A real expectation rather than a structural one: if scan selection ever
    silently compared a scan with itself, this would report no differences.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "fieldmaps.json"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            "--left-scan",
            "SpinEchoFieldMap_AP#1",
            "--right-scan",
            "SpinEchoFieldMap_PA#1",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    payload = json.loads(out.read_text())
    changed = [p for p in payload["parameters"] if p["status"] == "changed"]
    assert [p["key"] for p in changed] == ["Invert RO/PE polarity"]
    assert changed[0]["values_left"] == ["Off"]
    assert changed[0]["values_right"] == ["On"]


@requires_examples
def test_cli_diff_a_scan_against_itself_reports_nothing(tmp_path: Path) -> None:
    """Comparing one scan with itself is legal and finds no difference.

    It is the degenerate case of the same-file mode, and exiting zero is what
    distinguishes "no differences" from "the request failed".

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "self.json"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            "--left-scan",
            "localizer",
            "--right-scan",
            "localizer",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 0, "no differences must exit zero"
    assert json.loads(out.read_text())["parameters"] == []


@requires_examples
def test_flow_compensation_is_matched_across_releases(tmp_path: Path) -> None:
    """VE11C's ``Flow comp.`` pairs with the Numaris/X ``Flow Compensation``.

    The two are the same setting in the same section, and no release prints
    both spellings. Their values were recoded (``No``/``Yes`` became
    ``None``/``On``), so they report as changed rather than equal -- which is
    the intended outcome: the parameter is recognized, the value difference
    stays visible.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "flow.json"
    assert (
        main(
            [
                "diff",
                find_example("R01StressDyn.pdf", "VE11C"),
                find_example("R01StressDyn.pdf", "XA60"),
                "--json",
                "--out",
                str(out),
            ]
        )
        == 1
    )
    payload = json.loads(out.read_text())
    found = [
        p
        for scan in payload["scans"]
        for p in scan["parameters"]
        if "flow comp" in (p.get("key") or "").lower()
    ]
    assert found, "no flow compensation parameter appeared in the diff"

    # The VE11C spelling must never survive as an unmatched entry.
    assert not [p for p in found if p["status"] == "only_left"]
    # The base label and the first numbered slot pair up...
    paired = {(p.get("key_left"), p.get("key_right")) for p in found if p["status"] == "changed"}
    assert ("Flow comp.", "Flow Compensation") in paired
    assert ("Flow comp. 1", "Flow Compensation 1") in paired
    # ...while slots 2 to 4 exist only on the Numaris/X side, which is a real
    # structural difference rather than a matching failure.
    unmatched = sorted(p["key"] for p in found if p["status"] == "only_right")
    assert unmatched == ["Flow Compensation 2", "Flow Compensation 3", "Flow Compensation 4"]


# -- the name-mismatch note -------------------------------------------------


def test_name_mismatch_note_wording() -> None:
    """The note names both spellings and says which side each belongs to.

    Returns
    -------
    None
    """
    assert name_mismatch_note("AP_old", "AP_new") == (
        "Names do not match exactly - AP_old (left) corresponds to AP_new (right)"
    )


def test_name_mismatch_note_is_absent_when_the_names_agree() -> None:
    """Identical names produce no note at all.

    Returns
    -------
    None
    """
    assert name_mismatch_note("localizer", "localizer") is None


@requires_examples
def test_cli_diff_scan_mode_leads_with_the_note(tmp_path: Path) -> None:
    """In scan mode the note is the first line after the file headers.

    The two scans were named separately here, so which name belongs to which
    file is the first thing the reader needs.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "note.txt"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            find_example("R01StressDyn.pdf", "XA60"),
            "--left-scan",
            "localizer",
            "--right-scan",
            "AAHScout",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    lines = out.read_text().splitlines()
    assert lines[0].startswith("---")
    assert lines[1].startswith("+++")
    assert lines[2] == (
        "Names do not match exactly - localizer (left) corresponds to AAHScout (right)"
    )


@requires_examples
def test_cli_diff_scan_mode_omits_the_note_when_names_agree(tmp_path: Path) -> None:
    """Comparing like-named scans across two files needs no note.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "quiet.txt"
    code = main(
        [
            "diff",
            find_example("R01StressDyn.pdf", "VE11C"),
            find_example("R01StressDyn.pdf", "XA60"),
            "--scan",
            "localizer",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    assert "Names do not match exactly" not in out.read_text()


@requires_examples
def test_protocol_diff_notes_every_mismatched_pair(parsed: ParseFixture) -> None:
    """Every aligned pair with differing names carries its own note.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf", "VE11C")).protocol.to_dict()
    right = parsed(find_example("R01StressDyn.pdf", "XA60")).protocol.to_dict()
    result = diff_protocols(left, right)
    mismatched = [s for s in result.scans if s.name_left != s.name_right]
    assert mismatched, "this pair is expected to contain a renamed scan"

    text = render_protocol(result)
    for scan in mismatched:
        note = name_mismatch_note(scan.name_left, scan.name_right)
        assert note is not None and note in text
    assert text.count("Names do not match exactly") == len(mismatched)


# -- what counts as a difference --------------------------------------------


def _staged(*names: str) -> dict:
    """A protocol carrying scans of the given names and nothing else.

    Parameters
    ----------
    *names : str
        Scan names, in acquisition order.

    Returns
    -------
    dict
        A serialized protocol whose scans are identical but for their names.
    """
    return {
        "source_file": "staged.pdf",
        "software_version": "XA60",
        "scans": [
            {
                "index": index,
                "name": name,
                "header": {"ta": "1:00", "sequence": "gre"},
                "sections": {"Routine": {"TR": "2000 ms"}},
                "flat": {"TR": {"value": "2000 ms", "sections": ["Routine"], "conflict": False}},
            }
            for index, name in enumerate(names)
        ],
    }


def test_an_unmatched_scan_is_a_difference() -> None:
    """A protocol with a scan the other lacks differs from it.

    The scan has no counterpart, so it has no parameters to compare and
    contributes nothing to ``substantive_count``. Reading that count alone --
    which is what the exit status did -- reports two protocols of different
    lengths as matching.

    Returns
    -------
    None
    """
    result = diff_protocols(_staged("a", "b"), _staged("a", "b", "c"))
    assert result.substantive_count == 0
    assert result.unmatched_count == 1
    assert result.differs is True


def test_identical_protocols_do_not_differ() -> None:
    """Nothing unmatched and nothing substantive is no difference.

    Returns
    -------
    None
    """
    result = diff_protocols(_staged("a", "b"), _staged("a", "b"))
    assert (result.substantive_count, result.unmatched_count) == (0, 0)
    assert result.differs is False


def test_a_renamed_scan_is_not_counted_as_unmatched() -> None:
    """A scan matched under another name is matched, not missing.

    Counting it would make every cross-release comparison of a protocol whose
    scans were renamed fail a check that is asking about parameters, and the
    rename is already named in the report.

    Returns
    -------
    None
    """
    result = diff_protocols(_staged("a", "b", "c"), _staged("a", "b_renamed", "c"))
    assert result.unmatched_count == 0
    assert result.differs is False
    assert [(s.name_left, s.name_right) for s in result.scans if s.name_left != s.name_right] == [
        ("b", "b_renamed")
    ]


def test_the_tally_line_names_the_unmatched_scans() -> None:
    """The summary line must not read as "no differences" beside one.

    Returns
    -------
    None
    """
    one = render_protocol(diff_protocols(_staged("a"), _staged("a", "b")))
    assert "1 scan on one side only" in one
    two = render_protocol(diff_protocols(_staged("a"), _staged("a", "b", "c")))
    assert "2 scans on one side only" in two
    none = render_protocol(diff_protocols(_staged("a"), _staged("a")))
    assert "on one side only" not in none


def test_the_payload_carries_both_counts() -> None:
    """``--json`` says why the exit status is what it is.

    Returns
    -------
    None
    """
    payload = diff_protocols(_staged("a"), _staged("a", "b")).to_dict()
    assert payload["substantive_count"] == 0
    assert payload["unmatched_count"] == 1
    assert payload["scans_only_right"] == ["b"]


@requires_examples
def test_cli_diff_exits_nonzero_on_an_unmatched_scan(tmp_path: Path) -> None:
    """End to end: a scan on one side only sets the exit status.

    Driven against a real export with one scan dropped from it, so the two
    sides differ in exactly that one respect.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory for the trimmed copy.

    Returns
    -------
    None
    """
    parsed = tmp_path / "full.json"
    assert main(["parse", find_example("SYNCT.pdf"), "--out", str(parsed), "--quiet"]) == 0
    document = json.loads(parsed.read_text(encoding="utf-8"))
    assert len(document["scans"]) > 1

    trimmed = tmp_path / "trimmed.json"
    document["scans"] = document["scans"][:-1]
    trimmed.write_text(json.dumps(document), encoding="utf-8")

    assert main(["diff", str(parsed), str(parsed)]) == 0
    assert main(["diff", str(parsed), str(trimmed)]) == 1


# -- the address grammar ----------------------------------------------------


@pytest.mark.parametrize(
    "text,components,occurrence,index",
    [
        ("localizer", ("localizer",), None, None),
        ("a/b/c", ("a", "b", "c"), None, None),
        ("/a//b/", ("a", "b"), None, None),
        ("name#2", ("name",), 2, None),
        ("a/b#10", ("a", "b"), 10, None),
        ("3", ("3",), None, 3),
        ("prog/3", ("prog", "3"), None, 3),
        # An occurrence turns a numeric leaf back into a name: "3#2" asks for
        # the second scan called "3", which is not a position.
        ("3#2", ("3",), 2, None),
    ],
)
def test_addresses_parse(
    text: str, components: tuple, occurrence: int | None, index: int | None
) -> None:
    """Each spelling reads as the components, occurrence and index it means.

    Parameters
    ----------
    text : str
        The address as typed.
    components : tuple
        Expected path components.
    occurrence : int or None
        Expected occurrence.
    index : int or None
        Expected index.

    Returns
    -------
    None
    """
    parsed = address.parse(text)
    assert parsed.components == components
    assert parsed.occurrence == occurrence
    assert parsed.index == index


@pytest.mark.parametrize("text", ["", "/", "///"])
def test_an_empty_address_names_nothing(text: str) -> None:
    """A separator alone is refused rather than matching everything.

    Parameters
    ----------
    text : str
        Something that is not an address.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="names nothing"):
        address.parse(text)


def test_occurrences_count_from_one() -> None:
    """``#0`` is a typo, not the first item.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="count from one"):
        address.parse("name#0")


def test_an_address_is_the_unbroken_tail_of_a_path() -> None:
    """Components must be contiguous from the end.

    Allowing a skipped level would let two addresses that look equally
    specific behave differently, and would let one silently start matching
    something new when a protocol is added.

    Returns
    -------
    None
    """
    path = ("Root", "Export", "Inv", "Frederick", "CMRR spectro scans", "eja_svs_slaser")
    for text in ["eja_svs_slaser", "CMRR spectro scans/eja_svs_slaser", "/".join(path)]:
        assert address.matches(address.parse(text), path), text
    for text in ["Frederick/eja_svs_slaser", "Inv/CMRR spectro scans/eja_svs_slaser", "spectro"]:
        assert not address.matches(address.parse(text), path), text
    # An address longer than the path cannot match it.
    assert not address.matches(address.parse("Deeper/" + "/".join(path)), path)


# -- resolving against what a file holds ------------------------------------


def _candidates() -> list:
    """Two protocols sharing a scan name, and one repeating a name inside itself.

    Returns
    -------
    list
        ``(path, payload)`` pairs shaped like a real backup's.
    """
    root = ("Root", "Export", "Inv", "Frederick")
    return [
        (root + ("CMRR test scans", "eja_svs_slaser"), "test/slaser"),
        (root + ("CMRR spectro scans", "eja_svs_slaser"), "spectro/slaser"),
        (root + ("Functional TOF", "tof fast"), "tof#1"),
        (root + ("Functional TOF", "tof fast"), "tof#2"),
        (root + ("Mair test", "localizer"), "mair/localizer"),
    ]


def test_a_qualified_address_resolves() -> None:
    """Naming the protocol separates a scan name two protocols share.

    Returns
    -------
    None
    """
    got = address.resolve(
        "CMRR spectro scans/eja_svs_slaser", _candidates(), what="scan", source="f"
    )
    assert got == "spectro/slaser"


def test_a_bare_name_unique_in_the_file_resolves() -> None:
    """No path is needed where the name occurs once.

    Returns
    -------
    None
    """
    assert address.resolve("localizer", _candidates(), what="scan", source="f") == "mair/localizer"


def test_an_ambiguous_address_lists_the_full_paths() -> None:
    """A refusal has to say what to prepend, or it cannot be acted on.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError) as caught:
        address.resolve("eja_svs_slaser", _candidates(), what="scan", source="f")
    message = str(caught.value)
    assert "names 2 scans" in message
    assert "CMRR test scans/eja_svs_slaser" in message
    assert "CMRR spectro scans/eja_svs_slaser" in message


def test_a_skipped_level_reports_what_it_nearly_matched() -> None:
    """The component an address is missing is shown rather than guessed at.

    This is the spelling a reader reaches for first -- naming a directory
    they can see and skipping the protocol between it and the scan.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError) as caught:
        address.resolve("Frederick/eja_svs_slaser", _candidates(), what="scan", source="f")
    message = str(caught.value)
    assert "unbroken" in message
    assert "CMRR spectro scans/eja_svs_slaser" in message


def test_a_name_repeated_in_one_protocol_asks_for_an_occurrence() -> None:
    """Identical paths say nothing, so the refusal names the range instead.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError) as caught:
        address.resolve("tof fast", _candidates(), what="scan", source="f")
    message = str(caught.value)
    assert "holds 2 scans named 'tof fast'" in message
    assert "tof fast#1 .. tof fast#2" in message


def test_an_occurrence_picks_one_of_the_repeats() -> None:
    """``#n`` counts from one, in the order the candidates came.

    Returns
    -------
    None
    """
    assert address.resolve("tof fast#1", _candidates(), what="scan", source="f") == "tof#1"
    assert address.resolve("tof fast#2", _candidates(), what="scan", source="f") == "tof#2"


def test_an_occurrence_past_the_end_is_refused() -> None:
    """Asking for the fifth of two says how many there are.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="2 scans match"):
        address.resolve("tof fast#5", _candidates(), what="scan", source="f")


def test_a_name_matching_nothing_suggests_a_near_miss() -> None:
    """A typo gets a suggestion rather than a bare refusal.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="did you mean 'eja_svs_slaser'"):
        address.resolve("eja_svs_slase", _candidates(), what="scan", source="f")
