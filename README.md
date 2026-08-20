# siemens-protocol

Turn a Siemens MR protocol PDF — the human-readable export of a full exam
protocol — into hierarchical JSON describing every scan and every parameter.

The point is to make protocol rebuilds after a software upgrade faster and
less error-prone, and to leave a machine-readable record that can be diffed
across software versions.

See [Design.md](Design.md) for the design this implements.

## Install

```sh
python -m venv .venv
.venv/bin/pip install -e .
```

The OCR fallback additionally needs the `tesseract` binary on `PATH`
(`brew install tesseract`). Everything else is pure Python.

## Use

```sh
siemens-protocol parse examples/XA60/R01StressDynXA60.pdf
siemens-protocol parse examples/ --out parsed/          # batch a directory
siemens-protocol versions                               # list version profiles
```

| Option | Meaning |
| --- | --- |
| `--out PATH` | Write JSON here. Alongside the input as `.json` by default; a directory in batch mode. |
| `--version {auto,VE11C,XA60}` | Force a version profile. Default `auto`. |
| `--ocr {auto,always,never}` | Control the OCR fallback. Default `auto`. |
| `--dpi N` | Rasterization DPI for OCR pages. Default 300. |
| `--no-flatten` | Omit the flattened per-scan view (included by default). |
| `--emit-debug PATH` | Dump per-span geometry for tuning a new version. |
| `--stdout` | Write JSON to stdout instead of a file (single file only). |

## Output

```json
{
  "source_file": "examples/XA60/R01StressDynXA60.pdf",
  "software_version": "XA60",
  "detection": { "method": "header-string", "confidence": "high" },
  "scanner": "SIEMENS MAGNETOM 3.0T XR Numaris/X VA60A-0D4N",
  "page_count": 57,
  "scans": [
    {
      "index": 2,
      "name": "T1_MEMPRAGE_64ch",
      "path": "\\\\Research\\Investigators\\Belleau\\R01StressDyn\\T1_MEMPRAGE_64ch",
      "header": {
        "ta": "6:02 min",
        "coil_selection": "Manual",
        "voxel_size_mm": "1.0×1.0×1.0",
        "pat": "2",
        "rel_snr": "1.00",
        "sequence": "tfl_me"
      },
      "sections": {
        "Routine": { "Slab Group": "1", "Slices per Slab": "176" },
        "Contrast - Common": { "TR": "2530.0 ms", "TI": "1100 ms" }
      },
      "flat": {
        "TR": {
          "value": "2530.0 ms",
          "sections": ["Routine", "Contrast - Common", "Geometry - Common"],
          "conflict": false
        }
      },
      "pages": [7, 8, 9]
    }
  ]
}
```

Values are kept as raw strings. Splitting value from unit is deliberately left
to a later normalization layer, so the capture stays faithful to the printout.

### Parameters printed in several sections

Siemens prints many parameters more than once per scan — TR, FoV and slice
thickness are the usual suspects. `sections` keeps every occurrence under the
section it was printed in, so no reading is dropped.

`flat` then collapses each key to one entry and records where it came from:

* occurrences agree → `conflict: false` and the shared `value`;
* occurrences disagree → `conflict: true` and a `values` map of section to
  value, so the discrepancy is visible instead of one reading winning at
  random.

The CLI reports the conflict count per file. Those are worth reading before a
rebuild.

### Keys that repeat inside one section

A scan with three slice groups prints `Slice Group` three times, each followed
by its own indented `Slices` and `Distance Factor`. Dropping the repeats would
lose real readings, so second and later occurrences are suffixed positionally:

```json
{ "Slice Group": "1", "Slices": "5",
  "Slice Group #2": "2", "Slices #2": "1" }
```

## How it works

PDF in, JSON out, in five stages (`pipeline.py`):

1. **Load and inventory** (`extract/native.py`) — open with PyMuPDF, pull
   spans with geometry and font, compute a printable-character ratio per page.
2. **Text acquisition** — keep the native spans, or, for a page whose ratio is
   too low, rasterize and OCR it (`extract/ocr.py`). Both produce the same
   `Span` type, so nothing downstream knows or cares which path a page took.
3. **Layout reconstruction** (`layout/`) — split each page into its two
   columns, group spans into rows, pair labels with values, detect section
   titles, and emit an ordered `(section, key, value)` stream.
4. **Scan splitting** (`split.py`) — find the bordered header box that opens
   each scan and cut the page stream on it.
5. **Assembly** (`model.py`, `flatten.py`) — build the scans, attach header
   metadata, compute the flattened view, serialize.

### Things that are easy to get wrong

These are the parts worth knowing about before changing a threshold.

