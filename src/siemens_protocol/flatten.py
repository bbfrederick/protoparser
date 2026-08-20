"""The flattened per-scan view, and the conflict detection that comes with it.

Siemens prints many parameters in more than one section of the same scan --
TR, FoV and slice thickness are the usual suspects. The hierarchy keeps every
occurrence where it was printed; this module collapses them to one entry per
key and records which sections it came from.

When the occurrences agree the shared value is stored. When they disagree the
per-section values are kept and ``conflict`` is set, so a cross-section
inconsistency shows up as data rather than being resolved at random -- that
discrepancy is usually the interesting thing before a protocol rebuild.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Mapping


def flatten_sections(sections: Mapping[str, Mapping[str, str]]) -> OrderedDict[str, dict]:
    """Collapse ``{section: {key: value}}`` to ``{key: entry}``.

    Parameters
    ----------
    sections : mapping
        A scan's sections, each an ordered mapping of key to value.

    Returns
    -------
    OrderedDict
        One entry per key, in first-seen order. An entry has ``sections`` and
        ``conflict``; agreeing occurrences add ``value``, disagreeing ones add
        a ``values`` mapping of section to value instead.
    """
    occurrences: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    for section, entries in sections.items():
        for key, value in entries.items():
            occurrences.setdefault(key, []).append((section, value))

    flat: OrderedDict[str, dict] = OrderedDict()
    for key, items in occurrences.items():
        section_names = [section for section, _ in items]
        values = [value for _, value in items]
        if len(set(values)) == 1:
            flat[key] = {"value": values[0], "sections": section_names, "conflict": False}
        else:
            flat[key] = {
                "values": OrderedDict(items),
                "sections": section_names,
                "conflict": True,
            }
    return flat


def conflicts(flat: Mapping[str, dict]) -> dict[str, dict]:
    """Just the conflicting entries, for reporting.

    Parameters
    ----------
    flat : mapping
        A flattened view as returned by :func:`flatten_sections`.

    Returns
    -------
    dict
        The subset of entries whose ``conflict`` flag is set.
    """
    return {key: entry for key, entry in flat.items() if entry.get("conflict")}
