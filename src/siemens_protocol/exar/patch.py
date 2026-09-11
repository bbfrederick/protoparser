"""Write a printed parameter back into a protocol, in every place it lives.

A displayed value can be stored in more than one place, and the shapes differ:

``Preview`` and ASCCONV together
    ``TR`` is ``Preview["sub.0.msr.tr.0"]`` in milliseconds and ``alTR[0]`` in
    microseconds. Patching one and not the other is silently wrong in opposite
    directions -- the console lists a number the scan will not use, or the
    listing goes stale.
An ASCCONV *array*
    ``FOV Read`` and ``Slice Thickness`` are replicated across every element of
    ``sSliceArray.asSlice[]`` -- three of them on a localizer, sixty-four on a
    multi-slice EPI. Writing element zero alone leaves the rest at the old
    value, which loads, lists correctly, and is wrong.
A value derived from another
    ``FOV Phase`` is a percentage on the card but millimetres in the protocol:
    ``dPhaseFOV`` is ``dReadoutFOV`` times that percentage. It cannot be
    written without reading the read FOV first, which is what :attr:`Mapping.basis`
    is for. ``Preview`` also rounds it -- 96.7 against a stored 96.6667 -- so
    for this one parameter the two stores genuinely disagree slightly.
ASCCONV alone
    Nothing on the Special card appears in ``Preview``; the console lists only
    common parameters. Those mappings carry ``preview_path=None`` and there is
    nothing to keep in sync.

The Special card is also why mappings carry :attr:`Mapping.sequences`.
``sWipMemBlock`` is scratch memory the sequence binary interprets as it likes,
so an index has no global meaning: ``alFree[0]`` is MT Flip Angle on
``can_neuromelanin`` and a packed word of ten checkbox flags on CMRR's
multiband sequences. A table that treated it as one parameter would write a
flip angle into CMRR's flags.

What this module deliberately does *not* do is recompute derived values. The
console does: changing TR moved ``lScanTimeSec`` and ``lTotalScanTimeSec`` in
the reference pairs. A patched archive carries the old scan time, and
:class:`Manifest` says so rather than leaving it to be discovered later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from typing import Mapping as MappingType

from .archive import Archive, Protocol, Step

#: How a record spells an assignment that is not present. A sparse array omits
#: an element holding zero, so "absent" is a value rather than a gap.
ABSENT = "(absent)"

#: The CMRR multiband build every Special-card mapping here was derived from.
#: Compared against :func:`build_id`, so the timestamp is deliberately absent:
#: the three sequences of one release are built minutes apart and differ in
#: that field alone.
CMRR_R017 = "Sequence: R017 nxva60a/main r/91b106c1e"

#: Scalar assignments the console leaves out entirely rather than writing a
#: zero into. Each was observed absent on some corpus protocols and present on
#: others, and every option scan that turns one off deletes its line. This is a
#: list rather than a rule because it is not one: ``ucStaticFieldCorrection``
#: looks identical -- a ``uc`` flag written ``0x0``/``0x1`` -- and is written
#: on all 321 corpus scans even while off, so treating the shape as sparse
#: would delete an assignment the console keeps.
SPARSE_KEYS = frozenset(
    {
        "sAdjData.uiAdjWithBC",
        "sAdjData.uiAdjTableToleranceValid",
        "sAdjData.uiAdjFreSiliconeDetection",
        "sSliceArray.ucImageNumbCor",
        "sSliceArray.ucImageNumbMSMA",
        "sSliceArray.ucImageNumbSag",
        "sSliceArray.ucImageNumbTra",
        "sPreScanNormalizeFilter.ucOn",
        "ucReconstructionPrio",
        "sWorkflow.ucWaitForUserStart",
        "sAAInitialOffset.SliceInformation.dInPlaneRot",
        "sPrepPulses.ucMTC",
        "sGroupArray.asGroup[0].dDistFact",
        "lRepetitions",
    }
)

#: Assignments the console spells in hexadecimal. Only consulted when an
#: assignment is being *created*, since an existing literal supplies its own
#: spelling. Every entry was read off a corpus protocol that carries the key.
HEX_KEYS = frozenset(
    {
        "sAdjData.uiAdjWithBC",
        "sAdjData.uiAdjTableToleranceValid",
        "sAdjData.uiAdjFreSiliconeDetection",
        "sPreScanNormalizeFilter.ucOn",
        "ucReconstructionPrio",
        "sWorkflow.ucWaitForUserStart",
        "ucStaticFieldCorrection",
        "sPrepPulses.ucMTC",
    }
)

#: Sparse assignments the console spells as a plain integer. Distinguished
#: from :data:`HEX_KEYS` by reading the console's own output: these four sit
#: beside ``sSliceArray.ucMode`` and are written ``1``, while the flags in
#: ``sAdjData`` and ``sWorkflow`` beside them are written ``0x1``. Nothing in
#: the name says which, so both lists are observations rather than a rule.
INT_KEYS = frozenset(
    {
        "sSliceArray.ucImageNumbCor",
        "sSliceArray.ucImageNumbMSMA",
        "sSliceArray.ucImageNumbSag",
        "sSliceArray.ucImageNumbTra",
    }
)

#: Where the console writes each sparse scalar: the assignment it follows,
#: nearest candidate first. ASCCONV is written in the schema's order, not
#: alphabetically -- ``ulWrapUpMagn`` precedes ``ucReconstructionPrio`` which
#: precedes ``lAverages`` -- so a new line's position cannot be derived from
#: its name and is read off the console's own output instead. Every anchor
#: here is the single predecessor observed wherever the key appears across the
#: corpus and the option scans, on between 1 and 265 scans. ``uiAdjWithBC``
#: takes two because the ``sAdjData`` group has its own internal order.
SPARSE_ANCHORS: dict[str, tuple[str, ...]] = {
    "sAAInitialOffset.SliceInformation.dInPlaneRot": ("sAAInitialOffset.Laterality",),
    "sAdjData.uiAdjTableToleranceValid": ("sAdjData.uiAdjTableTolerance",),
    "sAdjData.uiAdjWithBC": (
        "sAdjData.uiAdjTableToleranceValid",
        "sAdjData.uiAdjTableTolerance",
    ),
    "sAdjData.uiAdjFreSiliconeDetection": ("sAdjData.uiAdjWithBC",),
    "sPreScanNormalizeFilter.ucOn": ("sAngio.sFlowArray.asElm.__attribute__.size",),
    "sSliceArray.ucImageNumbCor": ("sSliceArray.ucMode",),
    "sSliceArray.ucImageNumbMSMA": ("sSliceArray.ucMode",),
    "sSliceArray.ucImageNumbSag": ("sSliceArray.ucMode",),
    "sSliceArray.ucImageNumbTra": ("sSliceArray.ucMode",),
    "sWorkflow.ucWaitForUserStart": ("sInversionArray.asElm.__attribute__.size",),
    "lRepetitions": ("dAveragesDouble",),
    "sGroupArray.asGroup[0].dDistFact": ("sGroupArray.asGroup[0].nSize",),
    "sPrepPulses.ucMTC": ("sPrepPulses.ucTIScout",),
    "ucReconstructionPrio": ("ulWrapUpMagn",),
}

#: Delimiters of the ASCCONV block inside the XProtocol text.
ASCCONV_BEGIN = "### ASCCONV BEGIN"
ASCCONV_END = "### ASCCONV END"


@dataclass(frozen=True)
class Mapping:
    """One printed parameter, and everywhere its value is stored.

    Attributes
    ----------
    label : str
        The label the console and the PDF print, for example ``TR``.
    ascconv_key : str
        Assignment in the ASCCONV block. May contain ``[*]``, which stands for
        every index the block actually defines -- the slice arrays are sized
        per protocol, so the set is read from the document rather than assumed.
    evidence : str
        How the mapping was established. These were derived from a corpus
        rather than documentation, and a controlled edit is much stronger
        evidence than mere agreement, so the difference is recorded.
    preview_path : str or None
        Key into the protocol's ``Preview`` map, or ``None`` when the console
        does not list this parameter -- true of everything on the Special card.
    scale : float
        Multiplier taking the displayed value to the stored one. ``1000`` for
        the times, which are displayed in milliseconds and stored in
        microseconds.
    sign_from : str or None
        A printed label carrying the direction letter for this parameter. A
        Siemens printout gives a position as a magnitude and a letter -- the
        console shows ``F32`` where the protocol holds ``-32`` -- and on a
        two-column card the letter lands in its own field. Without it the
        magnitude alone does not determine the stored value.
    negative_letters : tuple of str
        The letters of that pair meaning a negative value. L, P, F and I are
        the negative halves of the three axes; only the H/F pair is exercised
        by the corpus, so only it is listed.
    offset : float
        Added to the displayed value before :attr:`scale`. ``Measurements``
        needs it: the card counts measurements from one and ``lRepetitions``
        counts repeats, so four measurements are stored as three.
    basis : str or None
        Another ASCCONV key whose value also multiplies the written one, for a
        parameter stored relative to a second field. ``[*]`` here resolves to
        the same index as the target, which is what makes ``dPhaseFOV`` follow
        its own slice's ``dReadoutFOV``.
    sequences : tuple of str
        Sequence names this mapping applies to, matched against the protocol's
        ``seq_subpath``. Empty means every sequence. Non-empty is mandatory for
        anything in ``sWipMemBlock``.
    choices : tuple of tuple
        ``(displayed text, stored integer)`` pairs for a parameter the card
        shows as a word rather than a number. Established by toggling one
        option per export and reading which integer moved.
    absent_choice : str or None
        The displayed text a protocol shows when the assignment is absent
        altogether. A ``sWipMemBlock`` array omits an element nobody has set,
        and the sequence then supplies its own default -- which is *not*
        necessarily the choice stored as zero, so it has to be observed rather
        than derived. Only ``Protocol filename`` has been seen this way, where
        ``Generic`` is both stored as ``1`` and displayed for nothing stored.
    read_only : bool
        Whether this mapping may only be *read*. A parameter the console
        derives is printed as a display of something it computed, so the
        printed number is sound to decode and unsound to write back: the
        scan resolution in the phase direction is stored as the line count
        the console worked out from base resolution, phase resolution and
        the phase FOV, and writing the printed number over it puts a figure
        there the console did not choose. Such a mapping decodes on a card
        and is invisible to the writer.
    bit : int or None
        Position of this parameter's flag within ``ascconv_key``, for a
        checkbox packed into a shared word. Writing one is a read-modify-write
        of that word, and an absent word counts as zero.
    builds : tuple of str
        Sequence builds this mapping is verified against, compared against
        :func:`build_id`. Empty means the mapping does not depend on the
        build, which is the honest default for a parameter stored in its own
        named field. It is *not* the right default for a bit packed into a
        shared word: a later release is free to renumber those, nothing in the
        protocol announces it, and the value would simply land in a different
        option. Only the CMRR sequences stamp a build at all -- the ABCD
        navigators write a ``.prot`` file name there and two sequences write
        nothing -- so their mappings cannot be guarded this way and say so by
        leaving this empty.
    when : tuple of str or None
        An ``(ASCCONV key, literal)`` pair that must hold for this mapping to
        apply, which is how one label can be stored two different ways. Slice
        thickness is the case: on a 2D acquisition ``dThickness`` is the slice,
        and on a 3D one it is the whole *slab*, so the two need separate
        entries selected by ``sKSpace.ucDimension``.
    """

    label: str
    ascconv_key: str
    evidence: str
    preview_path: str | None = None
    scale: float = 1.0
    sign_from: str | None = None
    negative_letters: tuple[str, ...] = ()
    offset: float = 0.0
    basis: str | None = None
    sequences: tuple[str, ...] = ()
    choices: tuple[tuple[str, int], ...] = ()
    absent_choice: str | None = None
    read_only: bool = False
    bit: int | None = None
    builds: tuple[str, ...] = ()
    when: tuple[str, str] | None = None

    @property
    def is_sequence_specific(self) -> bool:
        """Return whether this mapping is restricted to named sequences.

        Returns
        -------
        bool
            ``True`` when :attr:`sequences` is non-empty.
        """
        return bool(self.sequences)


#: Preview key holding the sequence name a protocol runs.
SEQUENCE_PATH = "sub.0.msr.seq_subpath"

#: Every mapping this module will write.
#:
#: All of it was established against ``examples/XA60/`` rather than from any
#: Siemens document, and the ``evidence`` field says how. A *controlled edit*
#: is the strong form: a pair of exports differing by one deliberate change,
#: which shows directly that a field moved with a label. *Agreement* is the
#: weak form: the two stores hold the same number under the stated scale
#: across every protocol, with no rival key and enough distinct values to make
#: coincidence implausible.
#:
#: Nothing is added on the strength of its name. ``FOV Read`` sat out of this
#: table until an export arrived with FOV Phase off 100%, because until then
#: ``dReadoutFOV`` and ``dPhaseFOV`` held the same number in every protocol
#: and the data could not say which one the label meant.
MAPPINGS: tuple[Mapping, ...] = (
    Mapping(
        label="TR",
        preview_path="sub.0.msr.tr.0",
        ascconv_key="alTR[0]",
        scale=1000.0,
        evidence="controlled edit: P2 pair, 5 scans; and P1 pair",
    ),
    Mapping(
        label="TE",
        preview_path="sub.0.msr.te.0",
        ascconv_key="alTE[0]",
        scale=1000.0,
        evidence="controlled edit: P1 pair, 4 scans. Multi-echo prints TE 1..TE 4 "
        "and Preview carries only the first; alTE[1..3] have no preview side.",
    ),
    # Each later echo is written on its own rather than by shifting the train
    # from TE 1. On CMRR's multi-echo BOLD the echo spacing sits at the
    # minimum, so moving the first echo does move the rest and keeps the
    # spacing -- but that is a property of that sequence at that setting, not
    # of the format, and spacing varies independently elsewhere. Writing every
    # echo the printout names reproduces either case without having to know
    # which one applies. Verified by agreement rather than a controlled edit:
    # printed TE n equals alTE[n-1] on every comparable scan in the corpus.
    Mapping(
        label="TE 2",
        ascconv_key="alTE[1]",
        scale=1000.0,
        evidence="corpus agreement: 129 scans, printed TE 2 against alTE[1] at the "
        "precision the export prints.",
    ),
    Mapping(
        label="TE 3",
        ascconv_key="alTE[2]",
        scale=1000.0,
        evidence="corpus agreement: 127 scans, printed TE 3 against alTE[2].",
    ),
    Mapping(
        label="TE 4",
        ascconv_key="alTE[3]",
        scale=1000.0,
        evidence="corpus agreement: 126 scans, printed TE 4 against alTE[3].",
    ),
    Mapping(
        label="Flip Angle",
        preview_path="sub.0.msr.angle_array.0",
        ascconv_key="adFlipAngleDegree[0]",
        evidence="controlled edit: P1 pair, localizer",
    ),
    Mapping(
        label="Base Resolution",
        preview_path="sub.0.msr.matrix",
        ascconv_key="sKSpace.lBaseResolution",
        evidence="controlled edit: P1 pair, 2 scans",
        # The corpus shows "Scan Res. R >> L" tracking this same key, and
        # the owner confirms it: the readout direction's scan resolution is
        # the base resolution, printed under a second name on the cards a
        # spectroscopy scan uses. One key takes one mapping, so it is
        # recorded here rather than added as a duplicate the writer would
        # have to choose between.
    ),
    Mapping(
        label="Slices per Slab",
        preview_path="sub.0.msr.ips",
        ascconv_key="sKSpace.lImagesPerSlab",
        evidence="agreement across 14 protocols, 4 distinct values, no rival key",
    ),
    Mapping(
        label="FOV Read",
        preview_path="sub.0.msr.readout_fov",
        ascconv_key="sSliceArray.asSlice[*].dReadoutFOV",
        evidence="controlled edit: P1 pair, CMRR 207->206 mm. Replicated across "
        "every slice -- 3 on a localizer, 64 on the CMRR EPI.",
    ),
    Mapping(
        label="Slice Thickness",
        preview_path="sub.0.msr.sl_thick",
        ascconv_key="sSliceArray.asSlice[*].dThickness",
        when=("sKSpace.ucDimension", "2"),
        evidence="controlled edit: P1 pair, localizer 7.0->7.5 and CMRR 2.3->2.2, "
        "both 2D. Replicated per slice.",
    ),
    Mapping(
        label="Slice Thickness",
        preview_path="sub.0.msr.sl_thick",
        ascconv_key="sSliceArray.asSlice[*].dThickness",
        basis="sKSpace.lImagesPerSlab",
        when=("sKSpace.ucDimension", "4"),
        evidence="agreement: on all 8 three-dimensional protocols dThickness is the "
        "whole slab -- exactly the displayed thickness times lImagesPerSlab. No "
        "controlled edit has moved a slab thickness, so this is the weaker form; "
        "the 2D entry above is what the edit covered.",
    ),
    Mapping(
        label="FOV Phase",
        preview_path="sub.0.msr.phase_fov",
        ascconv_key="sSliceArray.asSlice[*].dPhaseFOV",
        scale=0.01,
        basis="sSliceArray.asSlice[*].dReadoutFOV",
        evidence="controlled edit: P1 pair, 100%->96.7% and 100%->97.7%. Stored as "
        "millimetres, not percent: dPhaseFOV = dReadoutFOV * percent / 100.",
    ),
    # ---- Derived from the paramcheck option scans: one option varied per
    # ---- copy on cmrr_mbep2d_bold, each label read off the matching export.
    # ---- An enum carries only the choices those scans actually exercised,
    # ---- which is two apiece; encode() refuses anything else rather than
    # ---- guessing at a code it has never seen.
    Mapping(
        label="Measurements",
        ascconv_key="lRepetitions",
        offset=-1.0,
        evidence="controlled edit: contrastopts/C09, 4->3 measurements. The card counts "
        "measurements and lRepetitions counts repeats, so the stored value trails by one.",
    ),
    Mapping(
        label="Distance Factor",
        ascconv_key="sGroupArray.asGroup[*].dDistFact",
        scale=0.01,
        evidence="controlled edit: extravals X03 and X04, 0%->20%->50% stored as 0.2 and "
        "0.5. Sparse: absent on 245 corpus scans, which is a gap of zero. One of the six "
        "inputs the per-slice array is computed from -- writing it without recomputing "
        "sSliceArray leaves the slice positions describing the old spacing. Written to "
        "every slice group, which is exact for the 420 single-group scans and unverified "
        "for the 25 that carry three.",
    ),
    Mapping(
        label="Acceleration Factor PE",
        ascconv_key="sPat.lAccelFactPE",
        evidence="controlled edit: resolutionopts/RE09, 2->3",
    ),
    Mapping(
        label="Phase Partial Fourier",
        ascconv_key="sKSpace.ucPhasePartialFourier",
        choices=(("6/8", 4), ("7/8", 8), ("Off", 16), ("Allowed", 32)),
        evidence="controlled edit: resolutionopts/RE11, Off->7/8; 6/8 and Allowed "
        "by corpus agreement. The codes are powers of two rather than fractions, "
        "so anything not observed still cannot be guessed.",
    ),
    Mapping(
        label="MTC",
        ascconv_key="sPrepPulses.ucMTC",
        choices=(("Off", 0), ("On", 1)),
        evidence="controlled edit: contrastopts/C04, Off->On. Sparse.",
    ),
    Mapping(
        label="Reference Lines PE",
        ascconv_key="sPat.lRefLinesPE",
        evidence="controlled edit: resolutionopts/RE10, 24->26",
    ),
    Mapping(
        label="Table Position",
        ascconv_key="lScanRegionPosTra",
        sign_from="Table Position #2",
        negative_letters=("F", "I", "L", "P"),
        evidence="controlled edit: geomopts/G24, 6->7 mm. The card prints a magnitude "
        "and a direction letter in a field of its own, and the letter is what carries "
        "the sign: H against a non-negative value and F against a negative one on all "
        "300 corpus comparisons, none against. Reading the magnitude alone flipped the "
        "sign on the nineteen scans holding -32, which is what driving an archive from "
        "its own printout exposed.",
    ),
    Mapping(
        label="Image Scaling",
        ascconv_key="dOverallImageScaleCorrectionFactor",
        evidence="controlled edit: systemandphysioopts, 1.000->0.999",
    ),
    Mapping(
        label="Initial Rotation",
        ascconv_key="sAAInitialOffset.SliceInformation.dInPlaneRot",
        scale=0.017453292519943295,
        evidence="controlled edit: geomopts/G19, 0.00->0.02 deg stored as 0.000349065850399 "
        "radians. Sparse: absent on 309 of 321 corpus scans, which is zero rotation.",
    ),
    Mapping(
        label="Static Field Correction",
        ascconv_key="ucStaticFieldCorrection",
        choices=(("Off", 0), ("On", 1)),
        evidence="controlled edit: resolutionopts/RE18, Off->On. Written 0x0 on all 321 "
        "corpus scans even while off, so unlike its neighbours it is not sparse.",
    ),
    Mapping(
        label="Distortion Correction",
        ascconv_key="sDistortionCorrFilter.ucMode",
        choices=(("Off", 1), ("2D", 2), ("3D", 4)),
        evidence="controlled edit: resolutionopts/RE17, 2D->3D; Off by corpus "
        "agreement on 30 scans.",
    ),
    Mapping(
        label="Fat-Water Contrast",
        ascconv_key="sPrepPulses.lFatWaterContrast",
        choices=(
            ("Standard", 1),
            ("Fat Saturation", 4),
            ("Water Excitation", 16),
            ("Fast Water Excitation", 32),
        ),
        evidence="controlled edit: contrastopts/C06, Fat Saturation->Standard; the "
        "two water-excitation settings by corpus agreement.",
    ),
    Mapping(
        label="Normalize",
        ascconv_key="sPreScanNormalizeFilter.ucOn",
        choices=(("Off", 0), ("Prescan", 1)),
        evidence="controlled edit: resolutionopts/RE20, Prescan->Off. Sparse: the "
        "assignment is deleted rather than set to zero, seen on 168 corpus scans. "
        "'Image Based' is deliberately absent: it stores the same absence as Off, "
        "so it is distinguished by another field and cannot be written from here.",
    ),
    Mapping(
        label="Prio Recon",
        ascconv_key="ucReconstructionPrio",
        choices=(("Off", 0), ("On", 1)),
        evidence="controlled edit: executionopts/E06, Off->On. Sparse.",
    ),
    Mapping(
        label="Wait for User to Start",
        ascconv_key="sWorkflow.ucWaitForUserStart",
        choices=(("Off", 0), ("On", 1)),
        evidence="controlled edit: executionopts/E03, Off->On. Sparse.",
    ),
    Mapping(
        label="AutoAlign",
        ascconv_key="ucAARefMode",
        choices=(
            ("Head > Basis", 2),
            ("Head > Brain", 4),
            ("Head > IAC", 8),
            ("Head > Optic Nerves", 16),
            ("Head > Optic Nerve L", 32),
            ("Head > Optic Nerve R", 64),
            ("Head > Temporal Lobe", 512),
            ("Head > Orbits", 1024),
        ),
        evidence="controlled edit: geomopts/G16, plus corpus agreement over the "
        "menus scan, which steps the region list. Code 1 is deliberately absent: "
        "it prints as both '---' and 'Head', so it is not a function of this "
        "field alone and writing it would need ucAARegionMode too.",
    ),
    Mapping(
        label="Series",
        ascconv_key="sSliceArray.ucMode",
        choices=(("Ascending", 1), ("Descending", 2), ("Interleaved", 4)),
        evidence="controlled edit: geomopts/G14, Interleaved->Descending; "
        "Ascending by corpus agreement on 16 scans.",
    ),
    Mapping(
        label="B0 Shim",
        ascconv_key="sAdjData.uiAdjShimMode",
        choices=(("Tune up", 1), ("Standard", 2), ("Brain", 512)),
        evidence="controlled edit: systemandphysioopts, Standard->Brain; Tune up "
        "by corpus agreement on 43 scans.",
    ),
    Mapping(
        label="B1 Shim",
        ascconv_key="sTXSPEC.lB1ShimMode",
        choices=(("TrueForm", 1), ("TrueForm C", 8)),
        evidence="controlled edit: systemandphysioopts, TrueForm->TrueForm C",
    ),
    Mapping(
        label="Coil Focus",
        ascconv_key="sChannelMatrix.ucChannelDiscardMode",
        choices=(("Flat", 1), ("Center", 2)),
        evidence="controlled edit: systemandphysioopts, Flat->Center",
    ),
    Mapping(
        label="Matrix Optimization",
        ascconv_key="sChannelMatrix.ucChannelMixingMode",
        choices=(("Off", 1), ("Performance", 2)),
        evidence="controlled edit: systemandphysioopts, Performance->Off",
    ),
    Mapping(
        label="Confirm Frequency",
        ascconv_key="sAdjData.uiAdjFreqConfirmSpec",
        choices=(("Never", 1), ("Always", 2)),
        evidence="controlled edit: systemandphysioopts, Never->Always",
    ),
    Mapping(
        label="Adjust with Body Coil",
        ascconv_key="sAdjData.uiAdjWithBC",
        choices=(("Off", 0), ("On", 1)),
        evidence="controlled edit: systemandphysioopts, On->Off. Sparse.",
    ),
    Mapping(
        label="Assume Silicone",
        ascconv_key="sAdjData.uiAdjFreSiliconeDetection",
        choices=(("Off", 0), ("On", 1)),
        evidence="controlled edit: systemandphysioopts, Off->On. Sparse.",
    ),
    Mapping(
        label="Adjustment Tolerance",
        ascconv_key="sAdjData.uiAdjTableToleranceValid",
        choices=(("Auto", 0), ("Maximum", 1)),
        evidence="controlled edit: systemandphysioopts, Auto->Maximum. Sparse.",
    ),
    Mapping(
        label="Transversal",
        ascconv_key="sSliceArray.ucImageNumbTra",
        choices=(("F >> H", 0), ("H >> F", 1)),
        evidence="controlled edit: systemandphysioopts, F >> H -> H >> F. Sparse.",
    ),
    Mapping(
        label="Sagittal",
        ascconv_key="sSliceArray.ucImageNumbSag",
        choices=(("R >> L", 0), ("L >> R", 1)),
        evidence="controlled edit: systemandphysioopts, R >> L -> L >> R. Sparse.",
    ),
    Mapping(
        label="Coronal",
        ascconv_key="sSliceArray.ucImageNumbCor",
        choices=(("A >> P", 0), ("P >> A", 1)),
        evidence="controlled edit: systemandphysioopts, A >> P -> P >> A. Sparse.",
    ),
    Mapping(
        label="MSMA",
        ascconv_key="sSliceArray.ucImageNumbMSMA",
        choices=(("S - C - T", 0), ("S - T - C", 1)),
        evidence="controlled edit: systemandphysioopts, S - C - T -> S - T - C. Sparse.",
    ),
    # ---- The Special card. ASCCONV only, and meaningful per sequence. ----
    Mapping(
        label="Reference scan mode",
        ascconv_key="sWipMemBlock.alFree[26]",
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        choices=(("Single-shot", 1), ("Segmented", 2)),
        evidence="controlled edit: resolutionopts/RE08, Single-shot->Segmented",
    ),
    Mapping(
        label="MT Flip Angle",
        ascconv_key="sWipMemBlock.alFree[0]",
        sequences=("can_neuromelanin",),
        evidence="controlled edit: P1 pair, 370->360 degrees",
    ),
    Mapping(
        label="MT Offset",
        ascconv_key="sWipMemBlock.alFree[1]",
        sequences=("can_neuromelanin",),
        evidence="controlled edit: P1 pair, 1500->1490 Hz",
    ),
    Mapping(
        label="Add. grad time",
        ascconv_key="sWipMemBlock.adFree[3]",
        sequences=("tfl_mgh_epinav_ABCD",),
        evidence="controlled edit: P1 pair, 0.00->0.10 ms",
    ),
    # ---- CMRR's packed flags word. One bit per Special-card checkbox,
    # each pinned by an export toggling that box alone, and consistent
    # across the BOLD, SE and diffusion sequences.
    Mapping(
        label="Single-band images",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=0,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 0",
    ),
    Mapping(
        label="PF omits higher k-space",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=1,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 1",
    ),
    Mapping(
        label="SENSE1 coil combine",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=4,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 4",
    ),
    Mapping(
        label="Invert RO/PE polarity",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=8,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 8",
    ),
    Mapping(
        label="MB RF phase scramble",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=9,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 9",
    ),
    Mapping(
        label="Time-shifted MB RF",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=10,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 10",
    ),
    Mapping(
        label="MB LeakBlock kernel",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=12,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 12",
    ),
    Mapping(
        label="MB dual kernel",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=16,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 16",
    ),
    Mapping(
        label="Disable freq. update",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=18,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 18",
    ),
    Mapping(
        label="Force equal slice timing",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=20,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 20",
    ),
    Mapping(
        label="Opt. MB RF pulse BW",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=22,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 22",
    ),
    Mapping(
        label="Suppress 16-bit DICOM",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=25,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 25",
    ),
    Mapping(
        label="Disable B1 control loop",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=27,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 27",
    ),
    Mapping(
        label="Force GPA balance",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=28,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        builds=(CMRR_R017,),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 28",
    ),
    # ---- The ABCD navigated sequences. Shared between the MPRAGE and
    # SPACE variants, which agree on every index below.
    Mapping(
        label="Feedback Delay",
        ascconv_key="sWipMemBlock.alFree[6]",
        sequences=("tfl_mgh_epinav_ABCD", "space_mgh_epinav_ABCD"),
        evidence="controlled edit: NAV_optionscan_P1, single-option toggle. ms, written directly.",
    ),
    Mapping(
        label="Remeasure",
        ascconv_key="sWipMemBlock.alFree[9]",
        sequences=("tfl_mgh_epinav_ABCD", "space_mgh_epinav_ABCD"),
        evidence="controlled edit: NAV_optionscan_P1, single-option toggle. TRs, written directly.",
    ),
    Mapping(
        label="Reacq. threshold",
        ascconv_key="sWipMemBlock.adFree[2]",
        sequences=("tfl_mgh_epinav_ABCD", "space_mgh_epinav_ABCD"),
        evidence="controlled edit: NAV_optionscan_P1, single-option toggle. written directly.",
    ),
    Mapping(
        label="Moco ref. image",
        ascconv_key="sWipMemBlock.alFree[7]",
        choices=(("Use Temp Ref", 1), ("New Sess Ref", 2), ("Use Sess Ref", 3)),
        sequences=("tfl_mgh_epinav_ABCD", "space_mgh_epinav_ABCD"),
        evidence="controlled edit: NAV_optionscan_P1, single-option toggle. three-way choice.",
    ),
    Mapping(
        label="Apply moco to",
        ascconv_key="sWipMemBlock.alFree[8]",
        choices=(("neither", 1), ("nav only", 2), ("parent and nav", 3)),
        sequences=("tfl_mgh_epinav_ABCD", "space_mgh_epinav_ABCD"),
        evidence="controlled edit: NAV_optionscan_P1, single-option toggle. three-way choice.",
    ),
    Mapping(
        label="Apply freq to",
        ascconv_key="sWipMemBlock.alFree[10]",
        choices=(("neither", 1), ("parent and nav", 3)),
        sequences=("tfl_mgh_epinav_ABCD", "space_mgh_epinav_ABCD"),
        evidence="controlled edit: NAV_optionscan_P1, single-option toggle. only two states seen; 2 unobserved.",
    ),
    Mapping(
        label="K-space streaming",
        ascconv_key="sWipMemBlock.alFree[14]",
        choices=(("None", 1), ("File", 2), ("Network", 3)),
        sequences=("tfl_mgh_epinav_ABCD", "space_mgh_epinav_ABCD"),
        evidence="controlled edit: NAV_optionscan_P1, single-option toggle. three-way choice.",
    ),
    Mapping(
        label="ABCD navigator",
        ascconv_key="sWipMemBlock.alFree[15]",
        choices=(("Off", 1), ("On", 2)),
        sequences=("tfl_mgh_epinav_ABCD", "space_mgh_epinav_ABCD"),
        evidence="controlled edit: NAV_optionscan_P1, single-option toggle. two-way choice.",
    ),
    # alFree[12] carries a different parameter on each of the two, which is
    # why the nav family cannot share one entry for it.
    Mapping(
        label="Nav. location",
        ascconv_key="sWipMemBlock.alFree[12]",
        choices=(("Before", 1), ("After", 2), ("None", 3)),
        sequences=("tfl_mgh_epinav_ABCD",),
        evidence="controlled edit: NAV_optionscan_P1. Setting None also clears Remeasure.",
    ),
    Mapping(
        label="Include Nav.",
        ascconv_key="sWipMemBlock.alFree[12]",
        choices=(("Off", 1), ("On", 2)),
        sequences=("space_mgh_epinav_ABCD",),
        evidence="controlled edit: NAV_optionscan_P1. Same index as Nav. location on the "
        "MPRAGE variant, and a different parameter -- the reason these are per sequence.",
    ),
    Mapping(
        label="Readout polarity",
        ascconv_key="sWipMemBlock.alFree[1]",
        choices=(("Positive", 1), ("Negative", 2)),
        sequences=("tfl_mgh_epinav_ABCD",),
        evidence="controlled edit: NAV_optionscan_P1",
    ),
    Mapping(
        label="Protocol filename",
        ascconv_key="sWipMemBlock.alFree[1]",
        choices=(("Generic", 1), ("MPRAGE", 2), ("T2-SPACE", 3)),
        absent_choice="Generic",
        sequences=("ep_moco_nav_set_ABCD",),
        evidence="controlled edit: NAV_optionscan_P1. alFree[1] again, and again a "
        "different parameter -- Readout polarity on the MPRAGE sequence. The "
        "setter in allcustomer_20260909 prints Generic with no alFree[1] at "
        "all, so absence is a second spelling of it -- corroborated by tFree, "
        "which names Prisma_epi_moco_navigator.prot there and tracks this "
        "choice on all 46 corpus setters that store a value.",
    ),
    # ---- The multi-echo MEMPRAGE. ----
    Mapping(
        label="Readout polarity",
        ascconv_key="sWipMemBlock.alFree[1]",
        choices=(("Positive", 1), ("Negative", 2)),
        sequences=("tfl_mgh_multiecho",),
        evidence="controlled edit: MEMPRAGE_optionscan_P1",
    ),
    Mapping(
        label="Gradient spoiling",
        ascconv_key="sWipMemBlock.alFree[5]",
        choices=(("Siemens", 1), ("Integral", 2)),
        sequences=("tfl_mgh_multiecho",),
        evidence="controlled edit: MEMPRAGE_optionscan_P1. Echo Spacing follows as a "
        "consequence and is not written here.",
    ),
    Mapping(
        label="Averaging",
        ascconv_key="sWipMemBlock.alFree[4]",
        choices=(("None", 1), ("Linear", 2), ("RMS", 3), ("RMS only", 4), ("Mean", 5)),
        sequences=("tfl_mgh_multiecho",),
        evidence="controlled edit: MEMPRAGE_optionscan_P1, all five states observed",
    ),
    # -- derived from the corpus rather than from a controlled edit ---------
    #
    # Each of these was found by asking which ASCCONV key induces the same
    # partition over the 483 scans where an archive sits beside its own
    # printout: a consistent bijection between stored and printed values,
    # which covers numbers, scaled numbers and enums at once and is vacuous
    # unless the label actually varies. A candidate was kept only where
    # exactly one key tracks the label *and* the key's own name echoes it,
    # so the correlation and the naming are two independent witnesses.
    #
    # They are weaker evidence than the controlled edits above, in one
    # specific way: an enum carries only the choices the corpus happened to
    # exercise, so `encode` refuses a value nobody has printed. That is the
    # same conservatism the option-scan enums already have.
    #
    # Two candidates were dropped after the fact, for a reason the rule as
    # first written could not see: it checked for two candidates claiming one
    # key, but not for a candidate claiming a key an *existing* mapping
    # already writes. `Save Original Images` collided with `MSMA`, which is
    # verified and keeps it; `TE 1` collided with `TE`, which is the same
    # parameter under the name a multi-echo scan prints for it -- a case
    # `resolve` already covers by falling back to the label Preview carries.
    #
    # Deliberately excluded: anything in sWipMemBlock, which is scratch
    # memory whose meaning is per sequence and must name its sequences; any
    # key two labels track equally well; and any key whose name does not
    # corroborate, since co-occurrence alone proposes nonsense.
    Mapping(
        label="Adj. Water Suppr.",
        ascconv_key="sAdjData.uiAdjWatSupMode",
        choices=(("Off", 1), ("On", 2)),
        evidence=(
            "corpus correlation: 'Adj. Water Suppr.' and sAdjData.uiAdjWatSupMode induce the same partition over 64 paired scans, and the key's own name echoes the label (adj). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Coil Combination",
        ascconv_key="ucCoilCombineMode",
        choices=(("Adaptive Combine", 2), ("Sum of Squares", 1)),
        evidence=(
            "corpus correlation: 'Coil Combination' and ucCoilCombineMode induce the same partition over 413 paired scans, and the key's own name echoes the label (coil). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Concatenations",
        ascconv_key="sSliceArray.lConc",
        evidence=(
            "corpus correlation: 'Concatenations' and sSliceArray.lConc induce the same partition over 246 paired scans, and the key's own name echoes the label (concatenations~conc). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Contrasts",
        ascconv_key="lContrasts",
        evidence=(
            "corpus correlation: 'Contrasts' and lContrasts induce the same partition over 294 paired scans, and the key's own name echoes the label (contrasts). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Delta Frequency",
        ascconv_key="sSpecPara.dDeltaFrequency",
        absent_choice="0.00 ppm",
        evidence=(
            "corpus correlation: 'Delta Frequency' and sSpecPara.dDeltaFrequency induce the same partition over 69 paired scans, and the key's own name echoes the label (delta, frequency). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Dimension",
        ascconv_key="sKSpace.ucDimension",
        choices=(("2D", 2), ("3D", 4)),
        evidence=(
            "corpus correlation: 'Dimension' and sKSpace.ucDimension induce the same partition over 395 paired scans, and the key's own name echoes the label (dimension). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Elliptical Filter",
        ascconv_key="sEllipticalFilter.ucOn",
        choices=(("On", 1),),
        absent_choice="Off",
        evidence=(
            "corpus correlation: 'Elliptical Filter' and sEllipticalFilter.ucOn induce the same partition over 406 paired scans, and the key's own name echoes the label (elliptical, filter). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Fat Saturation",
        ascconv_key="sPrepPulses.ucFatSatMode",
        choices=(("Strong", 2), ("Weak", 1)),
        evidence=(
            "corpus correlation: 'Fat Saturation' and sPrepPulses.ucFatSatMode induce the same partition over 32 paired scans, and the key's own name echoes the label (fat). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Flip Angle Mode",
        ascconv_key="ucFlipAngleMode",
        choices=(("Constant", 1), ("T1 Var", 4), ("T2 Var", 16)),
        evidence=(
            "corpus correlation: 'Flip Angle Mode' and ucFlipAngleMode induce the same partition over 49 paired scans, and the key's own name echoes the label (angle, flip). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Flow Compensation",
        ascconv_key="acFlowComp[0]",
        choices=(("None", 1), ("On", 2), ("Slice/Read", 16)),
        evidence=(
            "corpus correlation: 'Flow Compensation' and acFlowComp[0] induce the same partition over 245 paired scans, and the key's own name echoes the label (flow). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Free Echo Spacing",
        ascconv_key="sFastImaging.ucFreeEchoSpacing",
        choices=(("On", 1),),
        absent_choice="Off",
        evidence=(
            "corpus correlation: 'Free Echo Spacing' and sFastImaging.ucFreeEchoSpacing induce the same partition over 232 paired scans, and the key's own name echoes the label (echo, free, spacing). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Gradient Mode",
        ascconv_key="sGRADSPEC.ucMode",
        choices=(
            ("Fast", 1),
            ("Fast*", 17),
            ("Normal", 2),
            ("Performance", 8),
            ("Performance*", 24),
            ("Whisper", 4),
        ),
        evidence=(
            "corpus correlation: 'Gradient Mode' and sGRADSPEC.ucMode induce the same partition over 413 paired scans, and the key's own name echoes the label (gradient~gradspec). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Introduction",
        ascconv_key="ucEnableIntro",
        choices=(("On", 1),),
        absent_choice="Off",
        evidence=(
            "corpus correlation: 'Introduction' and ucEnableIntro induce the same partition over 411 paired scans, and the key's own name echoes the label (introduction~intro). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Magn. Preparation",
        ascconv_key="sPrepPulses.ucInversion",
        choices=(("Non-sel. IR", 2), ("None", 4), ("Slice-sel. IR", 1)),
        evidence=(
            "corpus correlation: 'Magn. Preparation' and sPrepPulses.ucInversion induce the same partition over 345 paired scans, and the key's own name echoes the label (preparation~prep). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Multi-Slice Mode",
        ascconv_key="sKSpace.ucMultiSliceMode",
        choices=(("Interleaved", 2), ("Sequential", 1), ("Single Shot", 4)),
        evidence=(
            "corpus correlation: 'Multi-Slice Mode' and sKSpace.ucMultiSliceMode induce the same partition over 366 paired scans, and the key's own name echoes the label (multi, slice). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Multiple Series",
        ascconv_key="ucOneSeriesForAllMeas",
        choices=(("Each Measurement", 4), ("Off", 1)),
        evidence=(
            "corpus correlation: 'Multiple Series' and ucOneSeriesForAllMeas induce the same partition over 243 paired scans, and the key's own name echoes the label (series). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Phase Cycling",
        ascconv_key="sSpecPara.lPhaseCyclingType",
        choices=(("Auto", 2), ("None", 1), ("Two Step", 4)),
        evidence=(
            "corpus correlation: 'Phase Cycling' and sSpecPara.lPhaseCyclingType induce the same partition over 57 paired scans, and the key's own name echoes the label (cycling, phase). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Phase Encoding",
        ascconv_key="sSpecPara.lPhaseEncodingType",
        choices=(("Full", 1), ("Weighted", 4)),
        evidence=(
            "corpus correlation: 'Phase Encoding' and sSpecPara.lPhaseEncodingType induce the same partition over 23 paired scans, and the key's own name echoes the label (encoding, phase). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Phase Resolution",
        ascconv_key="sKSpace.dPhaseResolution",
        scale=0.01,
        evidence=(
            "corpus correlation: 'Phase Resolution' and sKSpace.dPhaseResolution induce the same partition over 412 paired scans, and the key's own name echoes the label (phase, resolution). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Preparation Scans",
        ascconv_key="sSpecPara.lPreparingScans",
        absent_choice="0",
        evidence=(
            "corpus correlation: 'Preparation Scans' and sSpecPara.lPreparingScans induce the same partition over 59 paired scans, and the key's own name echoes the label (scans). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="RF Pulse Type",
        ascconv_key="sTXSPEC.ucRFPulseType",
        choices=(("Fast", 1), ("Low SAR", 4), ("Normal", 2)),
        evidence=(
            "corpus correlation: 'RF Pulse Type' and sTXSPEC.ucRFPulseType induce the same partition over 283 paired scans, and the key's own name echoes the label (pulse, rf). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Reconstruction",
        ascconv_key="ucReconstructionMode",
        choices=(("Magn./Phase", 8), ("Magnitude", 1)),
        evidence=(
            "corpus correlation: 'Reconstruction' and ucReconstructionMode induce the same partition over 412 paired scans, and the key's own name echoes the label (reconstruction). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Reduce Motion Sens.",
        ascconv_key="ucReduceMotionSens",
        choices=(("On", 1),),
        absent_choice="Off",
        evidence=(
            "corpus correlation: 'Reduce Motion Sens.' and ucReduceMotionSens induce the same partition over 13 paired scans, and the key's own name echoes the label (motion, reduce, sens). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Reference Scans",
        ascconv_key="sPat.ucRefScanMode",
        choices=(
            ("EPI/Separate", 256),
            ("GRE/Separate", 4),
            ("Integrated", 2),
            ("TSE/Separate", 512),
        ),
        evidence=(
            "corpus correlation: 'Reference Scans' and sPat.ucRefScanMode induce the same partition over 128 paired scans, and the key's own name echoes the label (scans~scan). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Remove Oversampling",
        ascconv_key="sSpecPara.ucRemoveOversampling",
        choices=(("Off", 0), ("On", 1)),
        evidence=(
            "corpus correlation: 'Remove Oversampling' and sSpecPara.ucRemoveOversampling induce the same partition over 59 paired scans, and the key's own name echoes the label (oversampling, remove). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Slice Oversampling",
        ascconv_key="sKSpace.dSliceOversamplingForDialog",
        scale=0.01,
        absent_choice="0.0 %",
        evidence=(
            "corpus correlation: 'Slice Oversampling' and sKSpace.dSliceOversamplingForDialog induce the same partition over 155 paired scans, and the key's own name echoes the label (oversampling, slice). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Slice Partial Fourier",
        ascconv_key="sKSpace.ucSlicePartialFourier",
        choices=(("6/8", 4), ("7/8", 8), ("Off", 16)),
        evidence=(
            "corpus correlation: 'Slice Partial Fourier' and sKSpace.ucSlicePartialFourier induce the same partition over 154 paired scans, and the key's own name echoes the label (fourier, partial, slice). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Slice Resolution",
        ascconv_key="sKSpace.dSliceResolution",
        scale=0.01,
        evidence=(
            "corpus correlation: 'Slice Resolution' and sKSpace.dSliceResolution induce the same partition over 154 paired scans, and the key's own name echoes the label (resolution, slice). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="TI",
        ascconv_key="alTI[0]",
        scale=1000.0,
        evidence=(
            "corpus correlation: 'TI' and alTI[0] induce the same partition over 71 paired scans, and the key's own name echoes the label (ti). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Vector Size",
        ascconv_key="sSpecPara.lVectorSize",
        evidence=(
            "corpus correlation: 'Vector Size' and sSpecPara.lVectorSize induce the same partition over 64 paired scans, and the key's own name echoes the label (size, vector). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Water s. BW",
        ascconv_key="sSpecPara.dSpecWaterSupprBandwidth",
        evidence=(
            "corpus correlation: 'Water s. BW' and sSpecPara.dSpecWaterSupprBandwidth induce the same partition over 29 paired scans, and the key's own name echoes the label (water). Derived, not from a controlled edit."
        ),
    ),
    Mapping(
        label="Wrap-up Magn.",
        ascconv_key="ulWrapUpMagn",
        choices=(("None", 1), ("Restore", 2)),
        evidence=(
            "corpus correlation: 'Wrap-up Magn.' and ulWrapUpMagn induce the same partition over 50 paired scans, and the key's own name echoes the label (magn, up, wrap). Derived, not from a controlled edit."
        ),
    ),
    # -- derived from the corpus, then confirmed by the protocols' owner ----
    #
    # Each of these is a key the correlation picked out alone but the harvest
    # declined, because the key's own name does not echo the printed label --
    # no lexical rule gets from "Acceleration Mode" to ucPATMode. They were
    # put to the owner as derivations to check rather than written as
    # settled, and he confirmed them, which is the order that makes an
    # inference safe to keep.
    Mapping(
        label="Acceleration Mode",
        ascconv_key="sPat.ucPATMode",
        choices=(("GRAPPA", 2), ("None", 1), ("SMS", 32)),
        evidence=(
            "corpus correlation over 373 paired scans, put to the protocols' owner as a derivation and confirmed by him. The key's own name does not echo the label, which is why the automatic harvest declined it: PAT is the parallel acquisition technique the card calls acceleration"
        ),
    ),
    Mapping(
        label="Allowed Delay",
        ascconv_key="lMeasPause",
        scale=1000000.0,
        absent_choice="0 s",
        evidence=(
            "corpus correlation over 85 paired scans, put to the protocols' owner as a derivation and confirmed by him. The key's own name does not echo the label, which is why the automatic harvest declined it: lMeasPause is the pause between measurements the card calls a delay"
        ),
    ),
    Mapping(
        label="Scan Res. A >> P",
        read_only=True,
        ascconv_key="sKSpace.lPhaseEncodingLines",
        evidence=(
            "corpus correlation over 23 paired scans, put to the protocols' owner as a derivation and confirmed by him. The key's own name does not echo the label, which is why the automatic harvest declined it: the phase-encoding direction's scan resolution is its line count"
        ),
    ),
    Mapping(
        label="Scan Res. F >> H",
        read_only=True,
        ascconv_key="sKSpace.lPartitions",
        evidence=(
            "corpus correlation over 8 paired scans, put to the protocols' owner as a derivation and confirmed by him. The key's own name does not echo the label, which is why the automatic harvest declined it: the slice direction's scan resolution is the partition count"
        ),
    ),
    Mapping(
        label="Interpol. Res. R >> L",
        read_only=True,
        ascconv_key="sSpecPara.lFinalMatrixSizeRead",
        evidence=(
            "corpus correlation over 23 paired scans, put to the protocols' owner as a derivation and confirmed by him. The key's own name does not echo the label, which is why the automatic harvest declined it: the interpolated readout matrix, on the scans that have one"
        ),
    ),
    Mapping(
        label="Interpol. Res. A >> P",
        read_only=True,
        ascconv_key="sSpecPara.lFinalMatrixSizePhase",
        evidence=(
            "corpus correlation over 23 paired scans, put to the protocols' owner as a derivation and confirmed by him. The key's own name does not echo the label, which is why the automatic harvest declined it: the interpolated phase matrix, on the scans that have one"
        ),
    ),
)


@dataclass(frozen=True)
class Applied:
    """One value that was written, in both of its locations.

    Attributes
    ----------
    step : str
        Name of the measurement step whose protocol was patched.
    label : str
        The printed label.
    preview_path : str
        Preview key that was written.
    ascconv_key : str
        ASCCONV assignment that was written.
    previous : Any
        The preview value before the edit.
    value : Any
        The preview value after it.
    ascconv_previous : str
        The ASCCONV literal before the edit.
    ascconv_value : str
        The ASCCONV literal after it.
    """

    step: str
    label: str
    preview_path: str
    ascconv_key: str
    previous: Any
    value: Any
    ascconv_previous: str
    ascconv_value: str


@dataclass(frozen=True)
class Skipped:
    """One value that was asked for and not written.

    Attributes
    ----------
    step : str
        Name of the measurement step, or the requested name when no step
        matched it.
    label : str
        The label or preview path as the caller gave it.
    value : Any
        The value the caller asked for.
    reason : str
        Why it was not written, in a form fit to show a user.
    """

    step: str
    label: str
    value: Any
    reason: str


@dataclass
class Manifest:
    """What a patch run wrote, what it refused, and what it left alone.

    A patcher that reports only its successes is unusable for this job: the
    interesting failure is the value that was silently not written, and the
    interesting risk is the value that stayed at whatever the template said.

    Attributes
    ----------
    applied : list of Applied
        Every value written.
    skipped : list of Skipped
        Every value asked for and refused, with a reason.
    inherited : int
        Preview entries across the touched protocols that no request named, so
        that still hold whatever the source archive said.
    stale : list of str
        Values the console would have recomputed and this module did not.
    approximate : list of str
        Values written exactly as asked where the console would instead pick
        the nearest value its hardware can realise. ``FOV Phase`` is the case:
        the console quantises it to an achievable ratio -- 29/30 of the read
        FOV where 96.7% was displayed -- and the card prints that rounded to a
        tenth of a percent, so a percentage cannot reconstruct the millimetres
        it came from. The value written is the one requested, and differs from
        the console's by less than the rounding of the printed figure.
    """

    applied: list[Applied] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    inherited: int = 0
    stale: list[str] = field(default_factory=list)
    approximate: list[str] = field(default_factory=list)

    #: Substring identifying a refusal to write a value the console derives.
    #: Matched on the reason rather than carried as a flag because a `Skipped`
    #: records what happened, and what happened here is a refusal like any
    #: other -- it is only the *verdict* on the run that differs.
    DERIVED_REASON = "a value the console derives"

    @property
    def derived(self) -> list["Skipped"]:
        """The refusals that were the right answer rather than a shortfall.

        A parameter the console computes from others is printed as a display
        of that computation, so there is nothing to write: the scan
        resolution in the phase direction is the line count the console
        worked out, and putting the printed number there would overwrite its
        arithmetic with a rounding of its own output.

        Returns
        -------
        list of Skipped
            The read-only refusals among the skips.
        """
        return [one for one in self.skipped if self.DERIVED_REASON in one.reason]

    @property
    def complete(self) -> bool:
        """Return whether every value that *could* be written was.

        Returns
        -------
        bool
            ``True`` when nothing was skipped except values the console
            derives, which no run could have written.
        """
        return not [one for one in self.skipped if self.DERIVED_REASON not in one.reason]

    def report(self) -> str:
        """Render the manifest as text for a user to read.

        Returns
        -------
        str
            One line per applied and skipped value, then the counts that say
            how much of the protocol was left untouched.
        """
        lines: list[str] = []
        for one in self.applied:
            lines.append(
                f"set   {one.step}: {one.label} {one.previous} -> {one.value} "
                f"({one.ascconv_key} {one.ascconv_previous} -> {one.ascconv_value})"
            )
        for miss in self.skipped:
            lines.append(f"skip  {miss.step}: {miss.label}={miss.value} -- {miss.reason}")
        lines.append(f"inherited {self.inherited} preview value(s) from the source archive")
        if self.approximate:
            lines.append(
                "written as asked, but the console quantises these: "
                + ", ".join(sorted(set(self.approximate)))
            )
        if self.stale:
            lines.append("not recomputed, the console would have: " + ", ".join(self.stale))
        return "\n".join(lines)


def ascconv_bounds(text: str) -> tuple[int, int]:
    """Locate the ASCCONV block within an XProtocol document.

    Parameters
    ----------
    text : str
        The XProtocol text.

    Returns
    -------
    tuple of int
        Start and end offsets of the block, or ``(-1, -1)`` when the document
        has none.
    """
    start = text.find(ASCCONV_BEGIN)
    end = text.find(ASCCONV_END)
    if start < 0 or end < 0 or end < start:
        return (-1, -1)
    return (start, end)


def read_ascconv(text: str, key: str) -> str | None:
    """Return the literal an ASCCONV assignment holds, without interpreting it.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The assignment name, for example ``alTR[0]``.

    Returns
    -------
    str or None
        The literal as written, or ``None`` when the block has no such
        assignment.
    """
    start, end = ascconv_bounds(text)
    if start < 0:
        return None
    found = _assignment(key).search(text, start, end)
    return found.group("value") if found else None


def write_ascconv(text: str, key: str, literal: str) -> str:
    """Replace one ASCCONV assignment's literal, preserving its layout.

    The separator is captured and put back rather than normalized. Numaris/X
    writes ``key\\t = \\tvalue`` but flips to ``key  =  value`` between saves,
    so imposing either spelling would add churn that has nothing to do with
    the edit and make a later diff harder to read.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The assignment name.
    literal : str
        The replacement literal, already formatted.

    Returns
    -------
    str
        The text with that one assignment rewritten. Returned unchanged when
        the assignment is absent.
    """
    start, end = ascconv_bounds(text)
    if start < 0:
        return text
    found = _assignment(key).search(text, start, end)
    if not found:
        return text
    return text[: found.start("value")] + literal + text[found.end("value") :]


def omits_zero(key: str) -> bool:
    """Return whether an assignment is left out entirely when it holds zero.

    ``sWipMemBlock`` is written sparsely: its arrays list only the indices that
    carry a value, in ascending order, and the console adds and removes lines
    as options are set and cleared. A CMRR protocol with every Special-card box
    unticked has no ``alFree[0]`` at all, and setting ``Remeasure`` to zero
    deleted its line rather than writing ``0``. Both were observed directly in
    the option-scan exports.

    Parameters
    ----------
    key : str
        A concrete ASCCONV key.

    Returns
    -------
    bool
        ``True`` for a sparse array element.
    """
    return bool(re.match(r"sWipMemBlock\.(al|ad)Free\[\d+\]$", key)) or key in SPARSE_KEYS


def remove_ascconv(text: str, key: str) -> str:
    """Delete one ASCCONV assignment, line and all.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The assignment to remove.

    Returns
    -------
    str
        The text without that line, unchanged when it was already absent.
    """
    start, end = ascconv_bounds(text)
    if start < 0:
        return text
    line = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*?\r?\n", re.M)
    found = line.search(text, start, end)
    return text[: found.start()] + text[found.end() :] if found else text


def insert_ascconv(text: str, key: str, literal: str) -> str:
    """Add an assignment that the document does not yet carry.

    Sparse arrays are written in ascending index order, so a new element goes
    among its siblings rather than at the end of the block: the console emits
    ``alFree[1]``, ``alFree[4]``, ``alFree[6]`` and so on, and appending would
    break that order.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The assignment to add, for example ``sWipMemBlock.alFree[0]``.
    literal : str
        The value to write.

    Returns
    -------
    str
        The text with the assignment inserted, unchanged when there is no
        sibling to place it beside.
    """
    start, end = ascconv_bounds(text)
    if start < 0:
        return text
    match = re.fullmatch(r"(.*)\[(\d+)\]", key)
    if match is None:
        return _insert_scalar(text, key, literal, start, end)
    stem, index = match.group(1), int(match.group(2))
    sibling = re.compile(rf"^([ \t]*){re.escape(stem)}\[(\d+)\]([ \t]*=[ \t]*).*?\r?\n", re.M)
    found = [m for m in sibling.finditer(text, start, end)]
    if not found:
        return text
    after = next((m for m in found if int(m.group(2)) > index), None)
    at = after.start() if after is not None else found[-1].end()
    model = after if after is not None else found[-1]
    ending = "\r\n" if model.group(0).endswith("\r\n") else "\n"
    line = f"{model.group(1)}{key}{model.group(3)}{literal}{ending}"
    return text[:at] + line + text[at:]


def _insert_scalar(text: str, key: str, literal: str, start: int, end: int) -> str:
    """Place a non-array assignment where the console writes it.

    A sparse scalar has no sibling index to sort against, and ASCCONV is
    written in the schema's order rather than alphabetically, so the position
    is not derivable from the name: ``ulWrapUpMagn`` precedes
    ``ucReconstructionPrio`` precedes ``lAverages``. The anchor is read off
    the console's own output instead -- see :data:`SPARSE_ANCHORS` -- and a
    round-trip test removes each key from every protocol that carries it and
    requires re-inserting to reproduce the file byte for byte.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The assignment to add.
    literal : str
        The value to write.
    start : int
        Offset of the ASCCONV block's start.
    end : int
        Offset of its end.

    Returns
    -------
    str
        The text with the assignment inserted, unchanged when no anchor for
        the key is known or none of its anchors is present. Callers must
        treat "unchanged" as a refusal rather than a write -- silently
        writing nothing is the failure this shape invites.
    """
    for anchor in SPARSE_ANCHORS.get(key, ()):
        found = _assignment(anchor).search(text, start, end)
        if found is None:
            continue
        line = text[found.start() : found.end()]
        indent = re.match(r"[ \t]*", line).group(0)
        separator = re.search(r"[ \t]*=[ \t]*", line).group(0)
        at = text.index("\n", found.end()) + 1
        ending = "\r\n" if text[at - 2 : at] == "\r\n" else "\n"
        return text[:at] + f"{indent}{key}{separator}{literal}{ending}" + text[at:]
    return text


def _assignment(key: str) -> re.Pattern[str]:
    """Build the pattern matching one ASCCONV assignment.

    Parameters
    ----------
    key : str
        The assignment name. Escaped, since these contain ``[``, ``]`` and
        ``.`` and would otherwise be read as a pattern.

    Returns
    -------
    re.Pattern
        A pattern with a ``value`` group covering the literal.
    """
    return re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(?P<value>.*?)[ \t]*$", re.M)


def format_like(value: float, existing: str) -> str:
    """Format a number the way the literal beside it is written.

    ASCCONV distinguishes ``650000`` from ``650000.0`` and the two are not
    interchangeable to every reader of the file, so the existing literal
    decides which is written back.

    Doubles are written to twelve significant figures, which is what the
    console writes and what reproduces all 919 distinct float literals in the
    reference archives. Python's ``repr`` reproduces them too, but it spells a
    freshly computed value with its full binary tail -- ``201.26200000000003``
    where the console would write ``201.262`` -- so it is the wrong choice for
    the one job this function exists to do.

    Parameters
    ----------
    value : float
        The number to write.
    existing : str
        The literal currently in place.

    Returns
    -------
    str
        The formatted literal.
    """
    literal = existing.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]+", literal):
        # The console writes the flag-like fields as hex, and a reader that
        # accepts 1 for 0x1 is not something to rely on when the spelling is
        # right there to copy.
        return f"0x{int(round(value)):x}"
    if re.fullmatch(r"[-+]?\d+", literal):
        return str(int(round(value)))
    written = f"{float(value):.12g}"
    return written if ("." in written or "e" in written or "E" in written) else written + ".0"


def sequence_of(protocol: Protocol) -> str:
    """Return the sequence a protocol runs, as ``seq_subpath`` spells it.

    Parameters
    ----------
    protocol : Protocol
        The protocol to inspect.

    Returns
    -------
    str
        For example ``cmrr_mbep2d_bold``, or an empty string when the preview
        does not carry one.
    """
    entry = protocol.preview.get(SEQUENCE_PATH)
    return str(entry.value) if entry is not None and entry.value is not None else ""


#: ASCCONV assignments the console rewrites on every save, whatever the
#: protocol says. Re-saving an unmodified protocol regenerates the GUID
#: leading ``sWipMemBlock.tFree`` and every
#: ``sCoilSelectMeas.aRxCoilSelectData[N].tCheckUUID``, and updates
#: ``sSpecPara.lFinalMatrixSizePhase``/``...Read``, which despite their names
#: hold a date and a time. Two protocols differing only here are the same
#: protocol saved twice.
#:
#: ``tFree`` is on the list because of its GUID, not its tail: the rest of it
#: is the sequence build stamp, which is the only record of which binary
#: wrote a protocol. A reader wanting that asks :func:`sequence_stamp`.
#: Fields the console rewrites on every save, whatever the protocol says.
#: Re-saving an unmodified protocol regenerates the GUID leading
#: ``sWipMemBlock.tFree`` and every
#: ``sCoilSelectMeas.aRxCoilSelectData[N].tCheckUUID``.
#:
#: ``tFree`` is here for its GUID, not its tail: the rest of it is the
#: sequence build stamp, the only record of which binary wrote a protocol.
#: A reader wanting that asks :func:`sequence_stamp`.
CHURN_KEYS = re.compile(r"tCheckUUID|sWipMemBlock\.tFree$")

#: ``sSpecPara.lFinalMatrixSize{Read,Phase}`` is churn only *sometimes*, and
#: the name is honest when it is not. On a scan that prints an interpolation
#: resolution it holds exactly that -- 8, 16, 128 or 256 on all 23 such scans
#: in the corpus, matching the printed value -- and on the other 156 it holds
#: a clock reading in ``Read`` (91202..181127) and a date in ``Phase``
#: (20260528..20260904). The two populations do not overlap by three orders
#: of magnitude, so the value decides.
#:
#: This bound is deliberately generous. Reading a real matrix size as a save
#: stamp would *hide* a printed parameter; reading a stamp as a matrix size
#: only shows a difference that is real in the bytes. The second is the safe
#: way to be wrong, so the bound sits far above any plausible matrix size.
STAMP_KEYS = re.compile(r"sSpecPara\.lFinalMatrixSize")
#: Above this, a ``lFinalMatrixSize`` reading is a date or a time.
STAMP_FLOOR = 4096


def is_churn(key: str, value: str | None = None) -> bool:
    """Whether a difference in this field says only that the protocol was saved.

    Parameters
    ----------
    key : str
        A dotted ASCCONV key.
    value : str or None, optional
        What the field holds, where the caller has it. One key family is
        churn only for some values, and without the value it is reported as
        churn -- which is what the key alone can support, and is how this
        read before the exception was found.

    Returns
    -------
    bool
        ``True`` when a difference there says the protocol was saved again
        rather than that anything about it changed.
    """
    if CHURN_KEYS.search(key):
        return True
    if not STAMP_KEYS.search(key):
        return False
    if value is None:
        return True
    try:
        return abs(float(value)) > STAMP_FLOOR
    except (TypeError, ValueError):
        return True


#: The printed card each mapped parameter appears on, derived from every
#: shipped example rather than written by hand: for each label, the sections
#: the corpus actually prints it under, keeping any card accounting for a
#: tenth or more of its printings.
#:
#: A label may belong to several, and that is the printout rather than an
#: ambiguity -- a scan prints ``TR`` on Routine, Contrast, Geometry and
#: Physio, exactly as it prints ``Position`` up to four times. All of them
#: are kept, so the parameter is found wherever a person goes looking for it,
#: and the flattened view folds the repeats back into one reading.
#:
#: A bare group is dropped where a ``Group - Page`` for it is also kept:
#: ``System`` beside ``System - Adjustments`` is the same card on a release
#: that does not subdivide, named less precisely.
CARDS: dict[str, tuple[str, ...]] = {
    "ABCD navigator": ("Sequence - Special",),
    "Acceleration Factor PE": ("Resolution - Acceleration",),
    "Acceleration Mode": ("Resolution - Acceleration",),
    "Add. grad time": ("Sequence - Special",),
    "Adj. Water Suppr.": ("System - Adjustments",),
    "Adjust with Body Coil": ("System - Adjustments",),
    "Adjustment Tolerance": ("System - Adjustments",),
    "Allowed Delay": ("Sequence - Assistant",),
    "Apply freq to": ("Sequence - Special",),
    "Apply moco to": ("Sequence - Special",),
    "Assume Silicone": ("System - Adjustments",),
    "AutoAlign": ("Geometry - AutoAlign", "Routine"),
    "Averaging": ("Sequence - Special",),
    "B0 Shim": ("System - Adjustments",),
    "B1 Shim": ("System - Adjustments", "System - pTx"),
    "Base Resolution": ("Resolution - Common",),
    "Coil Combination": ("System - Miscellaneous",),
    "Coil Focus": ("System - Miscellaneous",),
    "Concatenations": ("Routine", "Geometry - Common", "Physio - Signal", "Physio - PACE"),
    "Confirm Frequency": ("System - Adjustments",),
    "Contrasts": ("Contrast - Common", "Sequence - Part 1"),
    "Coronal": ("System - Miscellaneous",),
    "Delta Frequency": ("Sequence - Common",),
    "Dimension": ("Sequence - Part 1",),
    "Disable B1 control loop": ("Sequence - Special",),
    "Disable freq. update": ("Sequence - Special",),
    "Distance Factor": ("Routine", "Geometry - Common"),
    "Distortion Correction": ("Resolution - Filter",),
    "Elliptical Filter": ("Resolution - Filter",),
    "FOV Phase": ("Routine", "Resolution - Common", "Geometry - Common", "Physio - Cardiac"),
    "FOV Read": ("Routine", "Resolution - Common", "Geometry - Common", "Physio - Cardiac"),
    "Fat Saturation": ("Contrast - Common",),
    "Fat-Water Contrast": ("Contrast - Common", "Physio - Cardiac"),
    "Feedback Delay": ("Sequence - Special",),
    "Flip Angle": ("Contrast - Common",),
    "Flip Angle Mode": ("Contrast - Common",),
    "Flow Compensation": ("Sequence - Part 1",),
    "Force GPA balance": ("Sequence - Special",),
    "Force equal slice timing": ("Sequence - Special",),
    "Free Echo Spacing": ("Sequence - Part 1",),
    "Gradient Mode": ("Sequence - Part 1",),
    "Gradient spoiling": ("Sequence - Special",),
    "Image Scaling": ("System - Tx/Rx",),
    "Include Nav.": ("Sequence - Special",),
    "Initial Rotation": ("Geometry - AutoAlign",),
    "Interpol. Res. A >> P": ("Resolution - Common",),
    "Interpol. Res. R >> L": ("Resolution - Common",),
    "Introduction": ("Sequence - Part 2", "Sequence - Part 1"),
    "Invert RO/PE polarity": ("Sequence - Special",),
    "K-space streaming": ("Sequence - Special",),
    "MB LeakBlock kernel": ("Sequence - Special",),
    "MB RF phase scramble": ("Sequence - Special",),
    "MB dual kernel": ("Sequence - Special",),
    "MSMA": ("System - Miscellaneous",),
    "MT Flip Angle": ("Sequence - Special",),
    "MT Offset": ("Sequence - Special",),
    "MTC": ("Contrast - Common",),
    "Magn. Preparation": ("Contrast - Common", "Physio - Cardiac"),
    "Matrix Optimization": ("System - Miscellaneous",),
    "Measurements": ("Contrast - Dynamic", "BOLD", "Inline - Subtraction"),
    "Moco ref. image": ("Sequence - Special",),
    "Multi-Slice Mode": ("Geometry - Common",),
    "Multiple Series": ("Contrast - Dynamic",),
    "Nav. location": ("Sequence - Special",),
    "Normalize": ("Resolution - Filter",),
    "Opt. MB RF pulse BW": ("Sequence - Special",),
    "PF omits higher k-space": ("Sequence - Special",),
    "Phase Cycling": ("Sequence - Common",),
    "Phase Encoding": ("Resolution - Common",),
    "Phase Partial Fourier": ("Resolution - Acceleration",),
    "Phase Resolution": ("Resolution - Common", "Physio - Cardiac"),
    "Preparation Scans": ("Contrast - Common", "Sequence - Common"),
    "Prio Recon": ("Properties",),
    "Protocol filename": ("Sequence - Special",),
    "RF Pulse Type": ("Sequence - Part 1",),
    "Reacq. threshold": ("Sequence - Special",),
    "Readout polarity": ("Sequence - Special",),
    "Reconstruction": ("Contrast - Common", "Contrast - Dynamic"),
    "Reduce Motion Sens.": ("Sequence - Part 2", "Sequence - Part 1"),
    "Reference Lines PE": ("Resolution - Acceleration",),
    "Reference Scans": ("Resolution - Acceleration",),
    "Reference scan mode": ("Resolution - iPAT", "Resolution - Acceleration"),
    "Remeasure": ("Sequence - Special",),
    "Remove Oversampling": ("Sequence - Common",),
    "SENSE1 coil combine": ("Sequence - Special",),
    "Sagittal": ("System - Miscellaneous",),
    "Scan Res. A >> P": ("Resolution - Common",),
    "Scan Res. F >> H": ("Resolution - Common",),
    "Series": ("Geometry - Common",),
    "Single-band images": ("Sequence - Special",),
    "Slice Oversampling": ("Routine", "Geometry - Common"),
    "Slice Partial Fourier": ("Resolution - Acceleration",),
    "Slice Resolution": ("Resolution - Common",),
    "Slice Thickness": ("Routine", "Resolution - Common", "Geometry - Common"),
    "Slices per Slab": ("Routine", "Geometry - Common"),
    "Static Field Correction": ("Resolution - Filter",),
    "Suppress 16-bit DICOM": ("Sequence - Special",),
    "TE": ("Routine", "Contrast - Common"),
    "TE 2": ("Routine", "Contrast - Common"),
    "TE 3": ("Routine", "Contrast - Common"),
    "TE 4": ("Routine", "Contrast - Common"),
    "TI": ("Contrast - Common", "Physio - Cardiac"),
    "TR": ("Routine", "Contrast - Common", "Geometry - Common", "Physio - Signal"),
    "Table Position": ("Geometry - Tim Planning Suite",),
    "Time-shifted MB RF": ("Sequence - Special",),
    "Transversal": ("System - Miscellaneous",),
    "Vector Size": ("Resolution - Common",),
    "Wait for User to Start": ("Properties",),
    "Water s. BW": ("Contrast - Common",),
    "Wrap-up Magn.": ("Contrast - Common",),
}


def cards_for(label: str) -> tuple[str, ...]:
    """The printed cards a mapped parameter appears on.

    Parameters
    ----------
    label : str
        A printed parameter label.

    Returns
    -------
    tuple of str
        The section titles, most-printed first. Empty for a label the corpus
        never prints, which no mapping currently has.
    """
    return CARDS.get(label, ())


def sequence_stamp(protocol: Protocol) -> str:
    """Return whatever the sequence wrote into ``sWipMemBlock.tFree``.

    That field is sequence-private free text, so what it means depends
    entirely on the binary that wrote it. CMRR's multiband sequences put a
    build stamp there behind a GUID that is regenerated on every save::

        <guid>||Sequence: R017 nxva60a/main r/91b106c1e; May 15 2026 12:56:25 by eja

    The ABCD navigator sequences write a protocol file name instead, with no
    GUID, and ``tfl_mgh_multiecho`` does not write the field at all. The
    leading GUID is dropped here because it carries no information and differs
    between two exports of one protocol; everything after it is stable across
    saves, edits and scanners.

    This matters beyond curiosity: the Special card's layout can change between
    sequence builds, so a mapping verified against one build is not
    automatically true of another, and this is the only thing in the protocol
    that says which build wrote it. Note what it does *not* pin down -- the
    string says ``R017`` whether the binary was 017pre15 or a later 017, so the
    commit and build time are the parts that identify a build exactly.

    Parameters
    ----------
    protocol : Protocol
        The protocol to inspect.

    Returns
    -------
    str
        The stamp with any leading GUID removed, or an empty string when the
        sequence writes nothing there.
    """
    raw = read_ascconv(protocol.xprotocol, "sWipMemBlock.tFree")
    if not raw:
        return ""
    text = raw.strip().strip('"')
    _guid, sep, tail = text.partition("||")
    return (tail if sep else text).strip()


def build_id(stamp: str) -> str:
    """Reduce a build stamp to the part identifying the binary.

    CMRR writes ``Sequence: R017 nxva60a/main r/91b106c1e; May 15 2026
    12:56:25 by eja``. The three sequences of one release are compiled minutes
    apart, so the timestamp distinguishes ``cmrr_mbep2d_bold`` from
    ``cmrr_mbep2d_se`` rather than one release from another -- comparing whole
    stamps would make every mapping sequence-specific by accident.

    Parameters
    ----------
    stamp : str
        A stamp as :func:`sequence_stamp` returns it.

    Returns
    -------
    str
        Everything before the first ``;``, stripped. A stamp with no ``;`` --
        the navigators' ``.prot`` file name -- comes back whole, which cannot
        match a build and so refuses rather than matching loosely.
    """
    return stamp.partition(";")[0].strip()


def applies_to(mapping: Mapping, protocol: Protocol) -> bool:
    """Return whether a mapping is meaningful for this protocol.

    Parameters
    ----------
    mapping : Mapping
        The mapping to test.
    protocol : Protocol
        The protocol it would be written into.

    Returns
    -------
    bool
        ``True`` for an unrestricted mapping, or one naming this sequence and
        the build it was derived from.
    """
    if mapping.sequences and sequence_of(protocol) not in mapping.sequences:
        return False
    if mapping.builds and build_id(sequence_stamp(protocol)) not in mapping.builds:
        return False
    if mapping.when is not None:
        key, expected = mapping.when
        return read_ascconv(protocol.xprotocol, key) == expected
    return True


def _stored_int(literal: str) -> int | None:
    """Read a stored assignment as an integer, whatever base it is written in.

    The console writes a flag as ``0x1`` and a small enum as ``2``, and the
    same field can arrive as ``1.0`` from something that round-tripped
    through a float.

    Parameters
    ----------
    literal : str
        The assignment's right-hand side.

    Returns
    -------
    int or None
        The value, or ``None`` when it is not a number.
    """
    text = literal.strip()
    try:
        return int(text, 0) if text.lower().startswith(("0x", "-0x")) else int(float(text))
    except (TypeError, ValueError):
        return None


def _first_element(mapping: Mapping, protocol: Protocol) -> str | None:
    """Read the first element of an assignment replicated across an array.

    ``FOV Read`` and ``Slice Thickness`` are stored on every
    ``sSliceArray.asSlice[]`` element and hold the same value on each, so the
    first is the displayed one. Reading it is not a shortcut: the whole point
    of the replication is that the elements agree.

    Parameters
    ----------
    mapping : Mapping
        A mapping whose key is an array pattern.
    protocol : Protocol
        The protocol to read from.

    Returns
    -------
    str or None
        The first element's literal, or ``None`` when the array is empty.
    """
    for key, _index in expand(mapping.ascconv_key, protocol.xprotocol):
        literal = read_ascconv(protocol.xprotocol, key)
        if literal is not None:
            return literal
    return None


def display(mapping: Mapping, protocol: Protocol) -> str | None:
    """What a protocol stores for a mapped parameter, in the form a card shows.

    The read direction of :data:`MAPPINGS`, which was built for writing. An
    archive carries no cards, so a comparison of two archives can only speak
    in printed terms by decoding: ``sAdjData.uiAdjWithBC = 0x1`` is
    ``Adjust with Body Coil: On`` on the System card, and that is the form
    someone changing a protocol on the console needs.

    Declines rather than guesses. A derived value (``basis``) and a signed
    coordinate (``sign_from``) are left to the raw parameter section, as is
    any mapping whose gates say it does not apply to this scan -- a flag bit
    belonging to another sequence, or to a build this one was not derived
    from.

    Parameters
    ----------
    mapping : Mapping
        The parameter to read.
    protocol : Protocol
        The protocol to read it from.

    Returns
    -------
    str or None
        The displayed value, or ``None`` when this mapping cannot be decoded
        for this protocol.
    """
    if not applies_to(mapping, protocol):
        return None
    if mapping.basis is not None or mapping.sign_from is not None:
        return None

    if "[*]" in mapping.ascconv_key:
        literal = _first_element(mapping, protocol)
    else:
        literal = read_ascconv(protocol.xprotocol, mapping.ascconv_key)

    # A field the console is currently using as a save stamp is not holding
    # this parameter, whatever its name says. One key family is a real
    # interpolation matrix on the scans that have one and a date or a time on
    # the rest, and the value is what tells them apart -- the same test that
    # decides whether a difference there is churn.
    if literal is not None and is_churn(mapping.ascconv_key, literal):
        return None

    if mapping.bit is not None:
        # A word holding zero is not written at all, so an absent assignment
        # is every flag off rather than an unknown.
        word = 0 if literal is None else (_stored_int(literal) or 0)
        return "On" if word >> mapping.bit & 1 else "Off"

    # An omitted assignment is not an unknown: this format does not write a
    # field holding zero, so where a mapping has been observed in that state
    # the absence *is* the reading. Applies to a scaled number as much as to
    # a choice -- Slice Oversampling omits the field for "0.0 %".
    if literal is None and mapping.absent_choice is not None:
        return mapping.absent_choice

    if mapping.choices:
        if literal is None:
            return None
        stored = _stored_int(literal)
        for text, number in mapping.choices:
            if stored == number:
                return text
        return None

    if literal is None:
        return None
    try:
        number = float(literal) / mapping.scale - mapping.offset
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f"{number:g}"


def expand(pattern: str, text: str) -> list[tuple[str, int | None]]:
    """Resolve an ``[*]`` target against the indices a document defines.

    The slice arrays are sized per protocol -- three elements on a localizer,
    sixty-four on a multi-slice EPI -- so the indices are read from the
    document rather than assumed. A pattern that matches nothing yields an
    empty list, which the caller reports rather than silently skipping.

    Parameters
    ----------
    pattern : str
        An ASCCONV key, possibly containing ``[*]``.
    text : str
        The XProtocol text to search.

    Returns
    -------
    list of tuple
        ``(concrete key, index)`` pairs in index order. The index is ``None``
        for a pattern with no ``[*]``.
    """
    if "[*]" not in pattern:
        return [(pattern, None)]
    start, end = ascconv_bounds(text)
    if start < 0:
        return []
    prefix, suffix = pattern.split("[*]", 1)
    found = re.compile(rf"^[ \t]*{re.escape(prefix)}\[(\d+)\]{re.escape(suffix)}[ \t]*=", re.M)
    indices = sorted({int(m.group(1)) for m in found.finditer(text, start, end)})
    if not indices and suffix.startswith("."):
        # The field is absent from every element, which is not the same as the
        # array being absent: a sparse member such as
        # sGroupArray.asGroup[0].dDistFact is simply left out while the group
        # it belongs to is right there. Ask which *elements* exist instead, or
        # a sparse field can never be created -- it resolves to nothing and
        # the write is refused for a reason that is not true.
        element = re.compile(rf"^[ \t]*{re.escape(prefix)}\[(\d+)\]\.", re.M)
        indices = sorted({int(m.group(1)) for m in element.finditer(text, start, end)})
    return [(f"{prefix}[{i}]{suffix}", i) for i in indices]


def resolve(protocol: Protocol, name: str) -> tuple[Mapping | None, str]:
    """Turn a caller's label into the mapping that writes it.

    Parameters
    ----------
    protocol : Protocol
        The protocol the name is being resolved against, which decides which
        sequence-specific mappings are in scope.
    name : str
        A printed label such as ``TR``, or a preview path.

    Returns
    -------
    tuple
        The mapping and an empty reason, or ``None`` and the reason it could
        not be resolved.
    """
    wanted = name.strip().casefold()
    # Read-only mappings are out of scope here by construction: this is the
    # lookup the writer uses, and a derived parameter is one the console
    # recomputes from its inputs. `display` consults MAPPINGS directly and
    # so still decodes them.
    in_scope = [m for m in MAPPINGS if not m.read_only and applies_to(m, protocol)]
    hits = [m for m in in_scope if m.label.strip().casefold() == wanted]
    if not hits:
        hits = [m for m in in_scope if m.preview_path == name]
    if len(hits) == 1:
        return (hits[0], "")
    if not hits:
        # The card does not always print a parameter under the name a mapping
        # carries: a multi-echo scan prints "TE 1" where a single-echo one
        # prints "TE", and Preview labels it the same way. Resolving through
        # the preview entry follows the printout rather than duplicating every
        # spelling in the table.
        paths = {
            entry.path
            for entry in protocol.preview.values()
            if entry.label.strip().casefold() == wanted
        }
        hits = [m for m in in_scope if m.preview_path in paths]
    if len(hits) == 1:
        return (hits[0], "")
    if len(hits) > 1:
        keys = ", ".join(sorted(m.ascconv_key for m in hits))
        return (None, f"label {name!r} maps to several parameters: {keys}")
    derived = [m for m in MAPPINGS if m.read_only and m.label.strip().casefold() == wanted]
    if derived:
        # Named for what it is, rather than falling through to the build-gate
        # reason below, which would blame a sequence build for a parameter
        # that no build would let anyone write.
        return (
            None,
            f"{name!r} is a value the console derives from other parameters, so it is "
            "read from a protocol and never written to one",
        )
    elsewhere = [m for m in MAPPINGS if m.label.strip().casefold() == wanted]
    if elsewhere:
        runs = sequence_of(protocol) or "an unnamed sequence"
        # Distinguish the two ways a mapping can be out of scope. Reporting a
        # build mismatch in terms of the sequence produces "mapped for
        # cmrr_mbep2d_bold, but this protocol runs cmrr_mbep2d_bold", which is
        # true, useless, and reads like a bug in the table.
        right_sequence = [m for m in elsewhere if not m.sequences or runs in m.sequences]
        if right_sequence:
            seen = build_id(sequence_stamp(protocol)) or "no build stamp"
            wants = ", ".join(sorted({b for m in right_sequence for b in m.builds}))
            return (
                None,
                f"{name!r} is mapped for {runs} built as {wants}, and this protocol "
                f"reports {seen}; a later build may pack that option differently, so "
                f"the mapping is not applied rather than guessed",
            )
        wants = ", ".join(sorted({q for m in elsewhere for q in m.sequences}))
        return (None, f"{name!r} is mapped for {wants}, but this protocol runs {runs}")
    printed = {e.label.strip().casefold() for e in protocol.preview.values()}
    if wanted in printed:
        return (None, f"{name!r} is printed by this protocol but no verified mapping writes it")
    return (None, f"no verified mapping for {name!r}")


def displays_when_absent(mapping: Mapping, number: float) -> bool:
    """Return whether an absent assignment already displays this choice.

    A ``sWipMemBlock`` array omits an element nobody has set, and the console
    then shows the sequence's own default. Writing the number that default
    corresponds to would change the bytes without changing what the card
    reads, so a protocol already in that state is left alone.

    Parameters
    ----------
    mapping : Mapping
        The parameter being written.
    number : float
        The stored value :func:`encode` produced for the caller's choice.

    Returns
    -------
    bool
        ``True`` when this mapping names a choice shown for an absent
        assignment and ``number`` is that choice.
    """
    if mapping.absent_choice is None:
        return False
    table = {text.strip().casefold(): stored for text, stored in mapping.choices}
    default = table.get(mapping.absent_choice.strip().casefold())
    return default is not None and float(default) == number


def encode(mapping: Mapping, value: Any) -> tuple[float | None, str]:
    """Turn a caller's value into the number to store.

    Parameters
    ----------
    mapping : Mapping
        The parameter being written.
    value : Any
        What the caller asked for: a number, or the text the card displays,
        or a boolean for a flag.

    Returns
    -------
    tuple
        The numeric value and an empty reason, or ``None`` and the reason it
        could not be encoded.
    """
    if mapping.bit is not None:
        truth = _as_bool(value)
        if truth is None:
            return (None, f"{mapping.label} is a checkbox; expected on/off, got {value!r}")
        return (float(truth), "")
    if mapping.choices:
        table = {text.strip().casefold(): number for text, number in mapping.choices}
        if isinstance(value, str):
            found = table.get(value.strip().casefold())
            if found is None:
                offered = ", ".join(text for text, _ in mapping.choices)
                return (None, f"{value!r} is not a {mapping.label}; expected one of: {offered}")
            return (float(found), "")
        if value in {number for _text, number in mapping.choices}:
            return (float(value), "")
        offered = ", ".join(text for text, _ in mapping.choices)
        return (None, f"{value!r} is not a {mapping.label}; expected one of: {offered}")
    try:
        return (float(value), "")
    except (TypeError, ValueError):
        return (None, f"{mapping.label} expects a number, got {value!r}")


def _as_bool(value: Any) -> bool | None:
    """Read a checkbox value written as a bool, a number or the printed word.

    Parameters
    ----------
    value : Any
        The caller's value.

    Returns
    -------
    bool or None
        The interpreted state, or ``None`` when it is not a checkbox value.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"on", "true", "yes", "1"}:
            return True
        if text in {"off", "false", "no", "0"}:
            return False
    return None


