"""Compare a submitted archive with a scanner re-export using stable identities.

This deliberately compares the submitted file, not its pre-edit donor. All
unmapped ASCCONV data, execution metadata, and owned add-in documents participate.
Only characterized save churn is excused automatically. Console observations are
separate evidence: faithful re-export alone does not prove a scan is runnable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import paths
from ..exar import ascconv, read
from ..exar.archive import Archive, Instance, Program
from ..exar.inspect import ascconv_table
from . import address
from .validation import validate

DERIVED_KEYS = frozenset({"lScanTimeSec", "lTotalScanTimeSec"})


def _archive(value: Archive | str | Path) -> Archive:
    """Load a path or retain a supplied archive.

    Parameters
    ----------
    value : Archive or str or Path
        Input archive.

    Returns
    -------
    Archive
        Read-only input to the comparison.
    """
    return value if isinstance(value, Archive) else read(str(value))


def _program(archive: Archive, selector: str | None) -> Program:
    """Require an unambiguous program, including when names repeat.

    Parameters
    ----------
    archive : Archive
        Archive containing the program.
    selector : str or None
        Qualified address, or omitted for a sole program.

    Returns
    -------
    Program
        Selected program.
    """
    if selector is None and len(archive.programs) == 1:
        return archive.programs[0]
    return address.resolve(
        selector or "",
        [(archive.path_of(p.instance), p) for p in archive.programs],
        what="protocol",
        source="scanner round trip",
    )


def checklist(archive: Archive | str | Path, program: str | None = None) -> list[dict[str, Any]]:
    """Create console-observation records bound to scan order and sequences.

    Parameters
    ----------
    archive : Archive or str or Path
        Submitted archive.
    program : str or None, optional
        Program address; required for multi-program inputs.

    Returns
    -------
    list of dict
        Change each status to ``runnable`` or ``greyed_out`` after observing the
        console. Positions include pauses and names need not be unique.
    """
    chosen = _program(_archive(archive), program)
    return [
        {
            "step_index": i,
            "scan": step.name,
            "sequences": [ascconv.sequence_of(p) for p in step.protocols],
            "protocol_hashes": [p.instance.content_hash for p in step.protocols],
            "status": "not_tested",
        }
        for i, step in enumerate(chosen.steps)
        if step.runs_a_protocol
    ]


def _canonical(value: Any, identities: Mapping[str, str]) -> Any:
    """Remove serialization reference numbering, retaining all domain fields.

    Parameters
    ----------
    value : Any
        Structured content document.
    identities : mapping
        Known graph GUIDs mapped to stable ownership/step positions.

    Returns
    -------
    Any
        JSON-compatible representation with XML attributes ordered semantically.
    """
    references: dict[str, Any] = {}

    def collect(one: Any) -> None:
        """Index Newtonsoft objects.

        Parameters
        ----------
        one : Any
            Subtree to visit.

        Returns
        -------
        None
        """
        if isinstance(one, dict):
            if "$id" in one:
                references[str(one["$id"])] = one
            for child in one.values():
                collect(child)
        elif isinstance(one, list):
            for child in one:
                collect(child)

    def clean(one: Any, active: tuple[str, ...] = ()) -> Any:
        """Expand references and normalize only known identities.

        Parameters
        ----------
        one : Any
            Subtree to normalize.
        active : tuple of str, optional
            Reference expansion stack, guarding cycles.

        Returns
        -------
        Any
            Canonical subtree.
        """
        if isinstance(one, dict):
            if "$ref" in one:
                ref = str(one["$ref"])
                if ref not in references or ref in active:
                    raise ValueError("unresolved or cyclic serialization reference in content")
                return clean(references[ref], active + (ref,))
            return {identities.get(k, k): clean(v, active) for k, v in one.items() if k != "$id"}
        if isinstance(one, list):
            return [clean(v, active) for v in one]
        if isinstance(one, str):
            if one in identities:
                return identities[one]
            if one.lstrip().startswith("<"):
                try:
                    return _xml(ET.fromstring(one))
                except ET.ParseError:
                    pass
            if ascconv.ascconv_bounds(one)[0] >= 0:
                return {"embedded_ascconv": ascconv_table(one)}
        return one

    collect(value)
    return clean(value)


def _xml(element: ET.Element) -> list[Any]:
    """Represent XML without depending on attribute order or namespace prefixes.

    Parameters
    ----------
    element : Element
        XML element.

    Returns
    -------
    list
        Tag, attributes, text, children, and tail, retaining configuration values.
    """
    return [
        element.tag,
        dict(sorted(element.attrib.items())),
        (element.text or "").strip(),
        [_xml(c) for c in element],
        (element.tail or "").strip(),
    ]


def _snapshot(archive: Archive, program: Program) -> dict[str, Any]:
    """Capture execution and owned content using running-order identities.

    Parameters
    ----------
    archive : Archive
        Archive to inspect.
    program : Program
        Owning program, scoping repeated ObjectIds.

    Returns
    -------
    dict
        Stable graph, complete ASCCONV assignments, Preview, and build stamps.
    """
    rows = {str(r["Id"]): r for r in archive.container.rows("Instance")}
    comments = {
        str(r["Id"]): r.get("CommentElement_id") for r in archive.container.rows("Element")
    }
    nodes: dict[str, Instance] = {}
    identities: dict[str, str] = {}

    def visit(node: Instance, token: str) -> None:
        """Walk owned children and labels, excluding upward references.

        Parameters
        ----------
        node : Instance
            Owned node.
        token : str
            Stable ownership address.

        Returns
        -------
        None
        """
        if node.element_id in identities:
            return
        nodes[token] = node
        for guid in (node.id, node.element_id, node.object_id):
            identities[guid] = token
        counts: dict[str, int] = {}
        for element in node.children:
            child = archive.by_element[element]
            counts[child.kind] = counts.get(child.kind, 0) + 1
            visit(child, f"{token}/{child.kind}[{counts[child.kind] - 1}]")
        row = rows[node.id]
        for key, element in (
            ("label", row.get("LabelElement_id")),
            ("description", row.get("DescriptionElement_id")),
            ("comment", comments.get(node.element_id)),
        ):
            if element:
                visit(archive.by_element[str(element)], f"{token}/{key}")

    # Pre-register step roots in running order, before storage-order children.
    for i, step in enumerate(program.steps):
        visit(step.instance, f"steps[{i}]")
    visit(program.instance, "program")
    result: dict[str, Any] = {
        "order": [{"name": s.name, "kind": s.instance.kind} for s in program.steps],
        "nodes": {},
    }
    protocols = {p.instance.element_id: p for s in program.steps for p in s.protocols}
    for token, node in nodes.items():
        document = dict(archive.document(node))
        if node.element_id in protocols:
            protocol = protocols[node.element_id]
            document.pop("Data", None)
            document["ascconv"] = ascconv_table(protocol.xprotocol)
            document["sequence_stamp"] = ascconv.sequence_stamp(protocol)
        result["nodes"][token] = {
            "kind": node.kind,
            "children": sorted(identities.get(e, e) for e in node.children),
            "document": _canonical(document, identities),
        }
    return result


def _same(left: Any, right: Any, *, numeric_literals: bool = False) -> bool:
    """Compare exact values, decoding numeric literals only in ASCCONV.

    Parameters
    ----------
    left, right : Any
        Compared values.
    numeric_literals : bool, optional
        Permit equivalent finite decimal/hex encodings for ASCCONV leaves.
        Metadata text, including numeric-looking scan names, remains exact.

    Returns
    -------
    bool
        Exact equality, or equivalent numeric literals when explicitly enabled.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if left == right:
        return True
    if not numeric_literals:
        return False
    try:
        a, b = str(left), str(right)
        x = Decimal(int(a, 16)) if a.lower().startswith(("0x", "-0x")) else Decimal(a)
        y = Decimal(int(b, 16)) if b.lower().startswith(("0x", "-0x")) else Decimal(b)
        return x.is_finite() and y.is_finite() and x == y
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return False


