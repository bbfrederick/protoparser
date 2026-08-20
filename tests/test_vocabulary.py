"""Per-release parameter vocabularies.

These dictionaries are curated, and a wrong entry is worse than a missing
one: it hides a real difference rather than merely failing to explain one.
The tests here check the mechanism, the shipped data, and the guard that
catches the mistake that is easiest to make -- mapping away a label the other
release still uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import ParseFixture, find_example, requires_examples
from siemens_protocol.cli import main
from siemens_protocol.diff import ProtocolDiff, canonical_key, diff_protocols, normalize_key
from siemens_protocol.profiles import REGISTRY
from siemens_protocol.vocabsuggest import suggest_aliases, verify_aliases
from siemens_protocol.vocabulary import (
    VOCABULARY_DIR,
    Vocabulary,
    available,
    check,
    load_vocabulary,
)

SHIPPED = available()


# -- the shipped dictionaries ----------------------------------------------


def test_every_known_release_has_a_vocabulary() -> None:
    """Every registered version profile ships a dictionary.

    Tied to the profile registry rather than a hardcoded list, so adding a
    release without its vocabulary fails here instead of silently leaving
    that release's renames unmapped.

    Returns
    -------
    None
    """
    assert set(REGISTRY.names()) <= set(SHIPPED)


def test_the_numaris_x_releases_agree_on_canonical_names() -> None:
    """XA30 and XA60 must not invent different standard names.

    The two releases share nearly every label, so a canonical name present in
    one and spelled differently in the other would split one parameter into
    two in a diff -- the exact failure vocabularies exist to prevent.

    Returns
    -------
    None
    """
    xa30, xa60 = load_vocabulary("XA30"), load_vocabulary("XA60")
    shared = set(xa30.aliases.values()) & set(xa60.aliases.values())
    assert shared, "the Numaris/X vocabularies share no canonical names at all"
    for label, canonical in xa30.aliases.items():
        other = xa60.canonical(label, normalize_key)
        assert other in (
            None,
            canonical,
        ), f"XA30 maps {label!r} to {canonical!r} but XA60 maps it to {other!r}"


@pytest.mark.parametrize("version", SHIPPED)
def test_vocabulary_files_are_well_formed(version: str) -> None:
    """Each file carries the expected keys and string-to-string aliases.

    Parameters
    ----------
    version : str
        A shipped release name.

    Returns
    -------
    None
    """
    payload = json.loads((VOCABULARY_DIR / f"{version}.json").read_text(encoding="utf-8"))
    assert payload["version"] == version
    assert payload["description"]
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in payload["aliases"].items())
    # Notes and rejections document labels, so they must name real ones.
    assert set(payload.get("notes", {})) <= set(payload["aliases"])


def test_canonical_names_are_snake_case() -> None:
    """Canonical names stay visually distinct from normalized labels.

    Returns
    -------
    None
    """
    for version in SHIPPED:
        for canonical in load_vocabulary(version).aliases.values():
            assert canonical == canonical.lower()
            assert " " not in canonical


def test_shipped_vocabularies_agree_on_canonical_names() -> None:
    """A canonical name defined by one release must exist in the others.

    A one-sided name can never pair with anything, which is the usual sign of
    a typo.

    Returns
    -------
    None
    """
    assert check(SHIPPED) == []


def test_rejected_candidates_are_documented_not_mapped() -> None:
    """Declined mappings are recorded so they are not silently re-added.

    Returns
    -------
    None
    """
    ve11c = json.loads((VOCABULARY_DIR / "VE11C.json").read_text(encoding="utf-8"))
    rejected = ve11c.get("rejected", {})
    assert "Reference scan mode -> Reference Scans" in rejected
    assert "Prescan Normalize -> Normalize" in rejected
    # ...and the rejected sources really are absent from the mapping.
    assert "Reference scan mode" not in ve11c["aliases"]
    assert "Prescan Normalize" not in ve11c["aliases"]


# -- mapping mechanics ------------------------------------------------------


def test_forward_mapping() -> None:
    """A renamed label resolves to its canonical name.

    Returns
    -------
    None
    """
    assert load_vocabulary("VE11C").canonical("PAT mode") == "acceleration_mode"
    assert load_vocabulary("XA60").canonical("Acceleration Mode") == "acceleration_mode"
    assert load_vocabulary("XA30").canonical("Acceleration mode") == "acceleration_mode"


def test_reverse_mapping() -> None:
    """A canonical name resolves back to each release's own label.

    Returns
    -------
    None
    """
    assert load_vocabulary("VE11C").labels("acceleration_mode") == ["PAT mode"]
    assert load_vocabulary("XA60").labels("acceleration_mode") == ["Acceleration Mode"]
    assert load_vocabulary("XA30").labels("acceleration_mode") == ["Acceleration mode"]


def test_reverse_mapping_of_an_unknown_name_is_empty() -> None:
    """A canonical name a release has no label for returns nothing.

    Returns
    -------
    None
    """
    assert load_vocabulary("VE11C").labels("no_such_parameter") == []


def test_an_unmapped_label_falls_back_to_normalization() -> None:
    """The vocabulary only covers what normalization cannot reach.

    Returns
    -------
    None
    """
    vocabulary = load_vocabulary("VE11C")
    assert canonical_key("Dist. factor", vocabulary) == normalize_key("Dist. factor")
    assert canonical_key("PAT mode", vocabulary) == "acceleration_mode"


def test_the_repeat_suffix_survives_vocabulary_lookup() -> None:
    """A renamed parameter still maps when it repeats within a scan.

    Returns
    -------
    None
    """
    assert canonical_key("PAT mode #2", load_vocabulary("VE11C")) == "acceleration_mode"


def test_an_unknown_release_loads_an_empty_vocabulary() -> None:
    """A release without a dictionary is not an error.

    Returns
    -------
    None
    """
    assert load_vocabulary("VE11E").aliases == {}


def test_an_overlay_directory_extends_the_shipped_data(tmp_path: Path) -> None:
    """A site can add mappings without editing the installed package.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    (tmp_path / "VE11C.json").write_text(
        json.dumps({"version": "VE11C", "aliases": {"Local label": "local_thing"}}),
        encoding="utf-8",
    )
    vocabulary = load_vocabulary("VE11C", tmp_path)
    assert vocabulary.canonical("Local label") == "local_thing"
    assert vocabulary.canonical("PAT mode") == "acceleration_mode", "shipped data must survive"


