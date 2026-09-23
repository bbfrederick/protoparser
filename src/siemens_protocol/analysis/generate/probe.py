"""Generate probe archives, and decode what a scanner says about them.

An option scan authored on the console varies a *printed option* and we diff
the archives afterwards: the label is known and the stored field is
discovered.  A probe archive inverts that.  We vary a stored field, the
scanner loads the archive and prints a card, and the printout says which
label -- if any -- that field drives.  The field is known and the label is
discovered.

The inversion is worth having because the two directions are not equally
cheap.  An option scan costs a console session per option; a probe costs a
dictionary entry, so a single load can put fifty questions at once.  It also
answers three things the console direction cannot ask:

* **Which fields may be written on their own.**  A probe the console greys
  out is a field coupled to something else, and that is a result rather than
  a failure -- the only signal of coupling this library can obtain.
* **What the console recomputes.**  Diffing what comes back against what was
  sent names every field the sequence derived from the one that moved, which
  is how ``alTI[0] = 24 * TR + 10320`` was found by accident once and can now
  be looked for on purpose.
* **Which fields are invisible.**  A probe that loads, stays consistent and
  changes nothing in the printout is a stored field with no printed
  representation, which the driver's coverage accounting currently cannot
  distinguish from a field nobody has looked at.

Nothing here decides that a probed value is *valid*.  That remains what only
a scanner can say, exactly as :mod:`~.mappings` records: this module promises
only that it wrote what it was asked to.
"""

from __future__ import annotations

import collections
import dataclasses
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Iterable
from typing import Mapping as MappingType
from typing import Sequence

from ...exar import ascconv, generate, inspect, validate
from ...exar.archive import Archive, Protocol, Step
from ...exar.archive import read as read_archive
from . import build, mappings

#: Key families a probe list should never target.  The coil selection block
#: is hardware rather than a protocol choice -- the two scanners in the corpus
#: differ only there -- and the slice array is recomputed from six group
#: inputs, so writing an element is writing a derived quantity.
NEVER_PROBE: tuple[str, ...] = (
    "sCoilSelectMeas",
    "sSliceArray.asSlice[",
)

#: Fields the console derives, so a probe would be overwritten on load.  They
#: are named rather than filtered silently: a probe list that includes one is
#: asking a question whose answer is already known.
DERIVED_KEYS: frozenset[str] = frozenset(
    {
        "lScanTimeSec",
        "lTotalScanTimeSec",
        "dRefSNR",
        "dRefSNR_VOI",
        "dOverallImageScaleFactor",
    }
)

#: Stands in for a label one scan prints and the other does not.  A plain
#: ``None`` would be indistinguishable from a label printed with an empty
#: value, which is what a blanked ``AutoAlign`` looks like.
NOT_PRINTED = "(not printed)"

#: Longest scan name a probe is given.  The console has accepted names of
#: this length in every option scan in the corpus.
NAME_LIMIT = 40


@dataclass(frozen=True)
class Probe:
    """One question to put to the scanner.

    Attributes
    ----------
    key : str
        The ASCCONV assignment to write, spelled exactly as the block spells
        it, for example ``sKSpace.dPhaseOversamplingForDialog``.
    literal : str or None
        The literal to store, already formatted the way the console writes
        that field.  ``None`` deletes the assignment, which is how a sparse
        array spells zero and is a question in its own right.
    slug : str
        Short tag for the scan name, so a person reading the printout can see
        what each scan asks without consulting the manifest.
    question : str
        What this probe is for, carried into the manifest and the report.
    anchors : tuple of str
        Assignments this key is known to follow, needed only when the
        template does not already carry it.  ASCCONV is written in the
        schema's order rather than alphabetically, so a created assignment's
        position cannot be derived from its name; :func:`mine_anchor` reads
        one out of the corpus.
    context : tuple of Probe
        Further assignments written into the same scan to
        put the probed one somewhere it can be *seen*.  A sub-option beneath
        a switch that is off is invisible by construction -- run 1 wrote
        ``sRawFilter.ucMode`` and ``lSlope_256`` faithfully and learned
        nothing, because ``ucOn`` was absent -- so the switch has to be set
        alongside.

        This is deliberately not a second question.  The finding is still
        attributed to :attr:`key`; the context is the state it was asked in,
        and a mapping derived this way is verified only in that state.

        Context entries are themselves :class:`Probe` instances so that each
        carries its own anchor ladder -- a switch is as likely to be absent
        from the template as the sub-option it enables, and run 1's whole
        point is that a write nobody checked is a write that did nothing.
    """

    key: str
    literal: str | None
    slug: str
    question: str = ""
    anchors: tuple[str, ...] = ()
    context: tuple["Probe", ...] = ()


