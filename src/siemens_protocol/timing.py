"""Opt-in run timings for the major operations, printed by ``--debug-timings``.

Timings are *aggregated* per operation rather than printed per call: name
matching runs once per scan pair, so a diff of two large protocols makes
thousands of calls and a line for each would bury everything else. What is
reported is how many calls each operation made, their total and their mean.

Totals are inclusive. Diffing two protocols includes matching their scan
names and diffing each pair, and both of those are reported on their own
lines as well, so the column does not sum to the wall clock. A label nested
inside *itself* -- :func:`.analysis.address.select_all` calling ``select``,
both reported as one operation -- is counted once, by the outermost call,
since counting both would report the same interval twice.

The module sits at the top of the package because every layer uses it: the
archive reader under ``exar/`` may not import ``analysis``, which is where
the diff and the matching live.

When timing is off -- the default -- :func:`timed` costs one boolean test,
which is what makes it safe to leave inside code called once per scan.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import time
from typing import Any, Callable, Iterator, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

#: Operation labels, spelled once so the report groups what belongs together.
READ_EXAR = "read .exar1 (sqlite tables)"
DECODE_EXAR = "decode .exar1 (content documents)"
LIST_EXAR = "list .exar1 protocols and their steps"
PATH_EXAR = "resolve .exar1 folder paths"
INDEX_EXAR = "build .exar1 instance index"
ARCHIVE_VIEW = "render .exar1 as a protocol"
PARSE_PDF = "parse PDF (total)"
EXTRACT_PDF = "parse PDF: extract page text"
MATCH_NAME = "match protocol/scan name"
ALIGN_SCANS = "match scans across protocols"
PAIR_NAMES = "pair archive and printout names"
DIFF_SCANS = "diff two scans"
DIFF_PROTOCOLS = "diff two protocols"
SERIALIZE_JSON = "serialize JSON"
WRITE_FILE = "write output file"
APPLY_PRINTOUT = "apply printout to archive"
VALIDATE_EXAR = "validate .exar1"
WRITE_EXAR = "write .exar1"


@dataclasses.dataclass
class _Tally:
    """Accumulated time for one operation.

    Attributes
    ----------
    calls : int
        How many times it ran.
    seconds : float
        Their total duration.
    """

    calls: int = 0
    seconds: float = 0.0


_enabled = False
_started: float | None = None
_tallies: dict[str, _Tally] = {}
_active: set[str] = set()


def enable() -> None:
    """Turn timing on and start the wall clock, discarding earlier tallies.

    Returns
    -------
    None
    """
    global _enabled, _started
    _enabled = True
    _started = time.perf_counter()
    _tallies.clear()
    _active.clear()


def disable() -> None:
    """Turn timing off and discard what was collected.

    Returns
    -------
    None
    """
    global _enabled, _started
    _enabled = False
    _started = None
    _tallies.clear()
    _active.clear()


def enabled() -> bool:
    """Whether timings are being collected.

    Returns
    -------
    bool
        ``True`` between :func:`enable` and :func:`disable`.
    """
    return _enabled


@contextlib.contextmanager
def timed(operation: str) -> Iterator[None]:
    """Time the enclosed block under ``operation`` when timing is on.

    A block that raises is still counted: a failed read took that long too,
    and leaving it out would make a run that errored look faster.

    Parameters
    ----------
    operation : str
        The label to accumulate under.

    Yields
    ------
    None
    """
    if not _enabled or operation in _active:
        yield
        return
    _active.add(operation)
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        _active.discard(operation)
        tally = _tallies.setdefault(operation, _Tally())
        tally.calls += 1
        tally.seconds += elapsed


def timed_function(operation: str) -> Callable[[F], F]:
    """Decorate a function so every call is timed under ``operation``.

    Parameters
    ----------
    operation : str
        The label to accumulate under.

    Returns
    -------
    callable
        A decorator preserving the wrapped function's signature and docstring.
    """

    def decorate(function: F) -> F:
        """Wrap ``function`` in :func:`timed`.

        Parameters
        ----------
        function : callable
            The function to time.

        Returns
        -------
        callable
            The timed wrapper.
        """

        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Call the wrapped function inside :func:`timed`.

            Parameters
            ----------
            *args : Any
                Positional arguments, passed through.
            **kwargs : Any
                Keyword arguments, passed through.

            Returns
            -------
            Any
                Whatever the wrapped function returns.
            """
            if not _enabled:
                return function(*args, **kwargs)
            with timed(operation):
                return function(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate


def tallies() -> dict[str, tuple[int, float]]:
    """What has been collected so far, in the order operations first ran.

    Returns
    -------
    dict
        ``{operation: (calls, total seconds)}``.
    """
    return {name: (t.calls, t.seconds) for name, t in _tallies.items()}


def _duration(seconds: float) -> str:
    """Format a duration with a unit suited to its size.

    Parameters
    ----------
    seconds : float
        The duration.

    Returns
    -------
    str
        Seconds at or above one second, otherwise milliseconds, or
        microseconds below one millisecond.
    """
    if seconds >= 1.0:
        return f"{seconds:.3f} s"
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.1f} ms"
    return f"{seconds * 1e6:.1f} us"


def report() -> str:
    """Render the collected timings as a table.

    Returns
    -------
    str
        One row per operation with its call count, total and mean, then the
        wall clock since :func:`enable`. Says so when nothing was timed.
    """
    width = max([len(name) for name in _tallies] + [len("operation")])
    lines = [
        "timings (totals are inclusive: nested operations also count in their parents)",
        f"  {'operation':<{width}}  {'calls':>7}  {'total':>11}  {'mean':>11}",
    ]
    if not _tallies:
        lines.append("  (no timed operation ran)")
    for name, tally in _tallies.items():
        mean = tally.seconds / tally.calls
        lines.append(
            f"  {name:<{width}}  {tally.calls:>7}  "
            f"{_duration(tally.seconds):>11}  {_duration(mean):>11}"
        )
    if _started is not None:
        wall = _duration(time.perf_counter() - _started)
        lines.append(f"  {'wall clock':<{width}}  {'':>7}  {wall:>11}")
    return "\n".join(lines)
