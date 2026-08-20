"""Check a protocol's parameters against preferred values.

A policy is a list of rules, each naming one parameter and what it should
read. Rules fire only when the parameter is actually present in a scan, so a
rule about multiband excitation stays silent on a localizer that never had the
setting -- there is no such thing as a scan "missing" a parameter it was never
going to print.

Parameters are matched by canonical name, the same machinery the comparison
uses, so one rule covers every release: ``PAT mode`` and ``Acceleration Mode``
are one rule, not two.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..diff import canonical_key, normalize_key
from ..vocabulary import Vocabulary

#: Where the shipped policies live.
POLICY_DIR = Path(__file__).parent

#: A numeric reading with an optional trailing unit.
_NUMERIC_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(.*?)\s*$")

#: Constraint keys a rule may carry, in the order they are reported.
CONSTRAINTS = ("equals", "one_of", "not_equals", "min", "max")


class PolicyError(ValueError):
    """Raised when a policy file is missing or malformed."""


@dataclass
class Rule:
    """One preference about one parameter.

    Attributes
    ----------
    parameter : str
        The parameter label, as printed by any release. Matched by canonical
        name, so a rule written against one release covers the others.
    section : str or None
        Restrict the rule to a section, matched case-insensitively. ``None``
        checks the parameter wherever it appears.
    equals : str or None
        The preferred exact value.
    one_of : list of str or None
        Acceptable values, when more than one will do.
    not_equals : str or None
        A value that is never acceptable.
    min, max : float or None
        Inclusive numeric bounds, compared against the leading number of the
        reading.
    unit : str or None
        Expected unit for a numeric bound. A reading in a different unit is
        reported rather than silently compared.
    reason : str
        Why the preference exists. Shown in the report.
    severity : str
        ``"error"`` or ``"warning"``. Only errors set a failing exit status.
    """

    parameter: str
    section: str | None = None
    equals: str | None = None
    one_of: list[str] | None = None
    not_equals: str | None = None
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    reason: str = ""
    severity: str = "error"

    @property
    def expectation(self) -> str:
        """The rule's constraint, phrased for a report.

        Returns
        -------
        str
            A short description such as ``"On"`` or ``">= 3000 us"``.
        """
        parts: list[str] = []
        if self.equals is not None:
            parts.append(repr(self.equals))
        if self.one_of:
            parts.append("one of " + ", ".join(repr(v) for v in self.one_of))
        if self.not_equals is not None:
            parts.append(f"not {self.not_equals!r}")
        unit = f" {self.unit}" if self.unit else ""
        if self.min is not None:
            parts.append(f">= {_trim(self.min)}{unit}")
        if self.max is not None:
            parts.append(f"<= {_trim(self.max)}{unit}")
        return " and ".join(parts) if parts else "(no constraint)"

    def to_dict(self) -> dict:
        """Serialize the rule.

        Returns
        -------
        dict
            The rule's fields, omitting unset constraints.
        """
        out: dict[str, Any] = {"parameter": self.parameter, "severity": self.severity}
        for name in ("section", "unit", "reason"):
            value = getattr(self, name)
            if value:
                out[name] = value
        for name in CONSTRAINTS:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out


def _trim(value: float) -> str:
    """Render a bound without a pointless trailing zero.

    Parameters
    ----------
    value : float
        A numeric bound.

    Returns
    -------
    str
        ``"3000"`` rather than ``"3000.0"``.
    """
    return f"{value:g}"


@dataclass
class Policy:
    """A named set of rules.

    Attributes
    ----------
    name : str
        The policy's name, matching its file stem.
    description : str
        What the policy is for.
    rules : list of Rule
        The preferences, in file order.
    """

    name: str
    description: str = ""
    rules: list[Rule] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize the policy in its on-disk form.

        Returns
        -------
        dict
            Name, description and rules.
        """
        return {
            "name": self.name,
            "description": self.description,
            "rules": [r.to_dict() for r in self.rules],
        }