def set_bit(word: str | None, bit: int, on: bool) -> str:
    """Set or clear one flag in a packed word.

    An absent assignment counts as zero: the console omits a flags word that
    holds no bits, so a protocol with every checkbox clear simply has no
    ``alFree[0]`` line at all.

    Parameters
    ----------
    word : str or None
        The current literal, or ``None`` when the assignment is absent.
    bit : int
        Bit position to write.
    on : bool
        The new state.

    Returns
    -------
    str
        The resulting literal.
    """
    current = int(word) if word not in (None, "") else 0
    return str(current | (1 << bit) if on else current & ~(1 << bit))


def patch_document(
    protocol: Protocol, requests: MappingType[str, Any], step: str = ""
) -> tuple[dict[str, Any], list[Applied], list[Skipped]]:
    """Apply requested values to one protocol's document.

    The document is copied shallowly and its two mutable parts replaced, so
    the caller's ``protocol`` is left as it was and nothing is written until
    :func:`apply` re-addresses the content.

    Parameters
    ----------
    protocol : Protocol
        The protocol to patch.
    requests : mapping
        Printed label or preview path to new displayed value.
    step : str, optional
        Name of the step holding this protocol, used only for the records.

    Returns
    -------
    tuple
        The new document, the values applied, and the values skipped.
    """
    document = dict(protocol.document)
    preview = {
        k: dict(v) if isinstance(v, dict) else v for k, v in document.get("Preview", {}).items()
    }
    text = document.get("Data", "")
    applied: list[Applied] = []
    skipped: list[Skipped] = []
    for name, value in requests.items():
        found, reason = resolve(protocol, name)
        if found is None:
            skipped.append(Skipped(step=step, label=name, value=value, reason=reason))
            continue
        record, text = _apply_one(found, preview, text, value, step)
        (applied if isinstance(record, Applied) else skipped).append(record)
    document["Preview"] = preview
    document["Data"] = text
    return (document, applied, skipped)


