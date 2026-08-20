"""Parse Siemens MR protocol PDF exports into hierarchical JSON.

The entry point is :func:`~siemens_protocol.pipeline.parse_document`; the
command line interface in :mod:`siemens_protocol.cli` is a thin wrapper
around it.
"""

from __future__ import annotations

from .model import Protocol, Scan
from .pipeline import ParseOptions, ParseResult, parse_document
from .profiles import REGISTRY

__version__ = "0.1.0"

__all__ = [
    "Protocol",
    "Scan",
    "ParseOptions",
    "ParseResult",
    "parse_document",
    "REGISTRY",
    "__version__",
]
