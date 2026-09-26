"""Copy-parameter links between scans, grouped into numbered sets.

A console link slaves one scan's slices, centre, table position and so on to
another scan's. The archive stores them as a star: every scan slaved to one
source hangs off that source's entry. A *link set* is one such star -- a
source and every scan copying from it -- and the sets are numbered from 1 in
the running order of their sources, so the numbering reads top to bottom in a
listing.

A scan is marked ``(X>)`` where it is the source of set ``X`` and ``(>X)``
where it copies from set ``X``. A scan belongs to at most one set, so it
carries at most one mark.

Only an archive records links; a printout does not, so a protocol read from a
PDF has no sets and every mark is empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class LinkSet:
    """One source scan and every scan copying parameters from it.

    Attributes
    ----------
    number : int
        The set's number, counting from 1.
    source : int
        Index of the scan the parameters are copied from.
    targets : list of int
        Indices of the scans copying from it, in running order.
    groups : list of str
        What each target copies, aligned with ``targets``.
    """

    number: int
    source: int
    targets: list[int] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the set.

        Returns
        -------
        dict
            ``number``, ``source`` and one ``{"target", "group"}`` entry per
            target.
        """
        return {
            "number": self.number,
            "source": self.source,
            "targets": [
                {"target": target, "group": group}
                for target, group in zip(self.targets, self.groups)
            ],
        }


def link_sets(protocol: Mapping) -> list[LinkSet]:
    """Group a protocol's links into numbered sets, one per source scan.

    Numbered by the source's running order rather than by the order the
    archive stores them, which is creation order and means nothing to a
    reader; the targets within a set are put in running order for the same
    reason. This is a display order only -- the archive's own order must be
    kept when *writing* links, for the reason
    :meth:`~..exar.archive.Archive.links_of` gives.

    Parameters
    ----------
    protocol : mapping
        A serialized protocol. Its ``links`` entry, where present, is a list
        of ``{"source", "target", "group"}`` scan-index links.

    Returns
    -------
    list of LinkSet
        The sets, numbered from 1; empty for a protocol with no links.
    """
    by_source: dict[int, LinkSet] = {}
    for link in protocol.get("links", []) or []:
        source = int(link["source"])
        entry = by_source.setdefault(source, LinkSet(number=0, source=source))
        entry.targets.append(int(link["target"]))
        entry.groups.append(str(link.get("group") or ""))
    ordered = [by_source[source] for source in sorted(by_source)]
    for number, entry in enumerate(ordered, start=1):
        entry.number = number
        pairs = sorted(zip(entry.targets, entry.groups), key=lambda pair: pair[0])
        entry.targets = [target for target, _ in pairs]
        entry.groups = [group for _, group in pairs]
    return ordered


def link_marks(sets: list[LinkSet]) -> dict[int, str]:
    """The link mark each scan carries, keyed by scan index.

    Parameters
    ----------
    sets : list of LinkSet
        The protocol's link sets, as :func:`link_sets` builds them.

    Returns
    -------
    dict of int to str
        ``"(X>)"`` for the source of set ``X`` and ``"(>X)"`` for each scan
        copying from it. Scans in no link are absent.
    """
    marks: dict[int, str] = {}
    for entry in sets:
        marks[entry.source] = f"({entry.number}>)"
        for target in entry.targets:
            marks[target] = f"(>{entry.number})"
    return marks


def link_groups(sets: list[LinkSet]) -> dict[int, str]:
    """What each destination scan copies from its source, keyed by scan index.

    Parameters
    ----------
    sets : list of LinkSet
        The protocol's link sets, as :func:`link_sets` builds them.

    Returns
    -------
    dict of int to str
        The copy-reference group -- ``Slices``, ``TablePosition`` and so on --
        for every scan copying from a set. Sources are absent, since they
        copy nothing.
    """
    return {target: group for entry in sets for target, group in zip(entry.targets, entry.groups)}
