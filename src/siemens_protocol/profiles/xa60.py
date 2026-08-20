"""XA60 (Numaris/X VA60) protocol exports.

Header summary looks like::

    TA: 19 sec Coil Selection: Auto Voxel Size: 0.5×0.5×10.0 mm3 Acc:: None Rel. SNR: 1.00

The grammar is shared with the other Numaris/X releases and lives in
:mod:`.numaris_x`; only the version discriminator is release-specific.
"""

from __future__ import annotations

from .base import REGISTRY, LayoutConfig
from .numaris_x import NUMARIS_X_HEADER_LABELS, NUMARIS_X_PARAM_FALLBACKS, NumarisXProfile


class XA60Profile(NumarisXProfile):
    """XA60 header grammar."""


PROFILE = REGISTRY.register(
    XA60Profile(
        name="XA60",
        # The running header reads "Numaris/X VA60A-0D4N". Matching the exact
        # release number matters: a bare "VA\d\d" also matches VA30, which
        # made XA30 exports detect as XA60 with high confidence.
        require=[r"SIEMENS\s+MAGNETOM", r"Numaris/X", r"\bVA60"],
        reject=[],
        header_labels=list(NUMARIS_X_HEADER_LABELS),
        param_fallbacks=dict(NUMARIS_X_PARAM_FALLBACKS),
        native_text_expected=True,
        layout=LayoutConfig(),
    )
)
