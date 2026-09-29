"""Look up known sequence variables or inspect variables in protocol files.

This is an inventory of the selected files, not a specification of every control
a sequence could expose. Writer support is checked using the same sequence,
build and acquisition-mode gates as the existing archive writer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from ..exar import read
from ..exar.archive import Protocol as ArchiveProtocol
from .flatten import flatten_sections
from .generate import mappings
from .query import (
    Query,
    Reading,
    _context,
    canonical_parameter_name,
    discover_inputs,
    load_protocols,
    parameter_reading,
    search,
)
from .vocabulary import load_vocabulary


@dataclass
class GlossaryReport:
    """Known or observed variables, coverage, and explicit failed inputs."""

    parameters: list[dict] = field(default_factory=list)
    scanned: int = 0
    selected: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "files"
    sequences: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-compatible inventory.

        Returns
        -------
        dict
            Parameters, scan counts, warnings and failed inputs.
        """
        return {
            "mode": self.mode,
            "sequences": self.sequences,
            "parameters": self.parameters,
            "parameter_count": len(self.parameters),
            "scanned": self.scanned,
            "selected": self.selected,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def sequence_glossary(
    query: Query | None = None,
    *,
    release: str = "XA60",
    vocabulary_dir: str | None = None,
) -> GlossaryReport:
    """Look up package sequence knowledge without requiring protocol files.

    Parameters
    ----------
    query : Query or None, optional
        Sequence, known scan name, vendor or family selectors. Omitted lists sequences.
    release : str, optional
        Catalog software release; XA60 is the default, including auto.
    vocabulary_dir : str or None, optional
        Parameter vocabulary overlays.

    Returns
    -------
    GlossaryReport
        Known printed variables and characterized mapping conditions.
    """
    from .glossary_catalog import lookup

    return lookup(query, release=release, vocabulary_dir=vocabulary_dir)


def modification_support(
    protocol: ArchiveProtocol | None, label: str, *, raw: bool = False
) -> dict:
    """Describe the writer mapping without modifying a protocol.

    Parameters
    ----------
    protocol : ArchiveProtocol or None
        Concrete archive scan, or None for PDF and parsed JSON inputs.
    label : str
        Printed parameter label or exact raw key.
    raw : bool, optional
        Whether this is a raw assignment rather than a displayed parameter.

    Returns
    -------
    dict
        Mapping status, reason, and encoding details when supported.
    """
    if raw:
        return {
            "status": "raw_only",
            "modifiable": False,
            "characterization_needed": False,
            "reason": "Searchable stored key; user editing requires a verified PDF-to-ASCCONV mapping.",
        }
    if protocol is None:
        return {
            "status": "unverified",
            "modifiable": False,
            "characterization_needed": not any(
                m.label.casefold() == label.casefold() for m in mappings.MAPPINGS
            ),
            "reason": "The original exar1 scan is required to check writer support.",
        }
    mapping, reason = mappings.resolve(protocol, label)
    if mapping is None:
        derived = any(
            m.label.casefold() == label.casefold() and m.read_only for m in mappings.MAPPINGS
        )
        return {
            "status": "unsupported",
            "modifiable": False,
            "characterization_needed": not derived,
            "reason": reason,
        }
    return {
        "status": "mapped",
        "modifiable": True,
        "characterization_needed": False,
        "reason": "The existing writer resolves this label for this scan; new values still require validation.",
        "write_name": label,
        "raw_key": mapping.ascconv_key,
        "choices": [choice for choice, _ in mapping.choices]
        or (["Off", "On"] if mapping.bit is not None else []),
        "scale": mapping.scale,
        "offset": mapping.offset,
        "basis": mapping.basis,
        "bit": mapping.bit,
        "builds": list(mapping.builds),
        "evidence": mapping.evidence,
    }


def _add_parameter(
    entries: dict,
    context: dict,
    label: str,
    query_name: str,
    reading: Reading,
    support: dict,
) -> None:
    """Aggregate a reading while retaining scan-specific modification support.

    Parameters
    ----------
    entries : dict
        Inventory entries updated in place.
    context : dict
        Selected scan identity from the query API.
    label : str
        Recorded parameter name.
    query_name : str
        Canonical or exact raw predicate name.
    reading : Reading
        Values, sections and state.
    support : dict
        Writer mapping checked against this scan.

    Returns
    -------
    None
    """
    key = (context["software_version"], context["sequence"], query_name)
    entry = entries.setdefault(
        key,
        {
            "name": label,
            "query_name": query_name,
            "software_version": context["software_version"],
            "sequence": context["sequence"],
            "labels": [],
            "sections": [],
            "values": [],
            "states": {},
            "occurrences": 0,
            "modifiable_occurrences": 0,
            "characterization_needed": False,
            "modification_counts": {},
            "examples": [],
        },
    )
    for field_name, values in (
        ("labels", reading.labels),
        ("sections", reading.sections),
        ("values", reading.values),
    ):
        for value in values:
            if value not in entry[field_name]:
                entry[field_name].append(value)
    entry["occurrences"] += 1
    entry["modifiable_occurrences"] += support["modifiable"]
    entry["characterization_needed"] |= support["characterization_needed"]
    entry["states"][reading.state] = entry["states"].get(reading.state, 0) + 1
    status = support["status"]
    entry["modification_counts"][status] = entry["modification_counts"].get(status, 0) + 1
    example = {**context, "reading": reading.to_dict(), "modification": support}
    # Preserve an example for each distinct mapping outcome/build restriction.
    signature = json.dumps(support, sort_keys=True)
    if all(json.dumps(e["modification"], sort_keys=True) != signature for e in entry["examples"]):
        entry["examples"].append(example)


def glossary_files(
    inputs: Iterable[str | Path],
    query: Query | None = None,
    *,
    include_raw: bool = False,
    release: str = "auto",
    vocabulary_dir: str | None = None,
) -> GlossaryReport:
    """Inventory variables for scans selected by the shared query API.

    Parameters
    ----------
    inputs : Iterable of str or Path
        Protocol files or recursively searched directories.
    query : Query or None, optional
        Scan, sequence or hierarchy selectors; omitted selects all scans.
    include_raw : bool, optional
        Include exact raw ASCCONV assignments in addition to displayed labels.
    release : str, optional
        PDF release profile, auto by default.
    vocabulary_dir : str or None, optional
        Release vocabulary overlay directory.

    Returns
    -------
    GlossaryReport
        Observed names, examples, reading states and writer mapping support.
    """
    query = query or Query()
    paths, errors = discover_inputs(inputs)
    report = GlossaryReport(errors=errors)
    entries: dict = {}
    for path in paths:
        # Build each file's inventory separately so a failed file cannot leave
        # partially included parameters beside an input error.
        partial: dict = {}
        try:
            documents = load_protocols(path, release=release)
            archive = read(str(path)) if path.suffix.lower() == ".exar1" else None
            scanned, selected, warnings = 0, 0, []
            for program_index, document in enumerate(documents):
                result = search([document], query, vocabulary_dir=vocabulary_dir)
                scanned += result.scanned
                selected += len(result.matches)
                warnings.extend(result.warnings)
                matches = {(m["scan_index"], m["path"]): m for m in result.matches}
                vocabulary = load_vocabulary(
                    document.get("software_version") or "", vocabulary_dir
                )
                steps = (
                    [s for s in archive.programs[program_index].steps if s.runs_a_protocol]
                    if archive
                    else []
                )
                for position, scan in enumerate(document["scans"]):
                    context = _context(document, scan)
                    identity = (scan.get("index", position), context["path"])
                    if identity not in matches:
                        continue
                    context = matches[identity]
                    archive_scan = steps[position].protocol if archive else None
                    internal_preview = (
                        {
                            e.path
                            for e in archive_scan.preview.values()
                            if not (e.label or "").strip()
                        }
                        if archive_scan
                        else set()
                    )
                    flat = scan.get("flat")
                    if flat is None:
                        flat = flatten_sections(scan.get("sections") or {})
                    if not isinstance(flat, Mapping):
                        raise ValueError("a scan's flat parameters must be an object")
                    observed: set[str] = set()
                    for label, value in flat.items():
                        if label in internal_preview:
                            continue
                        sections = value.get("sections", []) if isinstance(value, Mapping) else []
                        if sections and all(section == "ASCCONV" for section in sections):
                            continue
                        name = canonical_parameter_name(label, vocabulary, query.aliases)
                        if name in observed:
                            continue
                        observed.add(name)
                        reading = parameter_reading(scan, label, vocabulary, query.aliases)
                        support = modification_support(archive_scan, label)
                        _add_parameter(partial, context, label, name, reading, support)
                    if include_raw:
                        raw = scan.get("raw_parameters", {})
                        # Parsed JSON can retain unmapped assignments in its sections.
                        if not raw:
                            raw = (scan.get("sections") or {}).get("ASCCONV", {})
                        for key in raw:
                            if key.endswith(" (unlabelled bits)"):
                                continue
                            name = "raw:" + key
                            reading = parameter_reading(scan, name, vocabulary)
                            _add_parameter(
                                partial,
                                context,
                                key,
                                name,
                                reading,
                                modification_support(archive_scan, key, raw=True),
                            )
        except Exception as exc:
            report.errors.append({"source_file": path.as_posix(), "error": str(exc)})
            continue
        report.scanned += scanned
        report.selected += selected
        report.warnings.extend(warnings)
        for key, incoming in partial.items():
            if key not in entries:
                entries[key] = incoming
                continue
            target = entries[key]
            for name in ("labels", "sections", "values", "examples"):
                for value in incoming[name]:
                    if value not in target[name]:
                        target[name].append(value)
            target["occurrences"] += incoming["occurrences"]
            target["modifiable_occurrences"] += incoming["modifiable_occurrences"]
            target["characterization_needed"] |= incoming["characterization_needed"]
            for name in ("states", "modification_counts"):
                for value, count in incoming[name].items():
                    target[name][value] = target[name].get(value, 0) + count
    report.parameters = sorted(
        entries.values(),
        key=lambda e: (e["software_version"] or "", e["sequence"], e["query_name"].casefold()),
    )
    report.warnings = list(dict.fromkeys(report.warnings))
    return report


def render(report: GlossaryReport) -> str:
    """Render names, sample values and modification mapping status.

    Parameters
    ----------
    report : GlossaryReport
        Inventory to display.

    Returns
    -------
    str
        Readable glossary, grouped by software version and sequence.
    """
    lines = [
        "Observed parameters; user editing requires a verified PDF-to-ASCCONV mapping.",
        "Mapped: writer support checked for the archive scan; unverified/unsupported/raw_only: not user editable.",
    ]
    if report.mode == "catalog":
        lines = [
            "Known sequence variables from the packaged PDF catalog.",
            "Mapped_conditional: characterized for the stated sequence/build/mode; unsupported: not user editable.",
        ]
        if not report.parameters:
            lines.extend(
                f"  {s['sequence']} | {s['software_version']} | {s['family']}"
                for s in report.sequences
            )
    group = None
    for entry in report.parameters:
        identity = (entry["software_version"], entry["sequence"])
        if identity != group:
            lines.append(
                f"\n{identity[0] or 'unknown release'} | {identity[1] or 'unnamed sequence'}"
            )
            group = identity
        values = ", ".join(map(str, entry["values"][:4])) or "-"
        if report.mode == "catalog":
            support = entry["modification"]
            lines.append(f"  {entry['name']} [{entry['query_name']}] ({support['status']})")
            for mapping in support["mappings"]:
                gates = "; builds: " + ", ".join(mapping["builds"]) if mapping["builds"] else ""
                if mapping["when"]:
                    gates += f"; when {mapping['when'][0]} = {mapping['when'][1]}"
                lines.append(f"    write: {mapping['write_name']} -> {mapping['raw_key']}{gates}")
        else:
            counts = ", ".join(
                f"{status}: {n}" for status, n in entry["modification_counts"].items()
            )
            lines.append(f"  {entry['name']} [{entry['query_name']}] = {values} ({counts})")
        for example in entry["examples"]:
            support = example["modification"]
            if support["status"] == "mapped":
                choices = (
                    f"; choices: {', '.join(support['choices'])}" if support["choices"] else ""
                )
                lines.append(
                    f"    write: {support['write_name']} -> {support['raw_key']}{choices}"
                )
            elif support["status"] == "unsupported":
                lines.append(f"    {support['reason']}")
    lines.append(
        f"\n{len(report.parameters)} variables; {len(report.sequences)} sequences in catalog"
        if report.mode == "catalog"
        else f"\n{len(report.parameters)} variables; {report.selected} scans selected; {len(report.errors)} input errors"
    )
    lines.extend(f"warning: {w}" for w in report.warnings)
    lines.extend(f"error: {e['source_file']}: {e['error']}" for e in report.errors)
    return "\n".join(lines)
