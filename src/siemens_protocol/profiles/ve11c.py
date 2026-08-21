"""VE11C (Numaris/4 syngo MR E11) protocol exports.

Header summary looks like::

    TA: 0:19 PM: REF Voxel size: 0.5x0.5x10.0 mmPAT: Off Rel. SNR: 1.00 : fl

Spectroscopy scans substitute a volume of interest for the voxel size, and
omit ``PAT:`` entirely, which runs ``mm`` into the next label instead::

    TA: 0:36 PM: REF VoI: 25 ×25 ×40 mmRel. SNR: 1.00 : svs_se

Note there is no space before ``PAT:`` or, in that second form, before
``Rel.``; the sequence binary name is appended after a bare colon at the end
of the line.
"""

from __future__ import annotations

import re

from .base import REGISTRY, LayoutConfig, VersionProfile, strip_size_units


class VE11CProfile(VersionProfile):
    """VE11C header grammar."""

    def postprocess_header(self, fields: dict[str, str], line: str) -> dict[str, str]:
        """Split the sequence binary off the SNR field and trim size units.

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
            Fields with ``sequence`` separated out and the spatial-extent
            fields stripped of their trailing unit.
        """
        snr = fields.get("rel_snr", "")
        match = re.match(r"^(?P<snr>[^:]*?)\s*:\s*(?P<seq>\S+)\s*$", snr)
        if match:
            fields["rel_snr"] = match.group("snr").strip()
            fields["sequence"] = match.group("seq").strip()
        return strip_size_units(fields)


PROFILE = REGISTRY.register(
    VE11CProfile(
        name="VE11C",
        require=[r"SIEMENS\s+MAGNETOM"],
        # VE11C prints no version string of its own -- just the scanner
        # model -- so it can only be identified by what it is not. Each
        # rejection names one specific other release rather than all of
        # "syngo MR", which a future VE11C export could legitimately print.
        reject=[r"Numaris/X", r"syngo\s+MR\s+B\d\d"],
        header_labels=[
            ("ta", r"\bTA\s*:"),
            ("pm", r"\bPM\s*:"),
            ("voxel_size_mm", r"\bVoxel\s+size\s*:"),
            # Spectroscopy prints a volume of interest instead of a voxel
            # size. Kept as its own field: a 33x25x22 mm VoI is one
            # acquisition volume, not an imaging resolution, and folding the
            # two together would report a "changed" voxel size whenever the
            # acquisition type changed.
            ("voi_mm", r"\bVoI\s*:"),
            # No \b before PAT: VE11C prints "...1.0 mmPAT: 2" with no
            # separating space, so there is no word boundary to anchor to.
            ("pat", r"PAT\s*:"),
            # Same trap, and it bites whenever PAT is absent: the line then
            # reads "...25 ×22 mmRel. SNR: 1.00", and "m" to "R" is not a word
            # boundary. With \b here the field was silently never found, so
            # the whole tail -- SNR and sequence binary alike -- was absorbed
            # into the preceding field.
            ("rel_snr", r"Rel\.?\s*SNR\s*:"),
        ],
        # VE11C prints neither a coil selection nor a sequence name as an
        # ordinary parameter; the sequence binary comes out of the header line.
        param_fallbacks={},
        native_text_expected=True,
        layout=LayoutConfig(),
    )
)
