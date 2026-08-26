"""Shared fixtures.

The example tree is laid out as ``examples/<VERSION>/<file>.pdf``, so the
parent directory name is the ground-truth software version for every file.
That gives auto-detection a label for free.
"""

from __future__ import annotations

import os
from typing import Callable

import pytest

from siemens_protocol.pipeline import ParseResult

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO_ROOT, "examples")
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def example_files() -> list[tuple[str, str]]:
    """Every example PDF paired with the version its folder name declares.

    Returns
    -------
    list of tuple of str
        ``(pdf path, expected version)`` in sorted order. Empty when the
        example tree is absent.
    """
    found: list[tuple[str, str]] = []
    if not os.path.isdir(EXAMPLES):
        return found
    for version in sorted(os.listdir(EXAMPLES)):
        folder = os.path.join(EXAMPLES, version)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".pdf") and not name.startswith("."):
                found.append((os.path.join(folder, name), version))
    return found


EXAMPLE_FILES = example_files()
EXAMPLE_IDS = [os.path.basename(p) for p, _ in EXAMPLE_FILES]

requires_examples = pytest.mark.skipif(not EXAMPLE_FILES, reason="no example PDFs available")

#: Type of the ``parsed`` fixture: ``parsed(path, debug=False) -> ParseResult``.
ParseFixture = Callable[..., ParseResult]


def find_example(name: str, version: str | None = None) -> str:
    """Locate one example PDF by file name, skipping the test if it is absent.

    Parameters
    ----------
    name : str
        Base file name, such as ``"R01StressDyn.pdf"``.
    version : str or None, optional
        The release folder to take it from. Needed where the same protocol
        was exported from more than one release under the same file name, as
        ``R01_Mindfulness.pdf`` was. Default ``None``, the first match.

    Returns
    -------
    str
        The full path to the example.
    """
    for path, folder in EXAMPLE_FILES:
        if os.path.basename(path) == name and version in (None, folder):
            return path
    wanted = f"{version}/{name}" if version else name
    pytest.skip(f"{wanted} not available")


@pytest.fixture(scope="session")
def parsed() -> Callable[..., ParseResult]:
    """Parse each example once and share the result across tests.

    Parsing every example is the bulk of the suite's runtime, so results are
    memoized per ``(path, debug)`` for the session.

    Returns
    -------
    callable
        ``parsed(path, debug=False) -> ParseResult``.
    """
    from siemens_protocol import parse_document
    from siemens_protocol.pipeline import ParseOptions

    cache: dict[tuple[str, bool], ParseResult] = {}

    def _get(path: str, debug: bool = False) -> ParseResult:
        """Return the cached parse of one file.

        Parameters
        ----------
        path : str
            Path to a protocol PDF.
        debug : bool, optional
            Whether to collect the per-page record stream. Default ``False``.

        Returns
        -------
        ParseResult
            The parse result, computed on first request.
        """
        key = (path, debug)
        if key not in cache:
            cache[key] = parse_document(path, ParseOptions(debug=debug))
        return cache[key]

    return _get


#: An ``.exar1`` corpus kept outside the repository. Protocol archives are
#: real site exports and are large, so unlike the PDFs they are not required
#: to live in ``examples/``; point this at a directory to use one anyway.
EXAR_DIR_VARIABLE = "SIEMENS_PROTOCOL_EXAR_DIR"


def exar_files() -> list[tuple[str, str | None]]:
    """Every available ``.exar1`` archive paired with its declared version.

    Archives found under ``examples/<VERSION>/`` take the folder name as their
    ground-truth version, exactly as the PDFs do. Archives found through
    :data:`EXAR_DIR_VARIABLE` have no such label, so their version is ``None``
    and a test that needs one must read it from the archive's own baseline.

    Returns
    -------
    list of tuple
        ``(archive path, version or None)`` in sorted order. Empty when no
        archive is available.
    """
    found: list[tuple[str, str | None]] = []
    if os.path.isdir(EXAMPLES):
        for version in sorted(os.listdir(EXAMPLES)):
            folder = os.path.join(EXAMPLES, version)
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                if name.endswith(".exar1") and not name.startswith("."):
                    found.append((os.path.join(folder, name), version))
    outside = os.environ.get(EXAR_DIR_VARIABLE)
    if outside and os.path.isdir(outside):
        for name in sorted(os.listdir(outside)):
            if name.endswith(".exar1") and not name.startswith("."):
                found.append((os.path.join(outside, name), None))
    return found


EXAR_FILES = exar_files()
EXAR_IDS = [os.path.basename(p) for p, _ in EXAR_FILES]

requires_exar = pytest.mark.skipif(
    not EXAR_FILES,
    reason=f"no .exar1 archives available; set {EXAR_DIR_VARIABLE} or add one to examples/",
)


@pytest.fixture(params=[p for p, _ in EXAR_FILES], ids=EXAR_IDS)
def archive_path(request: pytest.FixtureRequest) -> str:
    """Parametrize over every available ``.exar1`` archive.

    Lives here rather than in one test module because both the reader tests
    and the patch tests sweep the same corpus.

    Parameters
    ----------
    request : pytest.FixtureRequest
        The parametrization hook.

    Returns
    -------
    str
        Path to one archive.
    """
    return str(request.param)


def find_exar(name: str) -> str:
    """Locate one ``.exar1`` archive by file name, skipping the test if absent.

    Parameters
    ----------
    name : str
        Base file name, such as ``"Potpourri.exar1"``.

    Returns
    -------
    str
        Full path to the archive.
    """
    for path, _version in EXAR_FILES:
        if os.path.basename(path) == name:
            return path
    pytest.skip(f"{name} is not among the available .exar1 archives")