@dataclass
class Placed:
    """A probe as it was actually written into an archive.

    Attributes
    ----------
    name : str
        The scan name the probe was given, which is how the return is joined
        back to it.  The index prefix is what survives a console renaming it.
    probe : Probe
        The question asked.
    before : str or None
        The literal the template held, ``None`` when the assignment was
        absent.
    how : str
        What the write did: ``set``, ``created``, ``removed``, ``unchanged``
        or ``refused``.  A refusal is :func:`ascconv.insert_ascconv` having
        nowhere to put a new assignment, and must not be read as a write.
    recentred : bool
        Whether the slice array was rebuilt because this write invalidated
        it.
    anchor : str
        The assignment a created one was placed after, empty when the key
        was already present and no position had to be chosen.
    context : tuple of tuple
        The ``(key, literal)`` pairs written alongside to make the probed
        assignment visible, flattened for the manifest.
    anchor_rank : int
        Where that anchor sat in the mined ladder.  ``0`` is the assignment
        the console itself writes immediately before this key, which
        reproduces its output; anything higher is a fallback used because the
        nearer ones are absent from this template, and puts the new
        assignment somewhere the schema may not place it.  A probe resting on
        a fallback confounds the value it asks about with its own position,
        so the number is recorded rather than left implicit.
    """

    name: str
    probe: Probe
    before: str | None
    how: str
    recentred: bool = False
    anchor: str = ""
    anchor_rank: int = -1
    context: tuple[tuple[str, str | None], ...] = ()


@dataclass
class ProbeManifest:
    """Everything needed to decode a probe archive when it comes back.

    Attributes
    ----------
    program : str
        Name the generated program was given.
    donor : str
        Archive the probed scan was copied out of.
    source_scan : str
        Name of the known-good scan every probe is a copy of.
    sequence : str
        Sequence file name that scan runs.
    build_id : str
        Sequence build stamp, so a mapping derived here records the build it
        was derived from, which is what :attr:`mappings.Mapping.builds` gates on.
    outbound : str
        Path the archive was written to.
    controls : list of str
        Names of the byte-identical copies, which are what a returned
        printout is compared against.
    placed : list of Placed
        One record per probe, in running order.
    """

    program: str
    donor: str
    source_scan: str
    sequence: str
    build_id: str
    outbound: str
    controls: list[str] = field(default_factory=list)
    placed: list[Placed] = field(default_factory=list)

    def to_json(self, path: str) -> None:
        """Write the manifest beside the archive it describes.

        Parameters
        ----------
        path : str
            Destination file.

        Returns
        -------
        None
        """
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(dataclasses.asdict(self), handle, indent=2)
            handle.write("\n")

    @classmethod
    def from_json(cls, path: str) -> "ProbeManifest":
        """Read a manifest written by :meth:`to_json`.

        Parameters
        ----------
        path : str
            The manifest file.

        Returns
        -------
        ProbeManifest
            The manifest, with its probes restored as :class:`Probe` and
            :attr:`outbound` re-pointed at the archive beside it when the
            recorded path no longer resolves.
        """
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        raw["outbound"] = _beside(path, raw.get("outbound", ""))
        placed = [
            Placed(
                name=entry["name"],
                probe=_probe_from(entry["probe"]),
                before=entry["before"],
                how=entry["how"],
                recentred=entry.get("recentred", False),
                anchor=entry.get("anchor", ""),
                anchor_rank=entry.get("anchor_rank", -1),
                context=tuple(tuple(pair) for pair in entry.get("context", ())),
            )
            for entry in raw.pop("placed", [])
        ]
        return cls(placed=placed, **raw)


def _beside(manifest: str, recorded: str) -> str:
    """Resolve the archive a manifest describes, wherever the pair now lives.

    A manifest records the path its archive was written to, and the two are
    then moved together -- out of a build directory, onto a scanner, back into
    an inbound folder.  The recorded path is kept when it still resolves,
    because it is the more specific answer; otherwise the archive is looked
    for next to the manifest under the name it was written as.

    Parameters
    ----------
    manifest : str
        Path to the manifest file being read.
    recorded : str
        The path the manifest recorded.

    Returns
    -------
    str
        A path that exists, or ``recorded`` unchanged when neither does --
        the caller then fails on the real file, which is a clearer error than
        one naming a directory nobody asked about.
    """
    if not recorded or os.path.exists(recorded):
        return recorded
    sibling = os.path.join(os.path.dirname(os.path.abspath(manifest)), os.path.basename(recorded))
    return sibling if os.path.exists(sibling) else recorded


def _probe_from(raw: MappingType[str, Any]) -> Probe:
    """Rebuild a probe from its JSON form, context and all.

    Parameters
    ----------
    raw : mapping
        One probe as :meth:`ProbeManifest.to_json` wrote it.

    Returns
    -------
    Probe
        The probe, with tuple fields restored -- JSON has only lists, and
        :class:`Probe` is frozen and hashed on its contents.
    """
    fields = dict(raw)
    fields["anchors"] = tuple(fields.get("anchors", ()))
    fields["context"] = tuple(_probe_from(entry) for entry in fields.get("context", ()))
    return Probe(**fields)


