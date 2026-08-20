"""Propose candidate vocabulary entries from a matched pair of exports.

This exists to make curating a vocabulary tractable, not to automate it. The
evidence it can gather is genuinely weak on its own: a parameter that exists
in only one release co-occurs perfectly with *every* parameter that exists
only in the other, so co-occurrence alone will happily propose
``Save uncombined`` -> ``Radial Sorting``. Value agreement and section
agreement narrow it, but the last step is reading the names and deciding
whether they mean the same thing.

Every candidate is therefore returned with its evidence and never applied.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .diff import ONLY_LEFT, ONLY_RIGHT, diff_protocols
from .vocabulary import load_vocabulary


@dataclass
class AliasCandidate:
    """A proposed mapping between two releases' labels.

    Attributes
    ----------
    left_label, right_label : str
        The labels each release uses.
    support : int
        How many scans back the candidate.
    value_ratio : float
        Fraction of those scans where both sides carried the same value.
    section_ratio : float
        Fraction where both sat in the same section.
    left_values, right_values : list of str
        The most common values on each side, for judging whether the two
        labels can plausibly mean the same thing.
    """

    left_label: str
    right_label: str
    support: int
    value_ratio: float
    section_ratio: float
    left_values: list[str]
    right_values: list[str]

    @property
    def score(self) -> float:
        """A single number for ranking, weighted toward value agreement.

        Returns
        -------
        float
            Higher is stronger. Not a probability, and not a substitute for
            reading the labels.
        """
        return self.support * (0.7 * self.value_ratio + 0.3 * self.section_ratio)

    def to_dict(self) -> dict:
        """Serialize the candidate.

        Returns
        -------
        dict
            Labels, support and the evidence ratios.
        """
        return {
            "left_label": self.left_label,
            "right_label": self.right_label,
            "support": self.support,
            "value_ratio": round(self.value_ratio, 3),
            "section_ratio": round(self.section_ratio, 3),
            "left_values": self.left_values,
            "right_values": self.right_values,
        }


def _sections_by_key(scan: Mapping) -> dict[str, str]:
    """Map each of a scan's keys to the section it was printed in.

    Parameters
    ----------
    scan : mapping
        A serialized scan.

    Returns
    -------
    dict
        Base key to section name.
    """
    out: dict[str, str] = {}
    for section, entries in scan.get("sections", {}).items():
        for key in entries:
            out.setdefault(key.split(" #")[0], section)
    return out


def suggest_aliases(
    left: Mapping,
    right: Mapping,
    min_support: int = 8,
    extra_dir: str | None = None,
) -> list[AliasCandidate]:
    """Propose vocabulary entries for parameters one release renamed.

    Parameters
    ----------
    left, right : mapping
        Two serialized protocols, ideally the same protocol exported from two
        releases so that scans line up.
    min_support : int, optional
        Ignore candidates backed by fewer scans than this. Default 8.
    extra_dir : str or None, optional
        An overlay vocabulary directory, so already-mapped labels are skipped.

    Returns
    -------
    list of AliasCandidate
        Candidates, strongest first, one per left label.
    """
    result = diff_protocols(left, right, extra_vocabulary_dir=extra_dir)
    left_scans = {s.get("name", ""): s for s in left.get("scans", [])}
    right_scans = {s.get("name", ""): s for s in right.get("scans", [])}

    pair_counts: Counter = Counter()
    same_value: Counter = Counter()
    same_section: Counter = Counter()
    left_total: Counter = Counter()
    right_total: Counter = Counter()
    left_values: dict[str, Counter] = {}
    right_values: dict[str, Counter] = {}

    for scan in result.scans:
        sections_left = _sections_by_key(left_scans.get(scan.name_left, {}))
        sections_right = _sections_by_key(right_scans.get(scan.name_right, {}))
        unmatched_left = [
            (d.key_left, tuple(d.values_left)) for d in scan.parameters if d.status == ONLY_LEFT
        ]
        unmatched_right = [
            (d.key_right, tuple(d.values_right)) for d in scan.parameters if d.status == ONLY_RIGHT
        ]
        for key, values in unmatched_left:
            left_total[key] += 1
            left_values.setdefault(key, Counter()).update(values)
        for key, values in unmatched_right:
            right_total[key] += 1
            right_values.setdefault(key, Counter()).update(values)
        for left_key, left_vals in unmatched_left:
            for right_key, right_vals in unmatched_right:
                pair = (left_key, right_key)
                pair_counts[pair] += 1
                if left_vals == right_vals:
                    same_value[pair] += 1
                if sections_left.get(left_key) == sections_right.get(right_key):
                    same_section[pair] += 1

    candidates: list[AliasCandidate] = []
    for (left_key, right_key), seen in pair_counts.items():
        support = min(left_total[left_key], right_total[right_key])
        if support < min_support or seen < support * 0.9:
            continue
        # A label the two releases use with wildly different frequency is very
        # unlikely to be the same parameter wearing a new name.
        if max(left_total[left_key], right_total[right_key]) > support * 1.5:
            continue
        candidates.append(
            AliasCandidate(
                left_label=left_key,
                right_label=right_key,
                support=support,
                value_ratio=same_value[(left_key, right_key)] / seen,
                section_ratio=same_section[(left_key, right_key)] / seen,
                left_values=[v for v, _ in left_values[left_key].most_common(3)],
                right_values=[v for v, _ in right_values[right_key].most_common(3)],
            )
        )

    candidates.sort(key=lambda c: -c.score)
    best: list[AliasCandidate] = []
    claimed: set[str] = set()
    for candidate in candidates:
        if candidate.left_label in claimed:
            continue
        claimed.add(candidate.left_label)
        best.append(candidate)
    return best


def _label_counts(protocol: Mapping) -> Counter:
    """How often each parameter label is printed across a protocol.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol.

    Returns
    -------
    Counter
        Base label to number of readings.
    """
    counts: Counter = Counter()
    for scan in protocol.get("scans", []):
        for entries in scan.get("sections", {}).values():
            for key in entries:
                counts[key.split(" #")[0]] += 1
    return counts


def verify_aliases(
    left: Mapping,
    right: Mapping,
    extra_dir: str | None = None,
) -> list[str]:
    """Check the two releases' vocabularies against real exports.

    The failure this catches is a mapping that *steals* a match: if the other
    release still prints the source label natively, then aliasing it away
    breaks a pairing that already worked. That is what a split parameter
    looks like -- XA60 keeps ``Reference scan mode`` for some sequences while
    adding ``Reference Scans`` for others -- and it is not something the
    canonical-name check can see, because both sides look well formed.

    Parameters
    ----------
    left, right : mapping
        The same protocol serialized from two releases.
    extra_dir : str or None, optional
        An overlay vocabulary directory.

    Returns
    -------
    list of str
        Human-readable problems, empty when every mapping holds up.
    """
    left_version = left.get("software_version") or ""
    right_version = right.get("software_version") or ""
    vocab_left = load_vocabulary(left_version, extra_dir)
    vocab_right = load_vocabulary(right_version, extra_dir)
    counts_left = _label_counts(left)
    counts_right = _label_counts(right)

    problems: list[str] = []
    for source, target, counts_target, source_version, target_version in (
        (vocab_left, vocab_right, counts_right, left_version, right_version),
        (vocab_right, vocab_left, counts_left, right_version, left_version),
    ):
        for label, canonical in sorted(source.aliases.items()):
            if counts_target.get(label):
                problems.append(
                    f"{source_version}: mapping {label!r} -> {canonical!r} steals a match: "
                    f"{target_version} still prints {label!r} itself "
                    f"({counts_target[label]} readings). Likely a split parameter, not a rename."
                )
            partners = target.labels(canonical)
            if not partners:
                problems.append(
                    f"{source_version}: {label!r} -> {canonical!r} has no counterpart "
                    f"in the {target_version} vocabulary"
                )

    seen_left = _label_counts(left)
    seen_right = _label_counts(right)
    for label in sorted(vocab_left.aliases):
        if not seen_left.get(label):
            problems.append(
                f"{left_version}: {label!r} is mapped but never printed in this export"
            )
    for label in sorted(vocab_right.aliases):
        if not seen_right.get(label):
            problems.append(
                f"{right_version}: {label!r} is mapped but never printed in this export"
            )
    return problems
