# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`siemens-protocol-tool` (the command; the package installs as `siemens-protocol` and
imports as `siemens_protocol`) parses Siemens MR protocol PDF exports into hierarchical JSON
(one entry per scan, sections of key/value parameters, plus a flattened view that
flags parameters printed inconsistently across sections). Supports VB17A, VE11C, XA30 and XA60.
Runs on Linux, macOS and Windows; the package is pure Python and PyMuPDF ships
wheels for all three. `siemens_protocol.exar` additionally reads and rewrites
XA `.exar1` protocol archives -- see [The .exar1 archives](#the-exar1-archives).
See `Design.md` for the design and `README.md` for usage.

### Environment

```bash
.venv/bin/python -m pytest          # always use .venv, not system python3
.venv/bin/pip install -e ".[dev]"   # pymupdf, pytesseract, pillow, pytest, black, isort
```

- `import pymupdf`, not `import fitz` (deprecated alias, emits a warning)
- zsh aborts the **entire** command line on a failed glob (`--include=*.py`,
  `rm -rf ... *.egg-info`). Quote globs: an unquoted one that matched nothing
  made a `rm -rf .git` never run, so a "builds without git" test silently read
  the real repo and passed for the wrong reason.
- No `timeout` on macOS. Use the Bash tool's own timeout, or
  `perl -e 'select(undef,undef,undef,20)'` to sleep.
- OCR is an optional extra (`pip install -e ".[ocr]"`) plus a native tesseract
  binary: `brew install tesseract`, `apt install tesseract-ocr`, or
  `winget install UB-Mannheim.TesseractOCR`. Found on PATH, else in the
  platform's usual install directory, else via `SIEMENS_PROTOCOL_TESSERACT`
  or `--tesseract`. Keep it out of the core dependencies: no example of any
  release takes the OCR path, so requiring it would put a non-pip step in
  front of every user.

### Versioning

- The git tag is the only source. `pyproject.toml` declares `dynamic = ["version"]`;
  never add a literal back to it or to `__init__.py`.
- `src/siemens_protocol/_version.py` is generated at build time and gitignored.
- Release with `git tag v0.2.0 && git push --tags`. Nothing else to edit.
- Docker excludes `.git`, so the version is passed in as a build arg and
  forwarded via `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SIEMENS_PROTOCOL`. That
  string must be PEP 440, so derive it with `python -m setuptools_scm`, never
  from `git describe`: `0.1.0-23-gabc1234` raises `InvalidVersion` in the build
  backend, so passing it did not make an unreleased-looking image, it made no
  image at all. `builddocker.sh` did exactly that, and both Docker workflows
  passed no version, which is why every published image reported
  `0.0.0.dev0+unknown`. A Docker tag cannot hold the result verbatim either --
  `+` is illegal in a tag, so `builddocker.sh` swaps it for `_`.
- `--version` before a subcommand = the tool's version; `--release` after one
  = the Siemens profile. `--version` survives as a hidden alias for the latter.
- Never find-and-replace across the three names (command `siemens-protocol-tool`,
  distribution `siemens-protocol`, import `siemens_protocol`). `__version__`,
  the OCR install hint and `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SIEMENS_PROTOCOL`
  all key off the *distribution* name and break silently if it moves.

### Cross-platform rules

- Never `open()` without `encoding=`; the default differs by platform.
- Any path written *into a file* (golden snapshots) must be normalized to `/`,
  or every snapshot fails on Windows on the separator alone.
- `cli.use_utf8_output()` runs first in `main()`. Windows leaves a redirected
  stdout on the legacy code page, which cannot encode the `×` and `³` these
  protocols print — so the failure appears only when output is piped.
- `tests/test_portability.py` covers all three from any machine. CI is the
  real check: Linux, macOS and Windows against Python 3.10 and 3.14.

### Testing

- `examples/<VERSION>/*.pdf` — the folder name is the ground-truth version label
- `examples/<VERSION>/*.exar1` — protocol archives, discovered the same way.
  `SIEMENS_PROTOCOL_EXAR_DIR` names a directory whose archives are used *as
  well*, not instead: `examples/` is always scanned, and archives found through
  the variable carry no version label, so a test needing one reads the
  archive's own baseline. Remove the shipped archives and 25 corpus-driven
  tests skip while 10 pure-unit ones still pass, which reads like a pass --
  so CI asserts both that discovery found something and that nothing skipped,
  the same two-halves guard the OCR and front-end suites have.
- `SIEMENS_PROTOCOL_REGEN=1 .venv/bin/python -m pytest tests/test_golden.py` — regenerate snapshots
- `pythonpath = ["tests"]` in pyproject lets tests import `conftest` helpers directly
- Verify layout changes with the token-conservation test in `tests/test_pipeline.py`:
  every body token must land in exactly one key, value, or section title. It catches
  silent drops that spot-checking values does not.
- A skip-guard must ask the same question the code asks. `_tesseract_available()`
  probed pytesseract directly (PATH only) while `ocr_page` uses full discovery, so
  the OCR tests skipped on Windows where the tool itself finds the binary --
  invisibly, because a skip reads like a pass. CI now asserts they actually ran.
- `gh` is installed and authenticated, so CI is readable directly: `gh pr checks <n>`
  for status (`--watch` blocks until they finish), `gh run list` to find a run, and
  `gh run view <id> --log` or `--log-failed` for the output. Job logs 403 only without
  auth, so the old `curl` route to a check-run's annotations is no longer needed.
  Still have CI steps emit `::error::<message>`: `--log-failed` hands back the whole
  step, and that one line is what says which of a few hundred assertions went red.

### The GUI

- It is a page served to the browser by a stdlib `http.server`, not a toolkit.
  Tkinter was the obvious choice and is the wrong one: `_tkinter` is missing
  from this machine's Homebrew Python 3.14 (only `python-tk@3.10` is installed),
  and Debian needs `apt install python3-tk`. That is the same non-pip step the
  OCR extra exists to avoid. PySide6 costs ~150 MB and LGPL. The browser is
  already installed on all three platforms.
- `gui/commands.py` is the single description of the CLI's surface. Forms are
  generated from it *and* arguments are built from it, so a new CLI flag is one
  entry there and nothing else. `tests/test_gui.py` fails if a subcommand is
  added to `cli.py` and not exposed (`gui` itself is the one exclusion — running
  it from inside itself would nest a second server).
- The command line the page shows comes from `/api/preview`, which calls the
  same `build_argv` the Run button does. Do not rebuild it in JavaScript; a
  preview that drifts from what runs is worse than no preview.
- Output is a subprocess (`python -m siemens_protocol.cli`), not an in-process
  `cli.main()`. `main` writes to the process-global `sys.stdout`, which cannot
  be captured safely while the server thread is live, and a thread cannot be
  cancelled. stdout and stderr are merged deliberately: a `warning:` line is
  only useful beside the file that produced it.
- The browser polls with `since` = lines it has seen, which is *absolute*,
  while the buffer's indices shift every time the cap trims its front. Indexing
  the list with `since` directly makes the browser silently skip output. Track
  lines dropped and subtract. `MAX_LINES` must also clear the biggest thing the
  tool prints — `parse --stdout` on the largest example is 33 222 lines, and a
  cap of 20 000 was cutting the JSON's own opening brace off.
- A loopback bind is not a security boundary: any page in the user's browser can
  reach it, and this server runs commands. Three guards, all tested — a
  per-session token in a header, a `Host` check (DNS rebinding), and no CORS
  header ever. Static files come from an explicit three-name map, not a path
  join.
- `subprocess.Popen(` contains the substring `open(`, so it trips
  `test_every_file_the_package_opens_declares_an_encoding`. Do not add it to
  that test's exclusions — `Popen` really does choose an encoding for its pipes,
  and forgetting it breaks on Windows exactly as a bare `open()` would. The test
  now balances parentheses to read the whole call, which also covers any
  `open(...)` Black has wrapped across lines.
- Path arithmetic in `browse._parent` takes the path module as an argument so
  `ntpath` rules can be driven from macOS. That is not decoration: stripping a
  trailing separator makes `C:\` look like it has a parent (`C:`), and a bare
  `C:` on Windows resolves against the current directory *on that drive*, so
  the picker's Up button would jump somewhere unrelated. Normalize, then
  compare. A test asserting `entry["path"].startswith(dir + os.sep)` cannot
  catch a hand-assembled `"/"` path on macOS, where the two are identical --
  drive the seam instead, the way the rest of `test_portability.py` does.
- Headless Chrome hangs on this machine, even on a `data:` URL, so the front end
  cannot be checked that way. `tests/test_frontend.py` runs `app.js` under
  `node:vm` against the DOM shim in `tests/frontend/dom.mjs` and a live server,
  driving it the way a person would. No dependency to install; the tests skip
  without `node`, and CI asserts they did not skip.
- The front end's expectations come from the server, not from this file: tabs
  are compared against the spec the page was sent, the picker's listing against
  `/api/browse` for the same directory, the previewed line against
  `/api/preview` for the values the page is holding. That is what keeps a new
  release or a new example folder from editing a test. Reading the page's own
  `state` needs `vm.runInContext` -- app.js declares it with `const`, which
  lands in the context's lexical scope and never on the sandbox object.
- Nothing in that harness sleeps for a fixed interval; every check polls its
  own condition to a deadline. Two things this caught, both of which had a
  check passing for the wrong reason first: a `<dialog>` keeps its contents
  after `close()`, so waiting for a reopened picker to show a directory is
  answered instantly by the previous session's entries -- drive the picker
  once, and only between directories whose listings differ. And the long
  subprocess timeout belongs on the one `settle` that waits for the run to
  finish, not on the checks that follow it: give them all 180 s and a page
  where Run never re-enables takes nine minutes to report instead of eight
  seconds.
- `GuiServer` overrides `server_bind` to skip `socket.getfqdn`. `HTTPServer`
  calls it to fill `server_name` for CGI handlers; there are none here and
  nothing reads it, but it is a *reverse DNS lookup* and it runs inside
  `serve()` -- before `launch` prints the URL that carries the session token.
  On a machine whose resolver has no answer for `127.0.0.1` the GUI therefore
  looks hung with no way in. This is what failed on macOS CI while Windows
  passed, and it was invisible locally, where the lookup returns in 6 ms. The
  test removes the resolver rather than the platform: monkeypatch
  `socket.getfqdn` to raise and assert binding never calls it.

- The shim records every `getElementById` that missed rather than returning
  `null`. Renaming an id in `index.html` alone otherwise throws inside a
  listener, which in a browser looks like a button that silently stopped
  working.

### Vocabularies

Adding one alias is rarely one edit. `saturation_region` was added to VE11C and
XA60 alone, turned four tests red, and had to be reverted; what re-landing it
needs is below, and the same applies to any new canonical name.

- **A canonical name must be accounted for by every release.** `check()` refuses
  one that only some define, because a one-sided name can never pair with
  anything and that is what a typo looks like. A release that genuinely lacks
  the parameter says so in its `absent` block, with a written reason — that is
  how `b1_shim` and `image_scaling` pass without VB17A defining them. `absent`
  and `aliases` may not both claim the same name.
- **Read the label off a real export, not off a sibling release.** VB17A prints
  `Sat. region 1`, numbering the region in the label itself, where VE11C prints
  `Sat. region` and lets the parser add `#2` for the repeat. Copying VE11C's key
  into VB17A would map nothing. Grep `tests/golden/*.json` for the spelling: the
  snapshots are every example's parsed output and are quicker to search than the
  PDFs.
- **A release with no example that prints the parameter is not evidence it lacks
  one.** No XA30 example prints a saturation region — only `Saturation Mode`,
  which is a different parameter — so `absent` there is a claim about XA30, not
  about the examples, and needs to be true of the release.
- `test_shipped_vocabularies_hold_up_against_the_examples` verifies every shipped
  alias against **one pair**, R01StressDyn in VE11C and XA60, and reports a
  mapping it does not see as "mapped but never printed in this export". A label
  that appears only in other examples fails there while being perfectly correct.
  Widen the check to every pair that prints the label rather than deleting the
  mapping or adding an example to that one pair.

### Third-party sequence detection

`sequences/catalog.json` names the customer sequences; `sequences/__init__.py`
matches scans against it. The point of the feature is that Siemens' conversion
handles stock sequences and third-party ones are what force a manual rebuild.

- **Three detectors, each sufficient alone, never AND-ed.** The sequence binary,
  the Special card, and VB17A's stated `sequence_owner`. An AND of the first two
  finds *zero* third-party sequences on VB17A,
  which prints no Special card in any of its 110 example scans -- the only
  `Special` string in those PDFs is `Special sat.`, a Geometry parameter, which
  is why the card is matched on the section *title* and not on a substring.
  VB17A compensates by printing the sequence *file* name (`cmrr_mbep2d_bold`)
  where Numaris/X prints only the kernel (`epfid`). Conversely one XA30 scan
  (`T1w_MEMPR_vNav`) has no readable sequence field and only the card finds it.
- **`base_binaries` gates the Special-card route only, never `binaries`.** It
  exists to split one fingerprint across the kernels it rides on -- CMRR's MB
  card is `epfid` for BOLD and `epse` for diffusion. Gating the binary route
  with it would drop every VB17A match. An empty binary deliberately *fails* a
  gate, so a kernel-less scan falls to a lower-priority entry that says the
  base sequence is unknown rather than being handed BOLD or diffusion by sort
  order.
- **`priority`, not condition count, decides between two matches.** The CMRR
  package entry names six labels; the multiband variant names two plus a
  kernel. The variant is the narrower answer and the count says the opposite.
- **`unrecognized` is a third verdict, not a soft "third-party".** On the
  corpus every non-empty Special card *is* third party, but that is a fact
  about 19 files, not a law: `resolve` is a Siemens product sequence that does
  print a Special card on some builds. Calling it `stock` is the worse error --
  it reports a protocol as converting cleanly when it may not. Keep the stock
  kernel list conservative for the same reason: a missing kernel costs a look,
  a wrong `stock` costs a rebuild.
- Do not read `\\USER\` as a customer marker. It is the protocol tree root, and
  `\\USER\...\localizer` is stock. `CustomerSeq` is the real marker and appears
  in none of the shipped examples, so `path_markers` is supported but
  unexercised by the corpus.
- `Scan.to_dict` recomputes provenance on every serialization rather than
  caching it, so a catalog fix reaches JSON parsed before the fix. `listing.py`
  recomputes it too. Both read `sections`, never `flat`, so `--no-flatten` JSON
  keeps working -- there is a test for that.
- `test_sequences.py` asserts against the golden snapshots rather than a frozen
  list, so a new example folder tightens it. It fails if a shipped signature
  matches nothing in the examples: a signature no example exercises is one
  nothing verifies. Widen the examples rather than deleting the signature.
- **Most vendor attributions come from the protocol's owner, not the exports.**
  No export names a sequence's author; the exports give a binary name, a
  parameter fingerprint, and (on VB17A only) SIEMENS-or-USER. Everything past
  that -- CMRR, MGH, UIUC, Manus Donahue -- was supplied by the user and lives
  in each signature's `note`. Do not "correct" one of these from a plausible
  inference: the note says where it came from, and a guess overwriting a
  confirmation is a silent regression. `dual-echo-pcasl` is deliberately left
  `unattributed (site-installed)` because that one is genuinely unknown.
- **`sequence_owner` is the export saying so, so it decides the verdict.** VB17A
  introduces the binary with `SIEMENS:` or `USER:`; `profiles/vb17a.py` has
  always parsed it and nothing consumed it until now. It partitions all 110
  VB17A scans and agrees with both fingerprints everywhere, which is what
  earns it precedence over `stock_binaries` -- a static list beaten by a
  per-scan statement. A signature still supplies the *identity*: `USER` says
  the sequence is not Siemens', not which one it is. Only VB17A prints it, so
  it cannot replace the other two.
- **A stated owner of SIEMENS contradicting a third-party signature yields
  `unrecognized`, not a silent pick.** Two disagreeing signals is exactly a
  scan a person should look at, which is what that verdict means. No shipped
  example does it; the branch exists so a future one is visible rather than
  quietly resolved.
- The corpus stands at 701 third-party, 299 stock and 31 unrecognized, pinned by
  `test_the_shipped_examples_are_accounted_for_apart_from_a_pinned_few`.
  `tse_crusher` (`Flair axial low SAR`) is labelled `USER` and so reports
  third-party; `fl3d_rd` (`vessels_head`) is labelled `SIEMENS` and is also in
  `stock_binaries`. The five unaccounted scans are all in `XA60-Potpourri`,
  added for the `.exar1` work rather than for sequence detection: Siemens
  kernels (`epfid`, `epse`, `fl`) carrying MGH's FLEET/ACS modifications, whose
  extra parameters no signature claims. They recur in all three Potpourri
  exports (P1, P1_changed, P2), so the pinned set is the product of five scan
  names and three export names rather than fifteen literals -- a *sixth*
  unaccounted scan, or one of these five quietly resolving, still fails. That test asserted zero before, and relaxing it was
  the deliberate act the previous wording called for; writing signatures to
  force it back to zero would mean attributing sequences from inference alone,
  which is exactly what the attribution rule above forbids.

### The .exar1 archives

`siemens_protocol.exar` reads and rewrites the protocol archives XA exports.
None of this is documented by Siemens; every claim below was established
against real exports and is asserted in `tests/test_exar.py` rather than
assumed. The corpus is one protocol carried on two XA60 scanners: `Potpourri_P1`
and `Potpourri_P2`, each with its PDF export. Two re-saved variants are the
answer keys the mappings rest on: `Potpourri_P2_changed` moves TR alone on five
scans, and `Potpourri_P1_changed` moves many parameters across five scans
including the Special card, with its own PDF. Having the PDF beside the archive
is what lets the two readers be checked against each other, and having a
before/after pair is the only way to learn where a printed value is stored --
`sWipMemBlock` in particular is unreadable without one.

- **The format is five layers deep**: SQLite -> raw DEFLATE (`wbits=-15`, no
  zlib or gzip wrapper) -> a one-line `EDF V1: ContentType=...;` header -> a
  Newtonsoft JSON document -> and, in `EdfProtocolContent.Data`, the XProtocol
  text with its `### ASCCONV BEGIN ###` block. Numaris/X did not replace
  XProtocol, it wrapped it, so `alTR[0] = 650000` sits in an XA archive exactly
  as it did on VB17.
- **`Content.Hash` is the SHA-1 of the *decompressed* header-plus-JSON**, not of
  the stored blob and not of the JSON alone. That is the whole write path: build
  the document, prepend the header, hash, deflate. Hash the wrong bytes and the
  archive's own references stop resolving.
- **Three GUID spaces meet in `Instance`, and mixing them yields a plausible
  wrong answer.** `Id` is one version of a node; `Element_id` is the node across
  versions and is what the packed `Children` blobs reference; `ObjectId` is the
  domain object and is what the *JSON payloads* reference (`FirstStepId`,
  `LinksFrom`). Indexing `Children` through the object map raises `KeyError` if
  you are lucky and silently finds nothing if you are not. A test in
  `test_exar.py` did exactly this while being the test written to guard it.
- **Step order is a linked list, not the `Children` blob.** Walk `FirstStepId`
  and follow `LinksFrom` to `LastStepId`. The blob holds the same eighteen steps
  permuted, so reading it yields a full set of scans with every value correct
  and every scan attached to the wrong name -- the TR *multiset* still matches
  the PDF, which is why a spot check passes. The order assertion also checks
  that the two orders genuinely differ, so the test cannot pass vacuously on an
  archive that happens to store steps in running order.
- **`Children` holds .NET mixed-endian GUIDs** (`uuid.UUID(bytes_le=...)`),
  sixteen bytes each. Reading them big-endian gives well-formed GUIDs that match
  no element, so the failure is an empty tree rather than an error.
- **Scan names are not in the protocol.** They hang off `Instance.LabelElement_id`
  on an `EdfString` node whose content is a locale table. That is deliberate:
  renaming a scan must not re-hash the protocol. The key is not always the same
  -- most archives use `""` for the default, but some write `"en"` with no empty
  key at all, and both spellings are in the corpus. Reading only `""` yields a
  nameless tree on those, which looks like a reader that cannot find the labels
  rather than one looking under the wrong key.
- **`Preview` is the bridge to the PDF.** Each protocol carries a flat map of
  `{Label, Unit, Value}` entries whose labels are the ones the PDF prints -- `TR`,
  `Slice Thickness`, `FOV Read`. It is a per-protocol dictionary from printed
  label to protocol path, so the PDF-to-protocol mapping can largely be derived
  rather than hand-authored. Multi-echo scans print `TE 1`..`TE 4` and have no
  bare `TE`, so an exact-label lookup returning nothing there is correct.
- **A value lives in two places and both must be patched.** Changing TR moved
  `Preview["sub.0.msr.tr.0"].Value` (ms, float) *and* ASCCONV `alTR[0]` (us, int).
  Patch only the preview and the console lists a number the scan will not use;
  patch only the ASCCONV and the list goes stale. The console also recomputes
  derived values -- `lScanTimeSec` followed TR -- which a patcher does not.
- **"Two places" is the simple case; there are four shapes.** Some parameters
  are an ASCCONV *array*: `FOV Read` and `Slice Thickness` are replicated across
  every `sSliceArray.asSlice[]` element -- three on a localizer, sixty-four on a
  multi-slice EPI -- and writing element zero alone yields a protocol that loads,
  lists correctly and is wrong. Some are *derived*: `FOV Phase` is a percentage
  on the card but millimetres in the protocol, `dPhaseFOV = dReadoutFOV * pct/100`.
  Some are ASCCONV *only*: nothing on the Special card appears in `Preview` at
  all, so those have no preview side to keep in sync. And `Preview` is the
  console's listing, not a mirror of the printout -- a multi-echo scan prints
  `TE 1`..`TE 4` and `Preview` carries only the first, the rest living in
  `alTE[1..3]` alone.
- **`dThickness` is the slice on a 2D acquisition and the whole slab on a 3D
  one.** `sKSpace.ucDimension` is 2 or 4 and decides which; on all eight 3D
  protocols `dThickness` is exactly the displayed thickness times
  `sKSpace.lImagesPerSlab`. Writing the displayed number straight in would put
  1.0 where the protocol holds 176.0. This was caught by the mapping test
  re-deriving its evidence rather than trusting the table, which is the whole
  reason that test exists.
- **`FOV Phase` cannot be reproduced exactly from a percentage.** The console
  quantises it to a ratio the hardware can realise -- 29/30 of the read FOV,
  printed as 96.7% -- so writing `read * 96.7/100` lands within a rounding of
  the console's value but not on it. Assert to within the printed precision and
  say so in the manifest; do not fudge the number to match.
- **`sWipMemBlock` has no global meaning, so its mappings are per sequence.**
  It is scratch memory the sequence binary reads as it likes. `alFree[0]` is MT
  Flip Angle on `can_neuromelanin` and a packed word of checkbox flags on CMRR's
  multiband sequences; `alFree[1]` is Readout polarity on `tfl_mgh_epinav_ABCD`
  and Protocol filename on `ep_moco_nav_set_ABCD`; `alFree[12]` is Nav. location
  on the MPRAGE navigator and Include Nav. on the SPACE one. A table treating an
  index as one parameter would write a flip angle into CMRR's flags, so anything
  reaching into `sWipMemBlock` must name its sequences and a test enforces it.
- **The option-scan archives are what pin the Special card**, and nothing else
  can. `CMRR_optionscan_P1`, `MEMPRAGE_optionscan_P1` and `NAV_optionscan_P1`
  each hold one sequence repeated with a single option changed per copy -- 33,
  7 and 31 scans -- so diffing each against its group's baseline gives one
  labelled change against one moved field. That yields three encodings: values
  written directly, small-integer enums (`Averaging` is 1=None 2=Linear 3=RMS
  4=RMS only 5=Mean), and CMRR's fourteen-bit flags word in `alFree[0]`.
  `tests/test_exar_patch.py` replays all 62 toggles through the patcher and
  requires every one to reproduce the console.
- **`sWipMemBlock` arrays are sparse: an assignment holding zero is not
  written at all.** A CMRR protocol with every Special-card box unticked has no
  `alFree[0]` line, and setting `Remeasure` to zero deleted its line rather
  than writing `0`. Elements are listed in ascending index order, so creating
  one means inserting it among its siblings and not appending. A patcher that
  only overwrites existing assignments cannot turn a first flag on, which is
  how this was found.
- **A flags word carries bits no mapping claims.** Comparing a whole `alFree[0]`
  against the console's is therefore wrong; compare the bit a mapping claims.
  The Potpourri edit toggled `Echoes in separate series`, which no option scan
  has pinned, and it shares the word with fourteen options that are pinned.
- **ASCCONV doubles carry twelve significant figures.** That reproduces all 919
  distinct float literals in the reference archives, and so does Python's
  `repr` -- but `repr` spells a freshly computed value with its binary tail
  (`201.26200000000003` where the console writes `201.262`), so it is the wrong
  choice for the one job the formatter has.
- **Byte-exact round-trip is not the goal, because the console is not
  byte-stable either.** Re-saving an unmodified protocol regenerates all 67
  GUIDs, rewrites `sCoilSelectMeas.aRxCoilSelectData[N].tCheckUUID` and the GUID
  *leading* `sWipMemBlock.tFree`, flips ASCCONV whitespace between `  =  ` and
  `\t = \t`, and updates `sSpecPara.lFinalMatrixSizePhase` / `...Read`, which
  despite their names hold a *date and time* (`20260825` / `155206`). Assert
  semantic equality against that churn list, never bytes. Establishing this
  needed the unchanged-vs-changed pair; without it the obvious acceptance test
  is the wrong one.
- **Only the GUID in `sWipMemBlock.tFree` is churn; the rest of it is the
  sequence build stamp.** CMRR writes
  `<guid>||Sequence: R017 nxva60a/main r/91b106c1e; May 15 2026 12:56:25 by eja`,
  and while the GUID differs between any two saves the tail is identical across
  every export, both scanners and every edit. `patch.sequence_stamp` returns it
  with the GUID dropped. Treating the whole field as churn -- which this file
  did at first -- would normalize away the only record of which binary wrote a
  protocol. And `tFree` is sequence-private like the rest of the block: the ABCD
  navigators put a `.prot` file name there with no GUID, and `tfl_mgh_multiecho`
  writes nothing at all.
- **The Special card can change between sequence builds, so a mapping is only
  verified for the build it was derived from.** Everything in `MAPPINGS` came
  from binaries stamped `R017 ... r/91b106c1e`. A later CMRR release is free to
  renumber `alFree` indices or move a flag bit, and nothing in the protocol
  would announce it -- the values would simply land in the wrong parameter. If
  a second build ever enters the corpus, check the option-scan diffs against it
  before trusting the table, and expect `Mapping` to need a version dimension
  beside `sequences`. Note also what the stamp does *not* say: it reads `R017`
  whether the binary was 017pre15 or a later 017, so the commit and build time
  are what identify a build exactly.
- **Unmodified content is written back from its original blob**, never
  recompressed. Our zlib does not reproduce the console's DEFLATE stream, so
  regenerating every blob would rewrite all fifty rows on a no-op round trip and
  make any later diff useless. `Envelope.stored` is what holds that; dropping it
  on edit is what re-addresses the content.
- **`json.dumps(obj, indent=2, ensure_ascii=False)` with CRLF and
  `envelope.dotnet_double` reproduces Newtonsoft byte for byte** -- all 596
  content blobs across every console-authored archive re-encode to their exact
  stored bytes and hash back to their stored address. Doubles are the whole
  difficulty: .NET's round-trip format writes fifteen significant figures when
  that round-trips and seventeen when it does not, where Python's `repr` gives
  the shortest round-tripping form. The two agree on almost everything and
  disagree on the field strength, `2.8936200141906738` against
  `2.893620014190674`. `json.dumps` has no hook for float formatting, so floats
  are carried through the encoder as marked strings and substituted after; the
  marker has to be printable, because the encoder escapes control characters
  whatever `ensure_ascii` says.
- **A scanner has now loaded patched archives and written them back**, which
  is the only test that can validate the write path rather than merely
  comparing it to another export. Five archives, 41 scans each changing one
  mapped parameter, every scan loaded and every value survived. Those returns
  ship as `examples/XA60/*_loadtest.{exar1,pdf}` beside the sources they came
  from, and `test_exar_patch.py` asserts that no scan went missing and that
  every ASCCONV field differing from the source is one a mapping writes. The re-saved
  files then differ from what we wrote in exactly one respect: the console
  recompresses (its DEFLATE is tighter -- 770 KB against our 934 KB) and
  otherwise the ASCCONV text is identical field for field, churn included.
- **A scan can be added to an archive, and the scanner accepts it.** Four
  duplicated scans loaded, kept their running order, and one carrying an edit
  kept its own protocol -- 19 distinct protocols in and 19 out. So generation
  is not blocked by the format. Adding one scan means three new instances
  (step, protocol, label) with fresh ids in all three GUID spaces, an `Element`
  row and an `InstanceChangeSet` row for each, three pairs appended to the head
  changeset's `ElementToInstanceMap`, and the step's element appended to the
  program's `Children`.
- **`EdfProgramContent` has five parallel maps keyed by step id, not one.**
  `LinksFrom` (outgoing), `LinksTo` (incoming, as `$ref` back-pointers into the
  link objects `LinksFrom` defines), `Ranks` (`{Rank: 0..N, StepId}`, the
  running order), and `RelationsFrom`/`RelationsTo` (an empty list each). A step
  present in only some of them leaves the console unable to build the program,
  which it reports by showing the folder tree with no protocols in it -- the
  same symptom as an archive exported from an empty folder node, and the first
  duplication attempt was rejected exactly that way.
- **A protocol and a label each carry `ParentElementId` pointing at their own
  step**, and only the step points at the program. The console resolves a
  step's protocol through that reverse link rather than through the step's
  `Children` blob, so a copy that keeps the source's pointer is served the
  source's protocol. That is invisible while the copy is identical and silently
  discards every edit once it is not: the second attempt loaded all 22 scans
  and returned the edited copy with its source's TR. Any generated scan needs
  one deliberately edited parameter or the test cannot fail.
- **`Instance.Tags` on a protocol carries `#ContentHash|<sha1 of the Data
  string>`**, which is neither the `ContentHash` column nor the stored blob's
  hash. It matches all 18 protocols in the reference archive.
  `replace_content` recomputes it. A stale one is tolerated on load -- two
  scans in the NAV option scan shared one and both kept their values -- but it
  is derivable and describes the content.
- **Import and re-export is not the same as editing.** `tCheckUUID`, the GUID
  leading `sWipMemBlock.tFree` and the date hidden in `sSpecPara.lFinalMatrixSize*`
  are regenerated when a protocol is *edited on the console*, and left alone
  when one is merely loaded and exported again. The churn list above was
  derived from an edited pair, so it describes the wider case; do not expect
  those fields to move on a round trip through the scanner.
- **`store.py` copies the schema out of `sqlite_master`** rather than declaring
  it, so a baseline that adds a column still round-trips without a code change.
  Storage classes are preserved deliberately: sqlite is dynamically typed, and a
  GUID written back as a blob would still store, still look right, and no longer
  equal the same GUID held as text elsewhere in the file. Both appear in one
  `Instance` row -- the id columns are text, `Children` is a blob.
- **Read at the real branch, not the placeholder.** Every archive carries a
  second `Branch` row whose `Baseline` is `-` and whose head resolves to no live
  instances; reading there yields an empty tree that looks like a corrupt file.
  The real row's baseline is the compatibility gate the scanner checks:
  `MAJORVERSION:VA60A, PROTOCOL:66010002, ADDIN:NXMAINLINE, EDF:1, SEQUENCE:1`.
- **Two scanners differ only in the churn fields, plus the coils.** P1 and P2
  are the same protocol imported onto two XA60 systems. Five of eighteen
  protocols are byte-identical; the other thirteen differ *only* in
  `tCheckUUID`, `sWipMemBlock.tFree` and the date-and-time hiding in
  `sSpecPara.lFinalMatrixSize*` -- every one already on the churn list above,
  which was derived from re-saving one archive and is here confirmed on a
  completely independent axis. No acquisition parameter differs. The single
  real difference is hardware: `aRxCoilSelectData[1].aFFT_SCALE` has 58
  entries on P1 and 20 on P2, on the one scan using a second coil array. A
  generator moving a protocol between scanners must not copy that across.
- **A step in the running order need not run anything.** `EdfPauseStep` is an
  instruction an operator put between scans -- "Count down with RA to start of
  scan", "Pause for saliva collection", "Do NOT add Raw Filter to 3D MPR" --
  carrying an `EdfMeasurementStepContent` with injector fields and no protocol
  child. Eleven of `CHR-MDD`'s thirty-four steps are pauses, and the reader
  raised on all three archives that arrived with them. They are named, they are
  in the chain, and the PDF does not print them as scans, so anything walking
  *scans* skips them: `Step.is_pause` reads the instance kind, `runs_a_protocol`
  reads the content, and a test asserts the two always agree because either
  alone could be wrong.
- **A printout carries fewer digits than the protocol.** One scan prints
  `TE 1 = 54 ms` for a stored `54.16`, so writing the printed value back drops
  0.16 ms. `build.agrees_at_printed_precision` treats a printed value as
  matching when the stored one rounds to it at the precision actually printed;
  without it, driving an archive from its own PDF degrades it.
- **Strip a printed unit only after whitespace.** Matching it anywhere turns
  `RMS` into `R`, because `MS` is a unit and the comparison is
  case-insensitive -- which then fails to resolve as an `Averaging` choice.
- **A readable archive can hold no protocols at all.** Exporting an empty
  folder node rather than the protocol tree yields a valid SQLite file with
  the directory scaffolding, a `Root` label and nothing else -- five
  `Instance` rows against a real export's sixty-seven. It is an export
  mistake, not a corrupt file, so `read` must not raise; but a corpus sweep
  asserting things about scans has nothing to say about one and would report
  the mistake as a reader failure. `conftest.EXAR_PROTOCOL_FILES` partitions
  the corpus, and the sweeps that need scans take `protocol_archive_path`
  while the structural ones (envelope, hashing, GUID layout) still take
  `archive_path` and are exercised by it.
- **`siemens-protocol-tool exar <archive> <pdf>` is the driver**, and its
  manifest is as much the point as its output. Roughly a tenth of what a
  protocol prints has a verified mapping, so a built archive is mostly the
  template it started from; the report states that fraction, counts inherited
  values and names the unmapped parameters by frequency, which is what says
  where the next mapping is worth deriving. Driving an archive from its *own*
  PDF must write nothing -- that one check exercises units, scales, the derived
  basis, sparse arrays and change detection at once, and it caught two spurious
  writes where a printed `0.00` met an assignment a sparse array omits.
- **Scans are matched to the template by name, and an unmatched one is
  reported rather than guessed at.** The PDF names a sequence by kernel
  (`epfid`) and the archive by sequence file (`cmrr_mbep2d_bold`), so there is
  no reliable way to pick a donor scan to copy. `generate.duplicate_step` is
  available to a caller who knows which scan to copy; the driver will not
  choose one.
- **`patch.resolve` falls back to the label `Preview` prints.** A multi-echo
  scan prints `TE 1` where a single-echo one prints `TE`, and the preview entry
  is labelled the same way, so resolving through it follows the printout
  instead of duplicating every spelling in the table. Without that the driver
  silently skipped TE on exactly the scans that print it differently.
- Everything so far is XA60 (`VA60A`). No XA30 archive has been seen, so the
  claim that the model is release-independent is untested -- that is the first
  thing to check when one arrives.

### Code Formatting

```bash
# Format code with black (line length: 99)
black .

# Sort imports (configured to match black)
isort .

# Spelling; config in pyproject skips examples/ and the golden snapshots,
# and allows TE and TR, which are parameter keys rather than typos
codespell

# Check specific file
black --check src/siemens_protocol/layout/sections.py
```

CI runs all three together under `shell: bash`. That is deliberate: a multi-line
`run` block uses pwsh on Windows, where a native command sets `$LASTEXITCODE`
without halting, so only the last line would decide the result.

### Building and Distribution

```bash
# Build package
python -m build

# Install locally
pip install .
```

## Important Constraints

### Code Style (NON-NEGOTIABLE)
- Write code that is clean and modular
- Prefer shorter functions/methods over longer ones
- **Every routine must have a numpydoc-style docstring** (Parameters, Returns, and any other
  relevant sections). When you modify a routine, re-read its docstring and verify it still matches
  the code — parameter names, types, defaults, return values, and raised exceptions. Fix any
  drift as part of the same change; a stale docstring is a bug.
- **Every function must have type annotations** for all arguments and for the return value
  (use `-> None` when nothing is returned). Applies to new code and to any existing function
  you touch.

### Python Version
- **Minimum**: Python 3.10
- **Maximum**: Python 3.14
- Uses modern Python features (f-strings, type hints)

### Data Formats
- Input: PDF files
- Output: json files

## Key Design Patterns

- `Design.md` says XA60 exports are scrambled CID needing OCR. Every current example
  (all three releases, 19 files) has a clean native text layer with a printable ratio
  of 1.0, so none takes the OCR path. The fallback is built and tested; exercise it
  with `--ocr always`, not by "fixing" it. Under forced OCR it recovers most but not
  all scan names — tesseract sometimes fails to read a protocol path outright.
- Version discriminators must match the exact release number (`VA30`, `VA60`), not
  `VA\d\d`. Every profile that scores at all is a detection candidate, so an
  overlapping pattern yields a *confident* wrong answer rather than an ambiguous one.
- XA30 and XA60 are both Numaris/X and share a header grammar verbatim; it lives in
  `profiles/numaris_x.py`, and each release module supplies only its discriminator.
- Every header label a release prints must be declared in `header_labels`. Each field
  takes the text up to the *next declared label*, so an undeclared one is absorbed by
  the field before it together with everything after it — a silent corruption, not an
  empty field. Spectroscopy's `VoI:` swallowed the SNR and sequence binary this way.
- Watch for labels with no preceding word boundary. VE11C prints `mmPAT:` *and*
  `mmRel. SNR:`, so neither pattern may start with `\b`. This has now bitten twice.
- The contents page is front matter *wherever it sits*: VE11C and Numaris/X lead with
  it, VB17A appends it. Detect it by its "Table of contents" heading, never by position,
  or a trailing one is read as the last scan's parameters. It is also front matter for
  *however many pages it runs*: a protocol with enough scans overruns the page and the
  rest carry entries with no heading, so the heading alone is a rule about where the
  listing starts, not where it ends. `in_contents_listing` runs it to the next header
  box, since every scan opens with one and a page without one can only continue what
  came before it. Ahead of the first scan a spill joins the front matter either way,
  which is why the two VE11C exports that spill parse correctly and this hid; on VB17A
  the spill is handed to the last scan, whose sections then include another scan's name.
- VE11C prints no version string of its own, only the scanner model, so it is identified
  by rejecting every other release. Adding a release means adding a rejection there too.
- VB17A wraps a long label onto a second line at the *same* pitch as an ordinary row,
  with the value on the first line, so no gap marks the continuation. Capitalization
  does (`lowercase_continues_label`), since these exports capitalize a label's first word.
- Batch output mirrors the input tree rather than flattening it: `examples/` holds the
  same protocol under two release folders, and flattening onto base names silently
  overwrote one with the other. Golden snapshots are keyed `<VERSION>-<stem>.json` for
  the same reason.
- Font size/weight are trustworthy only on native pages (`Row.has_font_metrics`).
  Section titles are detected by x-outdent, which survives OCR; row gaps do not.
- The label/value boundary cannot be a fraction of `x_max`, because `x_max` is a
  *maximum*. One value wide enough to overhang its column -- the sampling-table
  file name a spectroscopy sequence prints on its Special card,
  `PE Samp EPSI | SegsGS_216x108_...dat` -- pushed `value_x` right of the value
  cell itself, and then every value on that page read as label text: the first
  such row became a section title and the rest hung off it with empty values.
  This is why `columns.value_origin` measures where the values actually start
  (the densest cluster of left edges right of the labels) and caps the boundary
  there. It affected VE11C and XA60 alike -- the difference the user saw was only
  where the page break fell. The floor at `value_origin_min_ratio` is what keeps
  a repeating group's indented labels from being read as the value cell.
- A collapsed value column is invisible in a spot check, because every reading is
  still *there*, glued onto its own key. What makes it visible is the valueless
  rate: `test_a_scan_is_not_mostly_parameters_without_a_value` holds every scan
  under 15%, where the healthy worst case is 4.7% and the broken scans ran 21-30%.


## Style Conventions

Key points:
- Use Black formatter with 99-character line length
- Follow NumPy docstring format — see the mandatory docstring and type-annotation rules under
  [Code Style (NON-NEGOTIABLE)](#code-style-non-negotiable)
- Keep changes focused on specific issues/features


## VERIFICATION PROTOCOL (execute before returning control)
- Re-read the full original task specification.
- For each stated requirement: test it, confirm it works, and state the evidence.  Do not self-report "done" without executing the actual check.
- For each implicit quality bar (error handling, edge cases, formatting): apply the same standard.
- If something fails: fix and re-verify from scratch. Do not patch and assume.
- After 3 full fix-verify cycles with a persistent failure, stop and report the specific blocker with your diagnosis. Do not return broken work and do not loop silently.
- Only return control when every requirement has verified evidence of passing, or you've explicitly flagged what you couldn't solve and why.


## Model Delegation for Coding Tasks

For coding tasks, use your judgement to delegate work to a subagent running an appropriately lower-power model when the task doesn't need the full capability of the current model. For example: if you are Fable, delegate suitable tasks to Opus or Sonnet subagents; if you are Opus, delegate suitable tasks to Sonnet.
   
Model tiers for ANY delegated work - Agent-tool calls and Workflow-script `agent()` calls alike. Set the `model` parameter explicitly on every call; never omit it (omission silently inherits the session model):
- `haiku` - mechanical bulk work: renames, boilerplate, format conversion, log triage
- `sonnet` - default for well-specified implementation with clear acceptance criteria
- `opus` - genuinely tricky work: concurrency, subtle algorithms, adversarial verify/judge panels, gnarly debugging
- `fable` - rare; only when independence from your own context is the point (e.g. adversarial review of your own plan or a large diff). If you want to call a Fable sub-agent because the complexity of the task warrants it, ALWAYS check with me first - never spawn one unprompted.
   
When unsure between tiers, pick the cheaper and escalate on failure.
   
   
## Dynamic workflows (Workflow tool)

Applies to ALL sessions, any model. Dynamic workflows do not need to be avoided - reach for the Workflow tool when a task has 3+ independent parallelizable subtasks or would benefit from a pipeline/judge panel. Standing rule on opt-in: if ultracode is NOT on for the session (no "ultracode" keyword, toggle, or an orchestration request in my own words), check with me first - propose the workflow in one or two sentences with the rough shape and cost, and wait for my reply; my "yes" is the opt-in. If ultracode IS on, invoke directly.

**Agent models inside workflow scripts:** every `agent()` call MUST set the `model` parameter explicitly, chosen per "Delegating to sub-agents" above - with one tightening: NEVER use `fable` agents in a dynamic workflow, not even with approval. Only `haiku`, `sonnet`, or `opus`. If a Fable review is warranted, it happens AFTER the workflow completes, as a standalone Agent-tool call (ask first, per above) - never as a workflow stage.