def scan_name(index: int, slug: str) -> str:
    """Compose the name a probe's scan carries on the console.

    The index leads so that it survives the slug being truncated, and so a
    scan renamed on the console can still be recognised.  Only characters the
    printout and the archive both round-trip are kept.

    Parameters
    ----------
    index : int
        Position of this probe in the run, from one.
    slug : str
        Short tag naming what the probe asks.

    Returns
    -------
    str
        The scan name, no longer than :data:`NAME_LIMIT`.
    """
    clean = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_") or "probe"
    prefix = f"P{index:03d}_"
    return prefix + clean[: NAME_LIMIT - len(prefix)]


def write_key(
    text: str, key: str, literal: str | None, anchors: tuple[str, ...] = ()
) -> tuple[str, str]:
    """Write one ASCCONV assignment, creating or deleting it as needed.

    This is the raw counterpart of :func:`mappings.patch_document`, which writes
    through a verified mapping.  A probe has no mapping by definition -- the
    mapping is what it is trying to establish -- so it addresses the
    assignment directly, and takes on the obligation the mapping layer
    normally discharges: a created assignment has to go in the schema's
    position rather than at the end, and a refusal has to be reported rather
    than mistaken for a write.

    Parameters
    ----------
    text : str
        The XProtocol text holding the ASCCONV block.
    key : str
        The assignment to write.
    literal : str or None
        The literal to store, or ``None`` to delete the assignment.
    anchors : tuple of str, optional
        Assignments the key is known to follow, used only when it has to be
        created.

    Returns
    -------
    tuple of str
        The new text, and what was done: ``set``, ``created``, ``removed``,
        ``unchanged`` or ``refused``.
    """
    existing = ascconv.read_ascconv(text, key)
    if literal is None:
        if existing is None:
            return (text, "unchanged")
        return (ascconv.remove_ascconv(text, key), "removed")
    if existing is None:
        grown = ascconv.insert_ascconv(text, key, literal, anchors=tuple(anchors))
        # insert_ascconv reports "nowhere to put it" by returning the text
        # unchanged.  Unchecked, that is a write reported as applied which
        # wrote nothing.
        return (text, "refused") if grown == text else (grown, "created")
    if existing == literal:
        return (text, "unchanged")
    return (ascconv.write_ascconv(text, key, literal), "set")


def nearest_anchor(text: str, anchors: Sequence[str]) -> tuple[str, int]:
    """Pick the anchor an insertion will actually land on.

    :func:`ascconv.insert_ascconv` tries anchors in order and uses the first
    the document carries, so this reproduces that choice in order to record
    it.  Knowing which rung was used is what separates a probe that
    reproduces the console's own layout from one that guessed a position.

    Parameters
    ----------
    text : str
        The XProtocol text.
    anchors : sequence of str
        The mined ladder, nearest first.

    Returns
    -------
    tuple
        The anchor that will be used and its index in the ladder, or an
        empty string and ``-1`` when none of them is present.
    """
    for rank, anchor in enumerate(anchors):
        if ascconv.read_ascconv(text, anchor) is not None:
            return (anchor, rank)
    return ("", -1)


def apply_probe(protocol: Protocol, probe: Probe) -> tuple[dict[str, Any], str, bool, str, int]:
    """Write one probe into a copy of a protocol's document.

    The slice array is rebuilt when this write invalidated it, on the same
    terms as a driven build: a group input moved without its array following
    leaves a protocol that loads, prints nothing about it and describes no
    coherent geometry, which a scanner has been observed to accept in exactly
    that state.

    Parameters
    ----------
    protocol : Protocol
        The protocol to probe.  Left as it was.
    probe : Probe
        The question to write.

    Returns
    -------
    tuple
        The new document, what the write did, whether the slice array was
        recomputed, and the anchor a created assignment was placed after
        with its rank in the ladder.
    """
    document = dict(protocol.document)
    before = document.get("Data", "")
    for entry in probe.context:
        before, how = write_key(before, entry.key, entry.literal, entry.anchors)
        if how == "refused":
            return (document, f"context refused: {entry.key}", False, "", -1)
    anchor, rank = ("", -1)
    if probe.literal is not None and ascconv.read_ascconv(before, probe.key) is None:
        anchor, rank = nearest_anchor(before, probe.anchors)
    after, how = write_key(before, probe.key, probe.literal, probe.anchors)
    recentred = False
    if how in ("set", "created", "removed"):
        rebuilt = build.recentre(before, after)
        recentred = rebuilt != after
        after = rebuilt
    document["Data"] = after
    return (document, how, recentred, anchor, rank)


def _source_step(donor: Archive, name: str) -> Step:
    """Find the one step a probe run copies.

    Parameters
    ----------
    donor : Archive
        Archive holding the known-good scan.
    name : str
        Its scan name.

    Returns
    -------
    Step
        The step to copy.

    Raises
    ------
    ValueError
        If no step carries that name, or if several do.  A repeated name is
        ordinary in an option scan, and resolving it to one copy would probe
        an arbitrary protocol while the caller believed it had named one.
    """
    found = [step for step in donor.steps if step.name == name and step.acquires]
    if not found:
        known = ", ".join(sorted({s.name for s in donor.steps if s.acquires}))
        raise ValueError(f"no scan named {name!r}; the archive holds: {known}")
    if len(found) > 1:
        raise ValueError(f"{len(found)} scans are named {name!r}; probe an unambiguous one")
    return found[0]


