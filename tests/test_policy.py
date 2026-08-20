"""Checking protocols against preferred values.

A rule fires only where its parameter is present: a localizer that never
prints a multiband setting is not in violation of a multiband rule. Rules are
matched by canonical name, so one rule covers every release.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import ParseFixture, find_example, requires_examples
from siemens_protocol.cli import main
from siemens_protocol.policy import (
    POLICY_DIR,
    Policy,
    PolicyError,
    Rule,
    available,
    check_protocol,
    load_policy,
    parse_number,
)
from siemens_protocol.vocabulary import load_vocabulary


def protocol(*scans: dict) -> dict:
    """Build a minimal serialized protocol.

    Parameters
    ----------
    *scans : dict
        Serialized scans.

    Returns
    -------
    dict
        A protocol carrying those scans.
    """
    return {"source_file": "test.pdf", "software_version": "VE11C", "scans": list(scans)}


def scan(name: str, index: int = 0, **sections: dict) -> dict:
    """Build a minimal serialized scan.

    Parameters
    ----------
    name : str
        The scan's protocol name.
    index : int, optional
        Its position. Default 0.
    **sections : dict
        Section name to key/value mapping. Underscores become spaces and
        double underscores become ``" - "``, so ``Sequence__Special`` reads
        as ``"Sequence - Special"``.

    Returns
    -------
    dict
        The serialized scan.
    """
    renamed = {k.replace("__", " - ").replace("_", " "): v for k, v in sections.items()}
    return {"name": name, "index": index, "sections": renamed}


def policy_of(*rules: Rule) -> Policy:
    """Wrap rules in a policy.

    Parameters
    ----------
    *rules : Rule
        The rules to apply.

    Returns
    -------
    Policy
        A policy named ``"test"``.
    """
    return Policy(name="test", rules=list(rules))


# -- the shipped policy -----------------------------------------------------


def test_the_default_policy_ships() -> None:
    """A default policy is available without configuration.

    Returns
    -------
    None
    """
    assert "default" in available()
    assert load_policy().rules


def test_the_default_policy_is_well_formed() -> None:
    """Every shipped rule names a parameter, a constraint and a reason.

    Returns
    -------
    None
    """
    payload = json.loads((POLICY_DIR / "default.json").read_text(encoding="utf-8"))
    for rule in payload["rules"]:
        assert rule["parameter"]
        assert rule["reason"], "a preference without a stated reason cannot be judged"
        assert any(k in rule for k in ("equals", "one_of", "not_equals", "min", "max"))


def test_the_shipped_rules_are_the_requested_ones() -> None:
    """The default policy carries the two preferences it was built for.

    Returns
    -------
    None
    """
    rules = {r.parameter: r for r in load_policy().rules}
    assert rules["MB RF phase scramble"].equals == "On"
    assert rules["Excite pulse duration"].min == 3000
    assert rules["Excite pulse duration"].unit == "us"


# -- loading ----------------------------------------------------------------


def test_a_policy_can_be_loaded_from_a_path(tmp_path: Path) -> None:
    """A policy file can be named directly rather than by registered name.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    path = tmp_path / "local.json"
    path.write_text(
        json.dumps({"name": "local", "rules": [{"parameter": "TR", "equals": "20.0 ms"}]}),
        encoding="utf-8",
    )
    assert load_policy(str(path)).rules[0].parameter == "TR"


