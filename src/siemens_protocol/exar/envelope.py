"""The EDF content envelope: DEFLATE, a type header, and SHA-1 addressing.

Every row of an ``.exar1`` archive's ``Content`` table is one of these. The
stored blob is a *raw* DEFLATE stream -- no zlib or gzip wrapper, so it needs
``wbits=-15`` -- and decompresses to a one-line ASCII header naming a .NET
class, followed by a JSON document::

    EDF V1: ContentType=syngo.MR.ExamDataFoundation.Data.EdfProtocolContent;
    {
      "$id": "1",
      ...
    }

``Content.Hash`` is the SHA-1 of those decompressed bytes, header included --
not of the compressed blob and not of the JSON alone. That makes the table a
content-addressed store: two protocols that happen to be identical share one
row, which is why a 67-instance archive holds only 50 contents.

The JSON is Newtonsoft's (``$id``/``$ref``/``$values``/``$type`` come from
``PreserveReferencesHandling``), written with two-space indentation and CRLF
line endings. Python reproduces that byte for byte, with one exception
documented on :func:`dumps`, so an edited document can be re-encoded to a blob
the scanner accepts.
"""

from __future__ import annotations

import hashlib
import json
import re
import zlib
from dataclasses import dataclass
from typing import Any

#: Marks the start of a decompressed content blob and names its .NET class.
HEADER_PATTERN = re.compile(rb"EDF V1: ContentType=([^;]+);\r\n")

#: How that header is written back. The trailing CRLF is part of the hashed
#: bytes, so it is not cosmetic.
HEADER_TEMPLATE = "EDF V1: ContentType={};\r\n"

#: The only value ``Content.Format`` takes in any archive seen so far.
STORED_FORMAT = "DS"

#: Window size for the raw DEFLATE streams, negated as ``zlib`` requires to
#: signal "no wrapper".
_RAW_DEFLATE = -zlib.MAX_WBITS


def decompress(blob: bytes) -> bytes:
    """Inflate one stored ``Content.Data`` blob.

    Parameters
    ----------
    blob : bytes
        The raw DEFLATE stream as stored in the archive.

    Returns
    -------
    bytes
        The decompressed header-plus-JSON bytes.

    Raises
    ------
    zlib.error
        If the blob is not a raw DEFLATE stream.
    """
    return zlib.decompress(blob, _RAW_DEFLATE)


def compress(raw: bytes) -> bytes:
    """Deflate header-plus-JSON bytes for storage.

    The result is not expected to match the console's own compression byte for
    byte -- that depends on its zlib build and level -- which is why
    :class:`Envelope` keeps the original blob and only calls this for content
    it has actually changed.

    Parameters
    ----------
    raw : bytes
        Decompressed header-plus-JSON bytes.

    Returns
    -------
    bytes
        A raw DEFLATE stream, suitable for ``Content.Data``.
    """
    compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, _RAW_DEFLATE)
    return compressor.compress(raw) + compressor.flush()


def content_hash(raw: bytes) -> str:
    """Compute the ``Content.Hash`` of decompressed content bytes.

    Parameters
    ----------
    raw : bytes
        Decompressed header-plus-JSON bytes, exactly as :func:`decompress`
        returns them.

    Returns
    -------
    str
        Lowercase hex SHA-1 digest, the archive's primary key for the content.
    """
    return hashlib.sha1(raw).hexdigest()


#: Marker wrapped around a float while the document is encoded, then replaced
#: by the literal .NET would have written. Printable on purpose: the encoder
#: escapes control characters whatever ``ensure_ascii`` says, which would put
#: the marker beyond the reach of the substitution. Checked for collisions
#: before use all the same.
_FLOAT_MARK = "@@edf-double@@"


def dotnet_double(value: float) -> str:
    """Format a double the way .NET's round-trip format does.

    Newtonsoft writes doubles with ``ToString("R")``, which on the .NET
    Framework emits fifteen significant digits when that round-trips and
    seventeen when it does not. Python's ``repr`` gives the shortest form that
    round-trips, which is usually the same string and occasionally is not:
    the field strength in these protocols is ``2.8936200141906738`` to .NET
    and ``2.893620014190674`` to Python, both exact for the same double.

    This rule reproduces all 74 distinct float literals across every
    console-authored archive in the corpus, including the ones the scanner
    itself wrote back after loading a patched protocol. ``repr`` reproduces
    73 of them.

    Parameters
    ----------
    value : float
        The double to format.

    Returns
    -------
    str
        The literal, always carrying a decimal point so that a whole number
        stays a double.
    """
    text = f"{value:.15g}"
    if float(text) != value:
        text = f"{value:.17g}"
    if "." not in text and "e" not in text and "E" not in text:
        text += ".0"
    return text


