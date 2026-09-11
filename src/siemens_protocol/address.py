"""Naming one scan or one protocol inside a file that may hold many.

A PDF prints one protocol, so a scan there is named by itself. An ``.exar1``
archive may be an export of one protocol or a backup of every protocol on the
scanner, and the corpus shows both kinds of collision that follow:

*Across protocols.* ``Frederick_P2`` holds 31 protocols and 97 of its 195
distinct scan names occur in more than one of them -- ``eja_svs_slaser`` is in
both ``CMRR test scans`` and ``CMRR spectro scans``. The protocol name
separates those, so an address is the trailing part of the path::

    eja_svs_slaser
    CMRR spectro scans/eja_svs_slaser
    Investigators (2)/Frederick/NAV_optionscan_P1 (2)/T09

as much of it as it takes and no more. The components must be *contiguous*
from the end: skipping a level would let two addresses that look equally
specific behave differently, and would let an address silently start matching
something new when a protocol is added. An address that skips one is reported
with the full paths it nearly matched, so the missing component is visible
rather than guessed at.

The directory levels above the protocol are not decoration. Two protocols of
``NAV_optionscan_P1_loadtest`` are both named ``NAV_optionscan_P1 (2)``, under
``Investigators`` and ``Investigators (2)``, so only the path separates them.

*Within one protocol.* No amount of path separates a name a protocol uses
twice, and that is the common case rather than an edge: 11 of those 31
protocols repeat a name, ``Functional TOF`` runs ``tof_cs_acc10.3 fast`` five
times and ``Mair test`` repeats 15 names over 72 scans. So the leaf may carry
an occurrence, ``SpinEchoFieldMap_AP#2``, counting from one in acquisition
order -- the same ``#n`` the parser already appends to a parameter key a card
prints more than once.

A leaf that is a bare number stays what it has always been: a zero-based index
within the protocol, so ``--scan 0`` keeps working and ``Mair test/3`` is its
qualified form. No scan or protocol name in the corpus contains ``/`` or
``#``, so neither spelling is ambiguous against a real name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

#: A leaf carrying an occurrence: ``SpinEchoFieldMap_AP#2``. Anchored at both
#: ends so a name that merely contains ``#`` is not misread, though none in the
#: corpus does.
_OCCURRENCE = re.compile(r"^(?P<name>.+)#(?P<number>\d+)$")


@dataclass(frozen=True)
class Address:
    """A parsed address: trailing path components, and maybe an occurrence.

    Attributes
    ----------
    components : tuple of str
        The path components, outermost first, the last naming the scan or
        protocol itself. Never empty.
    occurrence : int or None
        Which of several identically named siblings is wanted, counting from
        one, or ``None`` when the address named none.
    """

    components: tuple[str, ...]
    occurrence: int | None = None

    @property
    def leaf(self) -> str:
        """The component naming the scan or protocol itself.

        Returns
        -------
        str
            The last component, without any occurrence suffix.
        """
        return self.components[-1]

    @property
    def parents(self) -> tuple[str, ...]:
        """The components above the leaf, outermost first.

        Returns
        -------
        tuple of str
            Empty when the address is a bare name.
        """
        return self.components[:-1]

    @property
    def index(self) -> int | None:
        """The zero-based position within a protocol, when the leaf is one.

        An occurrence and an index are mutually exclusive: ``3#2`` asks for
        the second scan named ``3``, which is a name and not a position.

        Returns
        -------
        int or None
            The index, or ``None`` when the leaf names something.
        """
        if self.occurrence is None and self.leaf.isdigit():
            return int(self.leaf)
        return None

    def __str__(self) -> str:
        """Render the address as it would be typed.

        Returns
        -------
        str
            The components joined by ``/``, with the occurrence restored.
        """
        text = "/".join(self.components)
        return text if self.occurrence is None else f"{text}#{self.occurrence}"


def parse(text: str) -> Address:
    """Read an address.

    Parameters
    ----------
    text : str
        The address as typed. Leading, trailing and repeated separators are
        ignored, so ``/a//b/`` is ``a/b``.

    Returns
    -------
    Address
        The parsed address.

    Raises
    ------
    ValueError
        If the text names nothing, or its occurrence is zero -- occurrences
        count from one, and ``#0`` is a typo rather than a request.
    """
    components = [part for part in text.split("/") if part]
    if not components:
        raise ValueError(f"{text!r} names nothing")
    occurrence: int | None = None
    found = _OCCURRENCE.match(components[-1])
    if found:
        occurrence = int(found.group("number"))
        if occurrence < 1:
            raise ValueError(f"{text!r}: occurrences count from one, so #0 is not a position")
        components[-1] = found.group("name")
    return Address(tuple(components), occurrence)


def matches(address: Address, path: Sequence[str]) -> bool:
    """Whether an address names the thing at ``path``.

    Parameters
    ----------
    address : Address
        The parsed address.
    path : sequence of str
        The full path of a candidate, outermost first, ending in its own name.

    Returns
    -------
    bool
        ``True`` when the address is exactly the path's trailing components.
        A longer address than the path never matches.
    """
    depth = len(address.components)
    return len(path) >= depth and tuple(path[-depth:]) == address.components


def _same_place(paths: Sequence[Sequence[str]]) -> bool:
    """Whether every candidate sits at one and the same path.

    Parameters
    ----------
    paths : sequence of sequence of str
        The matched candidates' paths.

    Returns
    -------
    bool
        ``True`` when they are all identical, which is what a name repeated
        inside one protocol looks like.
    """
    return len({tuple(path) for path in paths}) == 1


def _nearly(address: Address, candidates: Sequence[tuple[Sequence[str], Any]]) -> list[str]:
    """Full paths whose own name is the address's leaf.

    What an address that skipped a level nearly matched, so the component it
    is missing can be read off rather than guessed at.

    Parameters
    ----------
    address : Address
        The address that matched nothing.
    candidates : sequence of tuple
        ``(path, payload)`` pairs.

    Returns
    -------
    list of str
        The rendered paths, in the order the candidates came, without repeats.
    """
    seen: list[str] = []
    for path, _payload in candidates:
        if path and path[-1] == address.leaf:
            rendered = "/".join(path)
            if rendered not in seen:
                seen.append(rendered)
    return seen


def _hint(address: Address, candidates: Sequence[tuple[Sequence[str], Any]]) -> str:
    """A suggestion for an address matching nothing at all.

    Parameters
    ----------
    address : Address
        The address that matched nothing.
    candidates : sequence of tuple
        ``(path, payload)`` pairs.

    Returns
    -------
    str
        A trailing clause naming a near miss, or an empty string.
    """
    wanted = address.leaf.lower()
    for path, _payload in candidates:
        if path and wanted in path[-1].lower() and path[-1] != address.leaf:
            return f"; did you mean {path[-1]!r}?"
    return ""


def select(
    address: Address,
    candidates: Sequence[tuple[Sequence[str], Any]],
    *,
    what: str,
    source: str,
) -> Any:
    """Resolve an address against the things a file holds.

    Refuses rather than choosing whenever the address does not name exactly
    one, and the refusal carries what is needed to write a better address:
    the full paths when they differ, and the occurrence range when they do
    not, since paths that are identical say nothing on their own.

    Parameters
    ----------
    address : Address
        The parsed address.
    candidates : sequence of tuple
        ``(path, payload)`` pairs, in document order. ``path`` is the
        candidate's full path, outermost first, ending in its own name.
    what : str
        What is being addressed, for the messages: ``"scan"`` or
        ``"protocol"``.
    source : str
        The file the candidates came from, for the messages.

    Returns
    -------
    Any
        The single matching candidate's payload.

    Raises
    ------
    ValueError
        If nothing matches, if several do and no occurrence separates them, or
        if the occurrence is past the end.
    """
    found = [(path, payload) for path, payload in candidates if matches(address, path)]

    if not found:
        near = _nearly(address, candidates)
        if near:
            listed = "\n  ".join(near)
            raise ValueError(
                f"{source}: no {what} at {address!s}. Components must be the trailing "
                f"part of the path, unbroken. These end in {address.leaf!r}:\n  {listed}"
            )
        raise ValueError(f"{source}: no {what} named {address.leaf!r}{_hint(address, candidates)}")

    if address.occurrence is not None:
        if address.occurrence > len(found):
            plural = "" if len(found) == 1 else "s"
            raise ValueError(
                f"{source}: {address!s} asks for occurrence {address.occurrence}, but "
                f"{len(found)} {what}{plural} match {address.leaf!r}"
            )
        return found[address.occurrence - 1][1]

    if len(found) > 1:
        paths = [path for path, _payload in found]
        if _same_place(paths):
            where = "/".join(paths[0][:-1]) or source
            raise ValueError(
                f"{source}: {where} holds {len(found)} {what}s named {address.leaf!r}. "
                f"Add an occurrence to say which: {address.leaf}#1 .. "
                f"{address.leaf}#{len(found)}"
            )
        listed = "\n  ".join("/".join(path) for path in paths)
        raise ValueError(
            f"{source}: {address!s} names {len(found)} {what}s. Prepend enough of the "
            f"path to separate them:\n  {listed}"
        )

    return found[0][1]


def resolve(
    text: str,
    candidates: Sequence[tuple[Sequence[str], Any]],
    *,
    what: str,
    source: str,
) -> Any:
    """Parse an address and resolve it in one step.

    Parameters
    ----------
    text : str
        The address as typed.
    candidates : sequence of tuple
        ``(path, payload)`` pairs, in document order.
    what : str
        What is being addressed, for the messages.
    source : str
        The file the candidates came from, for the messages.

    Returns
    -------
    Any
        The single matching candidate's payload.

    Raises
    ------
    ValueError
        As :func:`parse` and :func:`select` do.
    """
    return select(parse(text), candidates, what=what, source=source)
