"""Transactional, mapped XA60 protocol editing and multi-archive assembly.

An edit session owns independent archive tables. JSON plans and Python callers
share one operation implementation; failed operations never publish partial
changes. Raw ASCCONV assignments remain readable but are not writable controls.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import paths
from ..exar import edit as graph
from ..exar import generate, read, validate
from ..exar.archive import COPY_REFERENCE, COPY_REFERENCE_GROUPS, Archive, Instance, Step
from ..exar.inspect import ascconv_table
from . import address, archive_view
from .generate import mappings
from .query import Query, _numeric, canonical_parameter_name, search
from .vocabulary import load_vocabulary


def _program(archive: Archive, selector: str = "") -> Instance:
    """Resolve a program address or stable element id.

    Parameters
    ----------
    archive : Archive
        Archive to search.
    selector : str, optional
        Address, element id, or empty for the sole program.

    Returns
    -------
    Instance
        Unambiguously selected program.
    """
    programs = archive.programs
    if not selector and len(programs) == 1:
        return programs[0].instance
    for program in programs:
        if selector == program.instance.element_id:
            return program.instance
    return address.resolve(
        selector,
        [(archive.path_of(p.instance), p.instance) for p in programs],
        what="protocol",
        source="edit session",
    )


def _step(archive: Archive, program: Instance, selector: str) -> Step:
    """Resolve a step inside its owning program, including pauses.

    Parameters
    ----------
    archive : Archive
        Archive to search.
    program : Instance
        Owning program, providing identity scope.
    selector : str
        Scan address or stable element id.

    Returns
    -------
    Step
        Unambiguously selected step.
    """
    steps = archive.steps_of(program)
    for step in steps:
        if selector == step.instance.element_id:
            return step
    folder = archive.path_of(program)
    return address.resolve(
        selector, [(folder + [s.name], s) for s in steps], what="step", source="edit session"
    )


def _select(archive: Archive, query: Query) -> list[tuple[Instance, Step]]:
    """Select live scans through the shared query engine, refusing unknowns.

    Parameters
    ----------
    archive : Archive
        Live archive.
    query : Query
        Hierarchy, sequence, and parameter filters.

    Returns
    -------
    list of tuple
        Owning program and selected acquisition.

    Raises
    ------
    ValueError
        If selection is empty or undecidable.
    """
    selected = []
    for program in archive.programs:
        document = archive_view.as_protocol(archive, program, "edit session")
        acquisitions = [s for s in program.steps if s.runs_a_protocol]
        for scan in document["scans"]:
            scan["raw_parameters"] = ascconv_table(acquisitions[scan["index"]].protocol.xprotocol)
        report = search([document], query)
        if report.unknown or report.errors:
            raise ValueError(
                "edit selector has unknown or conflicting readings; narrow it explicitly"
            )
        selected.extend((program.instance, acquisitions[m["scan_index"]]) for m in report.matches)
    if not selected:
        raise ValueError("edit selector matches no scans")
    return selected


def _requests(
    step: Step, changes: Mapping[str, Any], aliases: Mapping[str, str]
) -> dict[str, Any]:
    """Resolve canonical names to verified writable PDF/ASCCONV mappings.

    Parameters
    ----------
    step : Step
        Single-protocol acquisition to edit.
    changes : Mapping
        Requested printed or canonical names and displayed values.
    aliases : Mapping
        Verified query aliases.

    Returns
    -------
    dict
        Mapping labels and values converted to displayed units.

    Raises
    ------
    ValueError
        If any requested control is unmapped, ambiguous, or invalid.
    """
    if len(step.protocols) != 1:
        raise ValueError(f"{step.name!r} needs exactly one protocol for mapped editing")
    vocabulary = load_vocabulary("XA60")
    protocol = step.protocol
    result, seen = {}, set()
    for name, value in changes.items():
        if not isinstance(name, str) or name.startswith("raw:"):
            raise ValueError("only mapped PDF parameter names can be modified")
        wanted = canonical_parameter_name(name, vocabulary, aliases)
        labels = {m.label for m in mappings.MAPPINGS} | {
            e.label for e in protocol.preview.values()
        }
        candidates = [
            label
            for label in labels
            if canonical_parameter_name(label, vocabulary, aliases) == wanted
        ]
        resolved = {}
        reasons = []
        for label in candidates:
            mapping, reason = mappings.resolve(protocol, label)
            if mapping:
                resolved[(mapping.preview_path, mapping.ascconv_key)] = mapping
            elif reason:
                reasons.append(reason)
        if len(resolved) != 1:
            detail = "; ".join(sorted(set(reasons))) or "no verified PDF/ASCCONV mapping"
            raise ValueError(
                f"{step.name}: {name!r}: {detail}"
                if not resolved
                else f"{step.name}: {name!r} is ambiguous"
            )
        identity, mapping = next(iter(resolved.items()))
        if identity in seen:
            raise ValueError(f"{name!r} repeats a control under another alias")
        seen.add(identity)
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"{name!r} must be text, a number, or a boolean")
        try:
            number = float(value)
        except ValueError:
            number = None
        except OverflowError as exc:
            raise ValueError(f"{name!r} must have a finite value") from exc
        if number is not None and not math.isfinite(number):
            raise ValueError(f"{name!r} must have a finite value")
        entry = protocol.preview.get(mapping.preview_path)
        numeric = _numeric(value)
        if numeric and numeric[1]:
            unit = _numeric(f"1 {entry.unit}") if entry and entry.unit else None
            if unit is None or unit[1] != numeric[1]:
                raise ValueError(f"{name!r} has incompatible or unavailable display units")
            value = numeric[0] / unit[0]
        result[mapping.label] = value
    return result


def _boundary(archive: Archive, program: Instance, selected: set[str], policy: str) -> list[dict]:
    """Require an explicit choice before removing relations across a selection.

    Parameters
    ----------
    archive : Archive
        Archive to inspect.
    program : Instance
        Program owning the selected steps.
    selected : set of str
        Selected object ids.
    policy : str
        Reject or explicitly drop crossing relations.

    Returns
    -------
    list of dict
        Relations deliberately removed at the boundary.
    """
    if policy not in {"reject", "drop"}:
        raise ValueError("external_links must be reject or drop")
    crossing = [
        r
        for r in graph.relations(archive, program)
        if bool(r["SourceId"] in selected) != bool(r["TargetId"] in selected)
    ]
    if crossing and policy == "reject":
        raise ValueError("selection crosses a relation; explicitly choose external_links=drop")
    return crossing


def _ordered(archive: Archive, program: Instance) -> None:
    """Reject backwards copy-reference dependencies after an order change.

    Parameters
    ----------
    archive : Archive
        Edited archive.
    program : Instance
        Program whose relations must follow execution order.

    Returns
    -------
    None
    """
    positions = {s.instance.object_id: i for i, s in enumerate(archive.steps_of(program))}
    for relation in graph.relations(archive, program):
        if (
            relation.get("Kind") == COPY_REFERENCE
            and positions[relation["SourceId"]] >= positions[relation["TargetId"]]
        ):
            raise ValueError("copy-reference source must precede its target in the running order")


def _snapshot(archive: Archive, program: Instance) -> dict[str, Any]:
    """Describe a program's ordered steps and complete relation payloads.

    Parameters
    ----------
    archive : Archive
        Archive holding the program.
    program : Instance
        Program to record in a review manifest.

    Returns
    -------
    dict
        Path, step identities, protocol hashes, and relation evidence.
    """
    steps = archive.steps_of(program)
    names = {s.instance.object_id: s.name for s in steps}
    return {
        "path": paths.join(archive.path_of(program)),
        "element_id": program.element_id,
        "steps": [
            {
                "position": i,
                "name": s.name,
                "kind": s.instance.kind,
                "element_id": s.instance.element_id,
                "object_id": s.instance.object_id,
                "protocol_hashes": [p.instance.content_hash for p in s.protocols],
            }
            for i, s in enumerate(steps)
        ],
        "relations": [
            {
                **r,
                "source_name": names.get(r["SourceId"], ""),
                "target_name": names.get(r["TargetId"], ""),
            }
            for r in graph.relations(archive, program)
        ],
    }


class EditSession:
    """An isolated XA60 working archive with atomic plans and mapped patches.

    Parameters
    ----------
    source : Archive or str or Path
        Base archive. Existing programs and history are retained.
    source_path : str or Path or None, optional
        Path to protect when an already loaded archive is supplied.
    """

    def __init__(
        self, source: Archive | str | Path, *, source_path: str | Path | None = None
    ) -> None:
        """Validate the source and create an independent editing head.

        Parameters
        ----------
        source : Archive or str or Path
            Base archive.
        source_path : str or Path or None, optional
            Original file to protect.

        Returns
        -------
        None
        """
        if not isinstance(source, Archive):
            source_path, source = source, read(str(source))
        if source.major_version != "VA60A":
            raise ValueError("edit sessions currently support XA60 (VA60A) archives only")
        problems = validate.problems(source)
        if problems:
            raise ValueError("invalid source archive: " + "; ".join(problems))
        self.source = graph.clone(source)
        self.archive = graph.checkout(source)
        self.events: list[dict[str, Any]] = []
        self.protected = {Path(source_path).resolve()} if source_path else set()
        self.source_path = str(source_path or "")
        self.donor_paths: dict[str, str] = {}

    def manifest(self) -> dict[str, Any]:
        """Return an independent, JSON-compatible review of committed edits.

        Returns
        -------
        dict
            Source, release, edits, and structural-validation results.
        """
        return {
            "source": self.source_path,
            "release": "XA60",
            "head": self.archive.head,
            "complete": True,
            "donor_sources": dict(self.donor_paths),
            "protected_inputs": sorted(str(p) for p in self.protected),
            "operations": copy.deepcopy(self.events),
            "validation": validate.problems(self.archive),
        }

    def patch(self, query: Query, changes: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically patch every selected scan through verified mappings.

        Parameters
        ----------
        query : Query
            Shared query selector.
        changes : Mapping
            Printed or canonical control names and displayed values.

        Returns
        -------
        dict
            Committed operation and before/after parameter evidence.
        """
        candidate = graph.clone(self.archive)
        candidate, event = self._patch(candidate, query, changes)
        self._commit(candidate, [event])
        return copy.deepcopy(event)

    def _patch(
        self, archive: Archive, query: Query, changes: Mapping[str, Any]
    ) -> tuple[Archive, dict[str, Any]]:
        """Prepare a strict parameter patch on a candidate archive.

        Parameters
        ----------
        archive : Archive
            Isolated candidate.
        query : Query
            Scan selector.
        changes : Mapping
            Requested controls.

        Returns
        -------
        tuple
            Refreshed candidate and detailed patch event.
        """
        if not isinstance(changes, Mapping) or not changes:
            raise ValueError("patch needs a nonempty changes object")
        records = []
        selected = _select(archive, query)
        for owner, chosen in selected:
            program = archive.by_element[owner.element_id]
            step = _step(archive, program, chosen.instance.element_id)
            detached_from = None
            if (
                sum(chosen.instance.element_id in p.instance.children for p in archive.programs)
                > 1
            ):
                detached_from = chosen.instance.element_id
                archive, element = graph.detach_step(archive, program, step)
                program = archive.by_element[program.element_id]
                step = _step(archive, program, element)
            requests = _requests(step, changes, query.aliases)
            document, applied, skipped = mappings.patch_document(
                step.protocol, requests, step.name
            )
            if skipped:
                raise ValueError("; ".join(f"{s.step}: {s.label}: {s.reason}" for s in skipped))
            archive.replace_content(step.protocol.instance, document)
            records.append(
                {
                    "program": paths.join(archive.path_of(program)),
                    "step": step.name,
                    "element_id": step.instance.element_id,
                    "detached_from": detached_from,
                    "parameters": [asdict(a) for a in applied],
                }
            )
        return archive, {"op": "patch", "requested": dict(changes), "scans": records}

    def _commit(self, archive: Archive, events: list[dict]) -> None:
        """Publish a candidate only after structural validation succeeds.

        Parameters
        ----------
        archive : Archive
            Complete candidate archive.
        events : list of dict
            Events to append after successful validation.

        Returns
        -------
        None
        """
        archive = graph.refresh(archive)
        if any(not p.steps for p in archive.programs):
            raise ValueError("a resulting protocol cannot have an empty running order")
        archive.prune()
        problems = validate.problems(archive)
        if problems:
            raise ValueError("edited archive failed validation: " + "; ".join(problems))
        self.archive = archive
        self.events.extend(events)

    def apply_plan(
        self,
        plan: Mapping[str, Any],
        *,
        sources: Mapping[str, Archive | str | Path] | None = None,
        base_dir: str | Path = ".",
    ) -> list[dict[str, Any]]:
        """Atomically apply an ordered assembly/edit plan.

        Parameters
        ----------
        plan : Mapping
            Optional sources aliases and ordered operations; see README.
        sources : Mapping or None, optional
            Already loaded donors or paths. ``base`` names the original archive
            and ``session`` names the current working archive.
        base_dir : str or Path, optional
            Directory for relative paths in the plan's sources.

        Returns
        -------
        list of dict
            Committed operation events. A failed plan changes nothing.
        """
        if not isinstance(plan, Mapping) or set(plan) - {"sources", "operations"}:
            raise ValueError("plan accepts only sources and operations")
        operations = plan.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("plan needs a nonempty operations list")
        configured = plan.get("sources", {})
        if not isinstance(configured, Mapping):
            raise ValueError("sources must be an object of aliases to archive paths")
        donors = {"base": self.source}
        protected = set(self.protected)
        donor_paths = dict(self.donor_paths)
        cache = {}
        for alias, value in dict(configured, **dict(sources or {})).items():
            if not isinstance(alias, str) or not alias or alias in {"base", "session"}:
                raise ValueError("source aliases must be nonempty and cannot be base or session")
            if isinstance(value, Archive):
                donors[alias] = value
            else:
                file = (Path(base_dir) / value).resolve()
                protected.add(file)
                donor_paths[alias] = str(file)
                if file not in cache:
                    cache[file] = read(str(file))
                donors[alias] = cache[file]
            if donors[alias].major_version != self.archive.major_version:
                raise ValueError(f"source {alias!r} has an incompatible archive release")
        candidate, events = graph.clone(self.archive), []
        for index, operation in enumerate(operations):
            try:
                candidate, event = self._operation(candidate, operation, donors)
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"operation {index + 1}: {exc}") from exc
            events.append(event)
        self._commit(candidate, events)
        self.protected = protected
        self.donor_paths = donor_paths
        return copy.deepcopy(events)

    def _donors(
        self, archive: Archive, selections: Sequence[Mapping], sources: Mapping[str, Archive]
    ) -> list[tuple[Archive, Instance, Step]]:
        """Resolve ordered donor groups without losing program identity scope.

        Parameters
        ----------
        archive : Archive
            Working archive for the session alias.
        selections : sequence of Mapping
            Source, program, and ordered scan addresses or query.
        sources : Mapping
            Donor aliases.

        Returns
        -------
        list of tuple
            Donor archive, program, and scan in requested order.
        """
        if not isinstance(selections, list) or not selections:
            raise ValueError("donors must be a nonempty ordered list")
        result = []
        for selection in selections:
            if not isinstance(selection, Mapping) or set(selection) - {
                "source",
                "program",
                "scans",
                "query",
            }:
                raise ValueError("donor accepts source, program, and scans or query")
            alias = selection.get("source", "base")
            origin = archive if alias == "session" else sources[alias]
            if "query" in selection:
                if "scans" in selection or "program" in selection:
                    raise ValueError("donor query cannot be combined with scans or program")
                result.extend(
                    (origin, p, s) for p, s in _select(origin, Query.from_dict(selection["query"]))
                )
            else:
                owner = _program(origin, selection.get("program", ""))
                scans = selection.get("scans")
                if (
                    not isinstance(scans, list)
                    or not scans
                    or not all(isinstance(s, str) for s in scans)
                ):
                    raise ValueError("donor scans must be a nonempty ordered list of addresses")
                result.extend((origin, owner, _step(origin, owner, s)) for s in scans)
        return result

    def _operation(
        self, archive: Archive, operation: Mapping, sources: Mapping[str, Archive]
    ) -> tuple[Archive, dict]:
        """Execute one validated plan operation on an isolated candidate.

        Parameters
        ----------
        archive : Archive
            Candidate to edit.
        operation : Mapping
            Operation object.
        sources : Mapping
            Donor aliases.

        Returns
        -------
        tuple
            Updated candidate and review event.
        """
        fields = {
            "copy_protocol": {"source", "protocol", "parent", "name"},
            "assemble": {"source", "protocol", "parent", "name", "donors", "external_links"},
            "insert": {"program", "donors", "position", "external_links"},
            "move": {"program", "step", "position"},
            "delete": {"program", "steps", "external_links"},
            "pause": {"program", "name", "position"},
            "rename": {"program", "step", "name"},
            "link": {
                "program",
                "source",
                "target",
                "group",
                "copy_phase_encoding",
                "copy_steps",
                "ignore_last_step",
                "ignore_measurements",
            },
            "unlink": {"program", "source", "target", "group"},
            "patch": {"query", "changes"},
        }
        if not isinstance(operation, Mapping) or operation.get("op") not in fields:
            raise ValueError("unknown edit operation")
        op, data = operation["op"], dict(operation)
        if set(data) - (fields[op] | {"op"}):
            raise ValueError(f"unknown fields for {op}: {sorted(set(data) - fields[op] - {'op'})}")
        event = {"op": op, "request": copy.deepcopy(data)}
        if op == "patch":
            return self._patch(archive, Query.from_dict(data.get("query", {})), data["changes"])
        if op in {"copy_protocol", "assemble"}:
            alias = data.get("source", "base")
            origin = archive if alias == "session" else sources[alias]
            donor = _program(origin, data.get("protocol", ""))
            event["before"] = _snapshot(origin, donor)
            parent_path = data["parent"]
            if isinstance(parent_path, str):
                parent_path = paths.split(parent_path)
            if not isinstance(parent_path, list) or not all(
                isinstance(p, str) for p in parent_path
            ):
                raise ValueError("parent must be a folder path or list of names")
            name = data["name"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("new protocol name must be nonempty text")
            # Resolve donors before modifying any candidate nodes.
            donors = self._donors(archive, data["donors"], sources) if op == "assemble" else []
            archive, parent = graph.ensure_directory(archive, parent_path)
            directory = archive.by_element[parent]
            if any(
                archive.path_of(p.instance)[:-1] == archive.path_of(directory)
                and p.name.casefold() == name.casefold()
                for p in archive.programs
            ):
                raise ValueError("destination already contains a protocol with that name")
            archive, element = graph.copy_program(
                archive, origin, donor, directory, name, include_steps=op != "assemble"
            )
            program = archive.by_element[element]
            if op == "assemble":
                archive, copied, dropped = graph.copy_steps(
                    archive,
                    archive.by_element[element],
                    donors,
                    external_links=data.get("external_links", "reject"),
                )
                event.update(steps=copied, dropped_relations=dropped)
                _ordered(archive, archive.by_element[element])
            event.update(
                element_id=element, path=paths.join(archive.path_of(archive.by_element[element]))
            )
            event["after"] = _snapshot(archive, archive.by_element[element])
            return archive, event
        program = _program(archive, data.get("program", ""))
        program_id = program.element_id
        event["program"] = paths.join(archive.path_of(program))
        event["before"] = _snapshot(archive, program)
        if op == "insert":
            donors = self._donors(archive, data["donors"], sources)
            archive, copied, dropped = graph.copy_steps(
                archive,
                program,
                donors,
                position=data.get("position"),
                external_links=data.get("external_links", "reject"),
            )
            event.update(steps=copied, dropped_relations=dropped)
        elif op == "move":
            step = _step(archive, program, data["step"])
            order = [
                s.instance
                for s in archive.steps_of(program)
                if s.instance.element_id != step.instance.element_id
            ]
            position = data["position"]
            if (
                not isinstance(position, int)
                or isinstance(position, bool)
                or not 0 <= position <= len(order)
            ):
                raise ValueError("move position is outside the final running order")
            order.insert(position, step.instance)
            graph.set_order(archive, program, order)
            event["element_id"] = step.instance.element_id
        elif op == "delete":
            selectors = data["steps"]
            if not isinstance(selectors, list) or not selectors:
                raise ValueError("delete needs a nonempty steps list")
            steps = [_step(archive, program, s) for s in selectors]
            if len({s.instance.element_id for s in steps}) != len(steps):
                raise ValueError("delete repeats a step")
            event["dropped_relations"] = _boundary(
                archive,
                program,
                {s.instance.object_id for s in steps},
                data.get("external_links", "reject"),
            )
            event["steps"] = [s.instance.element_id for s in steps]
            archive = graph.delete_steps(archive, program, steps)
        elif op == "pause":
            archive, element = graph.add_pause(archive, program, data["name"], data["position"])
            event["element_id"] = element
        elif op == "rename":
            node = _step(archive, program, data["step"]).instance if "step" in data else program
            if (
                "step" in data
                and sum(node.element_id in p.instance.children for p in archive.programs) > 1
            ):
                event["detached_from"] = node.element_id
                archive, element = graph.detach_step(
                    archive, program, _step(archive, program, node.element_id)
                )
                program = archive.by_element[program_id]
                node = archive.by_element[element]
            if not isinstance(data["name"], str) or not data["name"].strip():
                raise ValueError("name must be nonempty text")
            if node is program and any(
                p.instance.element_id != program_id
                and p.name.casefold() == data["name"].casefold()
                and archive.path_of(p.instance)[:-1] == archive.path_of(program)[:-1]
                for p in archive.programs
            ):
                raise ValueError("destination already contains a protocol with that name")
            generate.rename(archive, node, data["name"])
            event["element_id"] = node.element_id
        elif op in {"link", "unlink"}:
            source, target = (_step(archive, program, data[k]) for k in ("source", "target"))
            if not source.runs_a_protocol or not target.runs_a_protocol:
                raise ValueError("copy references require two acquisition steps")
            group = data.get("group", "Slices" if op == "link" else None)
            if group is not None and group not in COPY_REFERENCE_GROUPS:
                raise ValueError("unknown copy-reference group")
            records, removed = [], []
            for record in graph.relations(archive, program):
                matching = (
                    record.get("Kind") == COPY_REFERENCE
                    and record["SourceId"] == source.instance.object_id
                    and record["TargetId"] == target.instance.object_id
                    and (group is None or ET.fromstring(record["Data"]).get("Group") == group)
                )
                (removed if matching else records).append(record)
            if op == "unlink" and not removed:
                raise ValueError("no matching copy reference to remove")
            graph.set_relations(archive, program, records)
            if op == "link":
                flags = {
                    k: data.get(k, False)
                    for k in (
                        "copy_phase_encoding",
                        "copy_steps",
                        "ignore_last_step",
                        "ignore_measurements",
                    )
                }
                if not all(isinstance(v, bool) for v in flags.values()):
                    raise ValueError("copy-reference flags must be booleans")
                generate.link_steps(archive, source, target, group=group, program=program, **flags)
            event.update(
                source=source.instance.element_id,
                target=target.instance.element_id,
                replaced_relations=removed,
            )
        archive = graph.refresh(archive)
        if op in {"insert", "move", "link"}:
            _ordered(archive, archive.by_element[program_id])
        event["after"] = _snapshot(archive, archive.by_element[program_id])
        return archive, event

    def write(self, output: str | Path, *, force: bool = False) -> dict[str, Any]:
        """Validate a serialized candidate before atomically publishing it.

        Parameters
        ----------
        output : str or Path
            New .exar1 path. Source and donor paths are always protected.
        force : bool, optional
            Permit replacement of an unrelated existing output.

        Returns
        -------
        dict
            Manifest including output path and serialized validation.

        Raises
        ------
        ValueError
            If output is unsafe or serialized validation fails.
        """
        target = Path(output)
        if target.suffix.casefold() != ".exar1":
            raise ValueError("output must have the .exar1 suffix")
        resolved = target.resolve()
        if resolved in self.protected or any(
            target.exists() and p.exists() and os.path.samefile(target, p) for p in self.protected
        ):
            raise ValueError("output would overwrite a source or donor archive")
        if target.is_dir() or (target.exists() and not force):
            raise ValueError("output already exists; use force to replace an unrelated file")
        archive = graph.clone(self.archive)
        problems = validate.problems(archive)
        if problems:
            raise ValueError("archive failed validation: " + "; ".join(problems))
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".exar1", dir=target.parent
        )
        os.close(descriptor)
        try:
            archive.write(temporary)
            problems = validate.problems(read(temporary))
            if problems:
                raise ValueError("serialized archive failed validation: " + "; ".join(problems))
            if force:
                os.replace(temporary, target)
            else:
                # An exclusive hard link also closes the check/write race.
                os.link(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {**self.manifest(), "output": str(target), "serialized_validation": []}


def load_plan(path: str | Path) -> dict[str, Any]:
    """Read a JSON edit plan without executing it.

    Parameters
    ----------
    path : str or Path
        Plan file.

    Returns
    -------
    dict
        Decoded plan, validated further by EditSession.apply_plan.
    """
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
