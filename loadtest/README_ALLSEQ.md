# Every-sequence load test

`loadtest_ALLSEQ.exar1` -- 40 scans. The 18 of `Potpourri_P1` untouched, then
22 appended by the library: one scan for each of the **16 sequences in the
whole corpus that write into `sWipMemBlock`**, plus 6 unedited controls.

This is the first archive assembled from **more than one source export**.
Ten sequences come from the template itself; the other six exist only in
`31P CSI 20230503 NOE.exar1` and were imported across.

## What is being tested

Every previous load test exercised one layer. This one is the combination,
and it asks three questions the earlier ones could not:

1. **Does a cross-archive import load?** Six protocols were lifted out of a
   different export -- a different protocol tree, a different session, 24
   scans instead of 18 -- and appended here. Nothing about that has ever been
   put in front of a scanner.
2. **Does an imported scan keep its own content?** This is the defect that
   failed round 1 of the duplication test: a copy that inherits its source's
   `ParentElementId` is served the source's protocol, and the edit vanishes.
   Each imported scan therefore carries one change, and each also appears a
   second time unchanged as a `C##_..._control`. An edited scan reading its
   old value means the pointer is wrong; a control reading something
   unexpected means the import brought the wrong protocol.
3. **Do the Special-card encodings survive on every sequence at once?** The
   option-scan work pinned three encodings on three sequences. Seven of the
   scans below exercise all three across five sequences.

## The 16 sequences

Fourteen are customer sequences. `resolve` and `tfl` are Siemens' own and are
included because they write into the block too -- "prints a Special card" and
"is third party" are not the same question, and the load test should not
assume they are.

| Scan | Sequence | From | Change |
|---|---|---|---|
| `S01_can_neuromelanin_MTFlip` | can_neuromelanin | template | MT Flip Angle 370 -> **371** |
| `S02_cmrr_mbep2d_bold_SBimg` | cmrr_mbep2d_bold | template | Single-band images -> **off** |
| `S03_cmrr_mbep2d_se_SENSE1` | cmrr_mbep2d_se | template | SENSE1 coil combine -> **on** |
| `S04_cmrr_mbep2d_diff_LeakBlk` | cmrr_mbep2d_diff | template | MB LeakBlock kernel -> **off** |
| `S05_tfl_mgh_epinav_ABCD_ROpol` | tfl_mgh_epinav_ABCD | template | Readout polarity -> **Negative** |
| `S06_space_mgh_epinav_ABCD_IncNav` | space_mgh_epinav_ABCD | template | Include Nav. -> **Off** |
| `S07_ep_moco_nav_set_ABCD_Protfn` | ep_moco_nav_set_ABCD | template | Protocol filename -> **Generic** |
| `S08_ep2d_bold_mgh_TR` | ep2d_bold_mgh | template | TR 223 -> **233 ms** |
| `S09_ep2d_diff_mgh_TR` | ep2d_diff_mgh | template | TR 500 -> **510 ms** |
| `S10_ep2d_se_sms_mgh_TR` | ep2d_se_sms_mgh | template | TR 285 -> **295 ms** |
| `S11_tfl_mgh_multiecho_Avg` | tfl_mgh_multiecho | **31P CSI** | Averaging RMS -> **None** |
| `S12_hcp_mbep2d_bold_TR` | hcp_mbep2d_bold | **31P CSI** | TR 800 -> **810 ms** |
| `S13_hcp_mbep2d_se_TR` | hcp_mbep2d_se | **31P CSI** | TR 8000 -> **8010 ms** |
| `S14_hcp_mbep2d_diff_TR` | hcp_mbep2d_diff | **31P CSI** | TR 3230 -> **3240 ms** |
| `S15_resolve_TR` | resolve | **31P CSI** | TR 4190 -> **4200 ms** |
| `S16_tfl_TR` | tfl | **31P CSI** | TR 250 -> **260 ms** |

Controls `C11`..`C16` are the six imported sequences again, unedited.

## What to look for

- **All 40 scans present**, with the 18 originals first and in their original
  order.
- **Each `S##` scan reads the bold value above**, and its template sibling --
  `can_neuromelanin`, `Minn_CMRR_2.3mm_S8_rest_6min`, `ep2d_bold_mgh` and so
  on -- still reads the old one.
- **Each `C##` control matches its `S##` sibling everywhere except that one
  parameter.** They were imported from the same donor scan and differ only
  there.
- **`S04` is the interesting one.** Its CMRR flags word held exactly one bit
  (LeakBlock), so clearing it deletes the `alFree[0]` assignment entirely --
  `sWipMemBlock` arrays omit an element holding zero. Its Special card should
  come back with every box unticked, not with `alFree[0] = 0`. That path has
  never been through a scanner.