def _mark_floats(node: Any) -> Any:
    """Replace every float with a marked string so it can be re-formatted.

    ``json.dumps`` offers no hook for float formatting, so the values are
    carried through the encoder as strings and substituted afterwards.

    Parameters
    ----------
    node : Any
        A decoded JSON value.

    Returns
    -------
    Any
        The same structure with floats replaced by marked strings.
    """
    if isinstance(node, float):
        return f"{_FLOAT_MARK}{dotnet_double(node)}"
    if isinstance(node, dict):
        return {k: _mark_floats(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_mark_floats(v) for v in node]
    return node


def dumps(document: Any) -> bytes:
    """Serialize a decoded document the way Newtonsoft wrote it.

    Two-space indentation, CRLF endings, no ASCII escaping, and doubles
    formatted by :func:`dotnet_double`. Together these reproduce every stored
    document in the reference archives byte for byte -- including the ones a
    scanner wrote back after loading a protocol this package had patched,
    which is what confirms the rule rather than merely fitting it.

    Parameters
    ----------
    document : Any
        A decoded JSON document, as produced by :meth:`Envelope.decode`.

    Returns
    -------
    bytes
        UTF-8 encoded JSON with CRLF line endings.

    Raises
    ------
    ValueError
        If the document already contains the internal float marker, which
        would make the substitution ambiguous.
    """
    marked = json.dumps(_mark_floats(document), indent=2, ensure_ascii=False)
    if marked.count(_FLOAT_MARK) != marked.count(f'"{_FLOAT_MARK}'):
        raise ValueError("document contains the internal float marker")
    text = marked
    text = re.sub(rf'"{re.escape(_FLOAT_MARK)}([^"]*)"', r"\1", text)
    return text.replace("\n", "\r\n").encode("utf-8")


@dataclass
class Envelope:
    """One decoded ``Content`` row.

    Attributes
    ----------
    content_type : str
        The fully qualified .NET class named in the header, for example
        ``syngo.MR.ExamDataFoundation.Data.EdfProtocolContent``.
    payload : bytes
        The JSON bytes that follow the header.
    stored : bytes or None
        The original compressed blob, kept so untouched content round-trips
        byte for byte. ``None`` for an envelope built in memory.
    """

    content_type: str
    payload: bytes
    stored: bytes | None = None

    @property
    def kind(self) -> str:
        """Return the unqualified class name, for example ``EdfProtocolContent``.

        Returns
        -------
        str
            The last dot-separated component of :attr:`content_type`.
        """
        return self.content_type.rsplit(".", 1)[-1]

    @property
    def raw(self) -> bytes:
        """Return the decompressed header-plus-JSON bytes that get hashed.

        Returns
        -------
        bytes
            The header line followed by :attr:`payload`.
        """
        return HEADER_TEMPLATE.format(self.content_type).encode("ascii") + self.payload

    @property
    def hash(self) -> str:
        """Return this envelope's content address.

        Returns
        -------
        str
            Lowercase hex SHA-1 of :attr:`raw`.
        """
        return content_hash(self.raw)

    def decode(self) -> Any:
        """Parse the payload as JSON.

        Returns
        -------
        Any
            The decoded document, normally a ``dict``.
        """
        return json.loads(self.payload.decode("utf-8"))

    def replace(self, document: Any) -> "Envelope":
        """Return a new envelope holding ``document`` in place of the payload.

        The original compressed blob is dropped, since it no longer describes
        the content; the result will be re-compressed when written.

        Parameters
        ----------
        document : Any
            The replacement document.

        Returns
        -------
        Envelope
            A new envelope with the same content type and a fresh payload.
        """
        return Envelope(content_type=self.content_type, payload=dumps(document), stored=None)

    def to_stored(self) -> bytes:
        """Return the blob to write to ``Content.Data``.

        Returns
        -------
        bytes
            The original blob when the payload is untouched, otherwise a
            freshly compressed stream.
        """
        return self.stored if self.stored is not None else compress(self.raw)


def parse(blob: bytes) -> Envelope:
    """Decode one stored ``Content.Data`` blob into an :class:`Envelope`.

    Parameters
    ----------
    blob : bytes
        The raw DEFLATE stream as stored in the archive.

    Returns
    -------
    Envelope
        The decoded envelope, carrying ``blob`` so it can round-trip exactly.

    Raises
    ------
    ValueError
        If the decompressed bytes do not start with an ``EDF V1:`` header.
    """
    raw = decompress(blob)
    match = HEADER_PATTERN.match(raw)
    if match is None:
        raise ValueError(f"content is not an EDF V1 envelope: {raw[:60]!r}")
    return Envelope(
        content_type=match.group(1).decode("ascii"),
        payload=raw[match.end() :],
        stored=blob,
    )


def build(content_type: str, document: Any) -> Envelope:
    """Create an envelope from a document, for content that did not exist yet.

    Parameters
    ----------
    content_type : str
        The fully qualified .NET class to name in the header.
    document : Any
        The document to serialize.

    Returns
    -------
    Envelope
        A new envelope with no stored blob.
    """
    return Envelope(content_type=content_type, payload=dumps(document), stored=None)
