# Option-scan worklist

Parameters a controlled edit would pin, most-printed first.
`invariant` = never varies in the corpus, so nothing can be inferred.
`N candidates` = several keys track it identically; one edit separates them.


## Resolved by `CMRR_PARAMSCAN`

A 16-copy option scan on `cmrr_mbep2d_bold` (P0 baseline, P1-P15 one console
option each) closed three rows, and the way it closed them is worth keeping.

| parameter | key | how |
|---|---|---|
| Physio recording | `sWipMemBlock.alFree[31]` | P6-P9, all five choices; `Off` is the omitted assignment |
| Triggering scheme | `sWipMemBlock.alFree[27]` | P10-P12, all four choices; eliminates `adFree[0]` |
| Echoes in separate series | `sWipMemBlock.alFree[0]` bit 29 | not from this scan -- see below |

**The correlation harvest named the wrong key for one of them, and the
acceptance rule's refusal is what saved it.** `Echoes in separate series` was
listed here as one rejected candidate, `alFree[31]` -- which is really
*Physio recording*. The two labels are not related; they were moved by the
console in the *same* edit, so each correlates perfectly with the other's key
over the corpus. Correlation cannot separate parameters that were always
changed together, and no amount of corpus sweeping would have.

What separated them was a **differential controlled edit**: the one console
edit toggled ten Special-card options on each of two scans, and only one of
those scans also toggled this label -- and only that scan's flag word also
moved bit 29. Two scans sharing ten simultaneous toggles isolate the
eleventh. Where an option scan varies one thing against a fixed baseline,
this varies everything but one across a fixed pair, and it reaches parameters
no option scan in the corpus happens to cover.

So a `rejected` row here is not dead weight: it records a key the evidence
*pointed at*, and one of them was a coincidence of the corpus rather than a
fact about the format.

## Still open

`1st Signal/Mode` and `Acquisition Window` are now understood and still
unmapped, because both need a shape `Mapping` does not have. P1-P5 show the
mode writing **two** keys at once -- `sPhysioImaging.lSignal1` (a bit field:
None 1, ECG 2, Pulse 4, Ext 8, Resp 16, 2nd Ext 64) together with
`lMethod1` (None 1, Trigger 2) -- which is why one-key correlation listed
both and could choose neither. `Acquisition Window` then lands in
`sPhysioImaging.sPhysio<Kind>.lScanWindow`, where `<Kind>` is whichever
signal the mode selected, so its key is not fixed either. One label to
several keys, and a key chosen by another parameter's value, are both new;
neither should be bolted on without deciding what `Mapping` ought to grow.

The four other labels P1-P5 reveal (`Trigger Delay`, `Phases`,
`Trigger Pulse`, `Average Cycle`) print only once physio is switched on and
moved no assignment at all, so they are at sequence defaults and this scan is
no evidence about where they live.

