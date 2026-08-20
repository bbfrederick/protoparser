"""XA60 (Numaris/X VA60) protocol exports.

Header summary looks like::

    TA: 19 sec Coil Selection: Auto Voxel Size: 0.5x0.5x10.0 mm3 Acc:: None Rel. SNR: 1.00

Two differences from VE11C matter: the accelerator field is spelled ``Acc::``
with a doubled colon, and the sequence binary name is not in the header box at
all -- it is printed as the ``Sequence Name`` parameter, so it is recovered
through ``param_fallbacks``.
"""

from __future__ import annotations

import re

from .base import REGISTRY, LayoutConfig, VersionProfile


class XA60Profile(VersionProfile):
    """XA60 header grammar."""

    def postprocess_header(self, fields: dict[str, str], line: str) -> dict[str, str]:
        """Trim the trailing unit from the voxel size.

        Parameters
        ----------
        fields : dict of str to str
            Fields as split out by the label parser.
        line : str
            The original summary line. Unused here.

        Returns
        -------
        dict of str to str
            Fields with ``voxel_size_mm`` stripped of its ``mm³`` suffix.
        """
        voxel = fields.get("voxel_size_mm")
        if voxel:
            fields["voxel_size_mm"] = re.sub(r"\s*mm[³²]?\s*$", "", voxel).strip()
        return fields


PROFILE = REGISTRY.register(
    XA60Profile(
        name="XA60",
        require=[r"SIEMENS\s+MAGNETOM", r"Numaris/X", r"\bVA\d{2}"],
        reject=[],
        header_labels=[
            ("ta", r"\bTA\s*:"),
            ("coil_selection", r"\bCoil\s+Selection\s*:"),
            ("voxel_size_mm", r"\bVoxel\s+Size\s*:"),
            ("pat", r"\bAcc\s*:{1,2}"),
            ("rel_snr", r"\bRel\.?\s*SNR\s*:"),
        ],
        param_fallbacks={"sequence": "Sequence Name"},
        native_text_expected=True,
        layout=LayoutConfig(),
    )
)
