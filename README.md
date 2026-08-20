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

# check a protocol against preferred values
siemens-protocol check protocol.pdf

# compare two protocols scan by scan
siemens-protocol diff old.pdf new.pdf

# compare one scan across two protocols
siemens-protocol diff old.pdf new.pdf --scan T1_MEMPRAGE_64ch

# compare two scans within one protocol
siemens-protocol diff protocol.pdf --scan SpinEchoFieldMap_AP --scan SpinEchoFieldMap_PA
```

| Option | Meaning |
| --- | --- |
| `--out PATH` | Write JSON here. Alongside the input as `.json` by default; a directory in batch mode. |
| `--version {auto,VE11C,XA30,XA60}` | Force a version profile. Default `auto`. |
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

## Checking against preferred values

`check` reports parameters that depart from a site's preferences, which is the
other half of a rebuild: the diff says what moved, this says what is wrong.

```sh
siemens-protocol check protocol.pdf
siemens-protocol check examples/ --quiet          # every PDF beneath a directory
siemens-protocol check protocol.pdf --json
```

```
NOCICEPT_Ph2MRI515_Second.pdf (VE11C) against policy 'default'
  scan 15: dMRI_dir99_AP
    ! MB RF phase scramble = 'Off' [Sequence - Special] -- prefer 'On'
        Phase scrambling reduces peak RF amplitude in multiband excitation;
        leaving it off risks SAR limiting and slice leakage.
  22 readings checked, 2 errors, 0 warnings
