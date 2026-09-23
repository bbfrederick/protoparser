"""Read and write the ASCCONV block, and the facts a protocol states about itself.

ASCCONV is a flat, hand-editable text block embedded in the XProtocol
document: dotted, optionally-indexed assignments like ``alTR[0] = 650000``.
This module is the codec for that text -- finding the block, reading and
writing one assignment, creating or deleting one, formatting a number the way
the console spells it -- plus a handful of facts a protocol's ASCCONV states
about itself: which sequence binary it runs, what build stamped it, and
whether a field is one the console rewrites on every save regardless of what
changed (:func:`is_churn`).

Nothing here knows what a printed label means. Which stored field a card's
``TR`` corresponds to, its unit scale, its enum choices -- that is domain
knowledge about the protocol's business semantics, not the file format, and
it lives one level up in
:mod:`siemens_protocol.analysis.generate.mappings`, built on top of this
module rather than the reverse.

A displayed value can be stored in more than one place, and the shapes
differ -- an ASCCONV *array* replicates one value across every element of
``sSliceArray.asSlice[]``; a sparse array omits an element holding zero
entirely rather than writing a zero into it; and some assignments are
scattered array elements the schema orders by *position*, not alphabetically,
so a newly created one has to be inserted beside a known neighbour
(:data:`SPARSE_ANCHORS`) rather than appended. Those are exactly the shapes
:func:`insert_ascconv`, :func:`omits_zero` and :func:`store_ascconv` exist to
handle, independent of what any particular assignment means.
"""

from __future__ import annotations

import re

from .archive import Protocol

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
        "sRawFilter.ucOn",
        "sHammingFilter.ucOn",
        "sKSpace.dPhaseOversamplingForDialog",
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
        "sRawFilter.ucOn",
        "sHammingFilter.ucOn",
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
    # Mined out of the corpus rather than read off a console, and then
    # validated the harder way: probe run 1 created both lines at these
    # positions, the scanner loaded them, and its own re-export left each
    # exactly where we had put it.
    "sKSpace.dPhaseOversamplingForDialog": ("sKSpace.dPhaseResolution",),
    "sRawFilter.ucOn": (
        "sRawFilter.lSlope_256",
        "sPreScanNormalizeFilter.ucMode",
    ),
    "sHammingFilter.ucOn": ("sPreScanNormalizeFilter.ucMode",),
    "ucReconstructionPrio": ("ulWrapUpMagn",),
}


#: Delimiters of the ASCCONV block inside the XProtocol text.
ASCCONV_BEGIN = "### ASCCONV BEGIN"


ASCCONV_END = "### ASCCONV END"


#: Preview key holding the sequence name a protocol runs.
SEQUENCE_PATH = "sub.0.msr.seq_subpath"


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


def insert_ascconv(text: str, key: str, literal: str, anchors: tuple[str, ...] = ()) -> str:
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
    anchors : tuple of str, optional
        Assignments this key is known to follow, tried ahead of
        :data:`SPARSE_ANCHORS`. A probe writes keys nobody has curated an
        anchor for, and mining one out of the corpus is weaker evidence than
        reading it off the console's output, so a mined anchor is passed in
        rather than joining the table.

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
        return _insert_scalar(text, key, literal, start, end, anchors)
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


def _insert_scalar(
    text: str, key: str, literal: str, start: int, end: int, anchors: tuple[str, ...] = ()
) -> str:
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
    anchors : tuple of str, optional
        Assignments to try ahead of :data:`SPARSE_ANCHORS`.

    Returns
    -------
    str
        The text with the assignment inserted, unchanged when no anchor for
        the key is known or none of its anchors is present. Callers must
        treat "unchanged" as a refusal rather than a write -- silently
        writing nothing is the failure this shape invites.
    """
    for anchor in tuple(anchors) + SPARSE_ANCHORS.get(key, ()):
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


def default_literal(key: str) -> str:
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


def store_ascconv(text: str, key: str, literal: str, existing: str | None, sparse: bool) -> str:
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
    if sparse and is_zero(literal):
        return remove_ascconv(text, key) if existing is not None else text
    if existing is None:
        return insert_ascconv(text, key, literal)
    return write_ascconv(text, key, literal)


def is_zero(literal: str) -> bool:
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