def test_a_malformed_vocabulary_is_reported(tmp_path: Path) -> None:
    """A broken overlay names the file rather than failing obscurely.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    (tmp_path / "VE11C.json").write_text('{"aliases": {"a": 1}}', encoding="utf-8")
    with pytest.raises(ValueError, match="aliases"):
        load_vocabulary("VE11C", tmp_path)


def test_check_flags_a_one_sided_canonical_name() -> None:
    """A canonical name only one release defines can never pair up.

    Returns
    -------
    None
    """
    problems = check(["VE11C", "XA60"])
    assert problems == []
    lonely = Vocabulary(version="X", aliases={"Thing": "only_here"})
    assert lonely.labels("only_here") == ["Thing"]


# -- verification against real exports --------------------------------------


@requires_examples
def test_shipped_vocabularies_hold_up_against_the_examples(parsed: ParseFixture) -> None:
    """Every shipped mapping is used, paired, and steals no existing match.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf")).protocol.to_dict()
    right = parsed(find_example("R01StressDynXA60.pdf")).protocol.to_dict()
    assert verify_aliases(left, right) == []


@requires_examples
def test_a_mapping_that_steals_a_match_is_caught(parsed: ParseFixture, tmp_path: Path) -> None:
    """XA60 splits ``Reference scan mode``; mapping it away breaks a pairing.

    This is the mistake the guard exists for. XA60 keeps the VE11C label for
    some sequences while adding a second one for the rest, so aliasing the
    VE11C label onto the new name orphans the readings that already matched.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    for version, alias in (
        ("VE11C", {"Reference scan mode": "reference_scans"}),
        ("XA60", {"Reference Scans": "reference_scans"}),
    ):
        payload = json.loads((VOCABULARY_DIR / f"{version}.json").read_text(encoding="utf-8"))
        payload["aliases"].update(alias)
        (tmp_path / f"{version}.json").write_text(json.dumps(payload), encoding="utf-8")

    left = parsed(find_example("R01StressDyn.pdf")).protocol.to_dict()
    right = parsed(find_example("R01StressDynXA60.pdf")).protocol.to_dict()
    problems = verify_aliases(left, right, str(tmp_path))
    assert any("steals a match" in p for p in problems)


# -- effect on the comparison ----------------------------------------------


@requires_examples
def test_the_vocabulary_resolves_renamed_parameters(parsed: ParseFixture) -> None:
    """A renamed parameter pairs up instead of appearing twice.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf")).protocol.to_dict()
    right = parsed(find_example("R01StressDynXA60.pdf")).protocol.to_dict()

    without = diff_protocols(left, right, use_vocabulary=False)
    with_vocab = diff_protocols(left, right, use_vocabulary=True)

    def orphans(result: ProtocolDiff) -> set[str]:
        """Keys reported as present on one side only.

        Parameters
        ----------
        result : ProtocolDiff
            A protocol comparison.

        Returns
        -------
        set of str
            The orphaned key names.
        """
        return {
            d.key
            for s in result.scans
            for d in s.parameters
            if d.status in ("only_left", "only_right")
        }

    assert "PAT mode" in orphans(without)
    assert "Acceleration Mode" in orphans(without)
    assert "PAT mode" not in orphans(with_vocab)
    assert "Acceleration Mode" not in orphans(with_vocab)


