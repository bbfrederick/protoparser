# Siemens Protocol PDF Parser — Design

## Purpose

Read a Siemens MR protocol PDF (the human-readable export of a full exam
protocol) and turn it into a hierarchical JSON description of every scan and
every parameter. The goal is to make protocol rebuilds after a software
upgrade faster and less error-prone, and to give a machine-readable record
that can be diffed across software versions.

The tool must handle more than one Siemens software version. The first two
targets were VE11C and XA60; XA30 has since been added and validated the
design — it needed a discriminator, a vocabulary file and no core changes at
all. The design keeps the version-specific logic small and isolated so that
VE11E, XA31, and later releases can be added without reworking the core.

## Why this is not a plain text extraction job

The two target versions behave differently at the PDF level, and any new
version might behave like either one:

- VE11C exports carry a real text layer. PyMuPDF pulls the body out cleanly
  with exact values. Only the header box (protocol name and the `TA:` line) is
  in a broken subset font.
- XA60 exports render the entire page in a scrambled CID font. Native
  extraction returns garbage, so those pages need OCR.

The parser therefore runs native extraction first and falls back to OCR only
where native text is missing or unusable. Exact values are preserved wherever
a real text layer exists, and OCR is used only where the file forces it. The
header box is OCR'd in both versions because its font is broken in both.

## Pipeline

The flow is: PDF in, JSON out, in five stages.

1. Load and page inventory. Open with PyMuPDF. For each page, extract native
   spans (text, bounding box, font name, size, weight) and compute a
   printable-character ratio. A low ratio flags a page that needs OCR.

2. Text acquisition per page. For native-text pages, keep the PyMuPDF spans.
   For OCR pages, rasterize at a configurable DPI (300 by default) and run
   tesseract in a mode that returns word-level bounding boxes, then rebuild
   span objects with the same shape as the native ones. Downstream code does
   not care which source a span came from.

3. Layout reconstruction. Group spans into rows by y-position and split each
   page into its two columns by x-position. Within a column, pair a
   left-aligned label with its right-aligned value. Detect section titles.
   Emit an ordered stream of (section, key, value) records per page.

4. Scan splitting. Detect the bordered header box that opens each scan and use
   it to cut the record stream into scans. A scan runs across page breaks
   until the next header box appears.

5. Assembly and output. Build the scan objects, attach the header metadata,
   collect the sections, compute the flattened view, and serialize to JSON.

## Layout reconstruction details

These printouts are two independent columns of small key/value tables. The
reconstruction has to deal with a few specifics that are easy to get wrong, so
they are called out here and are the main thing to tune against real files.

Column split. Each page has a left and a right column of tables. Assign every
span to a column by its x-coordinate against a page-width midpoint, then read
each column top to bottom, left column first. A scan's sections concatenate in
that order across all of its pages.

Section titles. A section title is a line that starts a run of key/value rows
and has no value paired to its right (for example `Routine`,
`Contrast - Common`, `Sequence - Part 1`). In native pages it is also bold,
which is a useful confirming signal. The primary rule is structural (a
standalone left-column line followed by key/value rows) so that it still works
on OCR pages, where font weight is not reliable.

Wrapped labels. Some labels wrap onto a second line while their value stays
aligned with the first line (for example `Start measurement without further
preparation` with the value `Off`). Row grouping must merge a continuation
line that has no value of its own into the label above it.

Header box. The box at the top of each scan holds two lines: a UNC-style path
whose last component is the protocol name, and a summary line of the form
`TA: ... Voxel size: ... PAT: ... Rel. SNR: ... : <sequence>`. Parse these into
scan-level metadata (name, path, TA, voxel size, PAT/iPAT, relative SNR,
sequence binary name, coil selection). The box is bold and boxed, which
distinguishes it from ordinary section titles by position.

## Scan splitting

The header box is the boundary marker. Detecting it gives both the split points
and the per-scan metadata. Practically, a page either opens a new scan (it
starts with a header box) or continues the previous one. The splitter walks the
page-ordered record stream, opens a new scan whenever it sees a header box, and
appends everything after it to the current scan until the next box.

## Output format

The JSON is hierarchical: file, then detected version, then a list of scans.
Each scan carries its header metadata, its sections as an ordered map of
key/value pairs, a flattened view, and the source page numbers.

```json
{
  "source_file": "examples/XA60/R01StressDyn.pdf",
  "software_version": "XA60",
  "detection": { "method": "header-string", "confidence": "high" },
  "scans": [
    {
      "index": 0,
      "name": "T1_MEMPRAGE_64ch",
      "path": "\\Research\\Investigators\\Belleau\\R01StressDyn\\T1_MEMPRAGE_64ch",
      "header": {
        "ta": "6:02 min",
        "voxel_size_mm": "1.0x1.0x1.0",
        // spectroscopy prints "voi_mm" here instead, from a "VoI:" label
        "pat": "2",
        "rel_snr": "1.00",
        "sequence": "tfl_me",
        "coil_selection": "Manual"
      },
      "sections": {
        "Routine": { "Slab Group": "1", "Slices per Slab": "176", "TR": "2530.0 ms" },
        "Contrast - Common": { "TR": "2530.0 ms", "TI": "1100 ms" }
      },
      "flat": {
        "TR": {
          "value": "2530.0 ms",
          "sections": ["Routine", "Contrast - Common"],
          "conflict": false
        }
      },
      "pages": [7, 8, 9]
    }
  ]
}
```

### Handling parameters that appear in several sections

Many parameters are printed in more than one section of the same scan (TR, FoV,
slice thickness, and so on). The hierarchy keeps each occurrence under the
section it appeared in, so no reading is dropped.

