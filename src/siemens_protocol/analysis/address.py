r"""Naming one scan or one protocol inside a file that may hold many.

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
rather than guessed at. An address naming a *container* -- the protocol when a
scan was wanted, the directory when a protocol was -- matches nothing for the
same reason, and is reported with what to append to it rather than as a name
the file does not hold.

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
qualified form. No scan or protocol name in the corpus contains ``#``, so
an occurrence is not ambiguous against a real name.

*A* ``/`` *inside a name.* Names can contain the separator: the whole-scanner
export ``Investigators20260918`` holds protocols named ``TIB/FIB ROUTINE METAL
SUPRESSION`` and ``SSIP_NOEXPIRATION 6/2022``, which ``examples/`` never
showed. Such a ``/`` is written ``\/`` -- ``SSIP_NOEXPIRATION 6\/2022`` --
and every path this package prints is written that way by
:func:`..paths.join`, so what a refusal lists can be pasted back as an
address. Without it the name reads as two levels and names nothing.

*Patterns.* An address starting ``re:`` is a regular expression, one per
component::

    re:MPRAGE
    re:^CMRR/eja_svs_(slaser|press)$
    re:Investigators \(2\)/Frederick/NAV.*/T0\d

Each component is searched for in the name at its level (``re.search``, so it
matches anywhere unless anchored with ``^``/``$``, and ``(?i)`` makes it
case-insensitive -- that component only, since each is compiled on its own),
and the components still have to be the unbroken tail of
the path -- a pattern cannot skip a level any more than a literal can. A
bare ``/`` always separates components; ``\/`` matches a ``/`` inside a name,
as it would in any regex.
An occurrence still applies, counting among the matches in document order,
and a bare number is a pattern rather than an index.

Patterns are opt-in rather than tried when a literal fails, and that is the
point. Real names are full of metacharacters -- ``Investigators (2)``,
``tof_cs_acc10.3 fast``, ``localizer *`` -- so reading every address as a
pattern would stop ``Investigators (2)`` matching itself; and falling back to
a pattern after a literal misses turns a misspelled name into a search that
can quietly pick a *different* scan, which is the confident wrong answer this
module exists to refuse.

A pattern is also the one way to name *several* things at once: where a
command can report on many, :func:`select_all` keeps every match, and where it
needs one, :func:`select` refuses a pattern matching several and lists them,
exactly as it does an ambiguous literal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from .. import timing
from ..paths import join, split

#: A leaf carrying an occurrence: ``SpinEchoFieldMap_AP#2``. Anchored at both
#: ends so a name that merely contains ``#`` is not misread, though none in the
#: corpus does.
_OCCURRENCE = re.compile(r"^(?P<name>.+)#(?P<number>\d+)$")

#: What an address starts with to be read as a regular expression.
PATTERN_PREFIX = "re:"


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
        one, or ``None`` when the address named none. For a pattern, which of
        its matches, in document order.
    pattern : bool
        Whether each component is a regular expression rather than a name.
    """

    components: tuple[str, ...]
    occurrence: int | None = None
    pattern: bool = False

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
        the second scan named ``3``, which is a name and not a position. A
        pattern is never an index either: ``re:3`` searches for a ``3``.

        Returns
        -------
        int or None
            The index, or ``None`` when the leaf names something.
        """
        if not self.pattern and self.occurrence is None and self.leaf.isdigit():
            return int(self.leaf)
        return None

    def __str__(self) -> str:
        """Render the address as it would be typed.

        Returns
        -------
        str
            The components joined by ``/``, with the occurrence and any
            pattern prefix restored, and a ``/`` inside a literal component
            escaped so the text parses back to this address.
        """
        text = "/".join(self.components) if self.pattern else join(self.components)
        if self.pattern:
            text = PATTERN_PREFIX + text
        return text if self.occurrence is None else f"{text}#{self.occurrence}"

    def names(self, wanted: str, name: str) -> bool:
        """Whether one component of this address names one path component.

        Parameters
        ----------
        wanted : str
            A component of this address.
        name : str
            The name at the same level of a candidate's path.

        Returns
        -------
        bool
            Equality for a literal address; for a pattern, whether the
            component is found anywhere in the name.
        """
        if self.pattern:
            return re.search(wanted, name) is not None
        return wanted == name


def parse(text: str) -> Address:
    r"""Read an address.

    Parameters
    ----------
    text : str
        The address as typed. Leading, trailing and repeated separators are
        ignored, so ``/a//b/`` is ``a/b``, and ``\/`` is a ``/`` inside a
        name rather than a separator. A leading ``re:`` makes every component
        a regular expression, which keeps ``\/`` as its own escape.

    Returns
    -------
    Address
        The parsed address.

    Raises
    ------
    ValueError
        If the text names nothing, its occurrence is zero -- occurrences
        count from one, and ``#0`` is a typo rather than a request -- or a
        pattern component is not a valid regular expression.
    """
    pattern = text.startswith(PATTERN_PREFIX)
    body = text[len(PATTERN_PREFIX) :] if pattern else text
    components = split(body, unescape=not pattern)
    if not components:
        raise ValueError(f"{text!r} names nothing")
    occurrence: int | None = None
    found = _OCCURRENCE.match(components[-1])
    if found:
        occurrence = int(found.group("number"))
        if occurrence < 1:
            raise ValueError(f"{text!r}: occurrences count from one, so #0 is not a position")
        components[-1] = found.group("name")
    if pattern:
        for component in components:
            try:
                re.compile(component)
            except re.error as exc:
                raise ValueError(
                    f"{text!r}: {component!r} is not a valid pattern ({exc})"
                ) from exc
    return Address(tuple(components), occurrence, pattern)


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
        ``True`` when the address names the path's trailing components, one
        for one -- equal to them, or for a pattern, found in them. A longer
        address than the path never matches.
    """
    depth = len(address.components)
    if len(path) < depth:
        return False
    return all(address.names(want, name) for want, name in zip(address.components, path[-depth:]))


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
        if path and address.names(address.leaf, path[-1]):
            rendered = join(path)
            if rendered not in seen:
                seen.append(rendered)
    return seen


