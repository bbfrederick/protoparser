"""Parse Siemens MR protocol PDF exports into hierarchical JSON.

The entry point is :func:`~siemens_protocol.pipeline.parse_document`; the
command line interface in :mod:`siemens_protocol.cli` is a thin wrapper
around it.
"""

from __future__ import annotations

from .model import Protocol, Scan
from .pipeline import ParseOptions, ParseResult, parse_document
from .profiles import REGISTRY

try:
    # Written at build time by setuptools-scm from the git tag, so an
    # installed copy reports its version without needing a git checkout.
    from ._version import __version__
except ImportError:  # pragma: no cover - only in a source tree never built
    try:
        from importlib.metadata import PackageNotFoundError, version

        __version__ = version("siemens-protocol")
    except PackageNotFoundError:
        # Running straight from a source tree that was never installed. Say
        # so rather than inventing a number that would later look like a
        # real release in a bug report.
        __version__ = "0.0.0+unknown"

__all__ = [
    "Protocol",
    "Scan",
    "ParseOptions",
    "ParseResult",
    "parse_document",
    "REGISTRY",
    "__version__",
]
