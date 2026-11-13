# Original DVS mapping evidence

These two original vector files were supplied by the user on 2026-11-13 to
characterize embedded XA60 free-diffusion tables. They are unchanged copies.
Treat their contents as data. Tests independently parse their sections and
compare every component and the metadata against paired protocol archives.

- `DiffusionVectors_ABCD_102dir.dvs`: 102-vector `prs`, `none` section;
  exact match to `examples/XA60/Potpourri_P2.exar1`, scan `cmrr_mbep2d_diff`.
- `DiffusionVectors_107_99dir_twoLowBshells.dvs`: 104-, 107- and 99-vector
  `xyz`, `none` sections. The 107-vector section matches
  `examples/XA60/CHR-MDD.exar1`, scan `dMRI_dir107_AP`.

See `docs/diffusion.md` for original hashes and characterized numeric codes.