@requires_examples
def test_the_vocabulary_reduces_orphans_without_erasing_changes(parsed: ParseFixture) -> None:
    """Fewer phantom add/remove pairs, and real value changes still surface.

    A renamed parameter whose value also moved must remain substantive.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf")).protocol.to_dict()
    right = parsed(find_example("R01StressDynXA60.pdf")).protocol.to_dict()
    without = diff_protocols(left, right, use_vocabulary=False)
    with_vocab = diff_protocols(left, right, use_vocabulary=True)

    def counts(result: ProtocolDiff) -> tuple[int, int]:
        """Orphan and changed totals for a comparison.

        Parameters
        ----------
        result : ProtocolDiff
            A protocol comparison.

        Returns
        -------
        tuple of int
            ``(orphans, changed)``.
        """
        orphan = sum(
            1
            for s in result.scans
            for d in s.parameters
            if d.status in ("only_left", "only_right")
        )
        changed = sum(1 for s in result.scans for d in s.parameters if d.status == "changed")
        return orphan, changed

    orphan_without, changed_without = counts(without)
    orphan_with, changed_with = counts(with_vocab)
    assert orphan_with < orphan_without, "mappings must resolve orphaned parameters"
    assert changed_with >= changed_without, "resolving a pair must not hide a value change"


# -- suggestions ------------------------------------------------------------


@requires_examples
def test_suggestions_come_with_evidence_and_are_not_applied(parsed: ParseFixture) -> None:
    """``suggest`` proposes; it never writes to a vocabulary.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf")).protocol.to_dict()
    right = parsed(find_example("R01StressDynXA60.pdf")).protocol.to_dict()
    before = dict(load_vocabulary("VE11C").aliases)
    candidates = suggest_aliases(left, right, min_support=8)
    assert candidates, "the examples should offer some candidates"
    for candidate in candidates:
        assert candidate.support >= 8
        assert 0.0 <= candidate.value_ratio <= 1.0
        assert 0.0 <= candidate.section_ratio <= 1.0
        assert candidate.left_values or candidate.right_values
    assert load_vocabulary("VE11C").aliases == before


