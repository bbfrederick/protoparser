# Recovering diffusion vector tables

`spt diffusion FILE.exar1` inventories embedded free-diffusion vector tables
across the archive's live protocols. `--json` includes every recovered vector.
`--program` restricts the search to one protocol; `--scan` selects one scan by
its address, with `#2` etc. to distinguish repeated names. Run the inventory
first to see available addresses. This reads the archive without changing it.
The GUI exposes the same options under **Archive → Extract diffusion vectors**.

```sh
spt diffusion examples/XA60/CHR-MDD.exar1
spt diffusion examples/XA60/CHR-MDD.exar1 --scan dMRI_dir107_AP --json
spt diffusion examples/XA60/CHR-MDD.exar1 --scan dMRI_dir107_AP --out recovered.dvs
```

To write one table, supply `--out vectors.dvs`. Known coordinate and
normalization codes are decoded automatically, without guessing defaults.
For unknown codes only, supply `--coordinate-system` (`xyz` or `prs`) and/or
`--normalization` (`none`, `unity` or `maximum`) from the original DVS or other
verified knowledge of that scan. Explicit values that conflict with known
stored settings are refused. Existing output needs `--force`, and the source archive
is protected even with that flag. Output uses UTF-8 and CRLF line endings.

The API is also public:

```python
from siemens_protocol.exar import read, find_diffusion, extract_diffusion

archive = read("protocol.exar1")
for scan in find_diffusion(archive):
    print(scan.path, scan.table.comment, scan.table.coordinate_system)
    print(scan.table.vectors)

# For a selected low-level Protocol object:
# table = extract_diffusion(protocol)  # None when no table is embedded
# table.write_dvs("vectors.dvs")
```

## Mapping established from existing archives

The vectors are in the protocol's XProtocol ASCCONV block, not its Preview.
The prefix is `sDiffusion.sFreeDiffusionData.`:

| Stored suffix | Meaning recovered by the reader |
| --- | --- |
| `lDiffDirections` | Number of vectors in the embedded set |
| `asDiffDirVector.__attribute__.size` | Array length; checked against `lDiffDirections` |
| `asDiffDirVector[i].dSag`, `.dCor`, `.dTra` | Three components, exported in this order |
| `ulCoordinateSystem` | `1 = xyz`, `2 = prs`; other codes remain unknown |
| `ulNormalization` | `1 = none`; other codes remain unknown |
| `sComment.0`, `.1`, ... | Encoded bytes, often containing a source DVS filename |

The array is **sparse**. Missing components are zero, and an entirely absent
vector inside the declared array is `(0, 0, 0)`. For example, the three-vector
distortion table stores only `[0].dSag = 1`, `[1].dCor = 1`, `[2].dTra = 1`.
Counting assignments or starting at the first stored index would corrupt it.
Both counts must agree; invalid values and out-of-range indices are refused.

An initial survey of the existing `examples/` and `paramcheck/` archives found
68 populated tables and seven distinct free-diffusion payloads (including
metadata). Representative occurrences:

| Archive | Scan | Vectors | Coordinate code | Normalization code |
| --- | --- | ---: | ---: | ---: |
| `Potpourri_P2.exar1` | `cmrr_mbep2d_diff` | 102 | 2 | 1 |
| `CHR-MDD.exar1` | `dMRI_dir107_AP` | 107 | 1 | 1 |
| `Frederick_P2/Frederick_P2.exar1` | `dMRI_dir99_AP` | 99 | 1 | 1 |
| same | `dMRI_dir97_AP` | 97 | 1 | 1 |
| same | `dMRI_dir98_AP` | 98 | 1 | 1 |
| same | `CMRR_ABCD_dMRI_DistortMAP_AP` | 3 | 1 | 1 |
| `scanner_returns/Keto MRS 20240709_reexport.exar1` | `dMRI_dir64_MGH_AP` | 64 | 1 | 1 |

The encoded comments recover, among others,
`DiffusionVectors_ABCD_102dir.dvs` and
`DiffusionVectors_107_99dir_twoLowBshells.dvs`. The comment is user text, so it
is not guaranteed to be a filename. It is reported in JSON and retained as
hash comments in exported DVS files, never used as an output path.

The populated examples run CMRR/HCP customer sequences. The extraction uses
the standard stored fields independently of sequence vendor, but a populated
Siemens stock-sequence example has not yet been verified. It
does not infer a direction set for scans with empty tables (including standard
built-in diffusion modes), decode undocumented sequence-specific storage, or
derive vectors from the PDF. Inventory reports `sDiffusion.ulMode` as a raw
code, since a populated free table can remain stored while another mode is
selected. It reports stored data rather than certifying that the scan uses it.

These vectors are the prescribed table, not image-space bvecs, acquired
volume order or b-values. Export performs no coordinate rotation or vector
normalization, and does not reconstruct the byte-for-byte original DVS file
or any other direction sets that original file may have held.

## Original DVS verification

The user supplied the two originals on 2026-11-13. They are preserved under
`tests/fixtures/diffusion/`. Independent parsing of each original and comparison
with its archive establishes:

| Original section | Archive scan | Coordinate directive / code | Normalisation directive / code | Component differences |
| --- | --- | --- | --- | ---: |
| ABCD, 102 vectors | `Potpourri_P2` / `cmrr_mbep2d_diff` | `prs` / `2` | `none` / `1` | 0 of 306 |
| Lifespan, 107 vectors | `CHR-MDD` / `dMRI_dir107_AP` | `xyz` / `1` | `none` / `1` | 0 of 321 |

Both use the spelling `Normalisation`, which the exporter now emits. Vector
indices, sparse zeros, signs and magnitudes match exactly without axis
permutation, rotation or scaling. The second original contains 104-, 107- and
99-vector sections; only the selected 107-vector section is embedded in this
scan and exported. Regression tests compare automatic exports back to the
independently parsed original sections, including metadata and every component.

SHA-256 of the supplied originals:

- ABCD: `3512dee07a02107685c929d263eaaea3e662690715d8c3deb35a403deec378d8`
- Lifespan: `091c8e65cd36d6d1c9a67a5f4bfd992210a7feabf3f62d8a0df1c5186f670899`

No scanner visit was needed to establish these mappings. Codes for `unity`
and `maximum` normalization remain uncharacterized; do not infer them from
enum ordering. A matching original/archive pair using either setting would
establish those codes. Scanner re-import of recovered DVS files remains untested,
as does a populated table from a Siemens stock sequence.

The external DVS syntax references are the
[Siemens Neuro operator manual](https://content.doclib.siemens-healthineers.com/rest/v1/view?document-id=388426)
and an independently implemented
[DVS reader/writer](https://github.com/andersonwinkler/toolbox/blob/master/bin/multishell.py).
[dcm2niix](https://github.com/rordenlab/dcm2niix/blob/master/console/nii_dicom_batch.cpp)
also reads the same `dSag`, `dCor`, `dTra` fields in that component order.
These sources corroborate syntax and component extraction; the supplied
originals establish the numeric coordinate/normalization mappings above.
