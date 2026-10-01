"""Evidence-backed offline validation, separate from scanner acceptance.

Structural rules stay in ``exar.validate``. This layer understands the XA60
PDF/ASCCONV mappings, their gates, and the supported slice geometry. A clean
report establishes only the listed checks, never installed-sequence compatibility
or arbitrary hardware limits.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from ..exar import ascconv, geometry, read
from ..exar import validate as structural
from ..exar.archive import COPY_REFERENCE, COPY_REFERENCE_GROUPS, Archive, Program, Protocol
from ..exar.inspect import ascconv_table
from .generate import mappings


@dataclass(frozen=True)
class Finding:
    """One located validation result.

    Attributes
    ----------
    severity, code, message : str
        Error or warning, stable rule name, and explanation.
    program, scan, parameter : str
        Protocol path, step name, and printed parameter or stored key.
    step_index : int or None
        Position including pauses, distinguishing repeated scan names.
    """

    severity: str
    code: str
    message: str
    program: str = ""
    scan: str = ""
    parameter: str = ""
    step_index: int | None = None


@dataclass
class ValidationReport:
    """Checked scope and findings for one archive.

    Attributes
    ----------
    release : str
        Archive major version.
    findings : list of Finding
        Errors and uncovered or uncertain cases.
    protocols_checked, mappings_checked, geometries_checked : int
        Nonvacuous coverage counts.
    """

    release: str
    findings: list[Finding] = field(default_factory=list)
    protocols_checked: int = 0
    mappings_checked: int = 0
    geometries_checked: int = 0

    @property
    def valid(self) -> bool:
        """Return whether all supported offline checks passed.

        Returns
        -------
        bool
            False when any finding is an error; warnings remain visible.
        """
        return not any(f.severity == "error" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        """Serialize findings with the limits of the validation claim.

        Returns
        -------
        dict
            JSON-compatible report, explicitly leaving scanner acceptance unknown.
        """
        return {
            **asdict(self),
            "valid": self.valid,
            "semantic_supported": self.release == "VA60A",
            "semantic_checked": self.protocols_checked > 0,
            "scanner_acceptance": "not_observed",
            "unchecked": [
                "installed sequence compatibility and hardware limits",
                "sequence-specific dependencies without characterized rules",
                "multi-group slice positioning",
                "derived scan times",
            ],
        }


def _number(value: Any) -> float | None:
    """Decode a finite stored numeric value, including hexadecimal codes.

    Parameters
    ----------
    value : Any
        Preview value or ASCCONV literal.

    Returns
    -------
    float or None
        Finite numeric reading, or None for invalid/nonfinite values.
    """
    try:
        s = str(value).strip()
        n = float(int(s, 16)) if s.lower().startswith(("0x", "-0x")) else float(s)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def protocol_findings(protocol: Protocol) -> tuple[list[Finding], int]:
    """Validate mapped stores without guessing missing enum meanings.

    Parameters
    ----------
    protocol : Protocol
        One XA60 protocol.

    Returns
    -------
    tuple
        Unlocated findings and the number of mapping/store comparisons.
    """
    found = [Finding("error", "geometry", s) for s in geometry.problems(protocol.xprotocol)]
    text = protocol.xprotocol
    table = ascconv_table(text)
    if not table:
        found.append(
            Finding(
                "error",
                "ascconv_missing",
                "protocol has no readable authoritative ASCCONV assignments",
            )
        )
    if ascconv.baseline_string(protocol) == ascconv.CONVERSION_NEEDED:
        found.append(
            Finding(
                "error",
                "conversion_needed",
                "scanner marks this protocol ConversionNeeded",
                parameter="sProtConsistencyInfo.tBaselineString",
            )
        )
    preview = protocol.preview
    sequence = ascconv.sequence_of(protocol)
    build = ascconv.build_id(ascconv.sequence_stamp(protocol))
    multiple = "sGroupArray.asGroup[1].nSize" in table
    if multiple:
        found.append(
            Finding(
                "warning", "geometry_unchecked", "multi-group positioning is not characterized"
            )
        )
    checked = 0
    for m in mappings.MAPPINGS:
        if (
            not m.ascconv_key
            or m.read_only
            or m.sign_from
            or (m.sequences and sequence not in m.sequences)
            or (m.builds and build not in m.builds)
            or (m.when and table.get(m.when[0]) != m.when[1])
        ):
            continue
        if "[*]" in m.ascconv_key:
            pattern = re.compile(re.escape(m.ascconv_key).replace(r"\[\*\]", r"\[(\d+)\]"))
            keys = [
                (key, int(match.group(1))) for key in table if (match := pattern.fullmatch(key))
            ]
            keys.sort(key=lambda item: item[1])
        else:
            keys = [(m.ascconv_key, None)]
        numbers = []
        for key, index in keys:
            literal = table.get(key)
            if literal is None:
                continue
            number = _number(literal)
            if number is None:
                found.append(
                    Finding(
                        "error",
                        "mapped_numeric",
                        "mapped assignment is not finite numeric data",
                        parameter=key,
                    )
                )
                continue
            if (m.bit is not None or m.choices) and not number.is_integer():
                found.append(
                    Finding(
                        "error",
                        "stored_code_integer",
                        "flag/choice storage must be integral",
                        parameter=key,
                    )
                )
            if ascconv.is_churn(key, literal):
                continue
            if m.choices and number not in {code for _, code in m.choices}:
                found.append(
                    Finding(
                        "warning",
                        "enum_uncharacterized",
                        f"stored code {literal} is outside characterized choices",
                        parameter=m.label,
                    )
                )
            numbers.append(number)
            entry = preview.get(m.preview_path or "")
            if entry is None or entry.value is None or m.bit is not None:
                continue
            expected = _number(entry.value)
            if expected is None:
                found.append(
                    Finding(
                        "warning",
                        "preview_numeric",
                        "Preview value is not finite numeric data; ASCCONV remains authoritative",
                        parameter=m.label,
                    )
                )
                continue
            basis = 1.0
            if m.basis:
                basis_key = m.basis.replace("[*]", f"[{index}]")
                basis = _number(table.get(basis_key))
                if basis is None:
                    found.append(
                        Finding(
                            "error",
                            "mapping_basis",
                            f"missing or invalid basis {basis_key}",
                            parameter=m.label,
                        )
                    )
                    continue
            expected = (expected + m.offset) * m.scale * basis
            # Phase FOV is a percentage rounded to one decimal in Preview.
            tolerance = abs(m.scale * basis) * 0.050001 if m.label == "FOV Phase" else 1e-6
            checked += 1
            if not multiple and not math.isclose(
                number, expected, rel_tol=1e-6, abs_tol=tolerance
            ):
                found.append(
                    Finding(
                        "warning",
                        "preview_ascconv",
                        f"Preview encodes {expected:.12g}, but authoritative {key} stores {literal}",
                        parameter=m.label,
                    )
                )
        if "[*]" in m.ascconv_key and not multiple and not m.basis and len(numbers) > 1:
            if any(
                not math.isclose(n, numbers[0], rel_tol=1e-6, abs_tol=1e-6) for n in numbers[1:]
            ):
                found.append(
                    Finding(
                        "error",
                        "replicated_parameter",
                        "mapped array elements disagree within a single group",
                        parameter=m.label,
                    )
                )
    return found, checked


def validate(archive: Archive | str | Path, *, program: Program | None = None) -> ValidationReport:
    """Inspect all programs, mapped protocols, geometry, and copy endpoints.

    Parameters
    ----------
    archive : Archive or str or Path
        Archive to inspect without repair or writing.
    program : Program or None, optional
        Restrict semantic checks to one live program. Structural checks always
        cover the whole archive.

    Returns
    -------
    ValidationReport
        Located findings and explicit coverage limitations.
    """
    if not isinstance(archive, Archive):
        archive = read(str(archive))
    report = ValidationReport(archive.major_version)
    report.findings.extend(Finding("error", "structure", s) for s in structural.problems(archive))
    if not report.valid:
        return report
    if archive.major_version != "VA60A":
        report.findings.append(
            Finding(
                "warning",
                "release_unchecked",
                "semantic validation is characterized only for XA60 (VA60A)",
            )
        )
        return report
    programs = archive.programs
    if program is not None:
        programs = [p for p in programs if p.instance.element_id == program.instance.element_id]
        if not programs:
            raise ValueError("semantic-validation program is not live in this archive")
    for program in programs:
        where = paths.join(archive.path_of(program.instance))
        positions = {s.instance.object_id: i for i, s in enumerate(program.steps)}
        for i, step in enumerate(program.steps):
            for protocol in step.protocols:
                found, checked = protocol_findings(protocol)
                report.findings.extend(
                    Finding(f.severity, f.code, f.message, where, step.name, f.parameter, i)
                    for f in found
                )
                report.protocols_checked += 1
                report.mappings_checked += checked
                table = ascconv_table(protocol.xprotocol)
                count = _number(table.get("sSliceArray.lSize"))
                if count is not None and count > 1 and "sGroupArray.asGroup[1].nSize" not in table:
                    report.geometries_checked += 1
        for link in program.links:
            if link.kind != COPY_REFERENCE:
                continue
            if link.group not in COPY_REFERENCE_GROUPS:
                report.findings.append(
                    Finding(
                        "warning",
                        "link_group_unchecked",
                        f"uncharacterized copy group {link.group}",
                        where,
                    )
                )
            source, target = positions.get(link.source), positions.get(link.target)
            if source is None or target is None:
                report.findings.append(
                    Finding(
                        "error", "link_endpoint", "copy reference leaves its owning program", where
                    )
                )
            elif (
                not program.steps[source].runs_a_protocol
                or not program.steps[target].runs_a_protocol
            ):
                report.findings.append(
                    Finding(
                        "error", "link_endpoint", "copy reference must join acquisitions", where
                    )
                )
            elif source == target:
                report.findings.append(
                    Finding("error", "link_self", "copy reference points to its own scan", where)
                )
    return report
