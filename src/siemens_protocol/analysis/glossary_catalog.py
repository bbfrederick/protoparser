"""Build the packaged sequence parameter index from normalized PDF snapshots.

Run ``python -m siemens_protocol.analysis.glossary_catalog tests/golden --out
src/siemens_protocol/analysis/glossary_catalog.json`` after updating the corpus.
Only names and their printed sections are retained; scan values are not defaults.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence

from .generate import mappings
from .query import All, Query, _pattern, canonical_parameter_name
from .sequences import Catalog, default_catalog, identify
from .vocabulary import Vocabulary, load_vocabulary

if TYPE_CHECKING:
    from .glossary import GlossaryReport


def _mapping_support(
    sequence: Mapping,
    label: str,
    vocabulary: Vocabulary,
    aliases: Mapping[str, str],
) -> dict:
    """Describe characterized mappings without assuming a scan build or mode.

    Parameters
    ----------
    sequence : Mapping
        Packaged sequence identity and aliases.
    label : str
        Canonical printed parameter identity.
    vocabulary : Vocabulary
        Release parameter vocabulary.
    aliases : Mapping
        Verified parameter aliases.

    Returns
    -------
    dict
        Characterized mappings with explicit applicability conditions.
    """
    applicable = [
        m
        for m in mappings.MAPPINGS
        if canonical_parameter_name(m.label, vocabulary, aliases) == label
        and (not m.sequences or set(m.sequences) & set(sequence["aliases"]))
    ]
    writable = [m for m in applicable if m.ascconv_key and not m.read_only]
    return {
        "status": "mapped_conditional" if writable else "unsupported",
        "modifiable": bool(writable),
        "characterization_needed": not writable and not any(m.read_only for m in applicable),
        "reason": (
            "Characterized mappings require the stated sequence/build/mode conditions."
            if writable
            else "No characterized writable mapping for this sequence and displayed label."
        ),
        "mappings": [
            {
                "write_name": m.label,
                "raw_key": m.ascconv_key,
                "choices": [c for c, _ in m.choices]
                or (["Off", "On"] if m.bit is not None else []),
                "scale": m.scale,
                "offset": m.offset,
                "basis": m.basis,
                "bit": m.bit,
                "builds": list(m.builds),
                "sequences": list(m.sequences),
                "when": list(m.when) if m.when else None,
                "sign_from": m.sign_from,
                "evidence": m.evidence,
            }
            for m in writable
        ],
    }


def lookup(
    query: Query | None = None,
    *,
    release: str = "XA60",
    vocabulary_dir: str | None = None,
) -> GlossaryReport:
    """Select known sequence variables from the packaged PDF name index.

    Parameters
    ----------
    query : Query or None, optional
        Sequence, known scan name, vendor or family filters. No filters lists sequences.
    release : str, optional
        Catalog release, XA60 by default; auto also selects XA60.
    vocabulary_dir : str or None, optional
        Release vocabulary overlays.

    Returns
    -------
    GlossaryReport
        Known names and conditional writer mapping support.

    Raises
    ------
    ValueError
        If file-specific hierarchy or parameter-value filters are supplied.
    """
    from .glossary import GlossaryReport

    query = query or Query()
    if any((query.region, query.exam, query.protocol, query.path)):
        raise ValueError("region, exam, protocol and path selectors require an input file")
    if not isinstance(query.where, All) or query.where.conditions:
        raise ValueError("parameter-value conditions require an input file")
    release = "XA60" if release == "auto" else release
    payload = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported sequence glossary catalog version")
    vocabulary = load_vocabulary(release, vocabulary_dir)
    report = GlossaryReport(mode="catalog")
    selected = bool(query.sequence or query.scan or query.vendor or query.family)
    for sequence in payload["sequences"]:
        if sequence["software_version"] != release:
            continue
        if query.sequence and not any(_pattern(query.sequence, n) for n in sequence["aliases"]):
            continue
        if query.scan and not any(_pattern(query.scan, n) for n in sequence["scans"]):
            continue
        if any(
            getattr(query, name) and not _pattern(getattr(query, name), sequence[name] or "")
            for name in ("family", "vendor")
        ):
            continue
        report.sequences.append({k: v for k, v in sequence.items() if k != "parameters"})
        if not selected:
            continue
        known: dict = {}
        for parameter in sequence["parameters"]:
            name = canonical_parameter_name(parameter["name"], vocabulary, query.aliases)
            entry = known.setdefault(
                name,
                {
                    "name": parameter["name"],
                    "query_name": name,
                    "software_version": release,
                    "sequence": sequence["sequence"],
                    "labels": [],
                    "sections": [],
                    "values": [],
                    "states": {},
                    "examples": [],
                    "occurrences": 0,
                    "modifiable_occurrences": 0,
                    "modification_counts": {},
                },
            )
            if parameter["name"] not in entry["labels"]:
                entry["labels"].append(parameter["name"])
            entry["sections"] = sorted(set(entry["sections"]) | set(parameter["sections"]))
        for name, entry in known.items():
            support = _mapping_support(sequence, name, vocabulary, query.aliases)
            entry.update(
                modification=support,
                modifiable=support["modifiable"],
                characterization_needed=support["characterization_needed"],
            )
            report.parameters.append(entry)
    report.parameters.sort(key=lambda e: (e["sequence"], e["query_name"].casefold()))
    return report


def build_catalog(protocols: Iterable[Mapping], catalog: Catalog | None = None) -> dict:
    """Collect known printed variables by sequence identity and release.

    Parameters
    ----------
    protocols : Iterable of Mapping
        Normalized documents parsed from PDF files.
    catalog : Catalog or None, optional
        Current sequence signatures; defaults to the packaged catalog.

    Returns
    -------
    dict
        Deterministic parameter-name index with corpus coverage evidence.
    """
    catalog = catalog or default_catalog()
    signatures = {s.id: s for s in catalog.signatures}
    groups: dict = {}
    for document in protocols:
        release = document.get("software_version") or "unknown"
        source = Path(document.get("source_file") or "unknown").name
        for scan in document.get("scans", []):
            identity = identify(scan, catalog)
            binary = scan.get("header", {}).get("sequence", "")
            signature = signatures.get(identity.signature)
            if signature:
                sequence = (
                    binary
                    if binary in signature.binaries
                    else signature.binaries[0] if len(signature.binaries) == 1 else signature.id
                )
                aliases = {sequence, signature.id, *signature.binaries}
            elif binary:
                sequence, aliases = binary, {binary}
            else:
                continue
            key = (release, sequence)
            group = groups.setdefault(
                key,
                {
                    "software_version": release,
                    "sequence": sequence,
                    "aliases": set(),
                    "signature": identity.signature,
                    "vendor": identity.vendor,
                    "family": identity.family,
                    "scans": set(),
                    "sources": set(),
                    "observed_scan_count": 0,
                    "parameters": {},
                },
            )
            group["aliases"].update(aliases)
            group["scans"].add(scan.get("name", ""))
            group["sources"].add(source)
            group["observed_scan_count"] += 1
            for section, parameters in (scan.get("sections") or {}).items():
                if section in ("ASCCONV", "Preview"):
                    continue
                for label in parameters:
                    group["parameters"].setdefault(label, set()).add(section)
    sequences = []
    for key in sorted(groups):
        group = groups[key]
        group["parameters"] = [
            {"name": label, "sections": sorted(sections)}
            for label, sections in sorted(group["parameters"].items())
        ]
        for field in ("aliases", "scans", "sources"):
            group[field] = sorted(group[field])
        sequences.append(group)
    return {
        "schema_version": 1,
        "description": "Known printed parameter names from the PDF corpus; not an exhaustive vendor specification.",
        "sequences": sequences,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate the packaged index from parsed PDF corpus snapshots.

    Parameters
    ----------
    argv : Sequence of str or None, optional
        Snapshot directory and output path; defaults to process arguments.

    Returns
    -------
    int
        Zero after writing the deterministic index.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    documents = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.snapshots.glob("*.json"))
    ]
    if not documents:
        parser.error("no parsed protocol snapshots found")
    payload = build_catalog(documents)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(payload['sequences'])} sequence/release entries written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
