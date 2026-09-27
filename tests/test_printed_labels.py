"""Where a stored field has a printed label, the label is what is shown.

ASCCONV is the protocol's ground truth, and the printout is the version an
operator acts on: its labels are the quantities the console lets someone
change. So wherever a mapping gives an assignment a label, a view meant for a
person names the label -- and does not name the assignment a second time
beside it, which made one edit to ``TE`` read as two differences.

The risk in hiding a raw assignment is losing a difference, so most of what
follows is about what must *stay* raw: a field whose mapping does not apply
to this sequence, a replicated array whose elements disagree, and the bits
of a flags word no label claims.
"""

from __future__ import annotations

import re

import pytest

from conftest import find_exar, requires_exar
from siemens_protocol import exar
from siemens_protocol.analysis import archive_view
from siemens_protocol.analysis.generate import mappings, probe
from siemens_protocol.exar import ascconv
from siemens_protocol.exar import inspect as ins
from siemens_protocol.exar.archive import Protocol, Step


def _scan(archive_name: str, scan_name: str) -> Step:
    """One scan-running step of a corpus archive, by name.

    Parameters
    ----------
    archive_name : str
        File name of the archive, found through ``find_exar``.
    scan_name : str
        The step's name.

    Returns
    -------
    Step
        The first step so named that runs a protocol.
    """
    archive = exar.read(find_exar(archive_name))
    return next(s for s in archive.steps if s.runs_a_protocol and s.name == scan_name)


def _edited(protocol: Protocol, key: str, literal: str) -> Protocol:
    """A copy of a protocol with one ASCCONV assignment rewritten.

    Parameters
    ----------
    protocol : Protocol
        The protocol to copy.
    key : str
        An assignment it already carries.
    literal : str
        The new literal.

    Returns
    -------
    Protocol
        The copy; the original is untouched.
    """
    text = ascconv.write_ascconv(protocol.xprotocol, key, literal)
    assert ascconv.read_ascconv(text, key) == literal, f"could not stage {key} = {literal}"
    return Protocol(protocol.instance, {**protocol.document, "Data": text})


@requires_exar
def test_an_archive_diff_names_each_edit_once_by_its_printed_label(
    capsys: pytest.CaptureFixture,
) -> None:
    """The console's own edit of ``Potpourri_P1`` reads in the card's terms.

    Every one of these edits used to appear twice, once under its label and
    once as the assignment -- ``Remeasure: 20 | 19`` beside
    ``sWipMemBlock.alFree[9]: 20 | 19``. The raw spelling is the one nobody
    at a console can act on.

    ``alFree[4]`` on the vNav must stay: it is ``Averaging`` on
    ``tfl_mgh_multiecho`` and the scanner refused that widening to
    ``tfl_mgh_epinav_ABCD``, so on this scan it has no verified label. (That
    is not a test of the applicability gate -- nothing shows a label for it
    here at all; the gate's own test is below.)

    Parameters
    ----------
    capsys : pytest.CaptureFixture
        Capture fixture for the report.

    Returns
    -------
    None
    """
    from siemens_protocol.cli import main

    main(["diff", find_exar("Potpourri_P1.exar1"), find_exar("Potpourri_P1_changed.exar1")])
    out = capsys.readouterr().out

    for label in ("Remeasure", "TE 2", "Readout polarity", "Triggering scheme"):
        assert f"~ {label}:" in out, f"::error::the {label} edit is not reported by its label"
    for raw in ("alTE[", "sWipMemBlock.alFree[9]", "dThickness", "adFlipAngleDegree"):
        assert raw not in out, f"::error::{raw} is reported beside the label that says it"
    assert "sWipMemBlock.alFree[4]:" in out, "::error::a field with no verified label was hidden"
    # The Preview's blank-labelled entries are ones the console never prints;
    # keyed by their blank they folded into a single nameless row.
    assert not re.search(r"^\s*[~+-] :", out, re.M), "::error::a Preview row has no name"


