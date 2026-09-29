"""Compare two parsed protocols, or two individual scans.

The job is to separate *substantive* differences -- a parameter that actually
changed, appeared or disappeared -- from the cosmetic churn that a software
upgrade drags along. Siemens recapitalizes and re-abbreviates freely between
releases: VE11C's ``Dist. factor`` is XA60's ``Distance Factor``, its
``Single shot`` is XA60's ``Single Shot``, and ``1`` becomes ``1.00``.

Cosmetic differences are classified and reported in their own buckets rather
than suppressed. Nothing is ever silently merged: if two keys were matched
through normalization, the report says so and shows both spellings. That
matters because the whole point of the tool is to surface real differences,
and a normalizer that manufactures agreement would defeat it.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Collection, Mapping, Sequence

from .. import timing
from .vocabulary import Vocabulary, load_vocabulary

#: Abbreviations Siemens expanded between releases. Deliberately short: each
#: entry was confirmed against the matched example pairs. Semantic renames
#: such as ``Coil Combine Mode`` -> ``Coil Combination`` are *not* listed,
#: because collapsing those would hide a genuine change of meaning.
ABBREVIATIONS = {
    "accel": "acceleration",
    # VE11C's "Flow comp." is XA30/XA60's "Flow Compensation": same section
    # in every release, and no release prints both spellings. Expanded here
    # rather than mapped in a vocabulary because it is a pure abbreviation,
    # which also covers the numbered variants ("Flow comp. 1") for free.
    # Only a bare "comp" token is expanded, so "Inline Composing" and
    # "Compensate T2 Decay" are untouched.
    "comp": "compensation",
    "corr": "correction",
    "dist": "distance",
    "enc": "encoding",
    "ref": "reference",
    "suppr": "suppression",
}

#: Trailing ``#2``/``#3`` marks a key repeated within one section.
_REPEAT_RE = re.compile(r"\s*#(\d+)\s*$")
#: A numeric value with an optional unit, for formatting-only comparison.
_NUMERIC_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(.*)$")

#: Classification of a single parameter difference.
CHANGED = "changed"
ONLY_LEFT = "only_left"
ONLY_RIGHT = "only_right"
RENAMED = "renamed"
REFORMATTED = "reformatted"
RECASED = "recased"
#: A field the console rewrites on every save, so a difference there says the
#: protocol was saved again rather than that anything about it changed. Only
#: an archive carries these; a printout does not print them.
CHURN = "churn"

#: Differences that represent a real protocol change.
SUBSTANTIVE = (CHANGED, ONLY_LEFT, ONLY_RIGHT)
#: Differences that are presentation only, or are not about the protocol.
COSMETIC = (RENAMED, REFORMATTED, RECASED, CHURN)

#: Separator between a section's card and its tab, as every release prints it.
_SECTION_SPLIT = " - "
#: Pseudo-section for the header box, so it can be named like any other.
HEADER_SECTION = "header"


def normalize_section(name: str) -> str:
    """Reduce a section name to the top-level card it belongs to.

    Siemens nests sections exactly one level deep: ``Contrast - Common`` and
    ``Contrast - Dynamic`` are two tabs of the same ``Contrast`` card, and
    VB17A prints the card on its own. Folding to the card is what makes a
    section name portable across releases, and short enough to type.

    Parameters
    ----------
    name : str
        A section name as printed, such as ``"Resolution - iPAT"``.

    Returns
    -------
    str
        The lower-cased card name, such as ``"resolution"``. An empty name
        comes back empty.
    """
    return name.split(_SECTION_SPLIT, 1)[0].strip().lower()


def merge_section_order(right: Sequence[str], left: Sequence[str]) -> list[str]:
    """Order two scans' sections the way the right-hand one prints them.

    The right-hand protocol is the one being edited, so its printed order is
    the order to report in. A section only the left-hand scan has -- a card a
    release dropped, or split differently -- is slotted in after the last
    right-hand section of the same card, so VB17A's ``Contrast`` lands beside
    VE11C's ``Contrast - Common`` rather than drifting to the end.

    Parameters
    ----------
    right, left : sequence of str
        Section names in printed order, right-hand scan first.

    Returns
    -------
    list of str
        Every section name from either side, each once.
    """
    order = list(dict.fromkeys(right))
    for name in left:
        if name in order:
            continue
        card = normalize_section(name)
        kin = [i for i, seen in enumerate(order) if normalize_section(seen) == card]
        order.insert(kin[-1] + 1 if kin else len(order), name)
    return order


def section_groups(protocol: Mapping) -> list[str]:
    """The section names a protocol offers to a filter.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol.

    Returns
    -------
    list of str
        Normalized card names, sorted, led by the header pseudo-section.
    """
    names = {
        normalize_section(section)
        for scan in protocol.get("scans", [])
        for section in scan.get("sections", {})
    }
    names.discard("")
    return [HEADER_SECTION, *sorted(names)]


def base_key(key: str) -> str:
    """Strip the repeat suffix from a key.

    Parameters
    ----------
    key : str
        A key as it appears in a scan's sections, possibly suffixed ``#2``.

    Returns
    -------
    str
        The key without its repeat suffix.
    """
    return _REPEAT_RE.sub("", key)


def normalize_key(key: str) -> str:
    """Reduce a key to a form comparable across releases.

    Case, punctuation and spacing are folded away, and the small confirmed
    abbreviation table is expanded. Anything beyond that is left alone, so a
    genuine rename stays a rename.

    Parameters
    ----------
    key : str
        A parameter label.

    Returns
    -------
    str
        The normalized form, used only for matching, never for display.
    """
    words = re.split(r"[^a-z0-9]+", base_key(key).lower())
    return " ".join(ABBREVIATIONS.get(w, w) for w in words if w)


def canonical_key(key: str, vocabulary: Vocabulary | None = None) -> str:
    """Reduce a key to the standard name shared across releases.

    Ordinary normalization -- case, punctuation, abbreviations -- handles the
    relabeling that is purely typographic. A release's vocabulary handles what
    is left: parameters the vendor actually renamed, such as VE11C's
    ``PAT mode`` becoming XA60's ``Acceleration Mode``.

    Parameters
    ----------
    key : str
        A parameter label, possibly with a repeat suffix.
    vocabulary : Vocabulary or None, optional
        The release's vocabulary. Without one, only normalization applies.

    Returns
    -------
    str
        The canonical name. Vocabulary hits come back snake_case, so they are
        distinguishable from the space-separated normalized forms.
    """
    if vocabulary is not None:
        mapped = vocabulary.canonical(base_key(key), normalize_key)
        if mapped is not None:
            return mapped
    return normalize_key(key)


def compare_values(left: str, right: str) -> str:
    """Classify how two values differ.

    Parameters
    ----------
    left, right : str
        Raw parameter values, units included.

    Returns
    -------
    str
        ``"equal"``, or one of :data:`RECASED`, :data:`REFORMATTED` or
        :data:`CHANGED`.
    """
    if left == right:
        return "equal"
    if left.casefold() == right.casefold():
        return RECASED
    ml, mr = _NUMERIC_RE.match(left.strip()), _NUMERIC_RE.match(right.strip())
    if ml and mr and ml.group(2).casefold() == mr.group(2).casefold():
        try:
            if float(ml.group(1)) == float(mr.group(1)):
                return REFORMATTED
        except ValueError:  # pragma: no cover - guarded by the regex
            pass
    return CHANGED


@dataclass
class ParameterDiff:
    """One parameter's difference between two scans.

    Attributes
    ----------
    key_left, key_right : str or None
        The parameter as spelled on each side. ``None`` where absent.
    values_left, values_right : list of str
        The readings on each side. More than one when the key repeats within
        a scan, in which case the group is compared and reported whole --
        pairing repeats positionally would invent misleading matches when the
        two releases print them in a different order. A conflicting reading
        names the sections alongside their individual values.
    status : str
        One of :data:`CHANGED`, :data:`ONLY_LEFT`, :data:`ONLY_RIGHT`,
        :data:`RENAMED`, :data:`REFORMATTED` or :data:`RECASED`.
    renamed : bool
        Whether the two sides were matched through key normalization.
    conflict_left, conflict_right : bool
        Whether the reading conflicted across sections on that side.
    sections_left, sections_right : list of str
        The sections that printed the parameter on each side, in printed
        order. More than one when a release repeats a parameter across cards,
        which it does often -- TR and FoV are printed in several.
    """

    key_left: str | None
    key_right: str | None
    values_left: list[str] = field(default_factory=list)
    values_right: list[str] = field(default_factory=list)
    status: str = CHANGED
    renamed: bool = False
    conflict_left: bool = False
    conflict_right: bool = False
    sections_left: list[str] = field(default_factory=list)
    sections_right: list[str] = field(default_factory=list)

    @property
    def section(self) -> str:
        """The section this difference is reported under.

        Returns
        -------
        str
            The first section that printed the parameter on the right-hand
            side, since that is the protocol being edited; the left-hand
            section for a parameter the right no longer prints; ``""`` when
            neither side recorded one.
        """
        if self.sections_right:
            return self.sections_right[0]
        if self.sections_left:
            return self.sections_left[0]
        return ""

    @property
    def section_group(self) -> str:
        """The card :attr:`section` belongs to, as a filter names it.

        Returns
        -------
        str
            The normalized top-level section name.
        """
        return normalize_section(self.section)

    @property
    def key(self) -> str:
        """The parameter's display name.

        Returns
        -------
        str
            The left spelling, falling back to the right one.
        """
        return self.key_left or self.key_right or ""

    @property
    def substantive(self) -> bool:
        """Whether this is a real protocol change rather than presentation.

        Returns
        -------
        bool
            ``True`` for changed, added and removed parameters.
        """
        return self.status in SUBSTANTIVE

    def to_dict(self) -> dict:
        """Serialize the difference.

        Returns
        -------
        dict
            Keys, values, sections, status and flags, omitting empty sides.
            Section lists appear only where a side printed the parameter in
            more than one, the single case being implied by ``section``.
        """
        out: dict = {"status": self.status, "key": self.key}
        if self.section:
            out["section"] = self.section
        if len(self.sections_left) > 1:
            out["sections_left"] = self.sections_left
        if len(self.sections_right) > 1:
            out["sections_right"] = self.sections_right
        if self.key_left != self.key_right:
            out["key_left"] = self.key_left
            out["key_right"] = self.key_right
        if self.key_left is not None:
            out["values_left"] = self.values_left
        if self.key_right is not None:
            out["values_right"] = self.values_right
        if self.renamed:
            out["renamed"] = True
        if self.conflict_left or self.conflict_right:
            out["conflict"] = {"left": self.conflict_left, "right": self.conflict_right}
        return out


@dataclass
class ScanDiff:
    """The comparison of one scan against another.

    Attributes
    ----------
    name_left, name_right : str
        Protocol names on each side.
    index_left, index_right : int or None
        Positions within their protocols. ``None`` for an unmatched scan.
    header : list of ParameterDiff
        Differences in the header box fields (TA, voxel size, sequence...).
    parameters : list of ParameterDiff
        Differences in the scan's parameters.
    unchanged : int
        Number of parameters that matched exactly.
    """

    name_left: str
    name_right: str
    index_left: int | None = None
    index_right: int | None = None
    header: list[ParameterDiff] = field(default_factory=list)
    parameters: list[ParameterDiff] = field(default_factory=list)
    unchanged: int = 0

    @property
    def renamed_scan(self) -> bool:
        """Whether the scan itself was renamed between the two protocols.

        Returns
        -------
        bool
            ``True`` when the aligned scans carry different names.
        """
        return self.name_left != self.name_right

    def of_status(self, *statuses: str) -> list[ParameterDiff]:
        """Parameter differences with any of the given statuses.

        Parameters
        ----------
        *statuses : str
            Status values to select.

        Returns
        -------
        list of ParameterDiff
            The matching differences, in report order.
        """
        return [d for d in self.parameters if d.status in statuses]

    @property
    def substantive(self) -> list[ParameterDiff]:
        """Real parameter changes, header included.

        Returns
        -------
        list of ParameterDiff
            Changed, added and removed parameters.
        """
        return [d for d in self.header + self.parameters if d.substantive]

    @property
    def identical(self) -> bool:
        """Whether the two scans differ in no way at all.

        Returns
        -------
        bool
            ``True`` when neither header nor parameters differ.
        """
        return not self.header and not self.parameters

    def to_dict(self) -> dict:
        """Serialize the scan comparison.

        Returns
        -------
        dict
            Names, indices, header and parameter differences, and counts.
        """
        return {
            "name_left": self.name_left,
            "name_right": self.name_right,
            "index_left": self.index_left,
            "index_right": self.index_right,
            "renamed_scan": self.renamed_scan,
            "unchanged": self.unchanged,
            "header": [d.to_dict() for d in self.header],
            "parameters": [d.to_dict() for d in self.parameters],
        }


@dataclass
class ProtocolDiff:
    """The comparison of two protocols.

    Attributes
    ----------
    left_file, right_file : str
        Source paths.
    left_version, right_version : str or None
        Software versions of each side.
    scans : list of ScanDiff
        One entry per aligned pair of scans.
    only_left, only_right : list of str
        Scans present on one side only.
    execution_differences : list of dict
        Added or removed links and pauses. Option changes and pause moves
        are reported as removal of the old entry and addition of the new.
    warnings : list of str
        Comparison limitations, including execution metadata absent from PDFs.
    """

    left_file: str
    right_file: str
    left_version: str | None = None
    right_version: str | None = None
    scans: list[ScanDiff] = field(default_factory=list)
    only_left: list[str] = field(default_factory=list)
    only_right: list[str] = field(default_factory=list)
    execution_differences: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def execution_count(self) -> int:
        """Number of added or removed copy links and pause instructions."""
        return len(self.execution_differences)

    @property
    def substantive_count(self) -> int:
        """Total number of real parameter changes across all scans.

        Returns
        -------
        int
            The count, ignoring cosmetic differences.
        """
        return sum(len(s.substantive) for s in self.scans)

    @property
    def unmatched_count(self) -> int:
        """How many scans are present on one side only.

        Counted apart from the parameter differences because it is a
        different kind of finding: a scan the other protocol does not have at
        all has no parameters to compare, so it contributes nothing to
        :attr:`substantive_count` however unlike the two protocols are.

        A section filter does not affect it. A scan absent from one side is
        absent whichever sections were asked for, so this count describes the
        whole protocol even where the parameter count describes a slice.

        Returns
        -------
        int
            The two unmatched lists' lengths together.
        """
        return len(self.only_left) + len(self.only_right)

    @property
    def differs(self) -> bool:
        """Whether the two protocols differ at all.

        The question the exit status answers, kept here rather than in the
        command so that "did these differ" has one definition. A protocol
        with a scan the other lacks differs from it even when every scan they
        share is identical -- which is why this is not ``substantive_count``
        alone, and why a caller reading only that count reports two protocols
        of different lengths as matching.

        A renamed scan is deliberately not counted. It is matched, its two
        spellings are named in the report, and calling it a difference would
        make every cross-release comparison of a renamed protocol fail a
        check that is asking about parameters.

        Returns
        -------
        bool
            ``True`` when any parameter differs substantively, any scan is
            unmatched, or a copy link or pause differs.
        """
        return bool(self.substantive_count or self.unmatched_count or self.execution_count)

    def to_dict(self) -> dict:
        """Serialize the protocol comparison.

        Returns
        -------
        dict
            Files, versions, per-scan comparisons and unmatched scans, with
            both counts the exit status is derived from.
        """
        return {
            "left_file": self.left_file,
            "right_file": self.right_file,
            "left_version": self.left_version,
            "right_version": self.right_version,
            "scans_only_left": self.only_left,
            "scans_only_right": self.only_right,
            "substantive_count": self.substantive_count,
            "unmatched_count": self.unmatched_count,
            "execution_count": self.execution_count,
            "execution_differences": self.execution_differences,
            "warnings": self.warnings,
            "scans": [s.to_dict() for s in self.scans],
        }


@dataclass
class _Group:
    """One base key's readings within a single scan.

    Attributes
    ----------
    values : list of str
        The readings, in printed order, one per repeat of the key. Conflicts
        summarize their distinct values for comparison across moved cards.
    conflicting_values : dict of int to dict
        Section-to-value assignments for each conflicting reading, keyed by
        its position in ``values``. These retain changes a value set loses.
    conflict : bool
        Whether any reading disagreed across the sections that printed it.
    sections : list of str
        The sections the key was printed in, in printed order, each once.
    rank : int
        Where the key first appears in the scan's flattened view, which is
        its printed position. Lets a section's parameters be listed in the
        order the scanner shows them rather than alphabetically.
    """

    values: list[str] = field(default_factory=list)
    conflicting_values: dict[int, dict[str, str]] = field(default_factory=dict)
    conflict: bool = False
    sections: list[str] = field(default_factory=list)
    rank: int = 0

    @property
    def display_values(self) -> list[str]:
        """Show which section holds each conflicting value in a report.

        Returns
        -------
        list of str
            Readings in printed order, with section names inside conflicts.
        """
        values = list(self.values)
        for index, assignments in self.conflicting_values.items():
            values[index] = (
                "<conflict: "
                + " / ".join(
                    f"{section}: {value}" for section, value in sorted(assignments.items())
                )
                + ">"
            )
        return values


def _flat_groups(flat: Mapping[str, dict]) -> dict[str, _Group]:
    """Group a scan's flattened view by base key.

    Repeats of one key (``Slice Group``, ``Slice Group #2``) collapse into an
    ordered list, so the group is compared as a whole. Conflicts retain their
    section assignments as well as their distinct-value summary.

    Parameters
    ----------
    flat : mapping
        A scan's flattened view.

    Returns
    -------
    dict
        Base key to its :class:`_Group`.
    """
    groups: dict[str, _Group] = {}
    for rank, (key, entry) in enumerate(flat.items()):
        name = base_key(key)
        group = groups.get(name)
        if group is None:
            group = groups[name] = _Group(rank=rank)
        if entry.get("conflict"):
            group.conflicting_values[len(group.values)] = dict(entry.get("values", {}))
            distinct = sorted(set(entry.get("values", {}).values()))
            group.values.append("<conflict: " + " / ".join(distinct) + ">")
            group.conflict = True
        else:
            group.values.append(entry.get("value", ""))
        for section in entry.get("sections", ()):
            if section not in group.sections:
                group.sections.append(section)
    return groups


def _is_churn(key: str, values: Sequence[str] = ()) -> bool:
    """Whether a key is one the scanner rewrites whenever it saves.

    Deferred rather than imported at module load: the list is the archive
    reader's knowledge and belongs with it, but a comparison of two printouts
    should not pay for reading that package to find out a printed label is
    not an ASCCONV key. The import is cached after the first archive scan.

    Parameters
    ----------
    key : str
        A parameter key, printed label or ASCCONV path.
    values : sequence of str, optional
        The readings on either side. One key family holds a save stamp on
        most scans and a real printed parameter on the rest, and only the
        value tells them apart -- so a reading either side calls real is
        real.

    Returns
    -------
    bool
        ``True`` for a churn field. A printed label is never one, so the
        cheap test comes first.
    """
    if "." not in key:
        return False
    from ..exar.ascconv import is_churn

    return all(is_churn(key, value) for value in values) if values else is_churn(key)


def _pair_status(left: _Group, right: _Group) -> str:
    """Classify a matched group of readings.

    Conflicts compare both their distinct readings and the assignments in
    sections present on both sides. Thus a moved card alone is not a change,
    but changing Routine from one already-conflicting value to another is.

    Parameters
    ----------
    left, right : _Group
        Readings and conflicting section assignments on each side.

    Returns
    -------
    str
        ``"equal"``, or the weakest classification that covers every reading:
        a group is only cosmetic if every reading in it is.
    """
    if len(left.values) != len(right.values):
        return CHANGED
    verdicts = [compare_values(a, b) for a, b in zip(left.values, right.values)]
    for index in left.conflicting_values.keys() & right.conflicting_values.keys():
        assignments_left = left.conflicting_values[index]
        assignments_right = right.conflicting_values[index]
        verdicts.extend(
            compare_values(assignments_left[section], assignments_right[section])
            for section in assignments_left.keys() & assignments_right.keys()
        )
    if all(v == "equal" for v in verdicts):
        return "equal"
    if CHANGED in verdicts:
        return CHANGED
    return RECASED if RECASED in verdicts else REFORMATTED


def diff_parameters(
    left: Mapping[str, dict],
    right: Mapping[str, dict],
    normalize: bool = True,
    vocabulary_left: Vocabulary | None = None,
    vocabulary_right: Vocabulary | None = None,
    section_order: Sequence[str] | None = None,
    sections: Collection[str] | None = None,
) -> tuple[list[ParameterDiff], int]:
    """Compare two flattened parameter views.

    Parameters
    ----------
    left, right : mapping
        Flattened views, as produced by
        :func:`~siemens_protocol.analysis.flatten.flatten_sections`.
    normalize : bool, optional
        Whether to match keys through :func:`canonical_key`, which is what
        lets a relabeled parameter be recognized as the same one. Default
        ``True``.
    vocabulary_left, vocabulary_right : Vocabulary or None, optional
        Each side's release vocabulary, for parameters the vendor renamed
        outright rather than merely respelled.
    section_order : sequence of str or None, optional
        Section names in the order to report them, as
        :func:`merge_section_order` returns them. Without one, sections are
        reported in the order their names sort.
    sections : collection of str or None, optional
        Normalized card names to restrict the comparison to, as
        :func:`normalize_section` spells them. Filtering happens after the
        two sides are paired, never before: dropping a key from one side
        first would report its surviving twin as an addition. The unchanged
        count is restricted too, so it keeps describing what was reported.

    Returns
    -------
    tuple
        ``(differences, unchanged count)``. Differences are ordered
        substantive first, then cosmetic; within each, by section and then by
        printed position, which is where a reader will look for them.
    """
    groups_left = _flat_groups(left)
    groups_right = _flat_groups(right)

    def index(
        groups: Mapping[str, tuple[list[str], bool]], vocabulary: Vocabulary | None
    ) -> dict[str, str]:
        """Map each matching form back to its printed key.

        Parameters
        ----------
        groups : mapping
            Base key to values and conflict flag.
        vocabulary : Vocabulary or None
            That side's release vocabulary.

        Returns
        -------
        dict
            Match form to printed key.
        """
        return {(canonical_key(k, vocabulary) if normalize else k): k for k in groups}

    index_left = index(groups_left, vocabulary_left)
    index_right = index(groups_right, vocabulary_right)
    section_rank = {name: i for i, name in enumerate(section_order or ())}
    ranked: list[tuple[tuple, ParameterDiff]] = []
    unchanged = 0

    for form in sorted(set(index_left) | set(index_right)):
        key_left = index_left.get(form)
        key_right = index_right.get(form)
        group_left = groups_left[key_left] if key_left is not None else _Group()
        group_right = groups_right[key_right] if key_right is not None else _Group()

        renamed = key_left is not None and key_right is not None and key_left != key_right
        if key_left is None:
            status = ONLY_RIGHT
        elif key_right is None:
            status = ONLY_LEFT
        else:
            status = _pair_status(group_left, group_right)
            # Reported rather than dropped, and cosmetic rather than
            # substantive: two archives differing only in their save stamps
            # are the same protocol, and counting those would make every
            # archive comparison report differences it cannot explain.
            if status == CHANGED and _is_churn(
                key_left or "", [*group_left.values, *group_right.values]
            ):
                status = CHURN

        diff = ParameterDiff(
            key_left,
            key_right,
            group_left.display_values,
            group_right.display_values,
            RENAMED if status == "equal" else status,
            renamed=renamed,
            conflict_left=group_left.conflict,
            conflict_right=group_right.conflict,
            sections_left=list(group_left.sections),
            sections_right=list(group_right.sections),
        )
        if sections is not None and diff.section_group not in sections:
            continue
        if status == "equal" and not renamed:
            unchanged += 1
            continue
        printed = group_right.rank if key_right is not None else group_left.rank
        ranked.append(
            (
                (
                    0 if diff.substantive else 1,
                    section_rank.get(diff.section, len(section_rank)),
                    diff.section,
                    printed,
                    diff.key.lower(),
                ),
                diff,
            )
        )

    ranked.sort(key=lambda item: item[0])
    return [diff for _, diff in ranked], unchanged


def _header_view(header: Mapping[str, str]) -> dict[str, dict]:
    """Present a scan header in the shape of a flattened view.

    Parameters
    ----------
    header : mapping
        A scan's header fields.

    Returns
    -------
    dict
        The same fields, wrapped so the parameter comparison can reuse them.
        Each is filed under the :data:`HEADER_SECTION` pseudo-section, so the
        header can be named in a section filter like any card.
    """
    return {
        key: {"value": value, "conflict": False, "sections": [HEADER_SECTION]}
        for key, value in header.items()
    }


@timing.timed_function(timing.DIFF_SCANS)
def diff_scans(
    left: Mapping,
    right: Mapping,
    normalize: bool = True,
    vocabulary_left: Vocabulary | None = None,
    vocabulary_right: Vocabulary | None = None,
    sections: Collection[str] | None = None,
) -> ScanDiff:
    """Compare two scans.

    The two may come from different protocols or from the same one, which is
    how a protocol is checked against itself for a scan that should have been
    a copy of another.

    Parameters
    ----------
    left, right : mapping
        Serialized scans, each carrying ``header`` and ``flat``.
    normalize : bool, optional
        Whether to match keys through :func:`canonical_key`. Default ``True``.
    vocabulary_left, vocabulary_right : Vocabulary or None, optional
        Each side's release vocabulary.
    sections : collection of str or None, optional
        Normalized card names to restrict the comparison to. The header
        answers to :data:`HEADER_SECTION`.

    Returns
    -------
    ScanDiff
        The comparison.
    """
    header, _ = diff_parameters(
        _header_view(left.get("header", {})),
        _header_view(right.get("header", {})),
        normalize=normalize,
        sections=sections,
    )
    parameters, unchanged = diff_parameters(
        left.get("flat", {}),
        right.get("flat", {}),
        normalize=normalize,
        vocabulary_left=vocabulary_left,
        vocabulary_right=vocabulary_right,
        section_order=merge_section_order(
            list(right.get("sections", {})), list(left.get("sections", {}))
        ),
        sections=sections,
    )
    return ScanDiff(
        name_left=left.get("name", ""),
        name_right=right.get("name", ""),
        index_left=left.get("index"),
        index_right=right.get("index"),
        header=header,
        parameters=parameters,
        unchanged=unchanged,
    )


@timing.timed_function(timing.ALIGN_SCANS)
def align_scans(left: Sequence[str], right: Sequence[str]) -> list[tuple[int | None, int | None]]:
    """Pair up two protocols' scans by sequence.

    Order is the reliable signal, not the name: a protocol can print the same
    name twice (two field maps), and a release can rename one scan while
    leaving its position alone. Sequence alignment handles both, and reports
    an inserted or deleted scan as such rather than shifting everything after
    it out of step.

    Parameters
    ----------
    left, right : sequence of str
        Scan names in printed order.

    Returns
    -------
    list of tuple
        ``(left index, right index)`` pairs. One side is ``None`` for a scan
        present in only one protocol.
    """
    matcher = difflib.SequenceMatcher(
        None, [normalize_key(n) for n in left], [normalize_key(n) for n in right], autojunk=False
    )
    pairs: list[tuple[int | None, int | None]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.extend((i, j) for i, j in zip(range(i1, i2), range(j1, j2)))
        elif tag == "replace":
            # Same position, different name: a renamed scan, paired up as far
            # as the shorter side goes.
            shared = min(i2 - i1, j2 - j1)
            pairs.extend((i1 + k, j1 + k) for k in range(shared))
            pairs.extend((i, None) for i in range(i1 + shared, i2))
            pairs.extend((None, j) for j in range(j1 + shared, j2))
        elif tag == "delete":
            pairs.extend((i, None) for i in range(i1, i2))
        else:  # insert
            pairs.extend((None, j) for j in range(j1, j2))
    return pairs


@timing.timed_function(timing.DIFF_PROTOCOLS)
def diff_protocols(
    left: Mapping,
    right: Mapping,
    normalize: bool = True,
    use_vocabulary: bool = True,
    extra_vocabulary_dir: str | None = None,
    sections: Collection[str] | None = None,
) -> ProtocolDiff:
    """Compare two protocols scan by scan.

    Parameters
    ----------
    left, right : mapping
        Serialized protocols, each carrying ``scans`` with flattened views.
    normalize : bool, optional
        Whether to match keys through :func:`canonical_key`. Default ``True``.
    use_vocabulary : bool, optional
        Whether to apply each release's vocabulary, which is what resolves
        parameters the vendor renamed rather than merely respelled. Default
        ``True``.
    extra_vocabulary_dir : str or None, optional
        A directory of additional dictionaries overlaying the shipped ones.
    sections : collection of str or None, optional
        Normalized card names to restrict every scan's comparison to, as
        :func:`section_groups` lists them.

    Returns
    -------
    ProtocolDiff
        The comparison, including scans present on only one side.
    """
    for side, document in (("left", left), ("right", right)):
        if "programs" in document or document.get("format") == "exar1":
            raise ValueError(
                f"{side}: archive JSON is not a protocol document; "
                "read the original .exar1 and use archive_view.as_protocol "
                "to select and adapt one program before comparing"
            )
    left_scans = list(left.get("scans", []))
    right_scans = list(right.get("scans", []))
    result = ProtocolDiff(
        left_file=left.get("source_file", ""),
        right_file=right.get("source_file", ""),
        left_version=left.get("software_version"),
        right_version=right.get("software_version"),
    )
    vocabulary_left = vocabulary_right = None
    if use_vocabulary and normalize:
        vocabulary_left = load_vocabulary(left.get("software_version") or "", extra_vocabulary_dir)
        vocabulary_right = load_vocabulary(
            right.get("software_version") or "", extra_vocabulary_dir
        )

    pairs = align_scans(
        [s.get("name", "") for s in left_scans], [s.get("name", "") for s in right_scans]
    )
    _compare_execution(left, right, pairs, result)
    for i, j in pairs:
        if i is None:
            result.only_right.append(right_scans[j].get("name", ""))
        elif j is None:
            result.only_left.append(left_scans[i].get("name", ""))
        else:
            result.scans.append(
                diff_scans(
                    left_scans[i],
                    right_scans[j],
                    normalize=normalize,
                    vocabulary_left=vocabulary_left,
                    vocabulary_right=vocabulary_right,
                    sections=sections,
                )
            )
    return result


def _compare_execution(
    left: Mapping,
    right: Mapping,
    pairs: Sequence[tuple[int | None, int | None]],
    result: ProtocolDiff,
) -> None:
    """Compare relations and ordered pauses using aligned scan identities.

    Link endpoints use the serialized scan index, not list position. Pause
    anchors use the following scan (or the end of the protocol). Empty lists
    mean known absence; missing metadata in a PDF means unknown, not absent.
    Older archive-backed documents can be recognized by baseline detection.
    Execution changes remain visible under a parameter-section filter, just
    as unmatched scans do.
    """
    identities: list[dict[int, int]] = [{}, {}]
    for identity, pair in enumerate(pairs):
        for side, position in enumerate(pair):
            if position is not None:
                document = (left, right)[side]
                index = document["scans"][position].get("index", position)
                identities[side][index] = identity

    def endpoint(index: int, side: int) -> tuple:
        if index not in identities[side]:
            raise ValueError(
                f"{'left' if side == 0 else 'right'}: execution metadata references unknown scan index {index}"
            )
        return ("scan", identities[side][index])

    def available(document: Mapping, kind: str) -> bool:
        return kind in document or document.get("detection", {}).get("method") == "baseline"

    option_names = {
        "copies_phase_encoding_direction",
        "copies_steps",
        "ignores_last_step",
        "ignores_measurements",
        "extra",
    }
    compare_options = True

    def link_key(link: Mapping, side: int) -> tuple:
        options = {
            "group": "",
            "copies_phase_encoding_direction": False,
            "copies_steps": False,
            "ignores_last_step": False,
            "ignores_measurements": False,
            "extra": {},
            **{k: v for k, v in link.items() if k not in ("source", "target")},
        }
        if not compare_options:
            options = {"group": link.get("group", "")}
        return (
            endpoint(link["source"], side),
            endpoint(link["target"], side),
            json.dumps(options, sort_keys=True),
        )

    def pause_key(pause: Mapping, side: int) -> tuple:
        before = pause["before"]
        anchor = (
            ("end",) if before == len((left, right)[side]["scans"]) else endpoint(before, side)
        )
        return (anchor, pause["name"])

    for kind, key in (("links", link_key), ("pauses", pause_key)):
        missing = [
            name for name, doc in (("left", left), ("right", right)) if not available(doc, kind)
        ]
        if missing:
            result.warnings.append(
                f"{kind} not compared: metadata unavailable in {' and '.join(missing)} input (PDF exports do not record it)"
            )
            continue
        entries = [list(doc.get(kind, [])) for doc in (left, right)]
        if kind == "links":
            compare_options = all(option_names <= item.keys() for side in entries for item in side)
            if not compare_options:
                result.warnings.append(
                    "copy-link options not compared: legacy link metadata omits options; "
                    "compare the original .exar1 files to include them"
                )
        keys = [[key(item, side) for item in entries[side]] for side in (0, 1)]
        if kind == "links":
            # Relation storage order has no execution meaning; multiplicity does.
            for side in (0, 1):
                ordered = sorted(zip(keys[side], entries[side]), key=lambda item: item[0])
                keys[side] = [item[0] for item in ordered]
                entries[side] = [item[1] for item in ordered]
        matcher = difflib.SequenceMatcher(None, keys[0], keys[1], autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            for side, start, stop in ((0, i1, i2), (1, j1, j2)):
                for item in entries[side][start:stop]:
                    result.execution_differences.append(
                        {
                            "kind": kind,
                            "status": ONLY_LEFT if side == 0 else ONLY_RIGHT,
                            "value": dict(item),
                        }
                    )