def _apply_one(
    mapping: Mapping,
    preview: dict[str, Any],
    text: str,
    value: Any,
    step: str,
) -> tuple[Applied | Skipped, str]:
    """Write one mapped value into every location it occupies.

    Parameters
    ----------
    mapping : Mapping
        The parameter being written.
    preview : dict
        The protocol's preview map, mutated in place when the mapping has a
        preview side.
    text : str
        The XProtocol text.
    value : Any
        The new displayed value.
    step : str
        Step name, for the record.

    Returns
    -------
    tuple
        The record describing what happened, and the resulting text.
    """

    def refused(why: str) -> tuple[Skipped, str]:
        return (Skipped(step=step, label=mapping.label, value=value, reason=why), text)

    targets = expand(mapping.ascconv_key, text)
    if not targets:
        return refused(f"ASCCONV block has no {mapping.ascconv_key}")

    entry = None
    if mapping.preview_path is not None:
        entry = preview.get(mapping.preview_path)
        if not isinstance(entry, dict):
            return refused(f"this protocol has no {mapping.preview_path} to write")

    number, why = encode(mapping, value)
    if number is None:
        return refused(why)

    first_before = first_after = ""
    for key, index in targets:
        existing = read_ascconv(text, key)
        sparse = omits_zero(key)
        if mapping.bit is not None:
            # A flags word the console leaves out while every box is unticked
            # is a legitimate state, not a missing target.
            literal = set_bit(existing, mapping.bit, bool(number))
            if not first_before:
                # Spell an absent word the same way on both sides. Reporting
                # it as "0" before and ABSENT after makes a protocol whose
                # flags are all unticked -- so the console omits the word
                # entirely -- read as a change on every bit the card prints,
                # and ten spurious writes is what that looked like.
                first_before = existing if existing is not None else ABSENT
                first_after = ABSENT if (sparse and _is_zero(literal)) else literal
            text = _store(text, key, literal, existing, sparse)
            continue
        if existing is None and not sparse:
            return refused(f"ASCCONV block has no {key}")
        if existing is None and displays_when_absent(mapping, number):
            # The protocol already shows this: the sequence supplies the
            # default for an element it was never given. Writing the number
            # anyway would be refused on a protocol with no sibling element to
            # insert beside, and reported as a change on one that has some.
            if not first_before:
                first_before = first_after = ABSENT
            continue
        written = (number + mapping.offset) * mapping.scale
        if mapping.basis is not None:
            basis_key = mapping.basis.replace("[*]", f"[{index}]")
            basis = read_ascconv(text, basis_key)
            if basis is None:
                return refused(f"ASCCONV block has no {basis_key} to scale against")
            written *= float(basis)
        literal = format_like(written, existing if existing is not None else _model(key))
        if not first_before:
            # Report what will actually be stored. Writing zero into a sparse
            # array removes the assignment, so an absent element asked to hold
            # zero does not change -- and saying otherwise makes a no-op run
            # look like it wrote something.
            first_before = existing or ABSENT
            first_after = ABSENT if (sparse and _is_zero(literal)) else literal
        text = _store(text, key, literal, existing, sparse)
        # Creating a sparse assignment needs somewhere to put it, and
        # insert_ascconv reports "nowhere" by returning the text unchanged.
        # Left unchecked that is a write reported as applied that wrote
        # nothing, which is worse than refusing.
        if read_ascconv(text, key) is None and not _is_zero(literal):
            return refused(f"no anchor to place {key} beside in this protocol")

    previous = None
    if entry is not None:
        previous = entry.get("Value")
        entry["Value"] = type(previous)(value) if isinstance(previous, (int, float)) else value
        shown = entry["Value"]
    else:
        shown = value
    return (
        Applied(
            step=step,
            label=mapping.label,
            preview_path=mapping.preview_path or "(not listed by the console)",
            ascconv_key=mapping.ascconv_key + (f" x{len(targets)}" if len(targets) > 1 else ""),
            previous=previous,
            value=shown,
            ascconv_previous=first_before,
            ascconv_value=first_after,
        ),
        text,
    )