def _build_rule(raw: Mapping, source: str, index: int) -> Rule:
    """Turn one raw JSON rule into a :class:`Rule`.

    Parameters
    ----------
    raw : mapping
        The rule as read from JSON.
    source : str
        Where it came from, for error messages.
    index : int
        Its position in the file, for error messages.

    Returns
    -------
    Rule
        The validated rule.

    Raises
    ------
    PolicyError
        If the rule names no parameter, carries no constraint, or gives a
        non-numeric bound.
    """
    where = f"{source}: rule {index}"
    parameter = raw.get("parameter")
    if not isinstance(parameter, str) or not parameter.strip():
        raise PolicyError(f"{where} has no 'parameter'")
    if not any(raw.get(name) is not None for name in CONSTRAINTS):
        raise PolicyError(
            f"{where} ({parameter!r}) carries no constraint; "
            f"expected one of {', '.join(CONSTRAINTS)}"
        )
    bounds: dict[str, float | None] = {}
    for name in ("min", "max"):
        value = raw.get(name)
        if value is None:
            bounds[name] = None
            continue
        try:
            bounds[name] = float(value)
        except (TypeError, ValueError):
            raise PolicyError(f"{where} ({parameter!r}) has a non-numeric {name!r}") from None
    one_of = raw.get("one_of")
    if one_of is not None and not isinstance(one_of, list):
        raise PolicyError(f"{where} ({parameter!r}) has a non-list 'one_of'")
    severity = raw.get("severity", "error")
    if severity not in ("error", "warning"):
        raise PolicyError(f"{where} ({parameter!r}) has severity {severity!r}")
    return Rule(
        parameter=parameter,
        section=raw.get("section"),
        equals=raw.get("equals"),
        one_of=[str(v) for v in one_of] if one_of else None,
        not_equals=raw.get("not_equals"),
        min=bounds["min"],
        max=bounds["max"],
        unit=raw.get("unit"),
        reason=raw.get("reason", ""),
        severity=severity,
    )


def load_policy(name: str = "default", extra_dir: str | os.PathLike | None = None) -> Policy:
    """Load a policy by name.

    Parameters
    ----------
    name : str, optional
        A policy name matching a ``<name>.json`` file, or a path to one.
        Default ``"default"``.
    extra_dir : path-like or None, optional
        A directory of policies searched before the shipped ones, so a site
        can keep its own preferences outside the installed package.

    Returns
    -------
    Policy
        The loaded policy.

    Raises
    ------
    PolicyError
        If the policy cannot be found or is malformed.
    """
    candidates: list[Path] = []
    direct = Path(name)
    if direct.suffix == ".json":
        candidates.append(direct)
    if extra_dir:
        candidates.append(Path(extra_dir) / f"{name}.json")
    candidates.append(POLICY_DIR / f"{name}.json")

    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise PolicyError(
            f"no policy named {name!r}; have {', '.join(available(extra_dir)) or 'none'}"
        )
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"could not read the policy at {path}: {exc}") from exc
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list):
        raise PolicyError(f"{path}: 'rules' must be a list")
    return Policy(
        name=payload.get("name", path.stem),
        description=payload.get("description", ""),
        rules=[_build_rule(r, str(path), i) for i, r in enumerate(raw_rules)],
    )


def available(extra_dir: str | os.PathLike | None = None) -> list[str]:
    """Every policy that can be loaded by name.

    Parameters
    ----------
    extra_dir : path-like or None, optional
        An additional directory to look in.

    Returns
    -------
    list of str
        Policy names, sorted.
    """
    found = {p.stem for p in POLICY_DIR.glob("*.json")}
    if extra_dir and Path(extra_dir).is_dir():
        found |= {p.stem for p in Path(extra_dir).glob("*.json")}
    return sorted(found)


@dataclass
class Violation:
    """One reading that departs from a preference.

    Attributes
    ----------
    scan_index : int
        Position of the scan within the protocol.
    scan_name : str
        The scan's protocol name.
    section : str
        Where the reading was printed.
    key : str
        The parameter label, as this release spells it.
    value : str
        The reading, verbatim.
    expected : str
        What the rule asked for.
    reason : str
        Why the preference exists.
    severity : str
        ``"error"`` or ``"warning"``.
    detail : str
        What specifically went wrong, such as a unit mismatch.
    """

    scan_index: int
    scan_name: str
    section: str
    key: str
    value: str
    expected: str
    reason: str = ""
    severity: str = "error"
    detail: str = ""

    def to_dict(self) -> dict:
        """Serialize the violation.

        Returns
        -------
        dict
            Scan, location, reading, expectation and severity.
        """
        out = {
            "scan_index": self.scan_index,
            "scan_name": self.scan_name,
            "section": self.section,
            "key": self.key,
            "value": self.value,
            "expected": self.expected,
            "severity": self.severity,
        }
        if self.detail:
            out["detail"] = self.detail
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class PolicyReport:
    """The result of checking a protocol.

    Attributes
    ----------
    source_file : str
        The protocol that was checked.
    software_version : str or None
        Its release.
    policy : str
        The policy that was applied.
    violations : list of Violation
        Every departure found, in scan order.
    checked : int
        How many readings a rule actually applied to.
    unused_rules : list of str
        Rules whose parameter appeared nowhere. Usually means the protocol
        has no such sequences, but a persistent one may be a stale rule.
    """

    source_file: str
    software_version: str | None
    policy: str
    violations: list[Violation] = field(default_factory=list)
    checked: int = 0
    unused_rules: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Violation]:
        """Violations at error severity.

        Returns
        -------
        list of Violation
            Only the ones that should fail a check.
        """
        return [v for v in self.violations if v.severity == "error"]

    def to_dict(self) -> dict:
        """Serialize the report.

        Returns
        -------
        dict
            Source, policy, counts and every violation.
        """
        return {
            "source_file": self.source_file,
            "software_version": self.software_version,
            "policy": self.policy,
            "readings_checked": self.checked,
            "violations": [v.to_dict() for v in self.violations],
            "unused_rules": self.unused_rules,
        }


