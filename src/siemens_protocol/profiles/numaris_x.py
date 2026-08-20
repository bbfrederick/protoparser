"""Behaviour shared by the Numaris/X releases (XA30, XA60).

The Numaris/X family prints one header summary grammar::

    TA: 19 sec Coil Selection: Auto Voxel Size: 0.5×0.5×10.0 mm³ Acc:: None Rel. SNR: 1.00

Two details separate it from Numaris/4 (VE11C): the accelerator field is
spelled ``Acc::`` with a doubled colon, and the sequence binary name is not in
the header box at all -- it is printed as the ``Sequence Name`` parameter and
recovered through ``param_fallbacks``.

Releases differ only in the version string in the running page header, so the
grammar lives here once and each release module supplies its own
discriminator. Keeping the shared part in one place is what stops a future
release from being added by copying a regex block that then drifts.
"""

from __future__ import annotations

from .base import VersionProfile, strip_size_units

#: The header summary grammar, shared verbatim by every Numaris/X release.
#: ``Acc`` is written with an optional doubled colon because the exports print
#: ``Acc::``; matching one or two colons covers both spellings.
NUMARIS_X_HEADER_LABELS: list[tuple[str, str]] = [
    ("ta", r"\bTA\s*:"),
    ("coil_selection", r"\bCoil\s+Selection\s*:"),
    ("voxel_size_mm", r"\bVoxel\s+Size\s*:"),
    # Spectroscopy prints a volume of interest instead of a voxel size. It is
    # a different quantity -- a 25x25x25 mm VoI is one acquisition volume, not
    # a resolution -- so it gets its own field rather than being folded in.
    ("voi_mm", r"\bVoI\s*:"),
    ("pat", r"\bAcc\s*:{1,2}"),
    ("rel_snr", r"\bRel\.?\s*SNR\s*:"),
]

#: Header fields these releases omit from the box and print as parameters.
NUMARIS_X_PARAM_FALLBACKS: dict[str, str] = {"sequence": "Sequence Name"}


class NumarisXProfile(VersionProfile):
    """Header handling common to the Numaris/X releases."""

    def postprocess_header(self, fields: dict[str, str], line: str) -> dict[str, str]:
        """Trim the trailing unit from the voxel size or volume of interest.

        Parameters
        ----------
        fields : dict of str to str
            Fields as split out by the label parser.
        line : str
            The original summary line. Unused here.

        Returns
        -------
        dict of str to str
            Fields with the spatial-extent fields stripped of their unit.
        """
        return strip_size_units(fields)
