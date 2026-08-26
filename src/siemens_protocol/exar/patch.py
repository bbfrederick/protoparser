"""Write a printed parameter back into a protocol, in both places it lives.

A value the console displays is stored twice. ``Preview`` holds what the
console lists and what the PDF export prints -- ``TR``, in milliseconds, as a
float -- while the XProtocol text's ASCCONV block holds what the sequence
actually runs -- ``alTR[0]``, in microseconds, as an integer. Patching one and
not the other is silently wrong in opposite directions: edit only the preview
and the console lists a number the scan will not use; edit only the ASCCONV and
the listing goes stale. Every mapping here therefore names both.

What this module deliberately does *not* do is recompute derived values. The
console does: changing TR moved ``lScanTimeSec`` and ``lTotalScanTimeSec`` in
the reference pair. A patched archive carries the old scan time, and
:class:`Manifest` says so rather than leaving it to be discovered later.

Mappings are keyed by preview path, not by printed label, because labels are
not unique within a protocol -- ``Slices`` is both ``sub.0.msr.sg.0.size`` and
``sub.0.msr.total_size``. A label is accepted as a convenience and resolved
against the protocol, and an ambiguous one is refused rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from typing import Mapping as MappingType

from .archive import Archive, Protocol

#: Delimiters of the ASCCONV block inside the XProtocol text.
ASCCONV_BEGIN = "### ASCCONV BEGIN"
ASCCONV_END = "### ASCCONV END"


@dataclass(frozen=True)
class Mapping:
    """One printed parameter, and the two places its value is stored.

    Attributes
    ----------
    preview_path : str
        Key into the protocol's ``Preview`` map, for example ``sub.0.msr.tr.0``.
    ascconv_key : str
        Assignment in the ASCCONV block, for example ``alTR[0]``.
    scale : float
        Multiplier taking the preview value to the ASCCONV value. ``1000`` for
        the times, which the preview holds in milliseconds and ASCCONV in
        microseconds.
    label : str
        The label the console and the PDF print, for reference and for
        resolving a caller's label. Not unique across a protocol.
    evidence : str
        How the mapping was established. Kept because these were derived from
        a corpus rather than from documentation, and a later reader needs to
        know which are backed by a controlled edit and which by agreement.
    """

    preview_path: str
    ascconv_key: str
    scale: float
    label: str
    evidence: str


#: The mappings this module will write, keyed by preview path.
#:
#: Every entry was established against ``examples/XA60/`` rather than from any
#: Siemens document. Two kinds of evidence appear, and they are not equal. TR
#: is backed by a *controlled edit*: ``Potpourri_changed.exar1`` is the same
#: export re-saved with TR altered on five scans, and the diff moved exactly
#: the preview entry and the ASCCONV key named here. The rest are backed by
#: *agreement*: across every protocol in the corpus the preview value equals
#: the ASCCONV value under the stated scale, no other ASCCONV key agrees, and
#: the parameter takes enough distinct values that the agreement cannot be
#: coincidence.
#:
#: That second bar is what keeps this table short. ``FOV Read`` looks
#: mappable and is left out: ``sSliceArray.asSlice[0].dReadoutFOV`` and
#: ``...dPhaseFOV`` hold the same number in every example, because FOV Phase
#: is 100% throughout the corpus, so the data cannot say which one the label
#: belongs to. An export with a non-square FOV would settle it. Adding an
#: entry on the strength of its name alone is how a patcher comes to write a
#: plausible value into the wrong field.
MAPPINGS: dict[str, Mapping] = {
    "sub.0.msr.tr.0": Mapping(
        preview_path="sub.0.msr.tr.0",
        ascconv_key="alTR[0]",
        scale=1000.0,
        label="TR",
        evidence="controlled edit: Potpourri vs Potpourri_changed, 5 scans, 20 distinct values",
    ),
    "sub.0.msr.te.0": Mapping(
        preview_path="sub.0.msr.te.0",
        ascconv_key="alTE[0]",
        scale=1000.0,
        label="TE",
        evidence="agreement across 36 protocols, 16 distinct values, no rival key",
    ),
    "sub.0.msr.angle_array.0": Mapping(
        preview_path="sub.0.msr.angle_array.0",
        ascconv_key="adFlipAngleDegree[0]",
        scale=1.0,
        label="Flip Angle",
        evidence="agreement across 34 protocols, 9 distinct values, no rival key",
    ),
    "sub.0.msr.matrix": Mapping(
        preview_path="sub.0.msr.matrix",
        ascconv_key="sKSpace.lBaseResolution",
        scale=1.0,
        label="Base Resolution",
        evidence="agreement across 36 protocols, 7 distinct values, no rival key",
    ),
    "sub.0.msr.ips": Mapping(
        preview_path="sub.0.msr.ips",
        ascconv_key="sKSpace.lImagesPerSlab",
        scale=1.0,
        label="Slices per Slab",
        evidence="agreement across 14 protocols, 4 distinct values, no rival key",
    ),
}


@dataclass(frozen=True)
class Applied:
    """One value that was written, in both of its locations.

    Attributes
    ----------
    step : str
        Name of the measurement step whose protocol was patched.
    label : str
        The printed label.
    preview_path : str
        Preview key that was written.
    ascconv_key : str
        ASCCONV assignment that was written.
    previous : Any
        The preview value before the edit.
    value : Any
        The preview value after it.
    ascconv_previous : str
        The ASCCONV literal before the edit.
    ascconv_value : str
        The ASCCONV literal after it.
    """

    step: str
    label: str
    preview_path: str
    ascconv_key: str
    previous: Any
    value: Any
    ascconv_previous: str
    ascconv_value: str


@dataclass(frozen=True)
class Skipped:
    """One value that was asked for and not written.

    Attributes
    ----------
    step : str
        Name of the measurement step, or the requested name when no step
        matched it.
    label : str
        The label or preview path as the caller gave it.
    value : Any
        The value the caller asked for.
    reason : str
        Why it was not written, in a form fit to show a user.
    """

    step: str
    label: str
    value: Any
    reason: str


@dataclass
class Manifest:
    """What a patch run wrote, what it refused, and what it left alone.

    A patcher that reports only its successes is unusable for this job: the
    interesting failure is the value that was silently not written, and the
    interesting risk is the value that stayed at whatever the template said.

    Attributes
    ----------
    applied : list of Applied
        Every value written.
    skipped : list of Skipped
        Every value asked for and refused, with a reason.
    inherited : int
        Preview entries across the touched protocols that no request named, so
        that still hold whatever the source archive said.
    stale : list of str
        Values the console would have recomputed and this module did not.
    """

    applied: list[Applied] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    inherited: int = 0
    stale: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Return whether every requested value was written.

        Returns
        -------
        bool
            ``True`` when nothing was skipped.
        """
        return not self.skipped

    def report(self) -> str:
        """Render the manifest as text for a user to read.

        Returns
        -------
        str
            One line per applied and skipped value, then the counts that say
            how much of the protocol was left untouched.
        """
        lines: list[str] = []
        for one in self.applied:
            lines.append(
                f"set   {one.step}: {one.label} {one.previous} -> {one.value} "
                f"({one.ascconv_key} {one.ascconv_previous} -> {one.ascconv_value})"
            )
        for miss in self.skipped:
            lines.append(f"skip  {miss.step}: {miss.label}={miss.value} -- {miss.reason}")
        lines.append(f"inherited {self.inherited} preview value(s) from the source archive")
        if self.stale:
            lines.append("not recomputed, the console would have: " + ", ".join(self.stale))
        return "\n".join(lines)


