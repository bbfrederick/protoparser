"""Assemble one archive holding every customer sequence in the corpus.

Ten come from Potpourri_P1, which is also the template; the remaining four
exist only in the 31P export and are imported across. Every appended scan
carries exactly one deliberate edit, because a copy identical to its source
cannot distinguish "created correctly" from "aliased to the original".
"""

import pathlib
import sys
from typing import Any

from siemens_protocol.exar import archive as A
from siemens_protocol.exar import generate, patch, validate

EX = pathlib.Path("examples/XA60")
OUT = pathlib.Path("loadtest/loadtest_ALLSEQ.exar1")

# (sequence, donor archive or None for the template, label, how to pick a new value)
PLAN = [
    ("can_neuromelanin", None, "MT Flip Angle", "bump"),
    ("cmrr_mbep2d_bold", None, "Single-band images", "toggle"),
    ("cmrr_mbep2d_se", None, "SENSE1 coil combine", "toggle"),
    ("cmrr_mbep2d_diff", None, "MB LeakBlock kernel", "toggle"),
    ("tfl_mgh_epinav_ABCD", None, "Readout polarity", "choice"),
    ("space_mgh_epinav_ABCD", None, "Include Nav.", "choice"),
    ("ep_moco_nav_set_ABCD", None, "Protocol filename", "choice"),
    ("ep2d_bold_mgh", None, "TR", "bump"),
    ("ep2d_diff_mgh", None, "TR", "bump"),
    ("ep2d_se_sms_mgh", None, "TR", "bump"),
    ("tfl_mgh_multiecho", "31P CSI 20230503 NOE", "Averaging", "choice"),
    ("hcp_mbep2d_bold", "31P CSI 20230503 NOE", "TR", "bump"),
    ("hcp_mbep2d_se", "31P CSI 20230503 NOE", "TR", "bump"),
    ("hcp_mbep2d_diff", "31P CSI 20230503 NOE", "TR", "bump"),
    # Siemens sequences that also write into the block. resolve prints a
    # Special card on some builds, so "has a card" is not the same question
    # as "is third party" and both belong in the load test.
    ("resolve", "31P CSI 20230503 NOE", "TR", "bump"),
    ("tfl", "31P CSI 20230503 NOE", "TR", "bump"),
]
SHORT = {
    "MT Flip Angle": "MTFlip",
    "Single-band images": "SBimg",
    "SENSE1 coil combine": "SENSE1",
    "MB LeakBlock kernel": "LeakBlk",
    "Readout polarity": "ROpol",
    "Include Nav.": "IncNav",
    "Protocol filename": "Protfn",
    "Averaging": "Avg",
    "TR": "TR",
}


def find(arc: A.Archive, sequence: str) -> A.Step:
    """Return the first scan in ``arc`` running ``sequence``.

    Parameters
    ----------
    arc : Archive
        The archive to search.
    sequence : str
        Sequence name as ``seq_subpath`` spells it.

    Returns
    -------
    Step
        The first matching step.

    Raises
    ------
    LookupError
        If no scan runs that sequence.
    """
    for step in arc.steps:
        if step.runs_a_protocol and patch.sequence_of(step.protocol) == sequence:
            return step
    raise LookupError(sequence)