def _model(key: str) -> str:
    """Return a stand-in literal deciding how a *new* assignment is formatted.

    An element being created has no existing literal to copy the integer or
    float spelling from, so the array it belongs to decides: ``alFree`` holds
    integers and ``adFree`` doubles.

    Parameters
    ----------
    key : str
        The concrete ASCCONV key.

    Returns
    -------
    str
        ``"0"`` for an integer array, ``"0.0"`` otherwise.
    """
    if ".alFree[" in key or key in INT_KEYS:
        return "0"
    return "0x0" if key in HEX_KEYS else "0.0"


def _store(text: str, key: str, literal: str, existing: str | None, sparse: bool) -> str:
    """Write, create or delete one assignment as its array's rules require.

    A sparse array lists only the indices carrying a value, so writing zero
    into one means removing its line, and writing a value into an absent one
    means inserting it in index order.

    Parameters
    ----------
    text : str
        The XProtocol text.
    key : str
        The concrete ASCCONV key.
    literal : str
        The value to store.
    existing : str or None
        The literal currently in place, if any.
    sparse : bool
        Whether the assignment is omitted when it holds zero.

    Returns
    -------
    str
        The resulting text.
    """
    if sparse and _is_zero(literal):
        return remove_ascconv(text, key) if existing is not None else text
    if existing is None:
        return insert_ascconv(text, key, literal)
    return write_ascconv(text, key, literal)


