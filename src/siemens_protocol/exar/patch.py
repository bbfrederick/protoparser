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
    bit : int or None
        Position of this parameter's flag within ``ascconv_key``, for a
        checkbox packed into a shared word. Writing one is a read-modify-write
        of that word, and an absent word counts as zero.
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
    basis: str | None = None
    sequences: tuple[str, ...] = ()
    choices: tuple[tuple[str, int], ...] = ()
    bit: int | None = None
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
    # ---- The Special card. ASCCONV only, and meaningful per sequence. ----
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
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 0",
    ),
    Mapping(
        label="PF omits higher k-space",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=1,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 1",
    ),
    Mapping(
        label="SENSE1 coil combine",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=4,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 4",
    ),
    Mapping(
        label="Invert RO/PE polarity",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=8,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 8",
    ),
    Mapping(
        label="MB RF phase scramble",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=9,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 9",
    ),
    Mapping(
        label="Time-shifted MB RF",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=10,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 10",
    ),
    Mapping(
        label="MB LeakBlock kernel",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=12,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 12",
    ),
    Mapping(
        label="MB dual kernel",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=16,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 16",
    ),
    Mapping(
        label="Disable freq. update",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=18,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 18",
    ),
    Mapping(
        label="Force equal slice timing",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=20,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 20",
    ),
    Mapping(
        label="Opt. MB RF pulse BW",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=22,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 22",
    ),
    Mapping(
        label="Suppress 16-bit DICOM",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=25,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 25",
    ),
    Mapping(
        label="Disable B1 control loop",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=27,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
        evidence="controlled edit: CMRR_optionscan_P1, single-option toggle -> bit 27",
    ),
    Mapping(
        label="Force GPA balance",
        ascconv_key="sWipMemBlock.alFree[0]",
        bit=28,
        sequences=("cmrr_mbep2d_bold", "cmrr_mbep2d_se", "cmrr_mbep2d_diff"),
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
        sequences=("ep_moco_nav_set_ABCD",),
        evidence="controlled edit: NAV_optionscan_P1. alFree[1] again, and again a "
        "different parameter -- Readout polarity on the MPRAGE sequence.",
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

    @property
    def complete(self) -> bool:
        """Return whether every requested value was written.

        Returns
        -------
        bool
            ``True`` when nothing was skipped.
        """
        return not self.skipped

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
    return bool(re.match(r"sWipMemBlock\.(al|ad)Free\[\d+\]$", key))


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
    match = re.fullmatch(r"(.*)\[(\d+)\]", key)
    if start < 0 or match is None:
        return text
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
    if re.fullmatch(r"[-+]?\d+", existing.strip()):
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
        ``True`` for an unrestricted mapping, or one naming this sequence.
    """
    if mapping.sequences and sequence_of(protocol) not in mapping.sequences:
        return False
    if mapping.when is not None:
        key, expected = mapping.when
        return read_ascconv(protocol.xprotocol, key) == expected
    return True


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
    in_scope = [m for m in MAPPINGS if applies_to(m, protocol)]
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
    elsewhere = [m for m in MAPPINGS if m.label.strip().casefold() == wanted]
    if elsewhere:
        runs = sequence_of(protocol) or "an unnamed sequence"
        wants = ", ".join(sorted({q for m in elsewhere for q in m.sequences}))
        return (None, f"{name!r} is mapped for {wants}, but this protocol runs {runs}")
    printed = {e.label.strip().casefold() for e in protocol.preview.values()}
    if wanted in printed:
        return (None, f"{name!r} is printed by this protocol but no verified mapping writes it")
    return (None, f"no verified mapping for {name!r}")


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
                first_before = existing or "0"
                first_after = ABSENT if (sparse and _is_zero(literal)) else literal
            text = _store(text, key, literal, existing, sparse)
            continue
        if existing is None and not sparse:
            return refused(f"ASCCONV block has no {key}")
        written = number * mapping.scale
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
    return "0" if ".alFree[" in key else "0.0"


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
    try:
        return float(literal) == 0.0
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
