"""Search protocol collections by hierarchy, sequence identity and parameters.

``search`` evaluates already loaded documents; ``search_files`` also reads PDFs,
XA archives and parsed protocol JSON. Predicates use three-valued logic: missing,
conflicting or uninterpretable values cannot satisfy a comparison or its negation.
Explicit state predicates let callers find those cases.
"""

from __future__ import annotations

import json
import math
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .. import paths
from ..exar import read
from ..exar.inspect import ascconv_table
from ..pipeline import ParseOptions, parse_document
from . import archive_view
from .diff import canonical_key, normalize_key
from .flatten import flatten_sections
from .sequences import default_catalog, identify
from .vocabulary import Vocabulary, load_vocabulary

STATES = ("known", "missing", "unknown", "conflicting")
OPS = ("=", "==", "!=", ">", ">=", "<", "<=", "~", "state", "exists")
_NUMBER = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*?)\s*$")
_WHERE = re.compile(r"^(.+?)\s*(?<![=!<>~])(==|!=|>=|<=|>|<|=|~)(?![=!<>~])\s*(.+)$")
# Unit -> (dimension, multiplier to its base unit). Units outside this table
# are comparable only to the same spelling; unitless and unitful values differ.
_UNITS = {
    "s": ("time", 1.0),
    "sec": ("time", 1.0),
    "ms": ("time", 1e-3),
    "us": ("time", 1e-6),
    "µs": ("time", 1e-6),
    "μs": ("time", 1e-6),
    "min": ("time", 60.0),
    "mm": ("length", 1e-3),
    "cm": ("length", 1e-2),
    "m": ("length", 1.0),
    "hz": ("frequency", 1.0),
    "khz": ("frequency", 1e3),
}


def _pattern(text: str, value: str) -> bool:
    """Names match literally, ignoring case; ``re:`` opts into regex search.

    Parameters
    ----------
    text : str
        Literal selector or a regex prefixed with re:.
    value : str
        Candidate name.

    Returns
    -------
    bool
        Whether the candidate matches.
    """
    if text.startswith("re:"):
        return re.search(text[3:], value, re.I) is not None
    return text.casefold() == value.casefold()


def canonical_parameter_name(
    label: str, vocabulary: Vocabulary, aliases: Mapping[str, str]
) -> str:
    """Resolve a label through verified aliases and the release vocabulary.

    Parameters
    ----------
    label : str
        Recorded label or canonical parameter name.
    vocabulary : Vocabulary
        Release-specific label aliases.
    aliases : Mapping
        Caller-verified label aliases.

    Returns
    -------
    str
        Canonical parameter identity.
    """
    label = next((v for k, v in aliases.items() if k.casefold() == label.casefold()), label)
    if label in vocabulary.aliases.values():
        return label
    return vocabulary.canonical(label, normalize_key) or canonical_key(label, vocabulary)


@dataclass(frozen=True)
class Reading:
    """A parameter's raw readings and evidence, without selecting a conflict."""

    state: str
    values: tuple[Any, ...] = ()
    labels: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """Return a JSON-compatible representation.

        Returns
        -------
        dict
            JSON-compatible representation of this object.
        """
        return {
            "state": self.state,
            "values": list(self.values),
            "labels": list(self.labels),
            "sections": list(self.sections),
        }