def _is_zero(literal: str) -> bool:
    """Return whether a literal represents zero.

    Parameters
    ----------
    literal : str
        The literal to test.

    Returns
    -------
    bool
        ``True`` when it parses as zero.
    """
    text = literal.strip()
    try:
        if re.fullmatch(r"0x[0-9a-fA-F]+", text):
            return int(text, 16) == 0
        return float(text) == 0.0
    except ValueError:
        return False


def apply(archive: Archive, changes: MappingType[str, MappingType[str, Any]]) -> Manifest:
    """Apply per-step parameter changes to an archive, in memory.

    The archive is edited but not written; call :meth:`Archive.write` to save
    it. Only the protocols that actually change are re-addressed, so an
    archive whose requests all fail is left byte-identical.

    Parameters
    ----------
    archive : Archive
        The archive to edit.
    changes : mapping
        Step name to a mapping of preview path or printed label to new value.

    Returns
    -------
    Manifest
        What was written, what was refused, and how much was inherited.
    """
    manifest = Manifest()
    steps: dict[str, list[Step]] = {}
    for step in archive.steps:
        steps.setdefault(step.name, []).append(step)
    for name, requests in changes.items():
        found = steps.get(name, [])
        # Scan names are not unique. An archive built by repeating one sequence
        # with a single option varied per copy -- the shape that pins the
        # Special card -- has a dozen scans sharing a name, and resolving that
        # to one of them would patch an arbitrary scan while the caller
        # believed it had named a particular one.
        if len(found) != 1:
            reason = (
                "no such step in archive"
                if not found
                else f"{len(found)} steps are named {name!r}; patch by instance instead"
            )
            for label, value in requests.items():
                manifest.skipped.append(
                    Skipped(step=name, label=label, value=value, reason=reason)
                )
            continue
        step = found[0]
        if not step.runs_a_protocol:
            for label, value in requests.items():
                manifest.skipped.append(
                    Skipped(
                        step=name,
                        label=label,
                        value=value,
                        reason="that step is a pause and holds no protocol",
                    )
                )
            continue
        protocol = step.protocol
        document, applied, skipped = patch_document(protocol, requests, step=name)
        manifest.applied.extend(applied)
        manifest.skipped.extend(skipped)
        manifest.inherited += max(0, len(protocol.preview) - len(applied))
        if applied:
            archive.replace_content(protocol.instance, document)
    if manifest.applied:
        manifest.stale = ["lScanTimeSec", "lTotalScanTimeSec"]
        quantised = {m.label for m in MAPPINGS if m.basis is not None}
        manifest.approximate = [a.label for a in manifest.applied if a.label in quantised]
    return manifest
