"""High-level manipulation of parsed protocols: compare, catalog, report.

Everything here operates on the document a low-level reader already
produced -- a :class:`~siemens_protocol.model.Protocol`/
:class:`~siemens_protocol.model.Scan`, or their ``dict`` serialization --
never on a PDF page or an ``.exar1`` archive's own tables directly. That
boundary is deliberate: nothing under ``exar``, ``extract``, ``layout``,
``profiles``, ``split`` or ``pipeline`` may import from this package.
"""

from __future__ import annotations