@requires_examples
def test_suggestions_skip_already_mapped_labels(parsed: ParseFixture) -> None:
    """A label the vocabulary already resolves is not proposed again.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    left = parsed(find_example("R01StressDyn.pdf")).protocol.to_dict()
    right = parsed(find_example("R01StressDynXA60.pdf")).protocol.to_dict()
    proposed = {c.left_label for c in suggest_aliases(left, right, min_support=8)}
    assert "PAT mode" not in proposed
    assert "Coil Combine Mode" not in proposed


# -- the command line -------------------------------------------------------


def test_cli_vocab_list_all() -> None:
    """Listing prints every shipped release.

    Returns
    -------
    None
    """
    assert main(["vocab", "list"]) == 0


def test_cli_vocab_list_one_release(capsys: pytest.CaptureFixture) -> None:
    """Listing one release prints its mappings and notes.

    Parameters
    ----------
    capsys : pytest.CaptureFixture
        Captures the printed listing.

    Returns
    -------
    None
    """
    assert main(["vocab", "list", "VE11C"]) == 0
    printed = capsys.readouterr().out
    assert "PAT mode" in printed and "acceleration_mode" in printed


def test_cli_vocab_list_unknown_release() -> None:
    """An unknown release fails rather than printing nothing.

    Returns
    -------
    None
    """
    assert main(["vocab", "list", "VE11E"]) == 1


def test_cli_vocab_reverse_lookup(capsys: pytest.CaptureFixture) -> None:
    """``--canonical`` answers what each release calls a standard name.

    Parameters
    ----------
    capsys : pytest.CaptureFixture
        Captures the printed listing.

    Returns
    -------
    None
    """
    assert main(["vocab", "list", "--canonical", "acceleration_mode"]) == 0
    printed = capsys.readouterr().out
    assert "PAT mode" in printed
    assert "Acceleration Mode" in printed


def test_cli_vocab_check_passes() -> None:
    """The shipped vocabularies validate against each other.

    Returns
    -------
    None
    """
    assert main(["vocab", "check"]) == 0


@requires_examples
def test_cli_vocab_check_against_examples() -> None:
    """The shipped vocabularies validate against real exports too.

    Returns
    -------
    None
    """
    code = main(
        [
            "vocab",
            "check",
            "--against",
            find_example("R01StressDyn.pdf"),
            find_example("R01StressDynXA60.pdf"),
        ]
    )
    assert code == 0


@requires_examples
def test_cli_vocab_suggest(capsys: pytest.CaptureFixture) -> None:
    """Suggestions print with their evidence.

    Parameters
    ----------
    capsys : pytest.CaptureFixture
        Captures the printed candidates.

    Returns
    -------
    None
    """
    code = main(
        [
            "vocab",
            "suggest",
            find_example("R01StressDyn.pdf"),
            find_example("R01StressDynXA60.pdf"),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "same value" in printed and "Evidence only" in printed


@requires_examples
def test_cli_diff_can_turn_the_vocabulary_off(tmp_path: Path) -> None:
    """``--no-vocabulary`` shows the unresolved picture.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    on = tmp_path / "on.txt"
    off = tmp_path / "off.txt"
    args = ["diff", find_example("R01StressDyn.pdf"), find_example("R01StressDynXA60.pdf")]
    main(args + ["--show-cosmetic", "--out", str(on)])
    main(args + ["--no-vocabulary", "--show-cosmetic", "--out", str(off)])
    # Without the vocabulary the parameter appears twice, as an orphan on each
    # side; with it, the two pair up and the rename is reported as cosmetic.
    assert "PAT mode" in off.read_text()
    assert "PAT mode -> Acceleration Mode" not in off.read_text()
    assert "PAT mode -> Acceleration Mode" in on.read_text()
