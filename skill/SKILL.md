---
name: siemens-protocol
description: Parse a Siemens MR protocol PDF export into hierarchical JSON — every scan, every parameter, with cross-section conflicts flagged — and diff two protocols or two scans, separating real parameter changes from cosmetic relabeling. Use whenever the user provides a Siemens MR protocol printout (VE11C, XA30, XA60) and wants its parameters read, compared across software versions, or checked before a protocol rebuild.
---

# Siemens protocol PDF parser

Reads a Siemens MR protocol PDF — the human-readable export of a full exam
protocol — and returns hierarchical JSON: one entry per scan, each with its
header metadata, its sections of key/value parameters, and a flattened view
that flags parameters printed inconsistently across sections.

## When to use this

* The user hands over a Siemens protocol PDF and asks what is in it.
* They are rebuilding a protocol after a scanner software upgrade and want the
  old and new exports compared.
* They want a specific parameter (TR, TE, FoV, slice thickness, PAT/Acc) read
  out of a printout, or checked for consistency across a protocol.

Do **not** reach for this for DICOM headers or for other vendors' printouts;
it targets the Siemens PDF export specifically.

## Running it

```sh
siemens-protocol parse PROTOCOL.pdf --out protocol.json
siemens-protocol parse DIR/ --out parsed/     # every PDF in a directory
```

Useful options:

* `--version {auto,VE11C,XA30,XA60}` — force a profile when auto-detection is
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
siemens-protocol check protocol.pdf          # exit 1 if anything deviates
siemens-protocol check DIR/ --quiet          # every PDF beneath a directory
siemens-protocol check protocol.pdf --json
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
siemens-protocol diff old.pdf new.pdf                       # whole protocol
siemens-protocol diff old.pdf new.pdf --scan T1_MEMPRAGE    # one scan, both files
siemens-protocol diff protocol.pdf --scan AP --scan PA      # two scans, one file
```

Either input may be a PDF or JSON produced by `parse`, so parse once and diff
many times. Add `--json` for a machine-readable comparison. Exit status is `1`
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

Scans align by sequence, not by name, so a renamed scan is flagged
`(scan renamed)` and an inserted or deleted one is listed separately rather
than knocking the rest out of step.

### Standard parameter names

`siemens-protocol vocab list --canonical NAME` answers what each release calls
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
  page header. If it picks wrong, pass `--version`.
* If a page has no usable text layer and tesseract is unavailable, the file
  still parses and the affected pages are listed in `warnings`. Check that
  field before trusting a result.
* OCR'd pages recover scan and section structure reliably but mis-read
  individual characters in 8pt text. `ocr_pages` in the output lists any page
  that took that path — treat values from those pages as approximate.
