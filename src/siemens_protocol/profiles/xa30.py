"""XA30 (Numaris/X VA30) protocol exports.

Header summary looks like::

    TA: 9 sec Coil Selection: Auto Voxel Size: 1.2×1.2×5.0 mm³ Acc:: None Rel. SNR: 1.00

XA30 shares the Numaris/X header grammar with XA60 verbatim -- same doubled
colon on ``Acc::``, same omission of the sequence binary from the box -- so
the profile is the shared behaviour plus a version discriminator. The two
releases still differ in their *parameter* vocabulary, which is expressed in
``vocabulary/XA30.json`` rather than here.
"""

from __future__ import annotations

from .base import REGISTRY, LayoutConfig
from .numaris_x import NUMARIS_X_HEADER_LABELS, NUMARIS_X_PARAM_FALLBACKS, NumarisXProfile


class XA30Profile(NumarisXProfile):
    """XA30 header grammar."""


PROFILE = REGISTRY.register(
    XA30Profile(
        name="XA30",
        # Observed running headers: "Numaris/X VA30A-03GR", "-03MV", "-03DZ".
        # The suffix is a site build tag, so only "VA30" is anchored on.
        require=[r"SIEMENS\s+MAGNETOM", r"Numaris/X", r"\bVA30"],
        reject=[],
        header_labels=list(NUMARIS_X_HEADER_LABELS),
        param_fallbacks=dict(NUMARIS_X_PARAM_FALLBACKS),
        native_text_expected=True,
        layout=LayoutConfig(),
    )
)