def _differences(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    """Find changed leaves, distinguishing absent fields from explicit nulls.

    Parameters
    ----------
    left, right : Any
        Compared snapshots.
    path : str, optional
        Starting address.

    Returns
    -------
    list of dict
        Located before/after values and presence evidence.
    """
    if isinstance(left, dict) and isinstance(right, dict):
        found = []
        for key in sorted(set(left) | set(right)):
            location = f"{path}/{key}"
            if key in left and key in right:
                found.extend(_differences(left[key], right[key], location))
            else:
                found.append(
                    {
                        "path": location,
                        "before": left.get(key),
                        "after": right.get(key),
                        "before_present": key in left,
                        "after_present": key in right,
                    }
                )
        return found
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [
            d
            for i, (a, b) in enumerate(zip(left, right))
            for d in _differences(a, b, f"{path}[{i}]")
        ]
    return (
        []
        if _same(
            left,
            right,
            numeric_literals="/ascconv/" in path or "/embedded_ascconv/" in path,
        )
        else [
            {
                "path": path,
                "before": left,
                "after": right,
                "before_present": True,
                "after_present": True,
            }
        ]
    )


def compare(
    sent: Archive | str | Path,
    returned: Archive | str | Path,
    *,
    sent_program: str | None = None,
    returned_program: str | None = None,
    observations: Sequence[Mapping[str, Any]] | None = None,
    allow_derived: bool = False,
) -> dict[str, Any]:
    """Check a scanner re-export and optional console observations.

    Parameters
    ----------
    sent, returned : Archive or str or Path
        Submitted file and scanner re-export, not a donor and edited file.
    sent_program, returned_program : str or None, optional
        Explicit program addresses when either archive has several programs.
    observations : sequence of mapping or None, optional
        Completed records from :func:`checklist`, bound to submitted step
        positions, names, and sequences. Missing records remain unobserved.
    allow_derived : bool, optional
        Permit changes only in the two characterized derived scan-time fields.
        They are always listed separately and never treated as save churn.

    Returns
    -------
    dict
        Offline validation, changed/churn/derived fields, coverage, and distinct
        preservation and observed-runnability verdicts.

    Raises
    ------
    ValueError
        If program selection, release support, or observations are ambiguous.
    """
    a, b = _archive(sent), _archive(returned)
    if observations is not None and (
        not isinstance(observations, Sequence)
        or isinstance(observations, (str, bytes))
        or any(not isinstance(item, Mapping) for item in observations)
    ):
        raise ValueError("observations must be a sequence of checklist records")
    if a.major_version != "VA60A" or b.major_version != "VA60A":
        raise ValueError("scanner round-trip comparison currently supports XA60 (VA60A) only")
    ap, bp = _program(a, sent_program), _program(b, returned_program)
    av, bv = validate(a, program=ap), validate(b, program=bp)
    try:
        before, after = _snapshot(a, ap), _snapshot(b, bp)
    except (KeyError, ValueError, IndexError) as exc:
        return {
            "preserved": False,
            "scanner_confirmed": False,
            "sent_validation": av.to_dict(),
            "returned_validation": bv.to_dict(),
            "changes": [],
            "churn": [],
            "derived": [],
            "preview": [],
            "observations": [],
            "comparison_performed": False,
            "comparison_error": str(exc),
        }
    changed, churn, derived, preview = [], [], [], []
    for difference in _differences(before, after):
        path = difference["path"]
        key = path.rsplit("/", 1)[-1]
        in_ascconv = "/ascconv/" in path
        values = [
            difference[side] for side in ("before", "after") if difference[side + "_present"]
        ]
        if "/document/Preview/" in path:
            preview.append(difference)
        elif in_ascconv and all(ascconv.is_churn(key, v) for v in values):
            churn.append(difference)
        elif in_ascconv and key in DERIVED_KEYS:
            derived.append(difference)
        else:
            changed.append(difference)
    records = checklist(a, sent_program)
    expected = {r["step_index"]: r for r in records}
    seen = set()
    for item in observations or ():
        i = item.get("step_index")
        if isinstance(i, bool) or not isinstance(i, int) or i not in expected or i in seen:
            raise ValueError("observation step_index is unknown or repeated")
        if set(item) != {"step_index", "scan", "sequences", "protocol_hashes", "status"}:
            raise ValueError(
                "observation must carry step_index, scan, sequences, protocol_hashes, and status"
            )
        if any(item[k] != expected[i][k] for k in ("scan", "sequences", "protocol_hashes")):
            raise ValueError("observation does not match the submitted scan and sequence")
        if item["status"] not in {"runnable", "greyed_out", "not_tested"}:
            raise ValueError("observation status must be runnable, greyed_out, or not_tested")
        expected[i]["status"] = item["status"]
        seen.add(i)
    preserved = not changed and (allow_derived or not derived) and av.valid and bv.valid
    return {
        "sent_program": paths.join(a.path_of(ap.instance)),
        "returned_program": paths.join(b.path_of(bp.instance)),
        "preserved": preserved,
        "comparison_performed": True,
        "scanner_confirmed": preserved
        and bool(records)
        and all(r["status"] == "runnable" for r in records),
        "sent_validation": av.to_dict(),
        "returned_validation": bv.to_dict(),
        "changes": changed,
        "churn": churn,
        "derived": derived,
        "preview": preview,
        "allow_derived": allow_derived,
        "observations": records,
        "coverage": {
            "steps": len(ap.steps),
            "protocols": sum(len(s.protocols) for s in ap.steps),
            "relations": len(ap.links),
            "owned_kinds": sorted({n["kind"] for n in before["nodes"].values()}),
            "ascconv_assignments": sum(
                len(n["document"].get("ascconv", {})) for n in before["nodes"].values()
            ),
        },
        "unchecked": [
            "non-ASCCONV XProtocol tree",
            "acquisition/image quality",
            "installed-sequence compatibility without console observations",
        ],
    }
