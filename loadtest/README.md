# Load-test archives

Each `Tnn` scan changes exactly ONE mapped parameter from its source protocol.
Everything else is byte-identical to a real export: same scan count, same tree,
same baseline, original GUIDs, no new changeset. Only values differ, so a
failure points at a parameter and not at a file we assembled.

`CTRLnn` scans are untouched controls. If the controls load and a `Tnn` does
not, that parameter is the cause. If a whole file fails, the controls say so.

## What to do

Load each file, then save the protocol back out as `.exar1` **and** `.pdf`.
Scans that fail to load will simply be absent -- that absence is the result,
and the surviving `Tnn` names say which parameters the loader accepted.

These are load-test artifacts, not protocols to scan with.

| file | source | test scans | controls |
|---|---|---|---|
| `loadtest_CMRR.exar1` | CMRR_optionscan_P1 | 15 | see below |
| `loadtest_NAV.exar1` | NAV_optionscan_P1 | 21 | see below |
| `loadtest_MEMPRAGE.exar1` | MEMPRAGE_optionscan_P1 | 3 | see below |
| `loadtest_MISC.exar1` | Potpourri_P1 | 1 | see below |
| `loadtest_MISC2.exar1` | Potpourri_P2 | 1 | see below |

## loadtest_CMRR.exar1

From `examples/XA60/CMRR_optionscan_P1.exar1`.

| scan | sequence | parameter | from | to |
|---|---|---|---|---|
| `T01_Single_band_images_True` | cmrr_mbep2d_diff | Single-band images | False | True |
| `T02_PF_omits_higher_k_space_True` | cmrr_mbep2d_diff | PF omits higher k-space | False | True |
| `T03_SENSE1_coil_combine_True` | cmrr_mbep2d_diff | SENSE1 coil combine | False | True |
| `T04_Invert_RO_PE_polarity_True` | cmrr_mbep2d_diff | Invert RO/PE polarity | False | True |
| `T05_MB_RF_phase_scramble_False` | cmrr_mbep2d_diff | MB RF phase scramble | True | False |
| `T06_Time_shifted_MB_RF_True` | cmrr_mbep2d_diff | Time-shifted MB RF | False | True |
| `T07_MB_LeakBlock_kernel_True` | cmrr_mbep2d_diff | MB LeakBlock kernel | False | True |
| `T08_MB_dual_kernel_True` | cmrr_mbep2d_diff | MB dual kernel | False | True |
| `T09_Disable_freq__update_True` | cmrr_mbep2d_diff | Disable freq. update | False | True |
| `T10_Force_equal_slice_timing_True` | cmrr_mbep2d_diff | Force equal slice timing | False | True |
| `T11_Opt__MB_RF_pulse_BW_True` | cmrr_mbep2d_diff | Opt. MB RF pulse BW | False | True |
| `T12_Suppress_16_bit_DICOM_True` | cmrr_mbep2d_diff | Suppress 16-bit DICOM | False | True |
| `T13_Disable_B1_control_loop_True` | cmrr_mbep2d_diff | Disable B1 control loop | False | True |
| `T14_Force_GPA_balance_True` | cmrr_mbep2d_diff | Force GPA balance | False | True |
| `T15_TR_6510` | cmrr_mbep2d_bold | TR | 650.0 | 651.0 |

## loadtest_NAV.exar1

From `examples/XA60/NAV_optionscan_P1.exar1`.