def parameter_reading(
    scan: Mapping,
    label: str,
    vocabulary: Vocabulary | None = None,
    aliases: Mapping[str, str] | None = None,
) -> Reading:
    """Find every occurrence of a canonical label. Missing means not recorded.

    ``raw:`` requests an exact ASCCONV key, preserving array indices. Ordinary
    labels use the existing release vocabulary and confirmed abbreviations.

    Parameters
    ----------
    scan : Mapping
        Normalized scan with flat parameters or sections.
    label : str
        Printed or canonical label, or raw: followed by an exact stored key.
    vocabulary : Vocabulary or None, optional
        Release-specific aliases; omitted means generic normalization.
    aliases : Mapping or None, optional
        Caller-verified parameter aliases.

    Returns
    -------
    Reading
        All values and their state, labels and section evidence.
    """
    vocabulary, aliases = vocabulary or Vocabulary(""), aliases or {}
    if label.startswith("raw:") and "raw_parameters" in scan:
        table = scan["raw_parameters"]
        key = label[4:]
        if key not in table:
            return Reading("missing")
        value = table[key]
        state = "unknown" if value is None or str(value).strip() == "" else "known"
        return Reading(state, (value,), (key,), ("ASCCONV",))
    wanted = canonical_parameter_name(label, vocabulary, aliases)
    flat = scan.get("flat")
    if flat is None:
        flat = flatten_sections(scan.get("sections") or {})
    if not isinstance(flat, Mapping):
        raise ValueError("a scan's flat parameters must be an object")
    values, labels, sections = [], [], []
    conflict = False
    for key, entry in flat.items():
        matches = (
            key == label[4:]
            if label.startswith("raw:")
            else canonical_parameter_name(key, vocabulary, aliases) == wanted
        )
        if not matches:
            continue
        labels.append(key)
        if isinstance(entry, Mapping):
            if entry.get("conflict") and not isinstance(entry.get("values"), Mapping):
                raise ValueError(f"conflicting readings for {key!r} must name their sections")
            sections.extend(entry.get("sections", []))
            conflict |= bool(entry.get("conflict"))
            values.extend(
                entry.get("values", {}).values() if entry.get("conflict") else [entry.get("value")]
            )
        else:
            values.append(entry)
    if not labels:
        return Reading("missing")
    if conflict or len({str(v) for v in values if v is not None}) > 1:
        state = "conflicting"
    elif not values or any(v is None or str(v).strip() == "" for v in values):
        state = "unknown"
    else:
        state = "known"
    return Reading(state, tuple(values), tuple(labels), tuple(dict.fromkeys(sections)))


def _numeric(value: Any) -> tuple[float, str] | None:
    """Parse a finite number and normalize its unit.

    Parameters
    ----------
    value : Any
        Stored or displayed numeric value, optionally with a unit.

    Returns
    -------
    tuple or None
        Base-unit number and dimension, or None when not numeric.
    """
    match = _NUMBER.fullmatch(str(value))
    if not match:
        return None
    number, unit = float(match[1]), match[2].casefold()
    if not math.isfinite(number):
        return None
    dimension, scale = _UNITS.get(unit, (unit, 1.0))
    return number * scale, dimension


def _compare(actual: Any, op: str, expected: Any) -> bool | None:
    """Compare readings without guessing units or numeric meaning.

    Parameters
    ----------
    actual : Any
        Recorded parameter value.
    op : str
        Validated comparison operator.
    expected : Any
        Requested parameter value.

    Returns
    -------
    bool or None
        Comparison result, or None when the values are incomparable.
    """
    if op == "~":
        return re.search(str(expected), str(actual), re.I) is not None
    a, b = _numeric(actual), _numeric(expected)
    if a is not None and b is not None:
        if a[1] != b[1]:
            return None
        equal = math.isclose(a[0], b[0], rel_tol=1e-12, abs_tol=0.0)
        if op in ("=", "==", "!="):
            return not equal if op == "!=" else equal
        if equal:
            return op in (">=", "<=")
        return {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}[op](
            a[0], b[0]
        )
    if op in ("=", "==", "!="):
        equal = str(actual).strip().casefold() == str(expected).strip().casefold()
        return not equal if op == "!=" else equal
    return None


@dataclass(frozen=True)
class Predicate:
    """A parameter condition. Multiple occurrences must all satisfy it."""

    parameter: str
    op: str = "="
    value: Any = None

    def __post_init__(self) -> None:
        """Validate predicate fields.
        Returns
        -------
        None
            Validates the constructed object.

        Raises
        ------
        ValueError
            If the query fields or expression are invalid.
        """
        if not isinstance(self.parameter, str) or not self.parameter.strip():
            raise ValueError("a predicate requires a parameter name")
        if self.op not in OPS:
            raise ValueError(f"unknown predicate operator {self.op!r}")
        if self.op == "state" and self.value not in STATES:
            raise ValueError(f"state must be one of {', '.join(STATES)}")
        if self.op == "exists" and self.value is not None:
            raise ValueError("exists takes no value; use a not expression to test absence")
        if self.op not in ("state", "exists") and not isinstance(
            self.value, (str, int, float, bool)
        ):
            raise ValueError("comparison value must be text or a number")
        if self.op == "~":
            re.compile(str(self.value), re.I)


@dataclass(frozen=True)
class All:
    """All conditions must hold, retaining unknown results."""

    conditions: tuple[Expression, ...] = ()


@dataclass(frozen=True)
class AnyOf:
    """At least one condition must hold."""

    conditions: tuple[Expression, ...] = ()


