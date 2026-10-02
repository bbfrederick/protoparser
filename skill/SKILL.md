---
name: siemens-protocol
description: Inspect, query and compare Siemens MR protocol PDF and exar1 exports, discover sequence parameters, and review characterized XA60 archive edits. Use for protocol inventories, parameter searches, comparisons, assembly and scanner-return checks; preserve internal calculated variables and distinguish offline validation from scanner evidence.
---

# Siemens protocol tools

The project aims to inspect acquisition order and copy links, search and compare
scans across PDF and `.exar1` exports, discover parameter names, and write
scanner-usable archives by copying protocols or combining donor scans with
characterized UI edits. The CLI is `spt`; Python callers import
`siemens_protocol`. See the [README](../README.md) for usage and current limits.

PDF profiles support VB17A, VE11C, XA30 and XA60. XA60 is the supported editing
target; older archive support needs representative fixtures. Mapping coverage,
sequence bounds and dependencies are incomplete. The new writer's control/edit
trial has no recorded hardware result yet.

**ASCCONV is authoritative.** PDF and Preview provide UI mapping evidence;
Preview may be regenerated. Edit only characterized representations of UI
controls through the supported writer. Preserve calculated and unmapped fields,
and refuse geometry edits requiring calculated slice-position writes. A glossary
mapping flag or offline validation pass does not establish scanner acceptance.

## When to use this

* The user hands over a Siemens protocol PDF and asks what is in it.
* They provide an `.exar1` archive and need hierarchy, order, copy links,
  parameter searches, comparisons, or review of an edited archive.
* They are rebuilding a protocol after a scanner software upgrade and want the
  old and new exports compared.
* They want a specific parameter (TR, TE, FoV, slice thickness, PAT/Acc) read
  out of a printout, or checked for consistency across a protocol.

This targets Siemens protocol exports, rather than DICOM headers or other
vendors' protocol formats.

## Queries, glossary and archive workflows

```sh
spt tree backup.exar1
spt query backup.exar1 protocol.pdf --sequence cmrr_mbep2d_bold \
    --where 'Suppress 16-bit DICOM = Off'
spt glossary --sequence cmrr_mbep2d_bold
spt glossary backup.exar1 --scan 're:REST' --raw
spt patch backup.exar1 --protocol Rest --scan bold --set 'tr=2 s' --manifest review.json
spt assemble base.exar1 plan.json --json
spt validate candidate.exar1 --program Rest --checklist --json > observations.json
spt roundtrip candidate.exar1 scanner-return.exar1 \
    --sent-program Rest --returned-program Rest \
    --observations observations.json --require-runnable --json
```

The patch and assembly examples review requests without publishing an archive;
`--out` writes a new file. Use strict `patch`/`assemble` requests for user edits.
An undecidable selection or unsupported edit refuses the batch. Raw ASCCONV
keys are searchable, not supported edit names. Copying preserves donor problems.

Glossary names in brackets are canonical query names, using lowercase words
joined with underscores. File-free lookup describes known sequence controls;
file-backed lookup checks observed variables and mapping scope. `modifiable`
does not fully reflect release, conversion or calculated-geometry restrictions;
the writer must review a concrete request. Older-release annotations can inherit
XA60 mapping information without enabling older-release writing.

Record observed console statuses as `runnable`, `greyed_out` or `not_tested`,
preserving the checklist's scan identities and hashes. Compare the exact submitted
archive with its initial re-export. Keep actual acquisition outcomes separate;
`scanner_confirmed` does not certify acquisition success or image quality. See
the [scanner trial guide](../docs/scanner_trial/README.md) for the procedure.

## Running it

```sh
spt parse PROTOCOL.pdf --out protocol.json
spt parse DIR/ --out parsed/     # every PDF in a directory
spt list PROTOCOL.pdf            # scans, sequences, times, total
```

Useful options:

* `--release {auto,VB17A,VE11C,XA30,XA60}` — force a profile when auto-detection is
  wrong or the file is from an unsupported release. Default `auto`.
* `--ocr {auto,always,never}` — the OCR fallback for exports without a usable
  text layer. Default `auto`, which OCRs only pages that need it.
* `--no-flatten` — drop the flattened view for a smaller file.
* `--emit-debug PATH` — per-span geometry, for when a new release parses badly.

The command prints a one-line summary per file to stderr — version, scan
count, page count, and the number of cross-section conflicts.

## Reading the output

```json
{
  "software_version": "XA60",
  "scans": [{
    "index": 2,
    "name": "T1_MEMPRAGE_64ch",
    "header": { "ta": "6:02 min", "voxel_size_mm": "1.0×1.0×1.0",
                "pat": "2", "rel_snr": "1.00", "sequence": "tfl_me" },
    // spectroscopy scans carry "voi_mm" (volume of interest) in place of
    // "voxel_size_mm"; the two are deliberately distinct fields
    "sections": { "Contrast - Common": { "TR": "2530.0 ms", "TI": "1100 ms" } },
    "flat": { "TR": { "value": "2530.0 ms",
                      "sections": ["Routine", "Contrast - Common"],
                      "conflict": false } },
    "pages": [7, 8, 9]
  }]
}
```

