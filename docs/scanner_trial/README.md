# Step 4 scanner trial

This trial exercises the new edit-session graph copier, a pause, a mapped TR
edit, twelve copy references, and the scout's add-in configuration together.
It has **no new scanner result yet**. The existing scanner-return fixtures test
the comparison tooling; they do not establish acceptance of this trial.

From the repository root:

```bash
mkdir -p loadtest/step4
spt assemble examples/XA60/copyparametertest.exar1 docs/scanner_trial/plan.json \
  --out loadtest/step4/STEP4_VALIDATION.exar1 \
  --manifest loadtest/step4/manifest.json
spt validate loadtest/step4/STEP4_VALIDATION.exar1 --json
```

The base program is retained. Under `Validation`, `STEP4_CONTROL` has the
fifteen original acquisitions, fresh graph identities, all twelve relations,
and unchanged acquisition/add-in content. `STEP4_EDIT` has the same fifteen
acquisitions and relations, a `Check setup` pause at step position 1, and one
explicit UI change: `rfMRI REST ME PA XA60` TR **1330 → 1340 ms**, represented
by `alTR[0]` **1330000 → 1340000 us**. The stored derived scan times and other
sequence-calculated fields remain unchanged. The manifest lists every ASCCONV
assignment that actually changes.

Generate one checklist per submitted program:

```bash
spt validate loadtest/step4/STEP4_VALIDATION.exar1 \
  --program Validation/STEP4_CONTROL --checklist --json > control-observations.json
spt validate loadtest/step4/STEP4_VALIDATION.exar1 \
  --program Validation/STEP4_EDIT --checklist --json > edit-observations.json
```

After importing and re-exporting through the XA60 console, record each scan's
observed status (`runnable` or `greyed_out`) in the corresponding checklist.
Also inspect the scout configuration, ordered pause, and copy-reference menu
groups/options; these are covered by the archive comparison, but their runtime
effect is a separate console observation. The tool does not operate the scanner.

Compare the actual submitted file against the re-export, selecting the paths
the console assigned. For example, if it retains the `Validation` paths:

```bash
spt roundtrip loadtest/step4/STEP4_VALIDATION.exar1 scanner-return.exar1 \
  --sent-program Validation/STEP4_CONTROL \
  --returned-program Validation/STEP4_CONTROL \
  --observations control-observations.json --require-runnable --json > control-return.json
spt roundtrip loadtest/step4/STEP4_VALIDATION.exar1 scanner-return.exar1 \
  --sent-program Validation/STEP4_EDIT \
  --returned-program Validation/STEP4_EDIT \
  --observations edit-observations.json --require-runnable --json > edit-return.json
```

Derived-time differences are failures by default. If the only changes are
`lScanTimeSec` and `lTotalScanTimeSec`, review them and repeat with
`--allow-derived`; they remain listed in the report. Other calculated-field
changes, missing scans, altered TR, removed add-ins, changed links, and geometry
defects are substantive. Preview regeneration is retained as separate evidence.
Do not infer runnability from unchanged ASCCONV or successful re-export.