def test_an_overlay_directory_is_searched_first(tmp_path: Path) -> None:
    """A site policy can sit outside the installed package.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    (tmp_path / "site.json").write_text(
        json.dumps({"name": "site", "rules": [{"parameter": "TE", "max": 30}]}),
        encoding="utf-8",
    )
    assert load_policy("site", tmp_path).rules[0].max == 30
    assert "site" in available(tmp_path)


def test_an_unknown_policy_names_the_ones_that_exist() -> None:
    """A typo fails clearly rather than checking nothing.

    Returns
    -------
    None
    """
    with pytest.raises(PolicyError, match="default"):
        load_policy("no-such-policy")


@pytest.mark.parametrize(
    "rule,message",
    [
        ({"equals": "On"}, "parameter"),
        ({"parameter": "TR"}, "constraint"),
        ({"parameter": "TR", "min": "soon"}, "non-numeric"),
        ({"parameter": "TR", "equals": "On", "severity": "loud"}, "severity"),
    ],
)
def test_malformed_rules_are_rejected_at_load(tmp_path: Path, rule: dict, message: str) -> None:
    """A broken rule fails loudly instead of silently never matching.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.
    rule : dict
        The malformed rule.
    message : str
        Text the error must mention.

    Returns
    -------
    None
    """
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"name": "bad", "rules": [rule]}), encoding="utf-8")
    with pytest.raises(PolicyError, match=message):
        load_policy(str(path))


# -- evaluation -------------------------------------------------------------


def test_an_exact_preference_passes_and_fails() -> None:
    """``equals`` accepts the preferred reading and rejects others.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="MB RF phase scramble", equals="On"))
    ok = protocol(scan("a", 0, Sequence__Special={"MB RF phase scramble": "On"}))
    bad = protocol(scan("b", 0, Sequence__Special={"MB RF phase scramble": "Off"}))
    assert check_protocol(ok, rules).violations == []
    found = check_protocol(bad, rules).violations
    assert len(found) == 1
    assert found[0].value == "Off"
    assert found[0].expected == "'On'"


def test_an_exact_preference_ignores_case() -> None:
    """Releases recapitalize values, which must not read as a violation.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="Mode", equals="Single Shot"))
    doc = protocol(scan("a", 0, Routine={"Mode": "Single shot"}))
    assert check_protocol(doc, rules).violations == []


def test_a_numeric_bound() -> None:
    """``min`` compares the leading number of a reading.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="Excite pulse duration", min=3000, unit="us"))
    ok = protocol(scan("a", 0, Sequence__Special={"Excite pulse duration": "3820 us"}))
    bad = protocol(scan("b", 0, Sequence__Special={"Excite pulse duration": "2000 us"}))
    assert check_protocol(ok, rules).violations == []
    assert len(check_protocol(bad, rules).violations) == 1


def test_a_bound_is_inclusive() -> None:
    """A reading exactly on the bound satisfies it.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="Excite pulse duration", min=3000, unit="us"))
    doc = protocol(scan("a", 0, Sequence__Special={"Excite pulse duration": "3000 us"}))
    assert check_protocol(doc, rules).violations == []


def test_a_unit_mismatch_is_reported_not_silently_compared() -> None:
    """3 ms against a bound of 3000 us would otherwise pass by accident.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="Excite pulse duration", min=3000, unit="us"))
    doc = protocol(scan("a", 0, Sequence__Special={"Excite pulse duration": "3 ms"}))
    found = check_protocol(doc, rules).violations
    assert len(found) == 1
    assert "unit is 'ms'" in found[0].detail


def test_a_non_numeric_reading_under_a_bound_is_reported() -> None:
    """A bound cannot be applied to a word, and that is a finding.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="Excite pulse duration", min=3000, unit="us"))
    doc = protocol(scan("a", 0, Sequence__Special={"Excite pulse duration": "Auto"}))
    found = check_protocol(doc, rules).violations
    assert len(found) == 1
    assert "not numeric" in found[0].detail


def test_one_of_and_not_equals() -> None:
    """A rule may list acceptable values, or forbid one.

    Returns
    -------
    None
    """
    allow = policy_of(Rule(parameter="Filter", one_of=["Off", "Prescan"]))
    forbid = policy_of(Rule(parameter="Filter", not_equals="Off"))
    doc = protocol(scan("a", 0, Routine={"Filter": "Off"}))
    assert check_protocol(doc, allow).violations == []
    assert len(check_protocol(doc, forbid).violations) == 1


def test_a_rule_only_fires_where_its_parameter_exists() -> None:
    """A scan without the setting is not in violation of anything.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="MB RF phase scramble", equals="On"))
    report = check_protocol(protocol(scan("localizer", 0, Routine={"TR": "20 ms"})), rules)
    assert report.violations == []
    assert report.checked == 0
    assert report.unused_rules == ["MB RF phase scramble"]


