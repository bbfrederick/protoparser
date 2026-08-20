---
name: siemens-protocol
description: Parse a Siemens MR protocol PDF export into hierarchical JSON — every scan, every parameter, with cross-section conflicts flagged. Use whenever the user provides a Siemens MR protocol printout (VE11C, XA60) and wants its parameters read, compared across software versions, or checked before a protocol rebuild.
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

* `--version {auto,VE11C,XA60}` — force a profile when auto-detection is
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

## Comparing two protocols

There is no `diff` subcommand yet. Parse both files and compare the `flat`
views scan by scan, matching scans by `name`. Expect cosmetic relabeling
between releases — VE11C's `Dist. factor` is XA60's `Distance Factor`, and
`PAT` becomes `Acc` — so match on normalized key names before reporting a
difference, and report those renames separately from real parameter changes.

## Caveats

* Version auto-detection is best effort; it reads the scanner string in the
  page header. If it picks wrong, pass `--version`.
* If a page has no usable text layer and tesseract is unavailable, the file
  still parses and the affected pages are listed in `warnings`. Check that
  field before trusting a result.
* OCR'd pages recover scan and section structure reliably but mis-read
  individual characters in 8pt text. `ocr_pages` in the output lists any page
  that took that path — treat values from those pages as approximate.
