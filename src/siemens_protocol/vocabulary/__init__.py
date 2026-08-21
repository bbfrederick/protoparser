"""Per-release parameter vocabularies, mapping labels to standard names.

Siemens does not only recapitalize between releases -- it reorganizes. VE11C's
``PAT mode`` is XA60's ``Acceleration Mode``, its ``Img. Scale Cor.`` is
``Image Scaling``, and its ``Load images to viewer`` refers to a viewer that
was itself renamed. None of that is reachable by folding case and expanding
abbreviations, so each release carries a JSON dictionary mapping its own
labels onto shared canonical names.

The dictionaries are curated rather than inferred, and deliberately so.
Statistical pairing of the matched example exports confidently proposes
``B0 Shim mode`` -> ``Adjustment Strategy`` and ``Save uncombined`` ->
``Radial Sorting``, both of which are wrong: any two parameters that happen
to exist in only one release and sit in the same section co-occur perfectly.
A wrong entry here is worse than a missing one, because it hides a real
difference instead of merely failing to explain one. ``suggest_aliases``
exists to propose candidates *with their evidence*, for a person to accept.

Canonical names are snake_case, which keeps them distinguishable at a glance
from the space-separated forms that ordinary key normalization produces.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: Where the shipped dictionaries live.
VOCABULARY_DIR = Path(__file__).parent


@dataclass
class Vocabulary:
    """One release's label-to-canonical mapping.

    Attributes
    ----------
    version : str
        The release this vocabulary belongs to.
    aliases : dict of str to str
        Printed label to canonical name.
    notes : dict of str to str
        Why an entry is there, keyed by the same labels. Documentation only.
    absent : dict of str to str
        Canonical names this release genuinely has no label for, mapped to
        the reason. Distinct from a name merely left unmapped: releases a
        decade apart do not all have the same parameters, and saying so
        explicitly is what lets :func:`check` tell a real gap from a typo.
    description : str
        What the file covers.
    """

    version: str
    aliases: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    absent: dict[str, str] = field(default_factory=dict)
    description: str = ""
    _index_cache: dict = field(default_factory=dict, repr=False, compare=False)

    def canonical(self, label: str, normalizer: Callable[[str], str] | None = None) -> str | None:
        """The canonical name for a printed label, if this release renames it.

        A release is not consistent with itself: XA60 prints both
        ``Acceleration Mode`` and ``Accel. Mode`` for the same setting. So a
        lookup that misses on the literal label is retried on its normalized
        form, letting one dictionary entry cover a release's own spelling
        variants instead of needing one entry per variant.

        Parameters
        ----------
        label : str
            A parameter label as printed, without any repeat suffix.
        normalizer : callable or None, optional
            Used to fold spelling variants together. Without it, only the
            literal label is looked up.

        Returns
        -------
        str or None
            The canonical name, or ``None`` when the label needs no mapping
            and ordinary normalization suffices.
        """
        direct = self.aliases.get(label)
        if direct is not None or normalizer is None:
            return direct
        return self._normalized_index(normalizer).get(normalizer(label))

    def _normalized_index(self, normalizer: Callable[[str], str]) -> dict[str, str]:
        """Aliases keyed by the normalized form of their label.

        Parameters
        ----------
        normalizer : callable
            The key normalizer to index by.

        Returns
        -------
        dict
            Normalized label to canonical name, built once per normalizer.
        """
        cached = self._index_cache.get(normalizer)
        if cached is None:
            cached = {normalizer(k): v for k, v in self.aliases.items()}
            self._index_cache[normalizer] = cached
        return cached

    def labels(self, canonical: str) -> list[str]:
        """Every label this release uses for a canonical name.

        This is the reverse direction: given a standard name, what does this
        software version call it.

        Parameters
        ----------
        canonical : str
            A canonical name.

        Returns
        -------
        list of str
            Matching labels, in sorted order. Empty when this release has no
            label for that name.
        """
        return sorted(k for k, v in self.aliases.items() if v == canonical)

    def to_dict(self) -> dict:
        """Serialize the vocabulary in its on-disk form.

        Returns
        -------
        dict
            Version, description, aliases and notes.
        """
        return {
            "version": self.version,
            "description": self.description,
            "aliases": dict(sorted(self.aliases.items())),
            "notes": dict(sorted(self.notes.items())),
        }


def load_vocabulary(version: str, extra_dir: str | os.PathLike | None = None) -> Vocabulary:
    """Load one release's vocabulary.

    Parameters
    ----------
    version : str
        A release name, matching a ``<version>.json`` file.
    extra_dir : path-like or None, optional
        A directory of additional dictionaries that overlay the shipped ones,
        so a site can add mappings without editing the installed package.
        Entries there win on conflict.

    Returns
    -------
    Vocabulary
        The mapping, empty if the release has no dictionary.

    Raises
    ------
    ValueError
        If a dictionary file is present but malformed.
    """
    vocabulary = Vocabulary(version=version)
    for directory in (VOCABULARY_DIR, Path(extra_dir) if extra_dir else None):
        if directory is None:
            continue
        path = directory / f"{version}.json"
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read the vocabulary at {path}: {exc}") from exc
        aliases = payload.get("aliases", {})
        if not isinstance(aliases, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()
        ):
            raise ValueError(f"{path}: 'aliases' must map label strings to canonical strings")
        absent = payload.get("absent", {})
        if not isinstance(absent, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in absent.items()
        ):
            raise ValueError(f"{path}: 'absent' must map canonical names to reason strings")
        vocabulary.aliases.update(aliases)
        vocabulary.notes.update(payload.get("notes", {}))
        vocabulary.absent.update(absent)
        vocabulary.description = payload.get("description", vocabulary.description)
    return vocabulary


def available(extra_dir: str | os.PathLike | None = None) -> list[str]:
    """Every release with a vocabulary dictionary.

    Parameters
    ----------
    extra_dir : path-like or None, optional
        An additional directory to look in.

    Returns
    -------
    list of str
        Release names, sorted.
    """
    found = {p.stem for p in VOCABULARY_DIR.glob("*.json")}
    if extra_dir:
        found |= {p.stem for p in Path(extra_dir).glob("*.json")}
    return sorted(found)


def check(versions: list[str], extra_dir: str | os.PathLike | None = None) -> list[str]:
    """Look for problems across a set of vocabularies.

    A canonical name defined by only one release is the usual sign of a typo:
    the mapping exists on one side and so can never pair up with anything.
    A release that genuinely lacks the parameter says so in its ``absent``
    block, which is what separates a real gap from a mistake -- releases a
    decade apart do not all have the same parameters.

    Parameters
    ----------
    versions : list of str
        Releases to check together.
    extra_dir : path-like or None, optional
        An additional directory to look in.

    Returns
    -------
    list of str
        Human-readable problems, empty when everything lines up.
    """
    loaded = {v: load_vocabulary(v, extra_dir) for v in versions}
    problems: list[str] = []
    by_canonical: dict[str, set[str]] = {}
    for version, vocabulary in loaded.items():
        for label, canonical in vocabulary.aliases.items():
            by_canonical.setdefault(canonical, set()).add(version)
            if canonical != canonical.lower() or " " in canonical:
                problems.append(
                    f"{version}: canonical name {canonical!r} for {label!r} should be "
                    "lower-case snake_case"
                )
    for version, vocabulary in loaded.items():
        for canonical in sorted(set(vocabulary.absent) & set(vocabulary.aliases.values())):
            problems.append(
                f"{version}: canonical name {canonical!r} is both mapped and declared absent"
            )

    if len(loaded) > 1:
        for canonical, owners in sorted(by_canonical.items()):
            missing = {
                version
                for version in set(loaded) - owners
                if canonical not in loaded[version].absent
            }
            if missing:
                problems.append(
                    f"canonical name {canonical!r} is defined by "
                    f"{', '.join(sorted(owners))} but not by {', '.join(sorted(missing))}"
                )
    return problems
