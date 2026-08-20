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
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

#: Abbreviations Siemens expanded between releases. Deliberately short: each
#: entry was confirmed against the matched example pairs. Semantic renames
#: such as ``Coil Combine Mode`` -> ``Coil Combination`` are *not* listed,
#: because collapsing those would hide a genuine change of meaning.
ABBREVIATIONS = {
    "accel": "acceleration",
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

#: Differences that represent a real protocol change.
SUBSTANTIVE = (CHANGED, ONLY_LEFT, ONLY_RIGHT)
#: Differences that are presentation only.
COSMETIC = (RENAMED, REFORMATTED, RECASED)


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
        two releases print them in a different order.
    status : str
        One of :data:`CHANGED`, :data:`ONLY_LEFT`, :data:`ONLY_RIGHT`,
        :data:`RENAMED`, :data:`REFORMATTED` or :data:`RECASED`.
    renamed : bool
        Whether the two sides were matched through key normalization.
    conflict_left, conflict_right : bool
        Whether the reading conflicted across sections on that side.
    """

    key_left: str | None
    key_right: str | None
    values_left: list[str] = field(default_factory=list)
    values_right: list[str] = field(default_factory=list)
    status: str = CHANGED
    renamed: bool = False
    conflict_left: bool = False
    conflict_right: bool = False

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
            Keys, values, status and flags, omitting empty sides.
        """
        out: dict = {"status": self.status, "key": self.key}
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
    """

    left_file: str
    right_file: str
    left_version: str | None = None
    right_version: str | None = None
    scans: list[ScanDiff] = field(default_factory=list)
    only_left: list[str] = field(default_factory=list)
    only_right: list[str] = field(default_factory=list)

    @property
    def substantive_count(self) -> int:
        """Total number of real parameter changes across all scans.

        Returns
        -------
        int
            The count, ignoring cosmetic differences.
        """
        return sum(len(s.substantive) for s in self.scans)

    def to_dict(self) -> dict:
        """Serialize the protocol comparison.

        Returns
        -------
        dict
            Files, versions, per-scan comparisons and unmatched scans.
        """
        return {
            "left_file": self.left_file,
            "right_file": self.right_file,
            "left_version": self.left_version,
            "right_version": self.right_version,
            "scans_only_left": self.only_left,
            "scans_only_right": self.only_right,
            "substantive_count": self.substantive_count,
            "scans": [s.to_dict() for s in self.scans],
        }


def _flat_groups(flat: Mapping[str, dict]) -> dict[str, tuple[list[str], bool]]:
    """Group a scan's flattened view by base key.

    Repeats of one key (``Slice Group``, ``Slice Group #2``) collapse into an
    ordered list, so the group is compared as a whole.

    Parameters
    ----------
    flat : mapping
        A scan's flattened view.

    Returns
    -------
    dict
        Base key to ``(values, conflict)``.
    """
    groups: dict[str, tuple[list[str], bool]] = {}
    for key, entry in flat.items():
        name = base_key(key)
        values, conflict = groups.get(name, ([], False))
        if entry.get("conflict"):
            distinct = sorted(set(entry.get("values", {}).values()))
            values.append("<conflict: " + " / ".join(distinct) + ">")
            conflict = True
        else:
            values.append(entry.get("value", ""))
        groups[name] = (values, conflict)
    return groups


def _pair_status(values_left: Sequence[str], values_right: Sequence[str]) -> str:
    """Classify a matched group of readings.

    Parameters
    ----------
    values_left, values_right : sequence of str
        The readings on each side, in printed order.

    Returns
    -------
    str
        ``"equal"``, or the weakest classification that covers every reading:
        a group is only cosmetic if every reading in it is.
    """
    if len(values_left) != len(values_right):
        return CHANGED
    verdicts = [compare_values(a, b) for a, b in zip(values_left, values_right)]
    if all(v == "equal" for v in verdicts):
        return "equal"
    if CHANGED in verdicts:
        return CHANGED
    return RECASED if RECASED in verdicts else REFORMATTED


def diff_parameters(
    left: Mapping[str, dict],
    right: Mapping[str, dict],
    normalize: bool = True,
) -> tuple[list[ParameterDiff], int]:
    """Compare two flattened parameter views.

    Parameters
    ----------
    left, right : mapping
        Flattened views, as produced by
        :func:`~siemens_protocol.flatten.flatten_sections`.
    normalize : bool, optional
        Whether to match keys through :func:`normalize_key`, which is what
        lets a relabeled parameter be recognized as the same one. Default
        ``True``.

    Returns
    -------
    tuple
        ``(differences, unchanged count)``. Differences are ordered
        substantive first, then cosmetic, each alphabetically.
    """
    groups_left = _flat_groups(left)
    groups_right = _flat_groups(right)

    def index(groups: Mapping[str, tuple[list[str], bool]]) -> dict[str, str]:
        """Map each matching form back to its printed key.

        Parameters
        ----------
        groups : mapping
            Base key to values and conflict flag.

        Returns
        -------
        dict
            Match form to printed key.
        """
        return {(normalize_key(k) if normalize else k): k for k in groups}

    index_left, index_right = index(groups_left), index(groups_right)
    diffs: list[ParameterDiff] = []
    unchanged = 0

    for form in sorted(set(index_left) | set(index_right)):
        key_left = index_left.get(form)
        key_right = index_right.get(form)
        if key_left is None:
            values, conflict = groups_right[key_right]
            diffs.append(
                ParameterDiff(None, key_right, [], values, ONLY_RIGHT, conflict_right=conflict)
            )
            continue
        if key_right is None:
            values, conflict = groups_left[key_left]
            diffs.append(
                ParameterDiff(key_left, None, values, [], ONLY_LEFT, conflict_left=conflict)
            )
            continue

        values_left, conflict_left = groups_left[key_left]
        values_right, conflict_right = groups_right[key_right]
        renamed = key_left != key_right
        status = _pair_status(values_left, values_right)
        if status == "equal" and not renamed:
            unchanged += 1
            continue
        if status == "equal":
            status = RENAMED
        diffs.append(
            ParameterDiff(
                key_left,
                key_right,
                values_left,
                values_right,
                status,
                renamed=renamed,
                conflict_left=conflict_left,
                conflict_right=conflict_right,
            )
        )

    diffs.sort(key=lambda d: (0 if d.substantive else 1, d.key.lower()))
    return diffs, unchanged


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
    """
    return {key: {"value": value, "conflict": False} for key, value in header.items()}


def diff_scans(left: Mapping, right: Mapping, normalize: bool = True) -> ScanDiff:
    """Compare two scans.

    The two may come from different protocols or from the same one, which is
    how a protocol is checked against itself for a scan that should have been
    a copy of another.

    Parameters
    ----------
    left, right : mapping
        Serialized scans, each carrying ``header`` and ``flat``.
    normalize : bool, optional
        Whether to match keys through :func:`normalize_key`. Default ``True``.

    Returns
    -------
    ScanDiff
        The comparison.
    """
    header, _ = diff_parameters(
        _header_view(left.get("header", {})),
        _header_view(right.get("header", {})),
        normalize=normalize,
    )
    parameters, unchanged = diff_parameters(
        left.get("flat", {}), right.get("flat", {}), normalize=normalize
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


def diff_protocols(left: Mapping, right: Mapping, normalize: bool = True) -> ProtocolDiff:
    """Compare two protocols scan by scan.

    Parameters
    ----------
    left, right : mapping
        Serialized protocols, each carrying ``scans`` with flattened views.
    normalize : bool, optional
        Whether to match keys through :func:`normalize_key`. Default ``True``.

    Returns
    -------
    ProtocolDiff
        The comparison, including scans present on only one side.
    """
    left_scans = list(left.get("scans", []))
    right_scans = list(right.get("scans", []))
    result = ProtocolDiff(
        left_file=left.get("source_file", ""),
        right_file=right.get("source_file", ""),
        left_version=left.get("software_version"),
        right_version=right.get("software_version"),
    )
    for i, j in align_scans(
        [s.get("name", "") for s in left_scans], [s.get("name", "") for s in right_scans]
    ):
        if i is None:
            result.only_right.append(right_scans[j].get("name", ""))
        elif j is None:
            result.only_left.append(left_scans[i].get("name", ""))
        else:
            result.scans.append(diff_scans(left_scans[i], right_scans[j], normalize=normalize))
    return result