| scans | parameter | state | candidate keys |
|---|---|---|---|
| 483 | Start measurements | invariant | `no candidate: nothing to observe` |
| 483 | Reset | invariant | `no candidate: nothing to observe` |
| 483 | Radial Sorting | invariant | `no candidate: nothing to observe` |
| 483 | Inline Movie | invariant | `no candidate: nothing to observe` |
| 483 | Disable auto transfer to PACS | invariant | `no candidate: nothing to observe` |
| 483 | Auto Store Images | invariant | `no candidate: nothing to observe` |
| 483 | Adjustment Strategy | invariant | `no candidate: nothing to observe` |
| 483 | ? Ref. Amplitude 1H | invariant | `no candidate: nothing to observe` |
| 444 | 1st Signal/Mode | needs a two-key mapping | `sPhysioImaging.lSignal1` **and** `lMethod1`, both |
| 413 | Set-n-Go Protocol | invariant | `no candidate: nothing to observe` |
| 413 | Inline Composing | invariant | `no candidate: nothing to observe` |
| 413 | Dynamic Mode | 7 candidates | `sKSpace.ucAsymmetricEchoMode, sKSpace.ucDynamicMode` |
| 412 | Raw Filter | 3 candidates | `sRawFilter.lSlope_256, sRawFilter.ucOn` |
| 373 | Acceleration Mode | 1 candidate, rejected | `sPat.ucPATMode` |
| 288 | RF Spoiling | 22 candidates | `sAdjData.uiAdjSliceBySliceTxRef, sAdjData.uiAdjSliceBySliceFrequency` |
| 272 | Slice Group | invariant | `no candidate: nothing to observe` |
| 222 | Correction Factor | invariant | `no candidate: nothing to observe` |
| 219 | Coil Elements | 2 candidates | `sCoilSelectMeas.aRxCoilSelectData[0].asList.__attribute__.size, sCoilSelectMeas.sCoilStringForConversion` |
| 195 | Hamming | 2 candidates | `sHammingFilter.ucOn, sHammingFilter.lWidthPercent` |
| 195 | Delay in TR | invariant | `no candidate: nothing to observe` |
| 185 | Image Filter | 2 candidates | `sImageFilter.lEdgeEnhance, sImageFilter.ucOn` |
| 181 | Subtract | 8 candidates | `sKSpace.ucAsymmetricEchoMode, sKSpace.ucDynamicMode` |
| 181 | StdDev | invariant | `no candidate: nothing to observe` |
| 181 | Radial MIP | 31 candidates | `sPhysioImaging.lRetroGatedImages, sPhysioImaging.lDummyHeartbeats` |
| 181 | MPR Tra | invariant | `no candidate: nothing to observe` |
| 181 | MPR Sag | invariant | `no candidate: nothing to observe` |
| 181 | MPR Cor | invariant | `no candidate: nothing to observe` |
| 181 | MIP Tra | 31 candidates | `sPhysioImaging.lRetroGatedImages, sPhysioImaging.lDummyHeartbeats` |
| 181 | MIP Time | invariant | `no candidate: nothing to observe` |
| 175 | Resp. Control | invariant | `no candidate: nothing to observe` |
| 164 | Slab Group | invariant | `no candidate: nothing to observe` |
| 156 | Slabs | 108 candidates | `sSliceArray.lSize, sSliceArray.lConc` |
| 156 | Physio recording | **mapped** | `sWipMemBlock.alFree[31]` (CMRR_PARAMSCAN P6-P9) |
| 156 | Min. prep scans | invariant | `no candidate: nothing to observe` |
| 156 | FFT scale factor | 2 candidates | `sWipMemBlock.alFree[27], sWipMemBlock.adFree[0]` |
| 156 | Delay before PC scans | invariant | `no candidate: nothing to observe` |
| 153 | Dark Blood | invariant | `no candidate: nothing to observe` |
| 134 | Threshold | invariant | `no candidate: nothing to observe` |
| 134 | Spatial Filter | invariant | `no candidate: nothing to observe` |
| 134 | Meas[2] | invariant | `no candidate: nothing to observe` |
| 134 | Meas[1] | invariant | `no candidate: nothing to observe` |
| 134 | Log Signals | invariant | `no candidate: nothing to observe` |
| 134 | Ignore Meas. at Start | invariant | `no candidate: nothing to observe` |
| 134 | Ignore After Transition | invariant | `no candidate: nothing to observe` |
| 126 | Online multi-band recon. | invariant | `no candidate: nothing to observe` |
| 126 | Min. prep scans SB | invariant | `no candidate: nothing to observe` |
| 115 | Triggering scheme | **mapped** | `sWipMemBlock.alFree[27]` (CMRR_PARAMSCAN P10-P12) |
| 113 | Reordering | 340 candidates | `tdefaultEVAProt, sAdjData.uiAdjSliceBySliceTxRef` |
| 108 | Elliptical Scanning | 13 candidates | `ucSequenceType, ulOrganUnderExamination` |
| 108 | Acceleration Factor 3D | 11 candidates | `lTOM, sKSpace.dAngioDynCentralRegionA` |
| 107 | > S | invariant | `no candidate: nothing to observe` |
| 105 | Refocus flip angle | 223 candidates | `lScanRegionPosTra, adFlipAngleDegree[0]` |
| 95 | F | 1 candidate, rejected | `sAAInitialOffset.SliceInformation.sPosition.dTra` |
| 85 | Allowed Delay | 1 candidate, rejected | `lMeasPause` |
| 75 | Incr. Gradient Spoiling | invariant | `no candidate: nothing to observe` |
| 71 | Phase Correction | 2 candidates | `ucSequenceType, sFastImaging.lEPIFactor` |
| 70 | Save Uncombined | invariant | `no candidate: nothing to observe` |
| 68 | Grad. rev. fat suppr. | 2 candidates | `sFastImaging.ucFreeEchoSpacing, sWipMemBlock.alFree[25]` |
| 65 | VoI R >> L | 1 candidate, rejected | `sSpecPara.sVoI.dPhaseFOV` |
| 65 | VoI A >> P | 1 candidate, rejected | `sSpecPara.sVoI.dPhaseFOV` |
| 56 | Trace Weighted Images | 1 candidate, rejected | `ucEnableIntro` |
| 56 | Tensor | 2 candidates | `sPrepPulses.lFatWaterContrast, sPat.ucTPatAverageAllFrames` |
| 56 | Invert Gray Scale | invariant | `no candidate: nothing to observe` |
| 56 | FA Maps | 2 candidates | `sPrepPulses.lFatWaterContrast, sPat.ucTPatAverageAllFrames` |
| 56 | Exponential ADC Maps | invariant | `no candidate: nothing to observe` |
| 56 | Diff. Weightings | 2 candidates | `sDiffusion.lDiffWeightings, sDiffusion.alAverages[1]` |
| 56 | Diff. Weighted Images | 115 candidates | `tProtocolName, lContrasts` |
| 56 | Calculated Image | invariant | `no candidate: nothing to observe` |
| 51 | Inversion pulse | invariant | `no candidate: nothing to observe` |
| 50 | Dynamic Field Correction | invariant | `no candidate: nothing to observe` |
| 49 | Trigger Delay | invariant | `no candidate: nothing to observe` |
| 47 | TX/RX Nucleus | 10 candidates | `sTXSPEC.asNucleusInfo[0].tNucleus, sRXSPEC.asNucleusInfo[0].tNucleus` |
| 47 | TX/RX Delta Frequency | invariant | `no candidate: nothing to observe` |
| 47 | TX Nucleus | 10 candidates | `sTXSPEC.asNucleusInfo[0].tNucleus, sRXSPEC.asNucleusInfo[0].tNucleus` |
| 47 | TX Delta Frequency | invariant | `no candidate: nothing to observe` |
| 46 | b-value | 325 candidates | `sDiffusion.sFreeDiffusionData.ulCoordinateSystem, sDiffusion.sFreeDiffusionData.asDiffDirVector.__attribute__.size` |
| 45 | Acoustic noise reduction | 17 candidates | `ucEnableIntro, sGRADSPEC.ucNoiseReduction` |
| 44 | LR Balancing | invariant | `no candidate: nothing to observe` |
| 40 | Saturation Mode | invariant | `no candidate: nothing to observe` |
| 40 | Meas[9] | invariant | `no candidate: nothing to observe` |
| 40 | Meas[8] | invariant | `no candidate: nothing to observe` |
| 40 | Meas[7] | invariant | `no candidate: nothing to observe` |
| 40 | Meas[6] | invariant | `no candidate: nothing to observe` |
| 40 | Meas[5] | invariant | `no candidate: nothing to observe` |
| 40 | Meas[4] | invariant | `no candidate: nothing to observe` |
| 40 | Meas[20] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[19] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[18] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[17] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[16] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[15] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[14] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[13] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[12] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[11] | 102 candidates | `ucEnableIntro, sSliceArray.asSlice[0].sNormal.dSag` |
| 40 | Meas[10] | invariant | `no candidate: nothing to observe` |
| 37 | Excite flip angle | invariant | `no candidate: nothing to observe` |
| 37 | Deep Resolve | invariant | `no candidate: nothing to observe` |
| 36 | Segments | 2 candidates | `sFastImaging.lTurboFactor, sFastImaging.lSegments` |
| 36 | Blood Suppression | invariant | `no candidate: nothing to observe` |
| 35 | Gain | invariant | `no candidate: nothing to observe` |
| 35 | Echo Train Duration | 6 candidates | `tProtocolName, dRefSNR` |
| 34 | Use expt. SAR calc | invariant | `no candidate: nothing to observe` |
| 34 | Shift RO frequency | invariant | `no candidate: nothing to observe` |
| 34 | Send ref. scans | invariant | `no candidate: nothing to observe` |
| 34 | Min. settling delay | invariant | `no candidate: nothing to observe` |
| 34 | Liver Registration | invariant | `no candidate: nothing to observe` |
| 34 | Gradient ramp time | 2 candidates | `sWipMemBlock.adFree[1], sWipMemBlock.alFree[34]` |
| 34 | Debug loop type | 1 candidate, rejected | `sWipMemBlock.alFree[4]` |
| 34 | Check RF clipping | invariant | `no candidate: nothing to observe` |
| 34 | Acq. window shift | invariant | `no candidate: nothing to observe` |
| 33 | Wash-out | invariant | `no candidate: nothing to observe` |
| 33 | Wash-in | invariant | `no candidate: nothing to observe` |
| 33 | TTP | invariant | `no candidate: nothing to observe` |
| 33 | PEI | invariant | `no candidate: nothing to observe` |
| 32 | Total Factor | invariant | `no candidate: nothing to observe` |
| 32 | SWI | invariant | `no candidate: nothing to observe` |
| 31 | Wait for TCP/IP Trigger | invariant | `no candidate: nothing to observe` |
| 31 | VERSE Factor | invariant | `no candidate: nothing to observe` |
| 31 | SMS ACS Dummy TRs | invariant | `no candidate: nothing to observe` |
| 31 | Reverse Phase Encoding | 145 candidates | `lScanRegionPosTra, sGRADSPEC.ucMode` |
| 31 | RF Clip | invariant | `no candidate: nothing to observe` |
| 31 | Kernel Size | invariant | `no candidate: nothing to observe` |
| 31 | Imaging Dummy TRs | 281 candidates | `sSliceArray.asSlice[0].dPhaseFOV, sSliceArray.asSlice[0].dReadoutFOV` |
| 31 | FLEET Dummy Pulses | invariant | `no candidate: nothing to observe` |
| 31 | ACS mode | invariant | `no candidate: nothing to observe` |
| 30 | VAPOR | 1 candidate, rejected | `sPrepPulses.lFatWaterContrast` |
| 30 | Spoiler max. amplitude | 2 candidates | `sWipMemBlock.adFree[1], sWipMemBlock.alFree[34]` |
| 30 | Spoiler duration | 357 candidates | `sTXSPEC.asNucleusInfo[0].lCoilSelectIndex, sRXSPEC.asNucleusInfo[0].lCoilSelectIndex` |
| 30 | Spoiler amp. ratio | 1 candidate, rejected | `sWipMemBlock.adFree[7]` |
| 30 | Invert SS grad. pol. | invariant | `no candidate: nothing to observe` |
| 29 | Water s. Delta Pos. | invariant | `no candidate: nothing to observe` |
| 29 | VAPOR suppr. | invariant | `no candidate: nothing to observe` |
| 29 | VAPOR flip angle | 3 candidates | `sCoilSelectMeas.aRxCoilSelectData[0].tCheckUUID, sCoilSelectMeas.aTxCoilSelectData[0].tCheckUUID` |
| 29 | VAPOR delay 8 | 357 candidates | `sTXSPEC.asNucleusInfo[0].lCoilSelectIndex, sRXSPEC.asNucleusInfo[0].lCoilSelectIndex` |
| 29 | VAPOR delay 7 | 1 candidate, rejected | `sWipMemBlock.alFree[8]` |
| 29 | VAPOR delay 6 | invariant | `no candidate: nothing to observe` |
| 29 | VAPOR delay 5 | invariant | `no candidate: nothing to observe` |
| 29 | VAPOR delay 4 | invariant | `no candidate: nothing to observe` |
| 29 | VAPOR delay 3 | invariant | `no candidate: nothing to observe` |
| 29 | VAPOR delay 2 | invariant | `no candidate: nothing to observe` |
| 29 | VAPOR delay 1 | invariant | `no candidate: nothing to observe` |
| 27 | Bandwidth 2 | 210 candidates | `lContrasts, ucAARegionMode` |
| 27 | Bandwidth 1 | 2 candidates | `sRXSPEC.alDwellTime[0], alTI[0]` |
| 26 | Refocus grad. factor | invariant | `no candidate: nothing to observe` |
| 26 | OVS delta frequency | invariant | `no candidate: nothing to observe` |
| 26 | Flow Compensation 4 | invariant | `no candidate: nothing to observe` |
| 26 | Flow Compensation 3 | invariant | `no candidate: nothing to observe` |
| 26 | Flow Compensation 2 | invariant | `no candidate: nothing to observe` |
| 26 | Flow Compensation 1 | invariant | `no candidate: nothing to observe` |
| 26 | Bandwidth 4 | invariant | `no candidate: nothing to observe` |
| 26 | Bandwidth 3 | invariant | `no candidate: nothing to observe` |
| 25 | OVS slab thickness | invariant | `no candidate: nothing to observe` |
| 25 | OVS slab pos. offset | invariant | `no candidate: nothing to observe` |
| 25 | OVS pulse duration | invariant | `no candidate: nothing to observe` |
| 25 | OVS flip angle SL | 2 candidates | `sWipMemBlock.alFree[36], sWipMemBlock.alFree[37]` |
| 25 | OVS flip angle RO | invariant | `no candidate: nothing to observe` |
| 25 | OVS flip angle PH | 2 candidates | `sWipMemBlock.alFree[36], sWipMemBlock.alFree[37]` |
| 25 | OVS HS pulse R | invariant | `no candidate: nothing to observe` |
| 25 | OVS HS pulse N | invariant | `no candidate: nothing to observe` |
| 24 | R | 14 candidates | `tSequenceFileName, tProtocolName` |
| 23 | Scan Res. R >> L | 1 candidate, rejected | `sKSpace.lBaseResolution` |
| 23 | Scan Res. A >> P | 1 candidate, rejected | `sKSpace.lPhaseEncodingLines` |
| 23 | Interpol. Res. R >> L | 1 candidate, rejected | `sSpecPara.lFinalMatrixSizeRead` |
| 23 | Interpol. Res. A >> P | 1 candidate, rejected | `sSpecPara.lFinalMatrixSizePhase` |
| 23 | FOV R >> L | 4 candidates | `sRXSPEC.alDwellTime[0], sSliceArray.asSlice[0].dPhaseFOV` |
| 23 | FOV A >> P | 4 candidates | `sRXSPEC.alDwellTime[0], sSliceArray.asSlice[0].dPhaseFOV` |
| 22 | TD | invariant | `no candidate: nothing to observe` |
| 21 | Resolve averages | 3 candidates | `sSpecPara.sVoI.dThickness, sSpecPara.sVoI.dPhaseFOV` |
| 21 | Readout trajectory | invariant | `no candidate: nothing to observe` |
| 21 | Gradient moment factor | 2 candidates | `sSliceArray.ucMode, sWipMemBlock.adFree[1]` |
| 19 | Inter-TE delay | invariant | `no candidate: nothing to observe` |
| 19 | Echoes in separate series | **mapped** | `sWipMemBlock.alFree[0]` bit 29 -- the candidate above was wrong |
| 18 | Optimization | 1 candidate, rejected | `lTOM` |
| 17 | Noise Masking | invariant | `no candidate: nothing to observe` |
| 17 | Flip Angle 2 | invariant | `no candidate: nothing to observe` |
| 17 | Flip Angle 1 | invariant | `no candidate: nothing to observe` |
| 16 | Trajectory | invariant | `no candidate: nothing to observe` |
| 15 | Water Suppr. BW | 1 candidate, rejected | `sSpecPara.lRFExcitationBandwidth` |
| 15 | Time to Center | 5 candidates | `lScanTimeSec, lTotalScanTimeSec` |
| 15 | Thickness F >> H | 3 candidates | `dOverallImageScaleFactor, sSliceArray.asSlice[0].dThickness` |
| 15 | CC Mode | invariant | `no candidate: nothing to observe` |
| 14 | Trigger Pulse | invariant | `no candidate: nothing to observe` |
| 14 | Phases | invariant | `no candidate: nothing to observe` |
| 14 | Phase Enc. Order | invariant | `no candidate: nothing to observe` |
| 14 | HS refoc. pulse R | invariant | `no candidate: nothing to observe` |
| 14 | HS refoc. pulse N | invariant | `no candidate: nothing to observe` |
| 14 | GOIA refoc. pulses | invariant | `no candidate: nothing to observe` |
| 14 | Define | 194 candidates | `ucSequenceType, ucReadOutMode` |
| 14 | Breast Application | invariant | `no candidate: nothing to observe` |
| 14 | Average Cycle | invariant | `no candidate: nothing to observe` |
| 14 | Acquisition Window | needs a signal-dependent key | `sPhysioImaging.sPhysio<Kind>.lScanWindow` |
| 12 | Red. EC Sensitivity | invariant | `no candidate: nothing to observe` |
| 12 | Forced min. TE1 | invariant | `no candidate: nothing to observe` |
| 12 | Echo Trains per Slice | 9 candidates | `lScanTimeSec, lTotalScanTimeSec` |
| 12 | 'RR' refoc. pulse | invariant | `no candidate: nothing to observe` |
| 11 | WARP | invariant | `no candidate: nothing to observe` |
| 11 | VoI fit factor | invariant | `no candidate: nothing to observe` |
| 11 | Type of fit | 1 candidate, rejected | `sWipMemBlock.alFree[1]` |
| 11 | Tau | invariant | `no candidate: nothing to observe` |
| 11 | Save plots to database | invariant | `no candidate: nothing to observe` |
| 11 | Refocus pulses | invariant | `no candidate: nothing to observe` |
| 11 | Number of echoes | 1 candidate, rejected | `sWipMemBlock.alFree[7]` |
| 11 | Multi-echo acquisition | invariant | `no candidate: nothing to observe` |
| 11 | Force spherical fit VoI | invariant | `no candidate: nothing to observe` |
| 11 | Fast Mode | invariant | `no candidate: nothing to observe` |
| 11 | Compensate T2 Decay | invariant | `no candidate: nothing to observe` |
| 11 | Bar thickness | invariant | `no candidate: nothing to observe` |
| 11 | Bar FoV | invariant | `no candidate: nothing to observe` |
| 10 | b-value 2 | 4 candidates | `sKSpace.lBaseResolution, sKSpace.lPhaseEncodingLines` |
| 10 | b-value 1 | invariant | `no candidate: nothing to observe` |
| 10 | Number of label freqs. | 2 candidates | `sWipMemBlock.alFree[4], sWipMemBlock.alFree[7]` |
| 10 | MEGA flip angle | invariant | `no candidate: nothing to observe` |
| 10 | Editing pulse freq. [1] | 2 candidates | `sWipMemBlock.adFree[2], sWipMemBlock.alFree[13]` |
| 10 | Editing pulse BW | 363 candidates | `sTXSPEC.asNucleusInfo[0].lCoilSelectIndex, sRXSPEC.asNucleusInfo[0].lCoilSelectIndex` |
| 10 | Averages 2 | invariant | `no candidate: nothing to observe` |
| 10 | Averages 1 | invariant | `no candidate: nothing to observe` |
| 10 | Asym. excit. pulse | invariant | `no candidate: nothing to observe` |
| 9 | TI 2 | 100 candidates | `tSequenceFileName, tProtocolName` |
| 9 | TI 1 | 100 candidates | `tSequenceFileName, tProtocolName` |
| 9 | Editing pulse freq. [2] | invariant | `no candidate: nothing to observe` |
| 9 | Acquisition Delay | 10 candidates | `sAdjData.uiAdjFreqConfirmSpec, sSpecPara.lPhaseCyclingType` |
| 8 | Vol. TR | invariant | `no candidate: nothing to observe` |
| 8 | Variable FA | invariant | `no candidate: nothing to observe` |
| 8 | VASO Pre-TI2 delay | invariant | `no candidate: nothing to observe` |
| 8 | VASO Pre-TI1 delay | invariant | `no candidate: nothing to observe` |
| 8 | VASO Pre-Inv delay | invariant | `no candidate: nothing to observe` |
| 8 | Trigger per shot | invariant | `no candidate: nothing to observe` |
| 8 | Slab Scale | invariant | `no candidate: nothing to observe` |
| 8 | Segmentation | invariant | `no candidate: nothing to observe` |
| 8 | Scan Res. F >> H | 1 candidate, rejected | `sKSpace.lPartitions` |
| 8 | SMS Factor | 242 candidates | `tProtocolName, ucEnableIntro` |
| 8 | Relax spoilers | invariant | `no candidate: nothing to observe` |
| 8 | Read polarity | invariant | `no candidate: nothing to observe` |
| 8 | Ramp sampling | invariant | `no candidate: nothing to observe` |
| 8 | RF time x BW | invariant | `no candidate: nothing to observe` |
| 8 | RF duration | invariant | `no candidate: nothing to observe` |
| 8 | PAT ref. FA | invariant | `no candidate: nothing to observe` |
| 8 | Noise image | invariant | `no candidate: nothing to observe` |
| 8 | Multi-echo spacing | invariant | `no candidate: nothing to observe` |
| 8 | Multi-echo Shots | invariant | `no candidate: nothing to observe` |
| 8 | Mosaic DICOMs | invariant | `no candidate: nothing to observe` |
| 8 | Modify Ice Config | invariant | `no candidate: nothing to observe` |
| 8 | Min. TE w/ PF | invariant | `no candidate: nothing to observe` |
| 8 | Min Flip Angle | 189 candidates | `ucEnableIntro, ucOneSeriesForAllMeas` |
| 8 | MT off-res. | invariant | `no candidate: nothing to observe` |
| 8 | MT flip angle | invariant | `no candidate: nothing to observe` |
| 8 | MT RF duration | invariant | `no candidate: nothing to observe` |
| 8 | MEGA water suppr. | 2 candidates | `sWipMemBlock.adFree[1], sWipMemBlock.alFree[34]` |
| 8 | Invert PE | invariant | `no candidate: nothing to observe` |
| 8 | Interpol. Res. F >> H | 6 candidates | `sKSpace.dSliceResolution, sSpecPara.lFinalMatrixSizeSlice` |
| 8 | GRAPPA Regularization | invariant | `no candidate: nothing to observe` |
| 8 | G. spoil dephasing[3] | invariant | `no candidate: nothing to observe` |
| 8 | G. spoil dephasing[2] | invariant | `no candidate: nothing to observe` |
| 8 | G. spoil dephasing[1] | invariant | `no candidate: nothing to observe` |
| 8 | G-factor map | invariant | `no candidate: nothing to observe` |
| 8 | Fat sat. FA | invariant | `no candidate: nothing to observe` |
| 8 | FOV F >> H | 2 candidates | `sSliceArray.asSlice[0].dThickness, sSpecPara.sVoI.dThickness` |
| 8 | Expert ramping | invariant | `no candidate: nothing to observe` |
| 8 | EPI ramp factor 2 | invariant | `no candidate: nothing to observe` |
| 8 | EPI ramp factor | invariant | `no candidate: nothing to observe` |
| 8 | ADC Noise Threshold | 146 candidates | `tProtocolName, lContrasts` |
| 7 | Fully Excited VoI | 45 candidates | `lAverages, ucSequenceType` |
| 6 | b-value >= | invariant | `no candidate: nothing to observe` |
| 6 | VAPOR WS | 1 candidate, rejected | `sWipMemBlock.alFree[13]` |
| 6 | Total TE | 365 candidates | `sTXSPEC.asNucleusInfo[0].lCoilSelectIndex, sRXSPEC.asNucleusInfo[0].lCoilSelectIndex` |
| 6 | TM | 172 candidates | `sGRADSPEC.ucMode, sRXSPEC.alDwellTime[0]` |
| 6 | Symm. MEGA timing | 363 candidates | `sTXSPEC.asNucleusInfo[0].lCoilSelectIndex, sRXSPEC.asNucleusInfo[0].lCoilSelectIndex` |
| 6 | Spinal Cord Navigator | invariant | `no candidate: nothing to observe` |
| 6 | Saturation Region | invariant | `no candidate: nothing to observe` |
| 6 | Sat. Delta Frequ. | 11 candidates | `alTE[0], aulServicePara[0]` |
| 6 | Refocusing duration | 365 candidates | `sTXSPEC.asNucleusInfo[0].lCoilSelectIndex, sRXSPEC.asNucleusInfo[0].lCoilSelectIndex` |
| 6 | Refocusing Flip angle | invariant | `no candidate: nothing to observe` |
| 6 | Readout Segments | invariant | `no candidate: nothing to observe` |
| 6 | Readout Partial Fourier | invariant | `no candidate: nothing to observe` |
| 6 | Reacquisition Mode | invariant | `no candidate: nothing to observe` |
| 6 | Ramp time | invariant | `no candidate: nothing to observe` |
| 6 | PRESS+4 | invariant | `no candidate: nothing to observe` |
| 6 | Mode: RFspoil | 9 candidates | `sKSpace.dSliceResolution, sWipMemBlock.alFree[4]` |
| 6 | Gradient Max. Amplitude | invariant | `no candidate: nothing to observe` |
| 6 | Grad duration (PE) | 1 candidate, rejected | `sWipMemBlock.alFree[7]` |
| 6 | GOIA/FOCI | 365 candidates | `sTXSPEC.asNucleusInfo[0].lCoilSelectIndex, sRXSPEC.asNucleusInfo[0].lCoilSelectIndex` |
| 6 | FOV Shift Factor | invariant | `no candidate: nothing to observe` |
| 6 | FOV Pos, Slice | invariant | `no candidate: nothing to observe` |
| 6 | FOV Pos, Read | invariant | `no candidate: nothing to observe` |
| 6 | FOV Pos, Phase | invariant | `no candidate: nothing to observe` |
| 6 | FA AutoCalib | invariant | `no candidate: nothing to observe` |
| 6 | Excitation duration | invariant | `no candidate: nothing to observe` |
| 6 | Excitation Flip angle | invariant | `no candidate: nothing to observe` |
| 6 | Ernst angle | 1 candidate, rejected | `alTR[0]` |
| 6 | Diffusion weighting | invariant | `no candidate: nothing to observe` |
| 6 | Debug Type | invariant | `no candidate: nothing to observe` |
| 6 | Calibration Type | 2 candidates | `sWipMemBlock.alFree[1], sWipMemBlock.alFree[2]` |
| 6 | Blip ramp time (SL) | invariant | `no candidate: nothing to observe` |
| 6 | Blip ramp time (PE) | invariant | `no candidate: nothing to observe` |
| 6 | AutoVOI | invariant | `no candidate: nothing to observe` |
| 6 | AutoShim | invariant | `no candidate: nothing to observe` |
| 6 | Advanced User | invariant | `no candidate: nothing to observe` |
| 6 | Accel. Mode | 78 candidates | `lScanTimeSec, lTotalScanTimeSec` |
| 5 | Spectral Suppr. | invariant | `no candidate: nothing to observe` |
| 5 | Ref. Scan Mode | 14 candidates | `tSequenceFileName, sGRADSPEC.ucMode` |
| 5 | NOE Type | 45 candidates | `tSequenceFileName, tProtocolName` |
| 5 | MapIt | invariant | `no candidate: nothing to observe` |
| 5 | Freq. Corr. Accumulation | invariant | `no candidate: nothing to observe` |
| 5 | Freeze Suppr. Tissue | 78 candidates | `lScanTimeSec, lTotalScanTimeSec` |
| 5 | Decoupling Type | 45 candidates | `tSequenceFileName, tProtocolName` |
| 5 | ? Ref. Amplitude 31P | invariant | `no candidate: nothing to observe` |
| 4 | WaterSupp Angle | invariant | `no candidate: nothing to observe` |
| 4 | T1 Estimated | invariant | `no candidate: nothing to observe` |
| 4 | Symmetric RF pulses | invariant | `no candidate: nothing to observe` |
| 4 | Spoiling scheme | invariant | `no candidate: nothing to observe` |
| 4 | Spoil duration (SP) | invariant | `no candidate: nothing to observe` |
| 4 | Rect excit. pulse | invariant | `no candidate: nothing to observe` |
| 4 | RF pulse duration | invariant | `no candidate: nothing to observe` |
| 4 | Prep. pulses | 12 candidates | `tProtocolName, alTR[0]` |
| 4 | PE Samp EPSI | 12 candidates | `tProtocolName, alTR[0]` |
| 4 | NOE Flip Angle | invariant | `no candidate: nothing to observe` |
| 4 | NOE Duration | invariant | `no candidate: nothing to observe` |
| 4 | NOE Count | 12 candidates | `lAverages, lScanTimeSec` |
| 4 | Mode: WatSupNav | 54 candidates | `tSequenceFileName, sPrepPulses.lFatWaterContrast` |
| 4 | Mode: MotionNav | 54 candidates | `tSequenceFileName, sPrepPulses.lFatWaterContrast` |
| 4 | Mode: BipolNav | invariant | `no candidate: nothing to observe` |
| 4 | EPSI. Ramp. Samp | invariant | `no candidate: nothing to observe` |
| 4 | EPSI. RO. Ramp time | invariant | `no candidate: nothing to observe` |
| 4 | EPSI. Num: Echo | 54 candidates | `tSequenceFileName, sPrepPulses.lFatWaterContrast` |
| 4 | EPSI. EchoSpace | 54 candidates | `tSequenceFileName, sPrepPulses.lFatWaterContrast` |
| 4 | Decoupling Flip Angle | invariant | `no candidate: nothing to observe` |
| 4 | Decoupling Duration | invariant | `no candidate: nothing to observe` |
| 4 | DC Total Duration | invariant | `no candidate: nothing to observe` |
| 4 | DC Pause Fract. | invariant | `no candidate: nothing to observe` |