@requires_exar
def test_a_flags_word_keeps_the_bits_no_label_claims() -> None:
    """A packed word is reduced to its unlabelled bits, never dropped whole.

    ``rfMRI_REST_MULTIECHO_MAGPHASE_PA`` sets bit 15 of CMRR's ``alFree[0]``,
    which no mapping claims. Every other bit it sets has a label on the
    Special card, so the residue is exactly that one bit -- and a scan whose
    set bits are all labelled leaves nothing behind.

    Returns
    -------
    None
    """
    step = _scan("Frederick_P2.exar1", "rfMRI_REST_MULTIECHO_MAGPHASE_PA")
    residue = archive_view.unlabelled_ascconv(step.protocol, ins.printed_view(step.protocol))
    assert "sWipMemBlock.alFree[0]" not in residue
    assert residue.get("sWipMemBlock.alFree[0] (unlabelled bits)") == "15"

    labelled = _scan("Potpourri_P1_changed.exar1", "rfMRI REST ME PA XA60")
    word = ascconv.read_ascconv(labelled.protocol.xprotocol, "sWipMemBlock.alFree[0]")
    assert word and int(word, 0) >> 29 & 1, "the fixture no longer sets a bit to check"
    rest = archive_view.unlabelled_ascconv(labelled.protocol, ins.printed_view(labelled.protocol))
    assert not [key for key in rest if key.startswith("sWipMemBlock.alFree[0]")]


@requires_exar
def test_a_replicated_array_stays_raw_when_its_elements_disagree() -> None:
    """``Slice Thickness`` shows the first slice, so it speaks for all only if they agree.

    Hiding every ``asSlice[].dThickness`` behind one label is sound because
    the console writes the same value on each. A protocol where they differ
    is exactly the case the label cannot express, and it must stay visible.

    Returns
    -------
    None
    """
    step = _scan("Potpourri_P1.exar1", "Minn_CMRR_2.3mm_S8_rest_6min")
    slices = re.compile(r"sSliceArray\.asSlice\[\d+\]\.dThickness")
    agreeing = archive_view.unlabelled_ascconv(step.protocol)
    assert not [key for key in agreeing if slices.fullmatch(key)]

    split = _edited(step.protocol, "sSliceArray.asSlice[5].dThickness", "2.5")
    kept = [key for key in archive_view.unlabelled_ascconv(split) if slices.fullmatch(key)]
    assert "sSliceArray.asSlice[5].dThickness" in kept
    assert len(kept) > 1, "only the odd element stayed; the others lost their label's cover"


@requires_exar
def test_a_decoded_label_is_as_precise_as_the_assignment_it_replaces() -> None:
    """A label standing in for an assignment must not be the lossier of the two.

    Six significant figures used to hide a seventh-digit change and spelled
    large values in exponent notation.

    Returns
    -------
    None
    """
    step = _scan("Potpourri_P1.exar1", "Minn_CMRR_2.3mm_S8_rest_6min")
    tr = next(m for m in mappings.MAPPINGS if m.label == "TR" and m.ascconv_key == "alTR[0]")
    assert mappings.display(tr, _edited(step.protocol, "alTR[0]", "1234567")) == "1234.567"
    assert mappings.display(tr, _edited(step.protocol, "alTR[0]", "1500000000")) == "1500000"


def test_an_assignment_is_named_by_the_label_it_has_on_that_sequence() -> None:
    """``label_for`` honours the same sequence and build gates as the writer.

    Returns
    -------
    None
    """
    cmrr = ("cmrr_mbep2d_bold", "Sequence: R017 nxva60a/main r/91b106c1e")
    assert mappings.label_for("alTE[1]", *cmrr) == "TE 2"
    assert mappings.label_for("sSliceArray.asSlice[7].dReadoutFOV", *cmrr) == "FOV Read"
    # Whether dThickness is the slice or the whole slab depends on another
    # field, which only a protocol can answer -- so no label is claimed.
    assert mappings.label_for("sSliceArray.asSlice[7].dThickness", *cmrr) is None
    assert mappings.label_for("sWipMemBlock.alFree[9]", "tfl_mgh_epinav_ABCD") == "Remeasure"
    # The same index on a sequence it was never derived for is not that label.
    assert mappings.label_for("sWipMemBlock.alFree[9]", "gre") is None
    # A flag word holds many labels, so naming it by one would misdescribe it.
    assert mappings.label_for("sWipMemBlock.alFree[0]", *cmrr) is None