def ascconv_bounds(text: str) -> tuple[int, int]:
    """Locate the ASCCONV block within an XProtocol document.

    Parameters
    ----------
    text : str
        The XProtocol text.

    Returns
    -------
    tuple of int
        Start and end offsets of the block, or ``(-1, -1)`` when the document
        has none.
    """
    start = text.find(ASCCONV_BEGIN)
    end = text.find(ASCCONV_END)
    if start < 0 or end < 0 or end < start:
        return (-1, -1)
    return (start, end)


def read_ascconv(text: str, key: str) -> str | None:
    """Return the literal an ASCCONV assignment holds, without interpreting it.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The assignment name, for example ``alTR[0]``.

    Returns
    -------
    str or None
        The literal as written, or ``None`` when the block has no such
        assignment.
    """
    start, end = ascconv_bounds(text)
    if start < 0:
        return None
    found = _assignment(key).search(text, start, end)
    return found.group("value") if found else None


def write_ascconv(text: str, key: str, literal: str) -> str:
    """Replace one ASCCONV assignment's literal, preserving its layout.

    The separator is captured and put back rather than normalized. Numaris/X
    writes ``key\\t = \\tvalue`` but flips to ``key  =  value`` between saves,
    so imposing either spelling would add churn that has nothing to do with
    the edit and make a later diff harder to read.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The assignment name.
    literal : str
        The replacement literal, already formatted.

    Returns
    -------
    str
        The text with that one assignment rewritten. Returned unchanged when
        the assignment is absent.
    """
    start, end = ascconv_bounds(text)
    if start < 0:
        return text
    found = _assignment(key).search(text, start, end)
    if not found:
        return text
    return text[: found.start("value")] + literal + text[found.end("value") :]