| scan | sequence | parameter | from | to |
|---|---|---|---|---|
| `T01_Readout_polarity_Negative` | tfl_mgh_epinav_ABCD | Readout polarity | Positive | Negative |
| `T02_Nav__location_After` | tfl_mgh_epinav_ABCD | Nav. location | Before | After |
| `T03_Apply_moco_to_neither` | tfl_mgh_epinav_ABCD | Apply moco to | parent and nav | neither |
| `T04_Remeasure_50` | tfl_mgh_epinav_ABCD | Remeasure | None | 5.0 |
| `T05_Reacq__threshold_055` | tfl_mgh_epinav_ABCD | Reacq. threshold | 0.5 | 0.55 |
| `T06_Feedback_Delay_610` | tfl_mgh_epinav_ABCD | Feedback Delay | 60.0 | 61.0 |
| `T07_Moco_ref__image_NewSessRef` | tfl_mgh_epinav_ABCD | Moco ref. image | Use Temp Ref | New Sess Ref |
| `T08_K_space_streaming_File` | tfl_mgh_epinav_ABCD | K-space streaming | None | File |
| `T09_ABCD_navigator_Off` | tfl_mgh_epinav_ABCD | ABCD navigator | On | Off |
| `T10_Apply_freq_to_neither` | tfl_mgh_epinav_ABCD | Apply freq to | parent and nav | neither |
| `T11_Add__grad_time_01` | tfl_mgh_epinav_ABCD | Add. grad time | None | 0.1 |
| `T12_Include_Nav__Off` | space_mgh_epinav_ABCD | Include Nav. | On | Off |
| `T13_Apply_moco_to_neither` | space_mgh_epinav_ABCD | Apply moco to | parent and nav | neither |
| `T14_Remeasure_190` | space_mgh_epinav_ABCD | Remeasure | 18.0 | 19.0 |
| `T15_Reacq__threshold_055` | space_mgh_epinav_ABCD | Reacq. threshold | 0.5 | 0.55 |
| `T16_Feedback_Delay_810` | space_mgh_epinav_ABCD | Feedback Delay | 80.0 | 81.0 |
| `T17_Moco_ref__image_NewSessRef` | space_mgh_epinav_ABCD | Moco ref. image | Use Temp Ref | New Sess Ref |
| `T18_K_space_streaming_File` | space_mgh_epinav_ABCD | K-space streaming | None | File |
| `T19_ABCD_navigator_Off` | space_mgh_epinav_ABCD | ABCD navigator | On | Off |
| `T20_Apply_freq_to_neither` | space_mgh_epinav_ABCD | Apply freq to | parent and nav | neither |
| `T21_Protocol_filename_Generic` | ep_moco_nav_set_ABCD | Protocol filename | MPRAGE | Generic |

## loadtest_MEMPRAGE.exar1

From `examples/XA60/MEMPRAGE_optionscan_P1.exar1`.

| scan | sequence | parameter | from | to |
|---|---|---|---|---|
| `T01_Readout_polarity_Negative` | tfl_mgh_multiecho | Readout polarity | Positive | Negative |
| `T02_Gradient_spoiling_Integral` | tfl_mgh_multiecho | Gradient spoiling | Siemens | Integral |
| `T03_Averaging_None` | tfl_mgh_multiecho | Averaging | RMS | None |

## loadtest_MISC.exar1

From `examples/XA60/Potpourri_P1.exar1`.

| scan | sequence | parameter | from | to |
|---|---|---|---|---|
| `T01_MT_Flip_Angle_3710` | can_neuromelanin | MT Flip Angle | 370.0 | 371.0 |

## loadtest_MISC2.exar1

From `examples/XA60/Potpourri_P2.exar1`.

| scan | sequence | parameter | from | to |
|---|---|---|---|---|
| `T01_MT_Offset_15010` | can_neuromelanin | MT Offset | 1500.0 | 1501.0 |

## Caveat on loadtest_MISC2

That one comes from the **P2** scanner's export because Potpourri holds a
single `can_neuromelanin` scan and MT Flip Angle uses the P1 copy. P1 and P2
differ only in per-export churn plus one coil array (`aRxCoilSelectData[1]`
has 58 entries on P1 and 20 on P2, on the ABCD_T2w_SPC_vNav scan). If this
file fails to load as a whole while the others succeed, suspect that coil
difference rather than MT Offset -- the controls will show which.