def _encloses(address: Address, candidates: Sequence[tuple[Sequence[str], Any]]) -> list[str]:
    """What an address names the container of, rather than one of.

    The other spelling a reader reaches for: naming the protocol when a scan
    was wanted, or the directory when a protocol was. The address matches
    nothing because it is a *proper* prefix of the candidates' paths rather
    than their trailing part, and the bare refusal then reports a protocol
    the file plainly holds as a scan it does not.

    Parameters
    ----------
    address : Address
        The address that matched nothing.
    candidates : sequence of tuple
        ``(path, payload)`` pairs.

    Returns
    -------
    list of str
        For each candidate the address encloses, the components below it --
        what appending to the address would name. Nearest ancestor first, in
        the order the candidates came, without repeats. Empty when the
        address encloses nothing.
    """
    below: list[str] = []
    for path, _payload in candidates:
        for depth in range(1, len(path)):
            if matches(address, path[:-depth]):
                rendered = join(path[-depth:])
                if rendered not in below:
                    below.append(rendered)
                break
    return below


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
        A trailing clause naming a near miss, or an empty string -- always
        empty for a pattern, which is its own search already.
    """
    if address.pattern:
        return ""
    wanted = address.leaf.lower()
    for path, _payload in candidates:
        if path and wanted in path[-1].lower() and path[-1] != address.leaf:
            return f"; did you mean {path[-1]!r}?"
    return ""


def _refuse_nothing(
    address: Address,
    candidates: Sequence[tuple[Sequence[str], Any]],
    *,
    what: str,
    source: str,
) -> ValueError:
    """The refusal for an address matching nothing, with what would fix it.

    Parameters
    ----------
    address : Address
        The address that matched nothing.
    candidates : sequence of tuple
        ``(path, payload)`` pairs.
    what : str
        What is being addressed, for the message.
    source : str
        The file the candidates came from, for the message.

    Returns
    -------
    ValueError
        The error to raise: naming the paths it nearly matched, what to
        append where it names a container, or a near-miss spelling.
    """
    near = _nearly(address, candidates)
    if near:
        listed = "\n  ".join(near)
        return ValueError(
            f"{source}: no {what} at {address!s}. Components must be the trailing "
            f"part of the path, unbroken. These end in {address.leaf!r}:\n  {listed}"
        )
    inside = _encloses(address, candidates)
    if inside:
        listed = "\n  ".join(inside)
        return ValueError(
            f"{source}: {address!s} names no {what}. It names something that holds "
            f"{what}s -- append one of these to it:\n  {listed}"
        )
    if address.pattern:
        return ValueError(f"{source}: no {what} matches {address!s}")
    return ValueError(f"{source}: no {what} named {address.leaf!r}{_hint(address, candidates)}")


def _matching(
    address: Address,
    candidates: Sequence[tuple[Sequence[str], Any]],
    *,
    what: str,
    source: str,
) -> list[tuple[Sequence[str], Any]]:
    """Every candidate an address matches, narrowed by its occurrence.

    Parameters
    ----------
    address : Address
        The parsed address.
    candidates : sequence of tuple
        ``(path, payload)`` pairs, in document order.
    what : str
        What is being addressed, for the messages.
    source : str
        The file the candidates came from, for the messages.

    Returns
    -------
    list of tuple
        The matching ``(path, payload)`` pairs, in document order; exactly
        one when the address carries an occurrence.

    Raises
    ------
    ValueError
        If nothing matches, or the occurrence is past the end.
    """
    found = [(path, payload) for path, payload in candidates if matches(address, path)]
    if not found:
        raise _refuse_nothing(address, candidates, what=what, source=source)
    if address.occurrence is None:
        return found
    if address.occurrence > len(found):
        plural = "" if len(found) == 1 else "s"
        target = f"match {address!s}" if address.pattern else f"match {address.leaf!r}"
        raise ValueError(
            f"{source}: {address!s} asks for occurrence {address.occurrence}, but "
            f"{len(found)} {what}{plural} {target}"
        )
    return [found[address.occurrence - 1]]


@timing.timed_function(timing.MATCH_NAME)
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
    the full paths when they differ, the occurrence range when they do not,
    since paths that are identical say nothing on their own, and what to
    append where the address names a container rather than one of its
    contents. A pattern matching several is refused the same way, listing
    what it matched.

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
    found = _matching(address, candidates, what=what, source=source)
    if len(found) == 1:
        return found[0][1]

    paths = [path for path, _payload in found]
    listed = "\n  ".join(join(path) for path in paths)
    if address.pattern:
        raise ValueError(
            f"{source}: {address!s} matches {len(found)} {what}s and one is needed here. "
            f"Narrow the pattern, or add an occurrence, #1 .. #{len(found)}:\n  {listed}"
        )
    if _same_place(paths):
        where = join(paths[0][:-1]) or source
        raise ValueError(
            f"{source}: {where} holds {len(found)} {what}s named {address.leaf!r}. "
            f"Add an occurrence to say which: {address.leaf}#1 .. "
            f"{address.leaf}#{len(found)}"
        )
    raise ValueError(
        f"{source}: {address!s} names {len(found)} {what}s. Prepend enough of the "
        f"path to separate them:\n  {listed}"
    )


@timing.timed_function(timing.MATCH_NAME)
def select_all(
    address: Address,
    candidates: Sequence[tuple[Sequence[str], Any]],
    *,
    what: str,
    source: str,
) -> list[Any]:
    """Resolve an address that may name several things.

    For a caller that can report on many. A pattern keeps every match; a
    literal address still names exactly one, and is refused as
    :func:`select` refuses it when it does not -- a repeated literal name is
    a request for one of the repeats, not for all of them, and quietly
    widening it would change what an existing address means.

    Parameters
    ----------
    address : Address
        The parsed address.
    candidates : sequence of tuple
        ``(path, payload)`` pairs, in document order.
    what : str
        What is being addressed, for the messages.
    source : str
        The file the candidates came from, for the messages.

    Returns
    -------
    list
        The matching payloads in document order. Never empty.

    Raises
    ------
    ValueError
        As :func:`select` does, except that a pattern matching several is
        the answer rather than a refusal.
    """
    if not address.pattern:
        return [select(address, candidates, what=what, source=source)]
    return [payload for _path, payload in _matching(address, candidates, what=what, source=source)]


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


def resolve_all(
    text: str,
    candidates: Sequence[tuple[Sequence[str], Any]],
    *,
    what: str,
    source: str,
) -> list[Any]:
    """Parse an address and resolve it to everything it names.

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
    list
        The matching payloads in document order. Never empty.

    Raises
    ------
    ValueError
        As :func:`parse` and :func:`select_all` do.
    """
    return select_all(parse(text), candidates, what=what, source=source)