def _assignment(key: str) -> re.Pattern[str]:
    """Build the pattern matching one ASCCONV assignment.

    Parameters
    ----------
    key : str
        The assignment name. Escaped, since these contain ``[``, ``]`` and
        ``.`` and would otherwise be read as a pattern.

    Returns
    -------
    re.Pattern
        A pattern with a ``value`` group covering the literal.
    """
    return re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(?P<value>.*?)[ \t]*$", re.M)


def format_like(value: float, existing: str) -> str:
    """Format a number the way the literal beside it is written.

    ASCCONV distinguishes ``650000`` from ``650000.0`` and the two are not
    interchangeable to every reader of the file, so the existing literal
    decides which is written back.

    Parameters
    ----------
    value : float
        The number to write.
    existing : str
        The literal currently in place.

    Returns
    -------
    str
        The formatted literal.
    """
    if re.fullmatch(r"[-+]?\d+", existing.strip()):
        return str(int(round(value)))
    return repr(float(value))


def resolve(protocol: Protocol, name: str) -> tuple[str | None, str]:
    """Turn a caller's label or preview path into a mapped preview path.

    Parameters
    ----------
    protocol : Protocol
        The protocol the name is being resolved against.
    name : str
        A preview path, or a printed label such as ``TR``.

    Returns
    -------
    tuple
        The resolved preview path and an empty reason, or ``None`` and the
        reason it could not be resolved.
    """
    if name in MAPPINGS:
        return (name, "")
    wanted = name.strip().casefold()
    hits = [m.preview_path for m in MAPPINGS.values() if m.label.strip().casefold() == wanted]
    if len(hits) == 1:
        return (hits[0], "")
    if len(hits) > 1:
        return (None, f"label {name!r} maps to several parameters: {', '.join(sorted(hits))}")
    printed = {e.label.strip().casefold() for e in protocol.preview.values()}
    if wanted in printed:
        return (None, f"{name!r} is printed by this protocol but no verified mapping writes it")
    return (None, f"no verified mapping for {name!r}")