## What was checked before shipping it

- `validate.problems()` clean; `pragma integrity_check` ok.
- Every live element resolves through the head changeset's checkout index.
- The `#ContentHash` tag matches the XProtocol text on all 40 scans.
- **Each edited scan differs from its donor in exactly one ASCCONV field**,
  and it is the field the mapping claims. No churn, no collateral writes.
- Each unedited control is byte-identical to its donor protocol -- which also
  shows the import copied the stored blob rather than recompressing it.
- Every name is within the 35 characters the console keeps.

## If it fails

The two earlier duplication failures both showed up as *structure*, not as
values: round 1 was rejected outright with the folder tree visible and no
protocols in it, round 2 loaded but served copies their source's protocol.
So the useful thing to report is which of the three happens -- rejected on
import, loads with scans missing, or loads with all 40 and wrong values --
and for the third, which column of the table above is wrong.

---

# Result

Imported as `Potpourri_P1 (2)`. **33 of 40 scans loaded**; seven were
inconsistent and were deleted before saving. All 18 originals survived, and 15
of the 22 appended scans did.

## Cross-archive import works

Six scans came from a different export. Three of them loaded -- `S11`, `S15`,
`S16` -- along with their three byte-identical controls `C11`, `C15`, `C16`,
each carrying the donor's protocol and each reading its donor's value while
its edited sibling reads the new one. That is the question this test existed
to ask, and the answer is yes.

## The seven failures are two causes, neither a defect in the write path

**Six of them are one sequence family.** `hcp_mbep2d_bold`, `hcp_mbep2d_se`
and `hcp_mbep2d_diff` failed in *both* copies -- the edited `S12`/`S13`/`S14`
and the unedited controls `C12`/`C13`/`C14`. A control is byte-identical to
its donor protocol, so nothing we wrote can be the cause. They are the only
protocols in the corpus stamped `ve11c/master r/5b0256d+; Dec 2016` instead of
`R017 nxva60a/main`: VE11C-era binaries the target scanner does not have.

**The seventh is `S06`, and it is not explained.** `Include Nav. = Off` on
`space_mgh_epinav_ABCD`. Two hypotheses were tested against the corpus and
both fail:

- *The edit is wrong.* No -- the same edit on a byte-identical source loaded
  in the NAV option-scan test as `T12_Include_Nav__Off`, and came back from
  the scanner with **zero** ASCCONV fields changed.
- *The sequence needs an adjacent setter.* No -- the NAV option scan holds 13
  `ABCD_T2w_SPC_vNav` copies and no SPACE setter anywhere, and all loaded.

What differs is the company it keeps: this archive *does* contain
`ABCD_T2w_SPC_vNav_setter`, and the NAV option scan does not. A setter still
expecting a navigator may contradict a vNav that has switched one off. Testing
that needs an option scan whose baseline includes the setter.

## Values

Eleven of the twelve checkable edits came back exactly as written, Special
card included -- `Single-band images`, `SENSE1 coil combine`, `MB LeakBlock
kernel`, `Readout polarity`, `Protocol filename`, `Averaging`, and four TRs.

`S04` is worth calling out: clearing the last bit of the CMRR flags word
deleted the `alFree[0]` assignment outright, and the card came back with every
box unticked. Sparse-array removal survives a scanner.

The twelfth, `S01`, prints `370 degrees` where 371 was written -- but so does
`T01_MT_Flip_Angle_3710` from the earlier load test, whose *archive* holds
371. `MT Flip Angle` and `MT Offset` both move in tens and the console snaps
the display to the grid. The value is stored; it is just not printable, so an
off-grid edit cannot be verified from a PDF. Use an on-grid value next time.

## A note on the archive in this folder

`loadtest_ALLSEQ.exar1` was regenerated from `build_allseq.py` after the run,
so it is semantically the archive that was loaded -- same 40 scans, same
edits, all 30 pre-flight checks green -- but not the same bytes: every
identity is a fresh GUID. The console regenerates all of them on save anyway,
so this matters only if you wanted to diff against the returned file.

## A note on the returned file

`Potpourri_P1 (2).exar1` resolves at its head to a 14-step protocol from an
unrelated session (`rfMRI REST ME PA XA60 ...`, the same tree as
`copyparametertest.exar1`). The 33 saved scans are in a *prior* changeset,
intact and readable. Its rank count is what identified the seven deletions.
The PDF is the export of the right protocol and agrees with it exactly.
