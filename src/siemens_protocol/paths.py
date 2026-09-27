"""Writing a path of names as one string, and reading it back.

A scan or protocol is addressed by its path -- ``Investigators/Yuksel/SSIP``
-- and the separator is ``/``. Names may contain one too: a whole-scanner
export holds protocols named ``TIB/FIB ROUTINE METAL SUPRESSION`` and
``SSIP_NOEXPIRATION 6/2022``. Joined naively, those read as an extra level of
the tree, and splitting the result back apart yields a path that names
nothing -- so the protocol can be listed but never addressed.

So a ``/`` inside a name is written ``\\/``, and every path string this
package builds goes through :func:`join`. That makes :func:`split` an exact
inverse, and a path printed in a message or stored in a document can be
pasted back as an address. The escape is the same one a regular expression
uses for a literal ``/``, so it reads the same in a ``re:`` address.

A backslash inside a name is not escaped, so a name *ending* in one would
read its following separator as an escape. No name in any export seen holds a
backslash at all -- a printout uses it as its own separator -- so this stays
a stated limit rather than a second escape.

Nothing here is specific to archives or printouts, which is why it sits at
the top of the package: the low-level readers build paths and must not
import ``analysis``, while ``analysis.address`` parses them.
"""

from __future__ import annotations

import re
from typing import Sequence

#: How a ``/`` inside one name is written in a joined path.
ESCAPED_SEPARATOR = "\\/"

#: A ``/`` that separates components: one not escaped by a backslash.
_SEPARATOR = re.compile(r"(?<!\\)/")


def escape(name: str) -> str:
    """Write one name so a ``/`` inside it is not read as a separator.

    Parameters
    ----------
    name : str
        A scan, protocol or directory name.

    Returns
    -------
    str
        The name with each ``/`` written ``\\/``.
    """
    return name.replace("/", ESCAPED_SEPARATOR)


def join(parts: Sequence[str]) -> str:
    """Join path components into one string.

    Parameters
    ----------
    parts : sequence of str
        The components, outermost first.

    Returns
    -------
    str
        The components separated by ``/``, each escaped by :func:`escape`.
    """
    return "/".join(escape(part) for part in parts)


def split(text: str, *, unescape: bool = True) -> list[str]:
    """Split a joined path back into its components.

    Parameters
    ----------
    text : str
        A path as :func:`join` writes it. Leading, trailing and repeated
        separators are ignored.
    unescape : bool, optional
        Whether ``\\/`` inside a component becomes ``/``. Default ``True``;
        a ``re:`` address keeps the escape, which its regex reads as a
        literal ``/`` anyway.

    Returns
    -------
    list of str
        The components, outermost first, none empty.
    """
    parts = [part for part in _SEPARATOR.split(text) if part]
    if not unescape:
        return parts
    return [part.replace(ESCAPED_SEPARATOR, "/") for part in parts]