def build_probes(
    probes: Sequence[Probe],
    donor_path: str,
    source_scan: str,
    out_path: str,
    *,
    seed_path: str,
    program: str,
    controls: int = 2,
) -> ProbeManifest:
    """Assemble a probe archive and the manifest that decodes its return.

    The archive is grown from a one-scan seed rather than from the donor, so
    the run carries the probes and nothing else: every extra protocol is a
    scan that can be greyed out, and one that refuses to import takes the
    whole program with it.

    Controls are byte-identical copies of the source scan, placed first and
    last.  They are what a returned printout is compared against, and the
    comparison has to be against a control *in the same return* -- the
    console repairs protocols on import, reproducibly and for reasons having
    nothing to do with the probe, so comparing against the donor's own
    printout would attribute those repairs to whichever probe sat beside
    them.

    Parameters
    ----------
    probes : sequence of Probe
        The questions to ask, in the order they should run.
    donor_path : str
        Archive holding the known-good scan.
    source_scan : str
        Its name.
    out_path : str
        Where to write the probe archive.
    seed_path : str
        A small archive to grow from, supplying the program and folder tree.
    program : str
        Name to give the generated program.  A generated archive otherwise
        inherits the seed's name and the console disambiguates it, which
        makes two runs hard to tell apart on the scanner.
    controls : int, optional
        How many byte-identical copies to include, at least one.

    Returns
    -------
    ProbeManifest
        What was written, and everything :func:`decode` needs.

    Raises
    ------
    ValueError
        If the seed and donor are different releases, if the source scan is
        ambiguous, or if the assembled archive does not validate.
    """
    if controls < 1:
        raise ValueError("a probe run needs at least one control to compare against")
    donor = read_archive(donor_path)
    origin = _source_step(donor, source_scan)
    sequence = inspect.sequence_file(origin.protocol)
    stamp = ascconv.build_id(ascconv.sequence_stamp(origin.protocol))

    names = _lay_out(probes, controls)
    with tempfile.TemporaryDirectory() as scratch:
        grown = os.path.join(scratch, "grown.exar1")
        _populate(seed_path, donor, origin, names, program, grown)
        final = read_archive(grown)
        placed = _patch_probes(final, probes, names)
        final.write(out_path)

    problems = validate.problems(read_archive(out_path))
    if problems:
        raise ValueError("the probe archive does not validate: " + "; ".join(problems))
    return ProbeManifest(
        program=program,
        donor=donor_path,
        source_scan=source_scan,
        sequence=sequence,
        build_id=stamp,
        outbound=out_path,
        controls=[name for name, probe in names if probe is None],
        placed=placed,
    )


