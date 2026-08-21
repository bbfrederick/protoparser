"""VB17A (syngo MR B17) protocol exports, the oldest release supported.

Header summary looks like::

    TA: 1:08   PAT: Off   Voxel size: 2.2×1.1×10.0 mm   Rel. SNR: 1.00   SIEMENS: gre

Three things separate it from VE11C. There is no ``PM:`` field. ``PAT:``
comes before the voxel size rather than after it, and is separated by real
spaces instead of running into ``mm``. And the sequence binary is introduced
by a label naming its provenance -- ``SIEMENS:`` for a stock sequence,
``USER:`` for one built at the site -- where VE11C hangs it on a bare colon.
That provenance is recorded as ``sequence_owner``, since it is the one place
the export says whether a sequence is the vendor's.

The body is laid out differently too: the left column's values start at
x=172 rather than x=198, and groups of parameters are separated by a drawn
rule of dashes. The rule is discarded with the running header and page
number; see ``split.is_decorative_rule`` for why it cannot simply be left in.
"""

from __future__ import annotations

import re

from .base import REGISTRY, LayoutConfig, VersionProfile, strip_size_units

#: Which label introduced the sequence name, and so who owns the sequence.
_OWNER_RE = re.compile(r"\b(SIEMENS|USER)\s*:", re.IGNORECASE)
#: A marker printed between the SNR and the sequence on some scans. Its
#: meaning is not documented in the exports, so it is stripped from the SNR
#: rather than interpreted; the raw line is kept in ``header_summary``.
_TRAILING_MARK_RE = re.compile(r"[\s!]+$")


class VB17AProfile(VersionProfile):
    """VB17A header grammar."""

    def postprocess_header(self, fields: dict[str, str], line: str) -> dict[str, str]:
        """Record the sequence's owner and tidy the SNR and voxel size.

        Parameters
        ----------
        fields : dict of str to str
            Fields as split out by the label parser.
        line : str
            The original summary line, read for the provenance label.

        Returns
        -------
        dict of str to str
            Fields with ``sequence_owner`` added where the line names one,
            ``rel_snr`` stripped of a trailing marker, and the voxel size
            stripped of its unit.
        """
        owner = _OWNER_RE.search(line)
        if owner and fields.get("sequence"):
            fields["sequence_owner"] = owner.group(1).upper()
        snr = fields.get("rel_snr")
        if snr:
            fields["rel_snr"] = _TRAILING_MARK_RE.sub("", snr)
        return strip_size_units(fields)


PROFILE = REGISTRY.register(
    VB17AProfile(
        name="VB17A",
        # The running header reads "SIEMENS MAGNETOM TrioTim syngo MR B17".
        # The scanner model varies by site, so only the software string is
        # anchored on.
        require=[r"SIEMENS\s+MAGNETOM", r"syngo\s+MR\s+B17"],
        reject=[],
        header_labels=[
            ("ta", r"\bTA\s*:"),
            ("pat", r"\bPAT\s*:"),
            ("voxel_size_mm", r"\bVoxel\s+size\s*:"),
            ("rel_snr", r"\bRel\.?\s*SNR\s*:"),
            # Two spellings, one field: the label says who wrote the sequence
            # and the text after it is the binary's name either way. Only one
            # of them appears on any given line.
            ("sequence", r"\bSIEMENS\s*:"),
            ("sequence", r"\bUSER\s*:"),
        ],
        # VB17A prints the sequence in the header box, not as a parameter.
        param_fallbacks={},
        native_text_expected=True,
        # The left column's values start further left than in later releases,
        # so the label/value boundary sits a little earlier in the column.
        # Measured across every example column: any ratio in (0.48, 0.58]
        # classifies correctly, and this is the middle of that window.
        # VB17A wraps a long label onto a second line at the *same* pitch as
        # an ordinary row, and prints the value on the first line, so neither
        # the gap nor the value position marks the continuation. Its
        # capitalization does: "Load images to graphic" / "segments".
        layout=LayoutConfig(value_x_ratio=0.53, lowercase_continues_label=True),
    )
)