def test_a_section_scope_restricts_the_rule() -> None:
    """The same label elsewhere is left alone.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="Mode", section="Sequence - Special", equals="On"))
    doc = protocol(
        scan("a", 0, Sequence__Special={"Mode": "Off"}, Routine={"Mode": "Off"}),
    )
    found = check_protocol(doc, rules).violations
    assert len(found) == 1
    assert found[0].section == "Sequence - Special"


def test_a_repeated_parameter_is_checked_every_time() -> None:
    """Each reading of a repeated key is its own opportunity to deviate.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="Slice group", equals="1"))
    doc = protocol(scan("a", 0, Geometry={"Slice group": "1", "Slice group #2": "2"}))
    report = check_protocol(doc, rules)
    assert report.checked == 2
    assert len(report.violations) == 1
    assert report.violations[0].value == "2"


def test_severity_separates_errors_from_warnings() -> None:
    """A warning is reported but does not have to fail a check.

    Returns
    -------
    None
    """
    rules = policy_of(
        Rule(parameter="A", equals="On", severity="error"),
        Rule(parameter="B", equals="On", severity="warning"),
    )
    doc = protocol(scan("a", 0, Routine={"A": "Off", "B": "Off"}))
    report = check_protocol(doc, rules)
    assert len(report.violations) == 2
    assert len(report.errors) == 1


@pytest.mark.parametrize(
    "value,expected",
    [("3820 us", (3820.0, "us")), ("2530.0 ms", (2530.0, "ms")), ("7", (7.0, "")), ("On", None)],
)
def test_number_parsing(value: str, expected: tuple | None) -> None:
    """Readings split into a number and a unit, or not at all.

    Parameters
    ----------
    value : str
        The reading.
    expected : tuple or None
        The expected split.

    Returns
    -------
    None
    """
    assert parse_number(value) == expected


# -- one rule, every release ------------------------------------------------


def test_a_rule_follows_a_renamed_parameter_across_releases() -> None:
    """Written with one release's label, a rule still finds the other's.

    Parameters are matched by canonical name, so ``PAT mode`` and
    ``Acceleration Mode`` need one rule rather than two.

    Returns
    -------
    None
    """
    rules = policy_of(Rule(parameter="PAT mode", equals="GRAPPA"))
    xa60 = {
        "source_file": "x.pdf",
        "software_version": "XA60",
        "scans": [scan("a", 0, Resolution={"Acceleration Mode": "None"})],
    }
    found = check_protocol(xa60, rules, load_vocabulary("XA60")).violations
    assert len(found) == 1
    assert found[0].key == "Acceleration Mode", "the report must quote the printed label"


# -- against the real examples ----------------------------------------------


@requires_examples
@pytest.mark.parametrize(
    "name,expected_scans",
    [
        ("NOCICEPT_Ph2MRI515_Second.pdf", ["dMRI_dir99_AP", "dMRI_dir99_PA"]),
        ("NOCICEPT_Ph2MRI515_SecondXA60.pdf", ["dMRI_dir99_AP", "dMRI_dir99_PA"]),
    ],
)
def test_the_default_policy_finds_the_real_violations(
    parsed: ParseFixture, name: str, expected_scans: list[str]
) -> None:
    """Both releases of this protocol leave phase scrambling off on dMRI.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.
    name : str
        Base file name of the example.
    expected_scans : list of str
        Scans expected to violate.

    Returns
    -------
    None
    """
    pdf = find_example(name)
    protocol_dict = parsed(pdf).protocol.to_dict()
    version = protocol_dict["software_version"]
    report = check_protocol(protocol_dict, load_policy(), load_vocabulary(version))
    # Scoped to the rule under test: the shipped policy is meant to be edited,
    # and an unrelated rule being added should not fail this.
    scrambling = [v for v in report.violations if v.key == "MB RF phase scramble"]
    assert [v.scan_name for v in scrambling] == expected_scans
    assert all(v.value == "Off" for v in scrambling)


@requires_examples
@pytest.mark.parametrize("name", ["R01StressDyn.pdf", "R01StressDynXA60.pdf"])
def test_excite_pulse_durations_clear_the_bound(parsed: ParseFixture, name: str) -> None:
    """Every excite pulse in these protocols already clears the bound.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.
    name : str
        Base file name of the example.

    Returns
    -------
    None
    """
    protocol_dict = parsed(find_example(name)).protocol.to_dict()
    version = protocol_dict["software_version"]
    report = check_protocol(protocol_dict, load_policy(), load_vocabulary(version))
    assert [v for v in report.violations if v.key == "Excite pulse duration"] == []
    assert report.checked > 0, "the rules must actually have been exercised"