def _lay_out(probes: Sequence[Probe], controls: int) -> list[tuple[str, Probe | None]]:
    """Decide the running order of a probe run.

    Parameters
    ----------
    probes : sequence of Probe
        The questions to ask.
    controls : int
        How many controls to interleave.

    Returns
    -------
    list of tuple
        Scan name and the probe it carries, ``None`` for a control.
    """
    order: list[tuple[str, Probe | None]] = [("C00_control", None)]
    step = max(1, len(probes) // max(1, controls - 1)) if controls > 1 else len(probes) + 1
    for index, probe in enumerate(probes, start=1):
        order.append((scan_name(index, probe.slug), probe))
        if index % step == 0 and sum(1 for _, p in order if p is None) < controls:
            order.append((f"C{index:02d}_control", None))
    if sum(1 for _, p in order if p is None) < controls:
        order.append((f"C{len(probes) + 1:02d}_control", None))
    return order


def _populate(
    seed_path: str,
    donor: Archive,
    origin: Step,
    names: Sequence[tuple[str, Probe | None]],
    program: str,
    destination: str,
) -> None:
    """Copy the source scan once per probe and write the result.

    The archive has to be written and re-read before the copies can be
    patched: the live instance map is rebuilt on read, so a step appended in
    memory is not addressable until then.

    Parameters
    ----------
    seed_path : str
        The archive to grow from.
    donor : Archive
        Archive the source scan comes from.
    origin : Step
        The scan to copy.
    names : sequence of tuple
        Scan names in running order, with their probes.
    program : str
        Name to give the program.
    destination : str
        Where to write the grown archive.

    Returns
    -------
    None
    """
    seeded = read_archive(seed_path)
    node = seeded.program
    if node is None:
        raise ValueError(f"{seed_path} holds no program to append to")
    generate.rename(seeded, node, program)
    for name, _ in names:
        generate.duplicate_step(seeded, origin, name, source=donor, program=node)
    seeded.write(destination)


def _patch_probes(
    final: Archive, probes: Sequence[Probe], names: Sequence[tuple[str, Probe | None]]
) -> list[Placed]:
    """Write each probe into its own copy.

    Parameters
    ----------
    final : Archive
        The grown archive, freshly read so its copies are addressable.
    probes : sequence of Probe
        The questions asked, for the records.
    names : sequence of tuple
        Scan names in running order, with their probes.

    Returns
    -------
    list of Placed
        One record per probe.
    """
    steps = {step.name: step for step in final.steps}
    placed: list[Placed] = []
    for name, probe in names:
        if probe is None:
            continue
        step = steps[name]
        before = ascconv.read_ascconv(step.protocol.xprotocol, probe.key)
        document, how, recentred, anchor, rank = apply_probe(step.protocol, probe)
        if how in ("set", "created", "removed"):
            final.replace_content(step.protocol.instance, document)
        placed.append(
            Placed(
                name=name,
                probe=probe,
                before=before,
                how=how,
                recentred=recentred,
                anchor=anchor,
                anchor_rank=rank,
                context=tuple((e.key, e.literal) for e in probe.context),
            )
        )
    return placed


# --------------------------------------------------------------------------
# Choosing what to ask
# --------------------------------------------------------------------------


def acquiring_protocols(paths: Iterable[str], sequence: str = "") -> list[Protocol]:
    """Collect the protocols a set of archives holds.

    Parameters
    ----------
    paths : iterable of str
        Archive paths.  Any that cannot be read is skipped, since a corpus
        sweep should not be stopped by one unreadable file.
    sequence : str, optional
        Substring the sequence file name must contain.  Empty takes every
        acquiring scan.

    Returns
    -------
    list of Protocol
        The protocols found, in the order the archives were given.
    """
    found: list[Protocol] = []
    for path in paths:
        try:
            held = read_archive(path)
        except Exception:
            continue
        for step in held.steps:
            if not step.acquires:
                continue
            try:
                name = inspect.sequence_file(step.protocol)
            except Exception:
                continue
            if sequence and sequence not in name:
                continue
            found.append(step.protocol)
    return found


def mine_anchor(
    key: str, protocols: Sequence[Protocol], limit: int = 6, depth: int = 5
) -> tuple[str, ...]:
    """Read out of the corpus which assignment a key follows.

    ASCCONV is emitted in the schema's order, so a created assignment cannot
    be placed by sorting its name; :data:`ascconv.SPARSE_ANCHORS` records the
    predecessor for each key that has needed one, read off the console's own
    output.  A probe targets keys nobody has curated, and the same fact is
    recoverable at scale: every protocol that carries the key states its
    predecessor by writing it on the line above.

    A ladder is returned rather than a single answer, because the immediate
    predecessor is often sparse itself and absent from the very protocol
    being probed -- both of this module's first two probes were refused that
    way, their anchors being ``ucOn`` flags the template omits.  Candidates
    are ordered by how close they sit to the key and then by how many
    protocols agree, which is why :data:`ascconv.SPARSE_ANCHORS` holds tuples
    too.

    Parameters
    ----------
    key : str
        The assignment to place.
    protocols : sequence of Protocol
        Protocols to read.  Only those carrying the key contribute.
    limit : int, optional
        How many candidates to return.
    depth : int, optional
        How far back to look for a fallback when a nearer anchor is absent.

    Returns
    -------
    tuple of str
        Assignments the key has been observed to follow, nearest first.
        Empty when no protocol given carries the key, in which case the
        assignment cannot be created and a probe for it must be refused
        rather than appended to the end of the block.
    """
    rungs: list[collections.Counter[str]] = [collections.Counter() for _ in range(depth)]
    for protocol in protocols:
        keys = list(inspect.ascconv_table(protocol.xprotocol))
        try:
            at = keys.index(key)
        except ValueError:
            continue
        for back in range(1, min(depth, at) + 1):
            rungs[back - 1][keys[at - back]] += 1
    ordered: list[str] = []
    for rung in rungs:
        for name, _ in rung.most_common():
            if name not in ordered:
                ordered.append(name)
    return tuple(ordered[:limit])


def settable_keys(
    protocols: Sequence[Protocol], skip_mapped: bool = True
) -> "collections.Counter[str]":
    """Rank a sequence's ASCCONV keys by how much the corpus varies them.

    A key holding one value across every scan anyone ever saved is either
    derived or never offered; a key taking many is one people set.  That is a
    prior rather than a proof -- ``lScanTimeSec`` varies as much as anything
    and is computed -- so :data:`DERIVED_KEYS` is subtracted by name and the
    result is a list of candidates to probe, not a list of findings.

    It is what makes the method tractable.  A CMRR BOLD protocol carries
    around 1800 assignments; strip :data:`NEVER_PROBE`, the churn, the
    mapped keys and everything constant, and what is left is small enough to
    put to a scanner in a single load.

    Parameters
    ----------
    protocols : sequence of Protocol
        Protocols of one sequence.
    skip_mapped : bool, optional
        Whether to drop keys a verified mapping already writes.

    Returns
    -------
    collections.Counter
        Key to the number of distinct values observed, counting only keys
        seen with more than one.
    """
    values: dict[str, set[str]] = collections.defaultdict(set)
    for protocol in protocols:
        for key, literal in inspect.ascconv_table(protocol.xprotocol).items():
            if ascconv.is_churn(key) or key.startswith(NEVER_PROBE) or key in DERIVED_KEYS:
                continue
            values[key].add(literal)
    if skip_mapped:
        values = {key: seen for key, seen in values.items() if not _is_mapped(key)}
    return collections.Counter({key: len(seen) for key, seen in values.items() if len(seen) > 1})


def _is_mapped(key: str) -> bool:
    """Return whether a verified mapping already writes this assignment.

    Parameters
    ----------
    key : str
        An ASCCONV key as the block spells it.

    Returns
    -------
    bool
        True when some mapping claims it, its wildcard form, or its stem.
    """
    wild = re.sub(r"\[\d+\]", "[*]", key)
    claimed = {mapping.ascconv_key for mapping in mappings.MAPPINGS}
    stems = {name.split("[")[0] for name in claimed}
    return key in claimed or wild in claimed or key.split("[")[0] in stems


# --------------------------------------------------------------------------
# Decoding the return
# --------------------------------------------------------------------------


@dataclass
class Finding:
    """What one probe turned out to say.

    Attributes
    ----------
    name : str
        The probe's scan name.
    probe : Probe
        The question asked.
    verdict : str
        ``inconsistent`` when the scan is not in the return, which on this
        workflow means it was deleted before the protocol could be saved --
        the console's way of saying the value is not consistent with the rest
        of the parameter set.  ``held`` when the written literal came back
        unchanged, ``revised`` when the console replaced it, and ``absent``
        when the assignment is gone.
    stored : str or None
        What the key holds in the return.
    recomputed : dict
        Other assignments the console moved, as ``key -> (sent, returned)``.
        Churn is excluded; the derived scan times are not, since a probe
        moving them is evidence about what the parameter feeds.
    uncontrolled : tuple of str
        Context assignments this run never asked on their own, so nothing
        here can say whether a printed difference belongs to the probe or
        to its switch. Absence of a control is not evidence the probe
        caused what moved, which is why such a finding is never reported
        as clean.
    from_context : dict
        The subset of :attr:`printed` a *context* write produced rather than
        the probed assignment, recognised by another probe in the same run
        having asked that context on its own.  Without this every probe
        carrying a switch inherits the switch's own printed change and reads
        as though its field caused it: run 2 wrote ``sRawFilter.ucMode``,
        ``lSlope_256`` and ``sHammingFilter.lWidthPercent`` beneath their
        switches, and all three appeared to move the filter's On/Off line,
        which is the switch's doing and was already known.
    printed : dict
        Printed parameters that differ from the control, as
        ``(section, label) -> (control, probe)``.  These are the candidate
        mappings, and a probe yielding exactly one of them with an empty
        ``recomputed`` is the clean case.

        :data:`NOT_PRINTED` on either side means the label appears on only
        one of the two scans.  That is a real outcome and not a bookkeeping
        detail: turning a physio signal on made the console print an
        ``Average Cycle`` row the control does not have, and a comparison
        restricted to labels common to both reported that probe as having no
        effect at all.
    """

    name: str
    probe: Probe
    verdict: str
    stored: str | None = None
    recomputed: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    printed: dict[tuple[str, str], tuple[Any, Any]] = field(default_factory=dict)
    from_context: dict[tuple[str, str], tuple[Any, Any]] = field(default_factory=dict)
    uncontrolled: tuple[str, ...] = ()

    @property
    def own_printed(self) -> dict[tuple[str, str], tuple[Any, Any]]:
        """Return the printed differences this probe's own field accounts for.

        Returns
        -------
        dict
            :attr:`printed` without whatever a context write explains.
        """
        return {
            where: value for where, value in self.printed.items() if where not in self.from_context
        }

    @property
    def confounded(self) -> bool:
        """Return whether a switch could account for what this probe printed.

        True when the probe wrote a context that no other probe in the run
        asked on its own, and something printed. The run cannot then
        separate the two, and the fix is a design rule rather than a
        calculation: a run that writes a context must also ask it alone.

        Returns
        -------
        bool
            Whether the attribution is unresolved.
        """
        return bool(self.uncontrolled) and bool(self.own_printed)

    @property
    def clean(self) -> bool:
        """Return whether this probe names one label and disturbed nothing.

        Returns
        -------
        bool
            True when the write survived, exactly one printed parameter
            moved that the probe's own field accounts for, and the console
            recomputed nothing beyond respelling what was written.
        """
        return (
            self.verdict == "held"
            and len(self.own_printed) == 1
            and not self.recomputed
            and not self.confounded
        )

    @property
    def reconciled(self) -> bool:
        """Return whether the console rejected this value and rebuilt around it.

        Three outcomes share the shape "something else moved", and they mean
        opposite things, so they are separated by whether the probed value
        itself survived rather than by how much moved.  A value that *held*
        beside other movement is a parameter the sequence derives from it --
        moving TR recomputed the scan time.  A value that did *not* hold, with
        nothing else moving, is a field the sequence simply owns, which is
        what the four ``sIR.adFree`` probes turned out to be.  A value that
        did not hold *and* dragged other fields with it is the console
        refusing the write and reconciling the protocol around the refusal.

        That third case is the dangerous one and it is why no threshold is
        used here: an impossible TR was reverted to the template's value and
        took the entire manual coil selection with it, 504 assignments
        deleted, while the scan loaded and was never greyed out.  A probe in
        that state has not answered its question and its printed differences
        belong to the reconciliation, not to the parameter.

        Returns
        -------
        bool
            True when the written value did not survive and other fields
            moved with it.
        """
        return self.verdict in ("revised", "absent") and bool(self.recomputed)


def same_value(stored: str, wanted: str | None) -> bool:
    """Return whether a returned literal is the value that was written.

    The console respells what it stores.  A ``uc`` flag written as ``1`` comes
    back as ``0x1``, and comparing the strings reports that as the console
    having *revised* the value -- which is the opposite of what happened, and
    would make every flag probe look like a refusal.  Numbers are therefore
    compared as numbers, and only a genuine difference counts.

    Parameters
    ----------
    stored : str
        The literal the scanner returned.
    wanted : str or None
        The literal this library wrote, ``None`` for a deletion.

    Returns
    -------
    bool
        True when the two denote the same value.
    """
    if wanted is None:
        return False
    if stored.strip() == wanted.strip():
        return True
    for parse in (lambda s: int(s, 0), float):
        try:
            return parse(stored) == parse(wanted)
        except (TypeError, ValueError):
            continue
    return False


def printed_by_section(scan: MappingType[str, Any]) -> dict[tuple[str, str], Any]:
    """Index a parsed scan's parameters by the section that printed them.

    Flattening a scan first is the trap the ``Position`` note describes: a
    label printed on several cards collapses to whichever won, and the
    comparison then reports a difference in a section that has none.

    Parameters
    ----------
    scan : mapping
        A parsed scan, as :meth:`Scan.to_dict` produces.

    Returns
    -------
    dict
        Section title and label to the printed value.
    """
    printed: dict[tuple[str, str], Any] = {}
    for section, parameters in scan.get("sections", {}).items():
        for label, value in parameters.items():
            printed[(section, label)] = build.printed_value(value)
    return printed


def _ascconv_of(step: Step) -> "collections.OrderedDict[str, str]":
    """Read one step's ASCCONV table.

    Parameters
    ----------
    step : Step
        The step to read.

    Returns
    -------
    collections.OrderedDict
        Assignment to literal.
    """
    return inspect.ascconv_table(step.protocol.xprotocol)


def _drift(
    sent: MappingType[str, str], back: MappingType[str, str], key: str
) -> dict[str, tuple[str | None, str | None]]:
    """Name every assignment the console moved, apart from the probed one.

    Parameters
    ----------
    sent : mapping
        The ASCCONV table as this library wrote it.
    back : mapping
        The table the scanner returned.
    key : str
        The probed assignment, excluded from the result.

    A literal the console merely respelled is not a move. Writing a ``uc``
    flag as ``1`` and reading ``0x1`` back would otherwise report the context
    of every switched probe as a field the console had changed.

    Returns
    -------
    dict
        Assignment to the pair of literals, sent then returned.
    """
    moved: dict[str, tuple[str | None, str | None]] = {}
    for name in set(sent) | set(back):
        if name == key or ascconv.is_churn(name):
            continue
        was, now = sent.get(name), back.get(name)
        if was == now:
            continue
        if was is not None and now is not None and same_value(now, was):
            continue
        moved[name] = (was, now)
    return moved


def decode(
    manifest: ProbeManifest,
    returned_archive: str,
    returned_scans: Sequence[MappingType[str, Any]],
) -> list[Finding]:
    """Read a scanner's answer to a probe run.

    Two baselines are used, for two different questions.  What the scanner
    did to a *stored* value is measured against the archive this library
    sent, because that is the only thing the return can be compared with
    field for field.  What the scanner *printed* is measured against a
    control in the same return, never against the donor's own printout: the
    console repairs protocols on import reproducibly, and comparing across
    imports would credit those repairs to whichever probe stood next to them.

    A probe missing from the return is reported as ``inconsistent`` rather
    than as an error.  On this workflow that is the informative outcome --
    an inconsistent scan cannot be saved or printed at all, so deleting it is
    forced, and the deletion is the console stating that the value does not
    go with the rest of the parameter set.

    Parameters
    ----------
    manifest : ProbeManifest
        The manifest written beside the outbound archive.
    returned_archive : str
        The archive the scanner saved back.
    returned_scans : sequence of mapping
        The returned PDF's parsed scans.  Empty is allowed, and then only the
        stored half is decoded.

    Returns
    -------
    list of Finding
        One per probe, in the order they were asked.

    Raises
    ------
    ValueError
        If no control survived, leaving the printed half with nothing to
        compare against.
    """
    sent = {step.name: _ascconv_of(step) for step in read_archive(manifest.outbound).steps}
    back_archive = read_archive(returned_archive)
    back = {step.name: _ascconv_of(step) for step in back_archive.steps}
    printed = {scan.get("name", ""): printed_by_section(scan) for scan in returned_scans}

    control = next((name for name in manifest.controls if name in back), None)
    if control is None and manifest.controls:
        raise ValueError("no control survived the round trip; the printed half cannot be read")
    reference = printed.get(control or "", {})

    findings = [_read_one(placed, sent, back, printed, reference) for placed in manifest.placed]
    _attribute_context(findings)
    return findings


def _attribute_context(findings: list[Finding]) -> None:
    """Credit a context write with the printed changes it explains.

    A probe asked beneath a switch inherits whatever the switch itself
    prints, and reading that as the probed field's doing is how three of
    run 2's filter probes appeared to move a line run 1 had already pinned
    to the switch. The correction needs no new evidence: a run that writes
    a context should also ask that context on its own, and the control is
    then sitting in the same return.

    Findings are edited in place.

    Parameters
    ----------
    findings : list of Finding
        Every probe in the run, already decoded.

    Returns
    -------
    None
    """
    controls = {
        (found.probe.key, found.probe.literal): found.printed
        for found in findings
        if not found.probe.context
    }
    for found in findings:
        missing = []
        for entry in found.probe.context:
            if (entry.key, entry.literal) not in controls:
                missing.append(entry.key)
                continue
            for where, value in controls[(entry.key, entry.literal)].items():
                if found.printed.get(where) == value:
                    found.from_context[where] = value
        found.uncontrolled = tuple(missing)


def _read_one(
    placed: Placed,
    sent: MappingType[str, MappingType[str, str]],
    back: MappingType[str, MappingType[str, str]],
    printed: MappingType[str, MappingType[tuple[str, str], Any]],
    reference: MappingType[tuple[str, str], Any],
) -> Finding:
    """Decode one probe's outcome.

    Parameters
    ----------
    placed : Placed
        The probe as it was written.
    sent : mapping
        Scan name to the ASCCONV table this library wrote.
    back : mapping
        Scan name to the table the scanner returned.
    printed : mapping
        Scan name to its printed parameters, indexed by section.
    reference : mapping
        The control's printed parameters.

    Returns
    -------
    Finding
        What this probe says.
    """
    if placed.how == "refused" or placed.how.startswith("context refused"):
        return Finding(name=placed.name, probe=placed.probe, verdict="not written")
    if placed.name not in back:
        return Finding(name=placed.name, probe=placed.probe, verdict="inconsistent")

    table = back[placed.name]
    stored = table.get(placed.probe.key)
    wanted = placed.probe.literal
    if stored is None:
        verdict = "held" if wanted is None else "absent"
    else:
        verdict = "held" if same_value(stored, wanted) else "revised"

    moved = _drift(sent.get(placed.name, {}), table, placed.probe.key)
    mine = printed.get(placed.name, {})
    differs = {}
    if mine or reference:
        for where in set(mine) | set(reference):
            was = reference.get(where, NOT_PRINTED)
            now = mine.get(where, NOT_PRINTED)
            if was != now:
                differs[where] = (was, now)
    return Finding(
        name=placed.name,
        probe=placed.probe,
        verdict=verdict,
        stored=stored,
        recomputed=moved,
        printed=differs,
    )


def report(findings: Sequence[Finding], limit: int = 6) -> str:
    """Render a probe run's answers for a person to read.

    Parameters
    ----------
    findings : sequence of Finding
        What :func:`decode` produced.
    limit : int, optional
        How many printed differences to name per probe.

    Returns
    -------
    str
        The report.
    """
    tally = collections.Counter(found.verdict for found in findings)
    shaken = [found.name for found in findings if found.reconciled]
    lines = [
        f"{len(findings)} probes: "
        + ", ".join(f"{count} {verdict}" for verdict, count in tally.most_common()),
        "",
    ]
    if shaken:
        lines[1:1] = [
            f"{len(shaken)} refused and reconciled -- the console rejected the value and "
            f"rebuilt around it, so these answered nothing: {', '.join(shaken)}",
        ]
    for found in findings:
        mark = "  !!" if found.reconciled else "  ok" if found.clean else "    "
        if found.confounded:
            mark = "  ??"
        lines.append(f"{mark}  {found.name}  {found.probe.key} = {found.probe.literal!r}")
        lines.append(
            f"        {found.verdict}" + (f", stored {found.stored!r}" if found.stored else "")
        )
        for (section, label), (was, now) in list(found.own_printed.items())[:limit]:
            lines.append(f"        prints  {section} / {label}: {was!r} -> {now!r}")
        if len(found.own_printed) > limit:
            lines.append(
                f"        ... and {len(found.own_printed) - limit} more printed differences"
            )
        if found.confounded:
            lines.append(
                "        cannot attribute: this run never asked "
                f"{', '.join(found.uncontrolled)} on its own"
            )
        for (section, label), (was, now) in list(found.from_context.items())[:limit]:
            lines.append(
                f"        (context)  {section} / {label}: {was!r} -> {now!r}"
                "  -- this probe's switch, not its field"
            )
        for key, (was, now) in list(found.recomputed.items())[:limit]:
            lines.append(f"        console moved  {key}: {was!r} -> {now!r}")
        if len(found.recomputed) > limit:
            lines.append(f"        ... and {len(found.recomputed) - limit} more recomputed")
    return "\n".join(lines)