@dataclass(frozen=True)
class Not:
    """Negate a known condition; unknown remains unknown."""

    condition: Expression


Expression = Predicate | All | AnyOf | Not


def expression_from_dict(payload: Mapping) -> Expression:
    """Read a Boolean expression without evaluating executable code.

    Parameters
    ----------
    payload : Mapping
        A predicate or nested all/any/not JSON object.

    Returns
    -------
    Expression
        Validated Boolean expression.

    Raises
    ------
    ValueError
        If the query fields or expression are invalid.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("query expressions must be JSON objects")
    for name, cls in (("all", All), ("any", AnyOf)):
        if name in payload:
            if set(payload) != {name} or not isinstance(payload[name], list):
                raise ValueError(f"{name} requires a list of expressions and no other keys")
            return cls(tuple(expression_from_dict(p) for p in payload[name]))
    if "not" in payload:
        if set(payload) != {"not"}:
            raise ValueError("not requires one expression and no other keys")
        return Not(expression_from_dict(payload["not"]))
    if set(payload) - {"parameter", "op", "value"} or "parameter" not in payload:
        raise ValueError("a predicate requires parameter, optional op and value")
    return Predicate(**payload)


def parse_predicate(text: str) -> Predicate:
    """Parse ``TR >= 2 s``, ``TE is missing`` or ``TR exists``.

    Parameters
    ----------
    text : str
        A comparison, state predicate or existence predicate.

    Returns
    -------
    Predicate
        Validated parameter predicate.

    Raises
    ------
    ValueError
        If the query fields or expression are invalid.
    """
    state = re.fullmatch(r"(.+?)\s+is\s+(known|missing|unknown|conflicting)", text.strip(), re.I)
    if state:
        return Predicate(state[1].strip(), "state", state[2].lower())
    exists = re.fullmatch(r"(.+?)\s+exists", text.strip(), re.I)
    if exists:
        return Predicate(exists[1].strip(), "exists")
    match = _WHERE.fullmatch(text.strip())
    if not match:
        raise ValueError(f"invalid condition {text!r}; use 'PARAM = VALUE' or 'PARAM is missing'")
    return Predicate(match[1].strip(), match[2], match[3].strip())


@dataclass(frozen=True)
class Query:
    """Hierarchy/name filters AND a Boolean parameter expression.

    Filters match names literally ignoring case, or regexes prefixed ``re:``.
    Region and exam use the conventional trailing Region/Exam/Protocol/Scan
    hierarchy; unavailable levels remain empty rather than guessed from names.
    Protocol selectors also accept contiguous trailing folder components,
    such as ``Frederick/UIC tests``.
    """

    region: str = ""
    exam: str = ""
    protocol: str = ""
    scan: str = ""
    path: str = ""
    sequence: str = ""
    family: str = ""
    vendor: str = ""
    where: Expression = field(default_factory=All)
    aliases: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate selectors, aliases and the top-level expression.
        Returns
        -------
        None
            Validates the constructed object.

        Raises
        ------
        ValueError
            If the query fields or expression are invalid.
        """
        if not isinstance(self.where, (Predicate, All, AnyOf, Not)):
            raise ValueError("where must be a Predicate, All, AnyOf or Not")
        for name in ("region", "exam", "protocol", "scan", "path", "sequence", "family", "vendor"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be text")
            if value.startswith("re:"):
                re.compile(value[3:], re.I)
        if not isinstance(self.aliases, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in self.aliases.items()
        ):
            raise ValueError("aliases must map parameter names to verified labels")

    @classmethod
    def from_dict(cls, payload: Mapping) -> Query:
        """Construct a query from selectors and a Boolean JSON expression.

        Parameters
        ----------
        payload : Mapping
            Selectors, optional aliases, and a Boolean where expression.

        Returns
        -------
        Query
            Validated query using the same representation as Python callers.

        Raises
        ------
        ValueError
            If the query fields or expression are invalid.
        """
        if not isinstance(payload, Mapping) or set(payload) - set(cls.__dataclass_fields__):
            raise ValueError("unknown query fields or query is not an object")
        data = dict(payload)
        if "where" in data:
            data["where"] = expression_from_dict(data["where"])
        return cls(**data)


def _evaluate(
    expression: Expression,
    scan: Mapping,
    vocabulary: Vocabulary,
    aliases: Mapping[str, str],
    evidence: dict,
) -> bool | None:
    """Evaluate an expression and retain the parameter evidence.

    Parameters
    ----------
    expression : Expression
        Parameter condition or Boolean expression.
    scan : Mapping
        Normalized scan to evaluate.
    vocabulary : Vocabulary
        Release vocabulary.
    aliases : Mapping
        Caller-verified aliases.
    evidence : dict
        Updated in place with every referenced parameter reading.

    Returns
    -------
    bool or None
        Result under three-valued Boolean logic.
    """
    if isinstance(expression, Predicate):
        reading = parameter_reading(scan, expression.parameter, vocabulary, aliases)
        evidence[expression.parameter] = reading.to_dict()
        if expression.op == "state":
            return reading.state == expression.value
        if expression.op == "exists":
            return reading.state != "missing"
        if reading.state != "known":
            return None
        results = [_compare(v, expression.op, expression.value) for v in reading.values]
        if any(r is None for r in results):
            evidence[expression.parameter]["comparison_unknown"] = True
            return None
        return all(results)
    if isinstance(expression, Not):
        result = _evaluate(expression.condition, scan, vocabulary, aliases, evidence)
        return None if result is None else not result
    if not isinstance(expression, (All, AnyOf)):
        raise ValueError("where must be a Predicate, All, AnyOf or Not")
    results = [_evaluate(p, scan, vocabulary, aliases, evidence) for p in expression.conditions]
    if isinstance(expression, All):
        return False if False in results else None if None in results else True
    return True if True in results else None if None in results else False


def _context(protocol: Mapping, scan: Mapping) -> dict:
    """Retain scan identity and decode its hierarchy.

    Parameters
    ----------
    protocol : Mapping
        Normalized protocol document.
    scan : Mapping
        Scan whose path and index are retained.

    Returns
    -------
    dict
        Source, software version, hierarchy, escaped path and scan index.
    """
    name = str(scan.get("name", ""))
    printed = str(scan.get("path", ""))
    parts = (
        [p for p in printed.split("\\") if p]
        if re.search(r"\\(?!/)", printed)
        else paths.split(printed)
    )
    if not parts or parts[-1] != name:
        parts = [name]
    hierarchy = protocol.get("hierarchy", {})
    return {
        "source_file": protocol.get("source_file", ""),
        "software_version": protocol.get("software_version"),
        "region": hierarchy.get("region", parts[-4] if len(parts) >= 4 else ""),
        "exam": hierarchy.get("exam", parts[-3] if len(parts) >= 3 else ""),
        "protocol": hierarchy.get(
            "protocol", protocol.get("program", parts[-2] if len(parts) >= 2 else "")
        ),
        "scan": name,
        "path": paths.join(parts),
        "scan_index": scan.get("index"),
    }


def _matches_hierarchy(query: Query, context: Mapping[str, Any]) -> bool:
    """Match cheap scan metadata, including a qualified protocol path.

    Parameters
    ----------
    query : Query
        Hierarchy selectors, with case-insensitive literals or regexes.
    context : Mapping of str to Any
        Scan identity produced by :func:`_context`.

    Returns
    -------
    bool
        Whether every hierarchy selector matches. Protocol selectors may
        name contiguous trailing components of the protocol's folder path.
    """
    for key in ("region", "exam", "protocol", "scan", "path"):
        selector = getattr(query, key)
        if not selector or _pattern(selector, str(context[key] or "")):
            continue
        if key == "protocol":
            regex = selector.startswith("re:")
            wanted = paths.split(selector[3:] if regex else selector, unescape=not regex)
            if regex:
                try:
                    for part in wanted:
                        re.compile(part, re.I)
                except re.error:
                    # A valid whole-name regex can contain a slash inside
                    # a group or character class without being a path.
                    return False
            if len(wanted) == 1 and _pattern(
                f"re:{wanted[0]}" if regex else wanted[0], str(context["protocol"] or "")
            ):
                continue
            folder = paths.split(context["path"])[:-1]
            if (
                len(wanted) > 1
                and len(folder) >= len(wanted)
                and all(
                    _pattern(f"re:{part}" if regex else part, name)
                    for part, name in zip(wanted, folder[-len(wanted) :])
                )
            ):
                continue
        return False
    return True


@dataclass
class QueryReport:
    """Results plus explicit counts for nonmatches, unknowns and failed inputs."""

    matches: list[dict] = field(default_factory=list)
    scanned: int = 0
    candidates: int = 0
    unknown: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-compatible representation.

        Returns
        -------
        dict
            JSON-compatible representation of this object.
        """
        return {
            "matches": self.matches,
            "match_count": sum(m["matched"] is True for m in self.matches),
            "scanned": self.scanned,
            "candidates": self.candidates,
            "unknown": self.unknown,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def search(
    protocols: Iterable[Mapping],
    query: Query | None = None,
    *,
    include_unknown: bool = False,
    vocabulary_dir: str | None = None,
) -> QueryReport:
    """Search normalized documents, retaining file/path/index and matched readings.

    Parameters
    ----------
    protocols : Iterable of Mapping
        Normalized protocol documents, such as Protocol.to_dict output.
    query : Query or None, optional
        Selectors and predicates; omitted selects every scan.
    include_unknown : bool, optional
        Include undecidable candidates with matched=None.
    vocabulary_dir : str or None, optional
        Directory containing release vocabulary overlays.

    Returns
    -------
    QueryReport
        Matching scans and evidence, including undecidable candidate counts.
    """
    query, report = query or Query(), QueryReport()
    vocabularies: dict[str, Vocabulary] = {}
    for protocol in protocols:
        if "programs" in protocol or not isinstance(protocol.get("scans"), list):
            raise ValueError(
                "query requires normalized protocol documents; use search_files for archives"
            )
        version = protocol.get("software_version") or ""
        if version not in vocabularies:
            vocabularies[version] = load_vocabulary(version, vocabulary_dir)
        vocabulary = vocabularies[version]
        report.warnings.extend(protocol.get("warnings", []))
        for position, scan in enumerate(protocol["scans"]):
            report.scanned += 1
            context = _context(protocol, scan)
            context["scan_index"] = scan.get("index", position)
            if not _matches_hierarchy(query, context):
                continue
            provenance = scan.get("provenance") or identify(scan, default_catalog()).to_dict()
            context.update(
                sequence=scan.get("header", {}).get("sequence", ""),
                family=provenance.get("family", ""),
                vendor=provenance.get("vendor", ""),
            )
            if any(
                getattr(query, key) and not _pattern(getattr(query, key), str(context[key] or ""))
                for key in ("sequence", "family", "vendor")
            ):
                continue
            report.candidates += 1
            evidence: dict = {}
            matched = _evaluate(query.where, scan, vocabulary, query.aliases, evidence)
            report.unknown += matched is None
            if matched is True or (matched is None and include_unknown):
                report.matches.append({**context, "matched": matched, "parameters": evidence})
    report.warnings = list(dict.fromkeys(report.warnings))
    return report


def load_protocols(
    path: str | Path, *, release: str = "auto", query: Query | None = None
) -> list[dict]:
    """Read every program in an archive, one PDF, or parsed protocol JSON.

    Parameters
    ----------
    path : str or Path
        PDF, exar1 archive, or explicitly named parsed protocol JSON.
    release : str, optional
        PDF release profile; auto detects it from the document.
    query : Query or None, optional
        Skip archive parameter decoding for programs rejected by hierarchy
        selectors. Rejected programs retain scan identities for counting;
        pass these documents to :func:`search` with the same query.

    Returns
    -------
    list of dict
        Normalized documents; one per program for an archive.
    """
    path = Path(path)
    source = path.as_posix()
    if path.suffix.lower() == ".exar1":
        archive = read(str(path))
        documents = []
        parents = archive.directory_parents if query is not None else None
        for program in archive.programs:
            steps = [s for s in program.steps if s.runs_a_protocol]
            if query is not None:
                folder = archive.path_of(program.instance, parents)
                document = {
                    "source_file": source,
                    "program": program.name,
                    "scans": [
                        {
                            "index": index,
                            "name": step.name,
                            "path": paths.join([*folder, step.name]),
                        }
                        for index, step in enumerate(steps)
                    ],
                }
                if not any(
                    _matches_hierarchy(query, _context(document, scan))
                    for scan in document["scans"]
                ):
                    documents.append(document)
                    continue
            document = archive_view.as_protocol(archive, program, source)
            for scan, step in zip(document["scans"], steps):
                scan["raw_parameters"] = ascconv_table(step.protocol.xprotocol)
            documents.append(document)
        return documents
    if path.suffix.lower() == ".pdf":
        document = parse_document(str(path), ParseOptions(version=release)).protocol.to_dict()
        document["source_file"] = source
        return [document]
    if path.suffix.lower() == ".json":
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        if (
            not isinstance(document, dict)
            or "programs" in document
            or document.get("format") == "exar1"
        ):
            raise ValueError(
                "expected parsed protocol JSON; use the original .exar1 for archive JSON"
            )
        if not isinstance(document.get("scans"), list) or any(
            not isinstance(s, dict) for s in document["scans"]
        ):
            raise ValueError("parsed protocol JSON requires a list of scan objects")
        # Report the actual input path, retaining embedded origin separately.
        document = {**document, "source_file": source}
        return [document]
    raise ValueError(f"unsupported query input {path}; expected PDF, .exar1 or parsed JSON")


def discover_inputs(inputs: Iterable[str | Path]) -> tuple[list[Path], list[dict[str, str]]]:
    """Discover unique protocol inputs while retaining traversal errors.

    Parameters
    ----------
    inputs : Iterable of str or Path
        Files or directories. Directory discovery includes PDFs and exar1 files.

    Returns
    -------
    tuple
        Unique paths in input order and explicit discovery errors.
    """
    found_paths, errors, seen = [], [], set()
    for target in inputs:
        target = Path(target)
        try:
            found = (
                sorted(
                    p
                    for p in target.rglob("*")
                    if p.is_file() and p.suffix.lower() in (".pdf", ".exar1")
                )
                if target.is_dir()
                else [target]
            )
            if not found:
                errors.append(
                    {"source_file": target.as_posix(), "error": "no PDF or exar1 files found"}
                )
            for path in found:
                identity = path.resolve()
                if identity not in seen:
                    found_paths.append(path)
                    seen.add(identity)
        except OSError as exc:
            errors.append({"source_file": target.as_posix(), "error": str(exc)})
    return found_paths, errors


def search_files(
    inputs: Iterable[str | Path],
    query: Query | None = None,
    *,
    release: str = "auto",
    include_unknown: bool = False,
    vocabulary_dir: str | None = None,
) -> QueryReport:
    """Search files/directories recursively, read each path once, keep batch errors.

    Directory discovery includes PDFs and exar1 archives. Parsed JSON is accepted
    when explicitly named, avoiding accidental ingestion of unrelated JSON reports
    or duplicate cached representations beside the original files.

    Parameters
    ----------
    inputs : Iterable of str or Path
        Files or directories; overlapping paths are read once.
    query : Query or None, optional
        Selectors and parameter predicates.
    release : str, optional
        Release profile for PDF inputs; default auto.
    include_unknown : bool, optional
        Retain undecidable candidates separately from true matches.
    vocabulary_dir : str or None, optional
        Release vocabulary overlay directory.

    Returns
    -------
    QueryReport
        Combined results with failed inputs explicitly recorded.
    """
    query = query or Query()
    inputs, errors = discover_inputs(inputs)
    report = QueryReport(errors=errors)
    for path in inputs:
        try:
            partial = search(
                load_protocols(path, release=release, query=query),
                query,
                include_unknown=include_unknown,
                vocabulary_dir=vocabulary_dir,
            )
        except Exception as exc:  # keep batch searches going and expose every failed input
            report.errors.append({"source_file": path.as_posix(), "error": str(exc)})
            continue
        report.matches.extend(partial.matches)
        report.scanned += partial.scanned
        report.candidates += partial.candidates
        report.unknown += partial.unknown
        report.warnings.extend(partial.warnings)
    report.warnings = list(dict.fromkeys(report.warnings))
    return report


def render(report: QueryReport) -> str:
    """Readable results with scan order, provenance and queried values.

    Parameters
    ----------
    report : QueryReport
        Search results, counts, warnings and input failures.

    Returns
    -------
    str
        Readable output with scan order and parameter evidence.
    """
    lines = []
    for match in report.matches:
        marker = "?" if match["matched"] is None else "*"
        lines.append(f"{marker} {match['source_file']}: [{match['scan_index']}] {match['path']}")
        lines.append(f"    {match['sequence']} | {match['vendor']} | {match['family']}")
        for label, reading in match["parameters"].items():
            lines.append(
                f"    {label}: {', '.join(map(str, reading['values'])) or '-'} ({reading['state']})"
            )
    data = report.to_dict()
    lines.append(
        f"{data['match_count']} matches; {report.unknown} unknown; {report.scanned} scans searched; {len(report.errors)} input errors"
    )
    lines.extend(f"warning: {w}" for w in report.warnings)
    lines.extend(f"error: {e['source_file']}: {e['error']}" for e in report.errors)
    return "\n".join(lines)