The `flat` view then collapses each key to a single entry and records which
sections it came from. When every occurrence agrees, `conflict` is false and
the shared value is stored. When occurrences disagree, `conflict` is true and
the per-section values are kept so the discrepancy is visible instead of one
value being chosen at random. This makes cross-section inconsistencies easy to
find, which is exactly the kind of thing worth catching before a rebuild.

Values are kept as raw strings by default (`"2530.0 ms"`). A normalized numeric
layer that splits value and unit (`{"value": 2530.0, "unit": "ms"}`) is a
reasonable later addition and is listed under future work.

## Version flexibility

Everything version-specific lives in a small profile object. A profile declares
only what differs between releases:

- how to recognize the version from a page (header string patterns, and whether
  native body text is present),
- where the version and scanner strings sit,
- header-box geometry and the summary-line format,
- whether pages are expected to be native-text or OCR-only,
- any column or row thresholds that need adjusting for that release's layout.

Profiles register themselves in a small registry. Auto-detection tries each
profile's recognizer against the first page or two and picks the best match,
with `--version` available to force a choice. Adding a new version means
writing a profile and, if its layout differs, adjusting a threshold or two. The
core extraction, layout, splitting, and output code stays shared.

Detection signals for the current three:

- XA60: the header contains `Numaris/X` and a `VA60` build string.
- XA30: the header contains `Numaris/X` and a `VA30` build string.
- VE11C: the header names the scanner (`Prisma`) without the Numaris/X build
  string, and the page body has a native Helvetica text layer.

The build string must be matched exactly, not as `VA__`. A pattern loose
enough to cover a sibling release makes that release detect as this one at
high confidence rather than ambiguously — the failure is silent, because
Numaris/X releases share a header grammar and parse fine under the wrong
profile while reporting the wrong version and selecting the wrong vocabulary.

In practice every example of every release carries a usable native text layer,
so the OCR expectation below has never fired; see the note in `README.md`.

Treat detection as best-effort and always allow the override. The examples
folder layout below also gives the test harness a ground-truth label per file.

## Proposed package layout

```
<tool-home>/
  Design.md
  README.md
  pyproject.toml
  src/siemens_protocol/
    __init__.py
    cli.py              # argument parsing, batch mode, entry point
    pipeline.py         # orchestrates the five stages
    model.py            # Protocol / Scan / Section dataclasses + JSON
    flatten.py          # flattened view and conflict detection
    debug.py            # --emit-debug span/geometry dumps
    extract/
      __init__.py
      spans.py          # Span dataclass and page model
      native.py         # PyMuPDF span extraction + printable-ratio check
      ocr.py            # rasterize + tesseract -> spans
    layout/
      __init__.py
      columns.py        # column assignment by x
      rows.py           # row grouping by y, wrapped-label merge
      sections.py       # section-title detection, key/value pairing
    split.py            # header-box detection and scan boundaries
    profiles/
      __init__.py
      base.py           # VersionProfile base class + registry
      numaris_x.py      # header grammar shared by XA30 and XA60
      ve11c.py
      xa30.py
      xa60.py
  examples/
    VE11C/*.pdf
    XA30/*.pdf
    XA60/*.pdf
  tests/
    test_*.py
  skill/
    SKILL.md            # lets Claude use the tool as a skill
```

## Command line interface

```
siemens-protocol parse FILE.pdf [options]
siemens-protocol parse DIR/     [options]   # batch over a directory
siemens-protocol list  FILE.pdf [options]   # scan inventory with total TA

--out PATH            write JSON here (default: alongside input, .json)
--version {auto,VE11C,XA30,XA60}  force a version profile (default: auto)
--ocr {auto,always,never}     control OCR fallback (default: auto)
--dpi N               rasterization DPI for OCR pages (default: 300)
--flatten             include the flattened per-scan view (on by default)
--emit-debug PATH     dump per-span geometry for tuning a new version
```

Batch mode walks a directory, parses each PDF, and writes one JSON per file,
which is what the `examples/` tree is set up for.

## Dependencies

- PyMuPDF for native extraction and rasterization.
- pytesseract plus the tesseract binary for the OCR path (tesseract already
  installed).
- Pillow for image handling on the OCR path.

## Testing

The example files live under `examples/VERSIONNUMBER/`, for instance
`examples/VE11C/`, `examples/XA30/` and `examples/XA60/`. The parent folder name is the expected
software version for each file, which gives the test harness a ground-truth
label for free and lets it check auto-detection at the same time.

Suggested checks:

- Version auto-detection matches the folder name for every example.
- Scan count and scan names match a small hand-checked expectation per file.
- A handful of known parameter values per scan match exactly, including at
  least one value that appears in multiple sections, to confirm both the
  hierarchy and the flattened view.
- Conflict detection fires on a constructed case where two sections disagree.
- Golden JSON snapshots per example file, regenerated deliberately, so layout
  or profile changes surface as reviewable diffs.

Use full, uncropped exports for the golden snapshots. Cropped samples do not
exercise the scan splitter across a realistic run of scans.

## Skill packaging

A `skill/SKILL.md` describes when to reach for the tool (any Siemens protocol
PDF) and how to call the CLI, sitting next to the installed script. Once the
skill is in the Claude skills directory, handing Claude a protocol PDF triggers
the parser and returns the JSON.

## Diff command

Implemented; see `README.md`. Compares two protocols scan by scan, or two
individual scans within or across protocols, separating substantive parameter
differences from cosmetic relabeling between releases.

## Future work

- Numeric normalization layer that splits value and unit and coerces types,
  kept separate from the raw string capture.
- Per-version fixtures added as new software releases arrive, each with its own
  profile and golden snapshots.
