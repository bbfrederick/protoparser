"""Read and write Siemens Numaris/X ``.exar1`` protocol archives.

An ``.exar1`` file is a SQLite database wrapping a content-addressed
version-control store, whose blobs are raw DEFLATE streams, each holding a
one-line ``EDF V1:`` type header followed by a Newtonsoft JSON document, and
whose protocol documents carry the classic XProtocol text -- ASCCONV block
included -- in a single ``Data`` string.

Nothing here is derived from Siemens documentation, which does not exist in
public form. Everything was established against real exports and is verified
against them by the tests: the compression, the hashing, the three GUID
spaces, and the linked list that fixes the running order of the scans.
"""

from __future__ import annotations

from . import generate, patch, validate
from .archive import (
    COPY_REFERENCE,
    COPY_REFERENCE_GROUPS,
    SPLIT_JOIN,
    Archive,
    Instance,
    Link,
    PreviewEntry,
    Program,
    Protocol,
    Step,
    pack_guids,
    parse_link,
    read,
    unpack_guids,
)
from .envelope import Envelope

__all__ = [
    "COPY_REFERENCE",
    "COPY_REFERENCE_GROUPS",
    "SPLIT_JOIN",
    "Archive",
    "Link",
    "Program",
    "generate",
    "patch",
    "validate",
    "Envelope",
    "Instance",
    "PreviewEntry",
    "Protocol",
    "Step",
    "pack_guids",
    "parse_link",
    "read",
    "unpack_guids",
]
