"""VE11C (Numaris/4 syngo MR E11) protocol exports.

Header summary looks like::

    TA: 0:19 PM: REF Voxel size: 0.5x0.5x10.0 mmPAT: Off Rel. SNR: 1.00 : fl

Note there is no space before ``PAT:``, and the sequence binary name is
appended after a bare colon at the end of the line.
"""

from __future__ import annotations

import re

from .base import REGISTRY, LayoutConfig, VersionProfile


class VE11CProfile(VersionProfile):
    """VE11C header grammar."""

    def postprocess_header(self, fields: dict[str, str], line: str) -> dict[str, str]:
        """Split the sequence binary off the SNR field and trim the voxel unit.

        VE11C hangs the sequence name on the end of the line after a bare
        colon, so it lands inside the SNR field when the labels are split.

        Parameters
        ----------
        fields : dict of str to str
            Fields as split out by the label parser.
        line : str
            The original summary line. Unused here.

        Returns
        -------
        dict of str to str
            Fields with ``sequence`` separated out and ``voxel_size_mm``
            stripped of its trailing unit.
        """
        snr = fields.get("rel_snr", "")
        match = re.match(r"^(?P<snr>[^:]*?)\s*:\s*(?P<seq>\S+)\s*$", snr)
        if match:
            fields["rel_snr"] = match.group("snr").strip()
            fields["sequence"] = match.group("seq").strip()
        voxel = fields.get("voxel_size_mm")
        if voxel:
            fields["voxel_size_mm"] = re.sub(r"\s*mm\s*$", "", voxel).strip()
        return fields


PROFILE = REGISTRY.register(
    VE11CProfile(
        name="VE11C",
        require=[r"SIEMENS\s+MAGNETOM"],
        reject=[r"Numaris/X"],
        header_labels=[
            ("ta", r"\bTA\s*:"),
            ("pm", r"\bPM\s*:"),
            ("voxel_size_mm", r"\bVoxel\s+size\s*:"),
            # No \b before PAT: VE11C prints "...1.0 mmPAT: 2" with no
            # separating space, so there is no word boundary to anchor to.
            ("pat", r"PAT\s*:"),
            ("rel_snr", r"\bRel\.?\s*SNR\s*:"),
        ],
        # VE11C prints neither a coil selection nor a sequence name as an
        # ordinary parameter; the sequence binary comes out of the header line.
        param_fallbacks={},
        native_text_expected=True,
        layout=LayoutConfig(),
    )
)
