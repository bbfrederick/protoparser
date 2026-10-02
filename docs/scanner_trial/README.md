# XA60 scanner validation trial

The project aims to write scanner-usable XA60 archives by copying existing
protocols and applying characterized UI edits. This first trial tests the
edit-session graph copier, a pause, a mapped TR edit, twelve copy references,
and the scout's preserved add-in configuration. It addresses the scanner-testing
priority in [TODO.md](../../TODO.md).

**Current status: no hardware result is recorded for this trial.** Existing
scanner-return fixtures test comparison behavior; they do not establish acceptance
of this archive. Offline checks cover characterized semantics, not every sequence
dependency or hardware limit. ASCCONV is authoritative; calculated variables are
preserved, and Preview regeneration is inspected separately. See the
[project limitations](../../README.md#current-limitations-and-editing-policy).

The tool does not operate the scanner. This trial establishes evidence for its
specific source protocols and installed sequence versions. Mixed-donor assembly
and broader edits require follow-up trials.

## Prepare the submission

Use an XA60 console with the source sequences installed, including the relevant
CMRR multi-echo BOLD sequence. Record the scanner software build, installed CMRR
sequence version, and hardware/coil configuration with the results. Differences
between the source environment and the test environment help explain failures.

From the repository root, with the Python environment activated:

```bash
mkdir -p loadtest/step4
spt assemble examples/XA60/copyparametertest.exar1 docs/scanner_trial/plan.json \
  --out loadtest/step4/STEP4_VALIDATION.exar1 \
  --manifest loadtest/step4/manifest.json
spt validate loadtest/step4/STEP4_VALIDATION.exar1 --json > loadtest/step4/validation.json
```

The base program is retained. Under `Validation`, `STEP4_CONTROL` has the
fifteen original acquisitions, fresh graph identities, all twelve relations,
and unchanged acquisition/add-in content. `STEP4_EDIT` has the same fifteen
acquisitions and relations, a `Check setup` pause at step position 1, and one
explicit UI change: `rfMRI REST ME PA XA60` TR **1330 → 1340 ms**, represented
by `alTR[0]` **1330000 → 1340000 us**. The stored derived scan times and other
sequence-calculated fields remain unchanged. The manifest lists every ASCCONV
assignment that actually changes. Copy the finished archive to the scanner using
your usual import workflow, and keep the local submitted file unchanged. Do not
regenerate it after import: new graph identities would change the comparison's
baseline. `loadtest/` is local working storage and is not committed to Git; retain
the submission and returned files with the trial results.

Generate one checklist per submitted program:

```bash
spt validate loadtest/step4/STEP4_VALIDATION.exar1 \
  --program Validation/STEP4_CONTROL --checklist --json \
  > loadtest/step4/control-observations.json
spt validate loadtest/step4/STEP4_VALIDATION.exar1 \
  --program Validation/STEP4_EDIT --checklist --json \
  > loadtest/step4/edit-observations.json
```

Warnings about uncharacterized donor settings are not proof that those settings
are invalid. Save the validation reports and investigate any console failures
against both CONTROL and EDIT.

## Import, inspect and re-export

1. Import the archive and locate `Validation/STEP4_CONTROL` and
   `Validation/STEP4_EDIT`. Preserve their names if possible. Record any import
   messages, missing sequences, conversion requests or greyed-out scans before
   trying to repair them.
2. Inspect both protocols. Each should have fifteen acquisitions in the source
   order and twelve copy-reference links. EDIT should have `Check setup` after
   the first acquisition, before the scout. Check link sources, targets, menu
   groups and options, plus the scout's add-in configuration.
3. Open `rfMRI REST ME PA XA60` in each protocol. Confirm TR is **1330 ms** in
   CONTROL and **1340 ms** in EDIT. Record unexpected changes or errors.
4. Record each acquisition's observed console status in its checklist by changing
   only `observations[*].status` to `runnable` or `greyed_out`. Leave unobserved
   scans as `not_tested`. Preserve all step indices, scan names, sequence names
   and protocol hashes: these bind the observations to the submitted scans. A
   scan appearing in a re-export does not by itself establish runnability.
5. Before making manual corrections, export both protocols to a new archive,
   for example `loadtest/step4/STEP4_RETURN_INITIAL.exar1` after copying it back.
   Export PDFs too if available. The initial return captures automatic scanner
   changes independently of later setup or troubleshooting edits.

## Check acquisition behavior

Using your normal phantom setup, acquire representative scans to check sequence
execution, the pause, and copy-link effects. Inspecting a link's menu configuration
does not establish that its runtime effect is correct. Broader link characterization
will need dedicated tests of each group and option.

Record actual acquisition outcomes separately from the console-status checklists.
Include scans attempted, errors, unexpected behavior, and any changes needed for
setup. If you modify a protocol after the initial export, save another archive,
such as `STEP4_RETURN_AFTER_SETUP.exar1`, and distinguish those edits in the notes.
Console runnability, acquisition success, and image quality are separate evidence.

## Compare the initial return

Compare the actual submitted file against the re-export, selecting the paths
the console assigned. For example, if it retains the `Validation` paths:

```bash
spt roundtrip loadtest/step4/STEP4_VALIDATION.exar1 \
  loadtest/step4/STEP4_RETURN_INITIAL.exar1 \
  --sent-program Validation/STEP4_CONTROL \
  --returned-program Validation/STEP4_CONTROL \
  --observations loadtest/step4/control-observations.json --require-runnable --json \
  > loadtest/step4/control-return.json
spt roundtrip loadtest/step4/STEP4_VALIDATION.exar1 \
  loadtest/step4/STEP4_RETURN_INITIAL.exar1 \
  --sent-program Validation/STEP4_EDIT \
  --returned-program Validation/STEP4_EDIT \
  --observations loadtest/step4/edit-observations.json --require-runnable --json \
  > loadtest/step4/edit-return.json
```

Use `spt tree loadtest/step4/STEP4_RETURN_INITIAL.exar1` to find the returned
paths if the console assigned different ones. Update `--returned-program`
accordingly. This selects the corresponding protocol; a changed program label
still appears as a substantive difference.

Derived-time differences are failures by default. If the only changes are
`lScanTimeSec` and `lTotalScanTimeSec`, review them and repeat with
`--allow-derived`; they remain listed in the report. Other calculated-field
changes, missing scans, altered TR, removed add-ins, changed links, and geometry
defects are substantive. Preview regeneration is retained as separate evidence.
Do not infer runnability from unchanged ASCCONV or successful re-export.
Even a report with `scanner_confirmed: true` does not establish acquisition
success or image quality; those require the separately recorded tests.

## Retain the evidence and extend coverage

Keep the submitted archive, plan, manifest, validation reports, initial return,
completed checklists, comparison reports, optional PDFs, and console/acquisition
notes together. Record any later export separately. A useful result includes
failures as well as passes, with exact scan names and error messages.

After reviewing this trial, test mixed-donor assembly, insert/move/delete
operations, links crossing selection boundaries, and further representative
parameter edits. Each trial should preserve its exact submitted archive and
identify its scanner and sequence builds. Do not generalize one passing trial
to all sequences or all allowed UI values.