def patch_document(
    protocol: Protocol, requests: MappingType[str, Any], step: str = ""
) -> tuple[dict[str, Any], list[Applied], list[Skipped]]:
    """Apply requested values to one protocol's document.

    The document is copied shallowly and its two mutable parts replaced, so
    the caller's ``protocol`` is left as it was and nothing is written until
    :func:`apply` re-addresses the content.

    Parameters
    ----------
    protocol : Protocol
        The protocol to patch.
    requests : mapping
        Preview path or printed label to new preview-side value.
    step : str, optional
        Name of the step holding this protocol, used only for the records.

    Returns
    -------
    tuple
        The new document, the values applied, and the values skipped.
    """
    document = dict(protocol.document)
    preview = {
        k: dict(v) if isinstance(v, dict) else v for k, v in document.get("Preview", {}).items()
    }
    text = document.get("Data", "")
    applied: list[Applied] = []
    skipped: list[Skipped] = []
    for name, value in requests.items():
        path, reason = resolve(protocol, name)
        if path is None:
            skipped.append(Skipped(step=step, label=name, value=value, reason=reason))
            continue
        record, text = _apply_one(MAPPINGS[path], preview, text, value, step)
        (applied if isinstance(record, Applied) else skipped).append(record)
    document["Preview"] = preview
    document["Data"] = text
    return (document, applied, skipped)


def _apply_one(
    mapping: Mapping,
    preview: dict[str, Any],
    text: str,
    value: Any,
    step: str,
) -> tuple[Applied | Skipped, str]:
    """Write one mapped value into the preview map and the ASCCONV text.

    Parameters
    ----------
    mapping : Mapping
        The parameter being written.
    preview : dict
        The protocol's preview map, mutated in place.
    text : str
        The XProtocol text.
    value : Any
        The new preview-side value.
    step : str
        Step name, for the record.

    Returns
    -------
    tuple
        The record describing what happened, and the resulting text.
    """
    entry = preview.get(mapping.preview_path)
    if not isinstance(entry, dict):
        return (
            Skipped(
                step=step,
                label=mapping.label,
                value=value,
                reason=f"this protocol has no {mapping.preview_path} to write",
            ),
            text,
        )
    existing = read_ascconv(text, mapping.ascconv_key)
    if existing is None:
        return (
            Skipped(
                step=step,
                label=mapping.label,
                value=value,
                reason=f"ASCCONV block has no {mapping.ascconv_key}",
            ),
            text,
        )
    literal = format_like(float(value) * mapping.scale, existing)
    previous = entry.get("Value")
    entry["Value"] = type(previous)(value) if isinstance(previous, (int, float)) else value
    return (
        Applied(
            step=step,
            label=mapping.label,
            preview_path=mapping.preview_path,
            ascconv_key=mapping.ascconv_key,
            previous=previous,
            value=entry["Value"],
            ascconv_previous=existing,
            ascconv_value=literal,
        ),
        write_ascconv(text, mapping.ascconv_key, literal),
    )


def apply(archive: Archive, changes: MappingType[str, MappingType[str, Any]]) -> Manifest:
    """Apply per-step parameter changes to an archive, in memory.

    The archive is edited but not written; call :meth:`Archive.write` to save
    it. Only the protocols that actually change are re-addressed, so an
    archive whose requests all fail is left byte-identical.

    Parameters
    ----------
    archive : Archive
        The archive to edit.
    changes : mapping
        Step name to a mapping of preview path or printed label to new value.

    Returns
    -------
    Manifest
        What was written, what was refused, and how much was inherited.
    """
    manifest = Manifest()
    steps = {step.name: step for step in archive.steps}
    for name, requests in changes.items():
        step = steps.get(name)
        if step is None:
            for label, value in requests.items():
                manifest.skipped.append(
                    Skipped(step=name, label=label, value=value, reason="no such step in archive")
                )
            continue
        protocol = step.protocol
        document, applied, skipped = patch_document(protocol, requests, step=name)
        manifest.applied.extend(applied)
        manifest.skipped.extend(skipped)
        manifest.inherited += max(0, len(protocol.preview) - len(applied))
        if applied:
            archive.replace_content(protocol.instance, document)
    if manifest.applied:
        manifest.stale = ["lScanTimeSec", "lTotalScanTimeSec"]
    return manifest
