# Remaining project work

The goals and current limitations are summarized at the beginning of the
[README](README.md#goals-and-current-capabilities). This list distinguishes
implemented workflows from the evidence still needed to meet those goals.

## Priorities

1. **Strengthen parameter validation.** Add characterized numeric bounds,
   discrete choices and sequence-specific dependency rules. The current checks
   catch selected structural, mapping and geometry defects, but do not establish
   that every proposed parameter combination is valid. Preserve calculated
   variables and refuse edits requiring unsupported recalculation.
2. **Complete XA60 scanner trials.** Start with the
   [control/edit trial](docs/scanner_trial/README.md), then cover mixed-donor
   assembly, representative mapped edits, reordering, link boundary handling,
   pauses and add-ins. Collect initial re-exports, console statuses and actual
   acquisition outcomes separately. The new workflow has no recorded hardware
   result yet; historical return fixtures validate comparison behavior only.
3. **Expand characterized UI-to-ASCCONV mappings.** Establish displayed labels,
   units, encodings, packed-bit meanings, sequence/build/mode scope and supporting
   PDF/archive evidence. Orientation, rotation, slice count and position editing
   remain unsupported. Do not enable user edits to internal variables simply
   because they occur in ASCCONV.
4. **Make glossary support release-aware and edit-aware.** Avoid applying XA60
   mapping annotations to VE11C/XA30 lookups. Distinguish a characterized mapping
   from a permitted edit for a concrete release, conversion state and geometry.
   Add sequence catalog entries where mappings exist but the PDF catalog has no
   sequence entry. Keep file-free lookup available.
5. **Characterize copy-link behavior on the console.** The archive already exposes
   and edits sources, targets, groups and flags. Verify the exact settings copied
   by each group and the runtime effects of `IgnoreLastStep`,
   `IgnoreMeasurements`, phase-encoding and step-copy options.
6. **Establish older-release archive read support.** Add genuine VE11C/XA30
   archive fixtures and paired PDFs, then verify structure, sequence identity,
   mappings, units and value equivalences. PDF profiles alone do not establish
   archive compatibility. Non-XA60 writing remains unsupported.

## Further improvements

- Extend scanner-return coverage beyond ASCCONV and the currently inspected
  execution, copy-link and owned add-in metadata to other XProtocol content.
- Curate cross-release value vocabularies as well as label vocabularies. Changes
  such as `Off` versus `Never` currently remain visible unless characterized.
- Extend abbreviation and release vocabulary entries against real matched
  exports; run `vocab check --against` before accepting new aliases.
- Add conditional site policy rules for sequence-specific preferences.
- Consider an MCP server and an optional local natural-language front end after
  the underlying validation and edit boundaries are established.
- Preserve existing add-in content during copying; arbitrary add-in authoring
  still needs characterization.

## Implemented foundations

- Shared file/directory queries by hierarchy, sequence/family and parameter
  predicates; file-free and file-backed parameter glossaries.
- Archive-to-archive comparison of mapped and remaining ASCCONV parameters.
- Transactional XA60 copying, assembly, step operations, copy-link changes and
  characterized UI parameter patches, with change manifests.
- Offline validation, scanner-return comparison and bound console checklists.
- Major-operation timing through `--debug-timings`.

These are implementation capabilities, not blanket scanner-compatibility claims.