def test_a_probe_report_names_a_field_by_its_label_first() -> None:
    """The label leads, with the assignment beside it for the record.

    Returns
    -------
    None
    """
    found = probe.Finding(
        name="T01",
        probe=probe.Probe("alTE[1]", "3650", "TE 2"),
        before="3550",
        verdict="held",
        recomputed={"alTE[2]": ("5410", "5510"), "lTotalScanTimeSec": ("249", "250")},
    )
    text = probe.report([found], sequence="%CustomerSeq%\\tfl_mgh_epinav_ABCD")
    assert "TE 2 (alTE[1]) = '3650'" in text
    assert "console moved  TE 3 (alTE[2])" in text
    assert "console moved  lTotalScanTimeSec" in text


@requires_exar
@pytest.mark.parametrize("sequence", ["hcp_mbep2d_diff", "hcp_mbep2d_se"])
def test_a_label_the_preview_prints_does_not_cover_a_field_on_an_unverified_build(
    sequence: str,
) -> None:
    """The Preview can show a label whose mapping does not hold on this scan.

    The console lists ``Grad. rev. fat suppr.`` for the 2016-2017 HCP builds
    too, but ``alFree[25]`` is mapped only for CMRR's R017 build, and a later
    build is free to renumber the card. Hiding the field behind that label
    would claim a correspondence nobody has verified. The label is still
    shown -- the console printed it -- and the field stays beside it.

    This is the case the gate exists for. The diff test's ``alFree[4]`` does
    not exercise it: ``Averaging`` has no Preview entry, so an inapplicable
    mapping there never shows a label to begin with.

    Parameters
    ----------
    sequence : str
        The old-build sequence to check.

    Returns
    -------
    None
    """
    archive = exar.read(find_exar("CORPUS_INCONSISTENT.exar1"))
    step = next(
        s
        for s in archive.steps
        if s.runs_a_protocol and ascconv.sequence_of(s.protocol) == sequence
    )
    printed = ins.printed_view(step.protocol)
    assert "Grad. rev. fat suppr." in printed, "the fixture no longer prints the label"
    residue = archive_view.unlabelled_ascconv(step.protocol, printed)
    assert "sWipMemBlock.alFree[25]" in residue


@requires_exar
def test_a_coded_choice_in_the_preview_reads_as_the_card_prints_it() -> None:
    """``Preview`` stores some choices as console codes; the card prints words.

    ``Gradient Mode`` holds ``107`` for ``Fast`` and ``AutoAlign`` ``6148``
    for ``Head > Brain``. The label is right and the value is one nobody at a
    console ever sees. Where a mapping decodes the choice, the Preview section
    and the cards both read the word -- they must agree, or the flattened view
    reports the parameter as disagreeing with itself -- and the archive then
    reads as its own printout does.

    Returns
    -------
    None
    """
    archive_path = find_exar("Potpourri_P1.exar1")
    archive = exar.read(archive_path)
    document = archive_view.as_protocol(archive, archive.programs[0], archive_path)
    scan = next(s for s in document["scans"] if s["name"] == "T1_MEMPRAGE_1.0mm_p4_vNav")
    raw = ins.printed_view(
        next(s for s in archive.steps if s.name == "T1_MEMPRAGE_1.0mm_p4_vNav").protocol
    )
    assert raw["AutoAlign"] == "6148", "the fixture no longer stores the code"
    assert scan["sections"]["Preview"]["AutoAlign"] == "Head > Brain"
    assert scan["flat"]["AutoAlign"]["value"] == "Head > Brain"
    assert scan["flat"]["AutoAlign"]["conflict"] is False
    # A plain number keeps the console's spelling, unit included.
    assert scan["sections"]["Preview"]["TR"] == raw["TR"]