* `sections` is the faithful hierarchy — every occurrence stays under the
  section it was printed in. Use it when the section matters.
* `flat` is one entry per key. Use it to look a parameter up quickly.
* `conflict: true` means the same key was printed with different values in
  different sections; the per-section `values` are kept. **Surface these** —
  they are usually the interesting finding before a rebuild.
* A key suffixed `#2`, `#3` is a repeat within one section, such as the second
  and third slice group. It is a real reading, not a duplicate to discard.
* Values are raw strings, units included (`"2530.0 ms"`). Parse them yourself
  if arithmetic is needed.

## Checking against preferred values

```sh
spt check protocol.pdf          # exit 1 if anything deviates
spt check DIR/ --quiet          # every PDF beneath a directory
spt check protocol.pdf --json
```

Reports parameters that depart from a site policy, with the reason for each
preference. `!` is an error, `?` a warning. A rule only fires where its
parameter is present, so silence means the setting was either correct or not
applicable — not that it went unchecked. The trailing count says how many
readings were actually examined.

Use this alongside `diff` before a protocol rebuild: the diff says what moved
between versions, the check says what is wrong regardless of version.

To add a preference, write a rule in a JSON policy file and pass
`--policy-dir`. See the README for the fields. One rule covers every release,
because parameters match on canonical name.

## Comparing protocols and scans

```sh
spt diff old.pdf new.pdf                       # whole protocol
spt diff old.pdf new.pdf --scan T1_MEMPRAGE    # one scan, both files
spt diff protocol.pdf --left-scan AP --right-scan PA   # two scans, one file
spt diff old.pdf new.pdf \
    --left-scan rfMRI_REST_AP --right-scan rfMRI_REST1_ME_AP        # renamed counterpart
```

Either input may be a PDF, JSON produced by `parse`, or an `.exar1` archive.
Archive-to-archive comparisons include mapped and remaining ASCCONV readings;
PDF comparisons cannot cover internal fields or copy links. Use `roundtrip` for
scanner-return execution/link/add-in preservation checks. Add `--json` for a
machine-readable comparison. Exit status is `1`
when a substantive difference was found, `0` when none was.

Report markers: `~` changed, `-` only on the left, `+` only on the right, and
`R`/`c`/`f` for relabeled, recased and reformatted. The first three are real
changes and are listed in full; the last three are cosmetic and are summarized
unless you pass `--show-cosmetic`.

**Report the substantive differences; do not present the cosmetic ones as
findings.** Siemens recapitalizes and re-abbreviates freely between releases —
VE11C's `Dist. factor` is XA60's `Distance Factor`, its `Single shot` is
`Single Shot` — and the tool already separates that churn out for you.

Two things to carry into your summary:

* A relabeled key whose value *also* changed is substantive and is shown with
  both spellings (`Distortion Corr. -> Distortion Correction: Off | 3D`). Do
  not dismiss it as a rename.
* An add plus a remove may be a rename the tool refuses to guess at. Say so
  rather than reporting a parameter as lost. Known renames are already
  resolved through per-release vocabularies (`PAT mode` ↔ `Acceleration
  Mode`); what remains unresolved is either a genuine change or a mapping
  nobody has vetted yet. Some are *structural* rather than renames — XA60
  merged `Normalize` and `Prescan Normalize` into one parameter and split
  `Reference scan mode` into two — and those are deliberately left visible.
* The tool never matches on similarity alone: `Fat sat. mode` and `Fast Mode`
  look alike and are unrelated parameters.

Scans align using normalized names in acquisition order, so a renamed scan is flagged
`(scan renamed)` and an inserted or deleted one is listed separately rather
than knocking the rest out of step.

### Standard parameter names

`spt vocab list --canonical NAME` answers what each release calls
a given parameter, and `vocab list VERSION` shows a release's whole mapping
with the notes explaining each entry. Use it when the user asks what a
parameter is called in another software version.

Do **not** add mappings yourself on a hunch. `vocab suggest LEFT RIGHT`
proposes candidates with evidence but co-occurrence is weak on its own, and a
wrong entry hides a real difference. Anything added must pass
`vocab check --against LEFT RIGHT`, which catches a mapping that steals a
pairing that already worked.

## Caveats

* Version auto-detection is best effort; it reads the scanner string in the
  page header. If it picks wrong, pass `--release`.
* If a page has no usable text layer and tesseract is unavailable, the file
  still parses and the affected pages are listed in `warnings`. Check that
  field before trusting a result.
* OCR can lose scan names and alter section structure as well as mis-read
  characters in 8pt text. `ocr_pages` lists pages that took that path; review
  their readings before relying on them.