def parse_number(value: str) -> tuple[float, str] | None:
    """Split a reading into its leading number and trailing unit.

    Parameters
    ----------
    value : str
        A reading such as ``"3820 us"``.

    Returns
    -------
    tuple or None
        ``(number, unit)``, or ``None`` when the reading is not numeric.
    """
    match = _NUMERIC_RE.match(value)
    if not match:
        return None
    try:
        return float(match.group(1)), match.group(2)
    except ValueError:  # pragma: no cover - guarded by the regex
        return None


def _check_reading(rule: Rule, value: str) -> tuple[bool, str]:
    """Test one reading against one rule.

    Parameters
    ----------
    rule : Rule
        The preference to apply.
    value : str
        The reading, verbatim.

    Returns
    -------
    tuple
        ``(ok, detail)``. ``detail`` explains a failure, and is empty on pass.
    """
    if rule.equals is not None and value.casefold() != rule.equals.casefold():
        return False, ""
    if rule.one_of and value.casefold() not in {v.casefold() for v in rule.one_of}:
        return False, ""
    if rule.not_equals is not None and value.casefold() == rule.not_equals.casefold():
        return False, ""

    if rule.min is None and rule.max is None:
        return True, ""

    parsed = parse_number(value)
    if parsed is None:
        return False, f"{value!r} is not numeric, so the bound could not be applied"
    number, unit = parsed
    if rule.unit and unit and unit.casefold() != rule.unit.casefold():
        # Comparing 3 ms against a bound of 3000 us would silently pass.
        return False, f"unit is {unit!r}, but the rule is written in {rule.unit!r}"
    if rule.min is not None and number < rule.min:
        return False, ""
    if rule.max is not None and number > rule.max:
        return False, ""
    return True, ""


def rule_key(parameter: str) -> str:
    """Canonicalize a rule's parameter, whichever release it was written for.

    A rule names a parameter using some release's spelling, and that need not
    be the release being checked. Resolving it against *every* known
    vocabulary is what makes one rule cover them all: a preference written as
    ``PAT mode`` still finds XA60's ``Acceleration Mode``. A parameter given
    directly in canonical form is honoured as it stands.

    Parameters
    ----------
    parameter : str
        The rule's parameter, as written.

    Returns
    -------
    str
        The canonical name to match scan keys against.
    """
    from ..vocabulary import available as available_vocabularies
    from ..vocabulary import load_vocabulary

    for version in available_vocabularies():
        vocabulary = load_vocabulary(version)
        if parameter in set(vocabulary.aliases.values()):
            return parameter
        mapped = canonical_key(parameter, vocabulary)
        if mapped != normalize_key(parameter):
            return mapped
    return normalize_key(parameter)


def check_protocol(
    protocol: Mapping,
    policy: Policy,
    vocabulary: Vocabulary | None = None,
) -> PolicyReport:
    """Check every scan of a protocol against a policy.

    A rule applies only where its parameter is present. A scan that never
    prints the setting is not in violation of anything.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol carrying ``scans`` with their ``sections``.
    policy : Policy
        The preferences to apply.
    vocabulary : Vocabulary or None, optional
        The release's vocabulary, so a rule written with one release's label
        still matches a release that renamed the parameter.

    Returns
    -------
    PolicyReport
        Violations found, plus rules that matched nothing.
    """
    report = PolicyReport(
        source_file=protocol.get("source_file", ""),
        software_version=protocol.get("software_version"),
        policy=policy.name,
    )
    wanted = {rule_key(rule.parameter): rule for rule in policy.rules}
    used: set[str] = set()

    for scan in protocol.get("scans", []):
        for section, entries in scan.get("sections", {}).items():
            for key, value in entries.items():
                rule = wanted.get(canonical_key(key, vocabulary))
                if rule is None:
                    continue
                if rule.section and normalize_key(rule.section) != normalize_key(section):
                    continue
                used.add(rule.parameter)
                report.checked += 1
                ok, detail = _check_reading(rule, value)
                if ok:
                    continue
                report.violations.append(
                    Violation(
                        scan_index=scan.get("index", -1),
                        scan_name=scan.get("name", ""),
                        section=section,
                        key=key,
                        value=value,
                        expected=rule.expectation,
                        reason=rule.reason,
                        severity=rule.severity,
                        detail=detail,
                    )
                )

    report.unused_rules = sorted(r.parameter for r in policy.rules if r.parameter not in used)
    return report