**Header-box detection anchors on the summary line, not the path.** Read as
one wide line, an ordinary body row can look path-shaped — `1st Signal/Mode`
next to `None` reads as a slash-separated path. A candidate is accepted only
when the row carries at least two of the profile's header fields, which no
parameter row does.

**A long path wraps mid-word.** The formatter breaks
`.../slice_positioning-Angle to ACPC li` + `ne` with no separator, so the box
lines are concatenated without one. Words *within* a line are still
space-joined, which is what OCR needs.

**Rows group by vertical overlap, not by matching top edges.** An OCR'd hyphen
sits mid-line, so its box starts several points below the letters beside it;
a top-edge test splits `Contrast - Common` into two rows.

**Wrapped labels and wrapped values are merged by row pitch.** A continuation
line shares a table cell with the line above and is set tighter than the
column's normal pitch, which is what distinguishes
`Start measurement without further` + `preparation` from a new entry.

**Section titles are found by outdent, not by boldness.** Titles hang about
2.4pt left of the parameter labels. That offset is measured against rows that
certainly are parameters — the ones with a value beside them — so a column
containing no title cannot produce a false one. Font weight and the 10pt/8pt
size step are used as well, but only on native pages: OCR supplies neither,
and its row gaps scatter too widely to separate anything.

## Version flexibility

Everything version-specific lives in a `VersionProfile` (`profiles/`), which
declares only what differs: how to recognize the release, the grammar of the
header summary line, any header field recovered from a parameter instead, and
layout thresholds worth nudging. Profiles self-register; auto-detection scores
each against the first pages and `--version` always overrides.

The two current releases differ mainly in that header grammar:

```
VE11C  TA: 6:02 PM: REF Voxel size: 1.0×1.0×1.0 mmPAT: 2 Rel. SNR: 1.00 : tfl_me
XA60   TA: 6:02 min Coil Selection: Manual Voxel Size: 1.0×1.0×1.0 mm³ Acc:: 2 Rel. SNR: 1.00
```

Note `mmPAT:` with no space and `Acc::` with two colons. Rather than one
brittle regex per release, a profile lists its field labels in order and the
parser takes the text between each label and the next — spacing quirks stop
mattering. XA60 omits the sequence binary from the box, so its profile
recovers it from the `Sequence Name` parameter via `param_fallbacks`.

### Adding a release

1. Copy `profiles/xa60.py`, give it a name, `require`/`reject` patterns and
   its `header_labels`.
2. Import it in `profiles/__init__.py`.
3. Drop example PDFs in `examples/<VERSION>/` — the folder name is the
   ground-truth label the tests use.
4. Run `siemens-protocol parse FILE --emit-debug geometry.json` and check the
   reported `value_x`, `row_pitch` and column bounds against the file. Adjust
   `LayoutConfig` on the profile only if they are off.

Core extraction, layout, splitting and output stay untouched.

## Tests

```sh
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest
```

The suite checks that auto-detection matches every example's folder name, that
scan counts and names match hand-checked expectations, that known values —
including ones printed in several sections — come through exactly, that
conflict detection fires on a constructed disagreement, and that forced OCR
recovers the same section structure as native text on the same page.

Two are worth calling out:

* **Token conservation** (`test_pipeline.py`) asserts that every body token on
  every page reappears in some key, value or section title. A mis-tuned
  threshold that drops readings cannot pass it quietly.
* **Golden snapshots** (`tests/golden/`) make a layout or profile change show
  up as a reviewable diff. Regenerate deliberately:

  ```sh
  SIEMENS_PROTOCOL_REGEN=1 .venv/bin/python -m pytest tests/test_golden.py
  ```

  Snapshots omit the flattened view, which is a pure function of `sections`
  and is tested directly, so the diffs stay about what changed.

## Note on OCR

The design anticipated that XA60 exports render in a scrambled CID font and
would need OCR throughout. The seven example files here — both releases,
header boxes included — carry a clean native text layer, so none of them
takes the OCR path, and all values are exact. The fallback is built, tested
and wired to the printable-ratio check, and `--ocr always` exercises it; it
simply is not needed by these files. Expect degraded fidelity when it does
fire: 8pt raster text mis-reads characters (`Auto`→`Auio`) and loses spacing
(`A >> P`→`A>>P`), though section and scan structure survive intact.

## Future work

* A numeric normalization layer splitting value and unit (`{"value": 2530.0,
  "unit": "ms"}`), kept separate from the raw string capture.
* A `diff` command comparing two parsed protocols scan by scan, ignoring
  cosmetic relabeling between versions (`Dist. factor` vs `Distance Factor`,
  `PAT` vs `Acc`) — the example tree has matched pairs to build it against.
* Per-version fixtures and profiles as new releases arrive.