```

Exit status is `1` when anything was found, `0` when nothing was; `!` marks an
error and `?` a warning, and `--warnings-ok` passes on warnings alone.

### Writing a policy

A policy is a JSON file of rules. Shipped ones live in
`src/siemens_protocol/policy/`; `--policy-dir DIR` searches your own first, and
`--policy` takes either a name or a path.

```json
{
  "name": "default",
  "rules": [
    { "parameter": "MB RF phase scramble", "section": "Sequence - Special",
      "equals": "On", "reason": "..." },
    { "parameter": "Excite pulse duration", "section": "Sequence - Special",
      "min": 3000, "unit": "us", "reason": "...", "severity": "warning" }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `parameter` | The parameter to check. Required. |
| `section` | Restrict to one section. Optional; otherwise checked wherever it appears. |
| `equals` / `one_of` / `not_equals` | Value preferences, compared case-insensitively. |
| `min` / `max` | Inclusive numeric bounds against the reading's leading number. |
| `unit` | Expected unit for a bound. A reading in another unit is reported, not compared. |
| `reason` | Why the preference exists. Shown in the report. |
| `severity` | `error` (default) or `warning`. |

Four things worth knowing:

- **A rule fires only where its parameter is present.** A localizer that never
  prints a multiband setting is not in violation of a multiband rule. Rules
  that matched nothing at all are listed separately, since a persistent one is
  usually stale.
- **One rule covers every release.** Parameters match on canonical name via the
  vocabularies, so a rule written as `PAT mode` also checks XA60's
  `Acceleration Mode`, and the report quotes whichever label that release
  actually printed.
- **Units are not assumed.** A bound of `3000 us` against a reading of `3 ms`
  is reported as a unit mismatch rather than silently passing.
- **A malformed rule fails at load.** A rule with no constraint, a non-numeric
  bound or an unknown severity is an error, not a rule that quietly never
  matches.

## Comparing protocols

`diff` answers the question a rebuild actually poses: what really changed, as
opposed to what Siemens merely renamed. It has two modes.

**Protocol against protocol.** Scans are aligned by *sequence*, not by name — a
protocol can print the same name twice (two field maps), and a release can
rename one scan while leaving its position alone. An inserted or deleted scan is
reported as such instead of shifting everything after it out of step.

**Scan against scan.** Give one file and two `--scan` names to compare two scans
within it, or two files and a `--scan` to compare the same scan across releases.
Scans are selected by name or by zero-based index. Comparing the two field maps
of one protocol is a good check that they differ only where they should:

```
$ siemens-protocol diff R01StressDyn.pdf --scan SpinEchoFieldMap_AP --scan SpinEchoFieldMap_PA
SpinEchoFieldMap_AP -> SpinEchoFieldMap_PA
  parameters
    ~ Invert RO/PE polarity: Off  |  On
```

| Option | Meaning |
| --- | --- |
| `--scan NAME` | Scan to compare, by name or index. Once for both sides, twice for left and right. |
| `--exact-keys` | Compare key spellings literally; do not match relabeled keys. |
| `--show-cosmetic` | List relabeled, recased and reformatted differences instead of counting them. |
| `--show-identical` | Include scans that have no differences. |
| `--json` | Emit the comparison as JSON. |
| `--out PATH` | Write the report to a file instead of stdout. |

Either input may be a PDF or JSON this tool wrote earlier, so a protocol can be
parsed once and compared many times. The exit status is `1` when any substantive
difference was found, `0` when none was, which makes it usable in a check.

### Standard parameter names

Case-folding and abbreviations only reach relabeling that is typographic.
Between releases Siemens also *reorganizes*: VE11C's `PAT mode` is XA60's
`Acceleration Mode`, its `Img. Scale Cor.` is `Image Scaling`, and its `Load
images to viewer` names a viewer that was itself renamed to MR View&GO. Nothing
in the spelling connects those.

So each release carries a JSON dictionary in
`src/siemens_protocol/vocabulary/` mapping its own labels onto shared
**canonical names**, which are snake_case so they stay distinguishable from the
space-separated forms ordinary normalization produces:

```json
{ "aliases": { "PAT mode": "acceleration_mode" } }     // VE11C.json
{ "aliases": { "Acceleration Mode": "acceleration_mode" } }   // XA60.json
```

The mapping works in both directions — `vocab list --canonical NAME` answers
what each release calls a standard parameter:

```sh
siemens-protocol vocab list --canonical acceleration_mode
siemens-protocol vocab list VE11C          # every mapping, with its notes
siemens-protocol vocab check               # validate the dictionaries
```

A lookup that misses on the literal label is retried on its normalized form, so
one entry covers a release's own spelling variants — XA60 prints both
`Acceleration Mode` and `Accel. Mode`, and a single mapping catches each.

Point `--vocabulary DIR` at a directory of JSON files to overlay the shipped
ones without editing the installed package; `--no-vocabulary` on `diff` turns
the layer off entirely.

#### Why these are curated, not inferred

A wrong entry is worse than a missing one: it hides a real difference instead of
merely failing to explain one. Statistical pairing cannot be trusted here — any
two parameters that exist in only one release and sit in the same section
co-occur perfectly, which is how a naive pass confidently proposes
`Save uncombined` → `Radial Sorting`.

`vocab suggest LEFT RIGHT` therefore proposes candidates *with their evidence*
(support, value agreement, section agreement, and the actual values on each
side) and never applies them. Two rules held while curating the shipped data:

- **Only one-to-one renames qualify.** XA60 *merged* VE11C's `Normalize` and
  `Prescan Normalize` into one parameter, and *split* `Reference scan mode` into
  two. Neither is a rename, so neither is mapped; both stay visible as an add
  plus a remove. Declined candidates are recorded in each file's `rejected`
  block with the reason, so nobody re-adds them later.
- **Mappings are verified against real exports.** `vocab check --against A.pdf
  B.pdf` catches a mapping that *steals* a match — one where the other release
  still prints the source label natively. That is exactly what a split parameter
  looks like, and it is invisible to a check of the dictionaries alone.

### Cosmetic versus substantive

Markers in the report: `~` changed, `-` only on the left, `+` only on the right,
`R` relabeled, `c` recased, `f` reformatted. The first three are substantive and
are listed in full; the last three are cosmetic and are summarized unless you
pass `--show-cosmetic`.

Keys are matched after folding case, punctuation and a **short table of
confirmed abbreviations** (`Dist.`→`Distance`, `Accel.`→`Acceleration`,
`Corr.`→`Correction`, `enc.`→`encoding`, `Ref.`→`Reference`,
`suppr.`→`Suppression`). Values are compared likewise: `Single shot` versus
`Single Shot` is recased, `1` versus `1.00` is reformatted.

Two rules keep this honest, and both matter more than they look:

- **Nothing is silently merged.** A relabeled key is reported with *both*
  spellings, and if its value also changed it stays substantive. `Distortion
  Corr.: Off` versus `Distortion Correction: 3D` is a real change that happens
  to be wearing a new name.
- **Only provable equivalences are folded.** The table is deliberately short
  rather than a similarity threshold. `Fat sat. mode` and `Fast Mode` are one
  letter apart and are entirely different parameters; a fuzzy matcher would pair
  them and invent a value change. Semantic renames like `Coil Select Mode` →
  `Coil Selection` are left as an add plus a remove for you to judge, because
  the tool cannot know they are the same thing.

Use `--exact-keys` to switch normalization off entirely and see the raw picture.
On the matched VE11C/XA60 examples that roughly doubles the reported count, which
is a fair measure of how much of a cross-version diff is pure relabeling.

A parameter that repeats within a scan (`Slice Group`, `Slice Group #2`) is
compared as a *group* rather than position by position, because the two releases
do not always print such a group in the same order and pairing `#2` against `#2`
would invent misleading matches.

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

Two layers sit on top of that output. `policy/` checks a protocol against
preferred values. For comparison, `diff.py` classifies differences,
`vocabulary/` maps each release's labels onto standard names, `vocabsuggest.py`
proposes and verifies those mappings, and `report.py` renders the result.

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

The releases differ mainly in that header grammar:

```
VE11C  TA: 6:02 PM: REF Voxel size: 1.0×1.0×1.0 mmPAT: 2 Rel. SNR: 1.00 : tfl_me
XA30   TA: 9 sec Coil Selection: Auto Voxel Size: 1.2×1.2×5.0 mm³ Acc:: None Rel. SNR: 1.00
XA60   TA: 6:02 min Coil Selection: Manual Voxel Size: 1.0×1.0×1.0 mm³ Acc:: 2 Rel. SNR: 1.00
```

Note `mmPAT:` with no space and `Acc::` with two colons. Rather than one
brittle regex per release, a profile lists its field labels in order and the
parser takes the text between each label and the next — spacing quirks stop
mattering. The Numaris/X releases omit the sequence binary from the box, so
their profiles recover it from the `Sequence Name` parameter via
`param_fallbacks`.

Spectroscopy prints a *volume of interest* where imaging prints a voxel size,
in every release:

```
VE11C  TA: 0:36 PM: REF VoI: 25 ×25 ×40 mmRel. SNR: 1.00 : svs_se
XA60   TA: 12 sec Coil Selection: Manual VoI: 25×25×22 mm³ Rel. SNR: 1.00
```

It is kept as its own `voi_mm` field rather than folded into
`voxel_size_mm`: a 25×25×22 mm acquisition volume and a 1×1×1 mm imaging
resolution are different quantities, and merging them would report a changed
voxel size whenever the acquisition type changed.

**Every label a release prints must be declared.** Because each field takes
the text running to the *next declared label*, an undeclared one is absorbed
by the field before it, along with everything after it. Undeclared `VoI:` did
not produce an empty field — it silently swallowed the SNR and the sequence
binary into `pm`. Two tests guard the class: no parsed header value may
contain a stray `:`, and every scan must report either a voxel size or a VoI.

XA30 and XA60 are both Numaris/X and share that grammar *verbatim*, so it is
declared once in `profiles/numaris_x.py` and each release module adds only its
version discriminator. They still differ in parameter vocabulary, which is
expressed in `vocabulary/*.json` rather than in the profile.

**Discriminators must be exact.** XA60 originally required `VA\d\d`, which also
matches `VA30A-03GR`; every XA30 export therefore detected as XA60 at *high*
confidence. Nothing failed loudly — the grammars are identical — but the
reported version was wrong and the wrong vocabulary was selected. Every profile
that scores at all is a detection candidate, so match the exact release number.

### Adding a release

1. Copy `profiles/xa30.py`, give it a name and `require`/`reject` patterns. If
   it shares an existing family's header grammar, import that family's labels;
   otherwise declare its own `header_labels`.
2. Import it in `profiles/__init__.py`.
3. Add `vocabulary/<VERSION>.json`, even if it only repeats a sibling release —
   a test asserts every registered profile ships one.
4. Drop example PDFs in `examples/<VERSION>/` — the folder name is the
   ground-truth label the tests use.
5. Add hand-checked scan counts to `tests/test_scans.py` and generate snapshots
   with `SIEMENS_PROTOCOL_REGEN=1`.
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
would need OCR throughout. All nineteen example files — every release, header
boxes included — carry a clean native text layer at a printable ratio of 1.0,
so none of them takes the OCR path, and all values are exact. The fallback is
built, tested and wired to the printable-ratio check, and `--ocr always`
exercises it; it simply is not needed by these files.

Expect degraded fidelity when it does fire: 8pt raster text mis-reads
characters (`Auto`→`Auio`) and loses spacing (`A >> P`→`A>>P`). Scan splitting
survives, but names do not always: on an XA30 file under forced OCR, 12 of 14
scan names come back exact and 2 are lost because tesseract fails to read the
protocol path at all. Section *sets* also drift under OCR on every release,
since title detection leans on geometry the raster round trip perturbs.

## Future work

* A numeric normalization layer splitting value and unit (`{"value": 2530.0,
  "unit": "ms"}`), kept separate from the raw string capture.
* Extending the abbreviation table and the per-release vocabularies as new
  releases arrive. Confirm each entry against a matched pair and run
  `vocab check --against` before committing it, since a wrong entry hides a
  real difference.
* Conditional policy rules, so a preference can apply only to certain sequence
  types rather than wherever the parameter appears.
* Value vocabularies. Renaming reaches parameter labels but not their values:
  VE11C's `Confirm freq. adjustment: Off` is XA60's `Confirm Frequency: Never`,
  and `Coil Select Mode`'s values were recoded wholesale. Those currently
  report as changed, which is honest but noisy.
* Per-version fixtures and profiles as new releases arrive.