# -- the command line -------------------------------------------------------


@requires_examples
def test_cli_check_reports_violations(tmp_path: Path) -> None:
    """A protocol with a violation exits non-zero and explains why.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    out = tmp_path / "report.txt"
    code = main(["check", find_example("NOCICEPT_Ph2MRI515_Second.pdf"), "--out", str(out)])
    assert code == 1
    text = out.read_text()
    assert "MB RF phase scramble" in text
    assert "prefer 'On'" in text
    assert "dMRI_dir99_AP" in text


@requires_examples
def test_cli_check_passes_a_clean_protocol(tmp_path: Path) -> None:
    """A protocol within preference exits zero.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    # A purpose-built policy rather than the shipped one: "clean" has to be a
    # property of the rules, and the shipped rules are expected to change.
    (tmp_path / "satisfied.json").write_text(
        json.dumps(
            {
                "name": "satisfied",
                "rules": [
                    {
                        "parameter": "Excite pulse duration",
                        "min": 1,
                        "unit": "us",
                        "reason": "every reading clears this",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "clean.txt"
    code = main(
        [
            "check",
            find_example("R01StressDyn.pdf"),
            "--policy",
            "satisfied",
            "--policy-dir",
            str(tmp_path),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert "all within preference" in out.read_text()


@requires_examples
def test_cli_check_json(tmp_path: Path) -> None:
    """The findings are available as JSON.

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
            "check",
            find_example("NOCICEPT_Ph2MRI515_Second.pdf"),
            "--json",
            "--out",
            str(out),
        ]
    )
    payload = json.loads(out.read_text())
    keys = [v["key"] for v in payload[0]["violations"]]
    assert "MB RF phase scramble" in keys
    assert payload[0]["readings_checked"] > 0


@requires_examples
def test_cli_check_batches_a_directory(tmp_path: Path) -> None:
    """A directory target checks every PDF beneath it.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    from conftest import EXAMPLE_FILES, EXAMPLES

    out = tmp_path / "all.txt"
    assert main(["check", EXAMPLES, "--out", str(out)]) == 1
    text = out.read_text()
    assert text.count("against policy 'default'") == len(EXAMPLE_FILES)


@requires_examples
def test_cli_check_warnings_can_pass(tmp_path: Path) -> None:
    """``--warnings-ok`` distinguishes advisory rules from hard ones.

    Parameters
    ----------
    tmp_path : Path
        Pytest's per-test temporary directory.

    Returns
    -------
    None
    """
    (tmp_path / "advisory.json").write_text(
        json.dumps(
            {
                "name": "advisory",
                "rules": [
                    {
                        "parameter": "Excite pulse duration",
                        "min": 999999,
                        "unit": "us",
                        "severity": "warning",
                        "reason": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    args = [
        "check",
        find_example("R01StressDyn.pdf"),
        "--policy",
        "advisory",
        "--policy-dir",
        str(tmp_path),
        "--out",
        str(tmp_path / "o.txt"),
    ]
    assert main(args) == 1
    assert main(args + ["--warnings-ok"]) == 0


def test_cli_check_rejects_an_unknown_policy() -> None:
    """A bad ``--policy`` fails rather than checking nothing.

    Returns
    -------
    None
    """
    assert main(["check", "whatever.pdf", "--policy", "nope"]) == 1


@requires_examples
def test_rules_resolve_against_an_xa30_export(parsed: ParseFixture) -> None:
    """A rule written once fires on XA30 as well as the older releases.

    ``ATE_Study`` is the only example whose excite pulse duration is out of
    preference, and it is XA30, so this also covers the numeric-bound path
    against real data rather than a fixture.

    Parameters
    ----------
    parsed : ParseFixture
        The session-scoped parse fixture.

    Returns
    -------
    None
    """
    protocol_dict = parsed(find_example("ATE_Study.pdf")).protocol.to_dict()
    assert protocol_dict["software_version"] == "XA30"
    report = check_protocol(protocol_dict, load_policy(), load_vocabulary("XA30"))
    assert report.checked > 0, "no XA30 reading was examined"
    pulses = [v for v in report.violations if v.key == "Excite pulse duration"]
    assert [(v.scan_name, v.value) for v in pulses] == [("dMRI_dir99_PA", "2560 us")]