def new_value(protocol: A.Protocol, label: str, how: str) -> Any:
    """Choose a value guaranteed to differ from what the protocol holds.

    An edit that does not move anything cannot tell a correctly created scan
    from one aliased to its source, so the new value is derived from the
    stored one rather than written down.

    Parameters
    ----------
    protocol : Protocol
        The protocol being edited.
    label : str
        Printed parameter name.
    how : str
        ``"toggle"`` to flip a packed flag, ``"choice"`` to pick any other
        option, ``"bump"`` to step a number off its current value.

    Returns
    -------
    Any
        A value the mapping accepts and the protocol does not already hold.

    Raises
    ------
    LookupError
        If the label does not resolve, or offers no alternative.
    """
    mapping, _ = patch.resolve(protocol, label)
    if mapping is None:
        raise LookupError(f"{label} does not resolve on {patch.sequence_of(protocol)}")
    text = protocol.xprotocol
    key = patch.expand(mapping.ascconv_key, text)[0][0]
    existing = patch.read_ascconv(text, key)
    if how == "toggle":
        word = int(float(existing or "0"))
        return not bool(word >> mapping.bit & 1)
    if how == "choice":
        current = int(float(existing)) if existing is not None else None
        for shown, number in mapping.choices:
            if number != current:
                return shown
        raise LookupError(label)
    stored = float(existing)
    return round(stored / mapping.scale + (1.0 if mapping.scale == 1.0 else 10.0), 3)


def main() -> None:
    """Assemble, patch, verify and write the archive.

    Returns
    -------
    None
    """
    target = A.read(str(EX / "Potpourri_P1.exar1"))
    donors = {n: A.read(str(EX / f"{n}.exar1")) for n in {p[1] for p in PLAN if p[1]}}
    before = len([s for s in target.steps if s.runs_a_protocol])

    wanted, names = {}, []
    for n, (sequence, donor, label, how) in enumerate(PLAN, start=1):
        origin = donors[donor] if donor else target
        step = find(origin, sequence)
        value = new_value(step.protocol, label, how)
        name = f"S{n:02d}_{sequence}_{SHORT[label]}"[:35]
        assert name not in wanted, name
        generate.duplicate_step(target, step, name, source=None if donor is None else origin)
        wanted[name] = {label: value}
        names.append((name, sequence, donor or "template", label, value))

    # Imported scans have no sibling in the template to compare against, so
    # each also arrives unedited: an edited copy that came back wrong is then
    # distinguishable from an import that brought the wrong protocol.
    for n, (sequence, donor, _label, _how) in enumerate(PLAN, start=1):
        if donor is None:
            continue
        origin = donors[donor]
        control = f"C{n:02d}_{sequence}_control"[:35]
        generate.duplicate_step(target, find(origin, sequence), control, source=origin)
        names.append((control, sequence, donor, "(none)", "unchanged"))

    tmp = pathlib.Path(OUT.parent / "_staged.exar1")
    OUT.parent.mkdir(exist_ok=True)
    target.write(str(tmp))

    staged = A.read(str(tmp))
    manifest = patch.apply(staged, wanted)
    if manifest.skipped:
        for s in manifest.skipped:
            print(f"REFUSED {s.step}: {s.label} -> {s.value}: {s.reason}")
        sys.exit(1)
    moved = [a for a in manifest.applied if a.ascconv_previous != a.ascconv_value]
    print(f"applied {len(manifest.applied)}, of which {len(moved)} changed a stored value")
    for a in manifest.applied:
        flag = " " if a.ascconv_previous != a.ascconv_value else "!"
        print(f" {flag} {a.step:36s} {a.label:22s} {a.ascconv_previous} -> {a.ascconv_value}")
    if len(moved) != len(PLAN):
        print("some edit did not move anything -- it cannot prove a copy is distinct")
        sys.exit(1)
    staged.write(str(OUT))
    tmp.unlink()

    check = A.read(str(OUT))
    problems = validate.problems(check)
    scans = [s for s in check.steps if s.runs_a_protocol]
    print(f"\n{before} scans in, {len(scans)} out; validator problems: {problems or 'none'}")
    order = [s.name for s in check.steps]
    assert order[: len(order) - len(names)] == [
        s.name for s in A.read(str(EX / "Potpourri_P1.exar1")).steps
    ]
    print("running order: originals first, then", len(names), "appended")
    for row in names:
        print("   ", row)
    distinct = len({s.protocol.instance.content_hash for s in scans})
    print(f"distinct protocols: {distinct} across {len(scans)} scans")


main()
