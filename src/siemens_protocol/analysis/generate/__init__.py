"""Generate and patch ``.exar1`` archives from a parsed protocol.

Everything here interprets a printed parameter in terms of
:mod:`siemens_protocol.analysis.generate.mappings`' business-rules table --
which stored field a printed label corresponds to, its unit scale, its enum
choices, which sequences and builds it applies to -- and drives writing or
reporting from that. The low-level ASCCONV text codec and the facts a
protocol states about itself (which sequence it runs, its build stamp,
whether a field is save-time churn) live in
:mod:`siemens_protocol.exar.ascconv`, one level down; this package is built
on top of it, never the reverse.
"""

from __future__ import annotations
