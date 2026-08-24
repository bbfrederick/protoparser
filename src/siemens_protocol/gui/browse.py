"""Server-side directory listing for the GUI's file picker.

A page in a browser cannot read the filesystem, and a browser's own file
dialog hands back an uploaded copy rather than the path this tool needs to
pass to the CLI. Since the server and the browser are the same machine, the
server does the browsing: the page asks what is in a directory and shows it,
and what comes back out is a real path the tool can open.

There is no sandbox here, and that is the intent rather than an oversight.
The GUI runs as the user, exposing the same commands they could type, and a
protocol export sitting outside a notional root would otherwise be
unreachable from a tool whose whole job is to read it. What keeps this off
the network is the loopback bind and the per-session token in
:mod:`siemens_protocol.gui.server`, not a path restriction.
"""

from __future__ import annotations

import os
import string
import sys
from types import ModuleType
from typing import Sequence

#: Suffixes offered as a filter when a field asks for no particular kind.
ALL_FILES: tuple[str, ...] = ()


def _drive_roots() -> list[str]:
    """Every drive letter present on Windows.

    Returns
    -------
    list of str
        Roots such as ``C:\\``, in alphabetical order. Empty off Windows,
        where a single ``/`` reaches everything.
    """
    if not sys.platform.startswith("win"):
        return []
    return [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]


def shortcuts(cwd: str) -> list[dict]:
    """Places worth offering as one-click starting points.

    Parameters
    ----------
    cwd : str
        The directory the server was started in.

    Returns
    -------
    list of dict
        Mappings with ``label`` and ``path``, skipping any that do not exist
        and any duplicate of one already listed.
    """
    candidates = [
        ("Working directory", cwd),
        ("Home", os.path.expanduser("~")),
        ("Desktop", os.path.join(os.path.expanduser("~"), "Desktop")),
        ("Documents", os.path.join(os.path.expanduser("~"), "Documents")),
    ]
    candidates.extend((root, root) for root in _drive_roots())

    seen: set[str] = set()
    places: list[dict] = []
    for label, path in candidates:
        resolved = os.path.abspath(path)
        if resolved in seen or not os.path.isdir(resolved):
            continue
        seen.add(resolved)
        places.append({"label": label, "path": resolved})
    return places


def _matches(name: str, accept: Sequence[str]) -> bool:
    """Whether a file should be offered under a suffix filter.

    Parameters
    ----------
    name : str
        The file's base name.
    accept : Sequence of str
        Lower-case suffixes to allow. Empty allows everything.

    Returns
    -------
    bool
        ``True`` if the filter is empty or the name ends with one of them.
    """
    if not accept:
        return True
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in accept)


def _parent(path: str, paths: ModuleType = os.path) -> str | None:
    """The directory above one, or ``None`` at a filesystem root.

    Normalizing before taking the parent is what makes a drive root behave
    like ``/``. Stripping the trailing separator instead turns ``C:\\`` into
    ``C:``, whose parent is again ``C:`` -- different from what was passed in,
    so the root looks as though it has a parent. Offering that as a directory
    is worse than it sounds: on Windows a bare ``C:`` is resolved relative to
    the current directory *on that drive*, so the picker would jump somewhere
    unrelated.

    Parameters
    ----------
    path : str
        An absolute directory path.
    paths : ModuleType, optional
        The path module to reason with. Default :mod:`os.path`; passing
        :mod:`ntpath` or :mod:`posixpath` exercises the other platform's rules
        from any machine.

    Returns
    -------
    str or None
        The parent, or ``None`` when ``path`` is a root, which is how both
        ``/`` and ``C:\\`` behave.
    """
    normalized = paths.normpath(path)
    above = paths.dirname(normalized)
    if not above or above == normalized:
        return None
    return above


def listing(path: str, accept: Sequence[str] = ALL_FILES) -> dict:
    """List one directory for the picker.

    Directories are always listed whatever the filter says, because a filter
    for ``.pdf`` must not hide the folder the PDFs are in. Entries whose
    metadata cannot be read are skipped rather than failing the listing, so a
    single unreadable name in a large directory does not blank the dialog.

    Parameters
    ----------
    path : str
        Directory to list. A relative path resolves against the process's
        working directory; a file resolves to the directory containing it.
    accept : Sequence of str, optional
        Lower-case suffixes to show. Default :data:`ALL_FILES`, meaning all.

    Returns
    -------
    dict
        ``path`` the directory listed, ``parent`` the one above or ``None``,
        ``entries`` a list of ``{"name", "path", "dir"}`` with directories
        first and each group sorted case-insensitively, and ``error`` a
        message when the directory could not be read.

    Raises
    ------
    ValueError
        If ``path`` names something that is neither a file nor a directory.
    """
    resolved = os.path.abspath(os.path.expanduser(path or "."))
    if os.path.isfile(resolved):
        resolved = os.path.dirname(resolved)
    if not os.path.isdir(resolved):
        raise ValueError(f"not a directory: {resolved}")

    entries: list[dict] = []
    error: str | None = None
    try:
        for item in os.scandir(resolved):
            try:
                is_dir = item.is_dir()
            except OSError:  # a broken link, or a mount that will not stat
                continue
            if item.name.startswith(".") and item.name not in (".",):
                continue
            if not is_dir and not _matches(item.name, accept):
                continue
            entries.append({"name": item.name, "path": item.path, "dir": is_dir})
    except OSError as exc:
        error = str(exc)

    entries.sort(key=lambda item: (not item["dir"], item["name"].lower()))
    return {
        "path": resolved,
        "parent": _parent(resolved),
        "entries": entries,
        "error": error,
    }
