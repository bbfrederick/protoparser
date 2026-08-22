# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`mr-protocol-tool` (the command; the package installs as `siemens-protocol` and
imports as `siemens_protocol`) parses Siemens MR protocol PDF exports into hierarchical JSON
(one entry per scan, sections of key/value parameters, plus a flattened view that
flags parameters printed inconsistently across sections). Supports VB17A, VE11C, XA30 and XA60.
Runs on Linux, macOS and Windows; the package is pure Python and PyMuPDF ships
wheels for all three. See `Design.md` for the design and `README.md` for usage.

### Environment

```bash
.venv/bin/python -m pytest          # always use .venv, not system python3
.venv/bin/pip install -e ".[dev]"   # pymupdf, pytesseract, pillow, pytest, black, isort
```

- `import pymupdf`, not `import fitz` (deprecated alias, emits a warning)
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
  forwarded via `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SIEMENS_PROTOCOL`.
- `--version` before a subcommand = the tool's version; `--release` after one
  = the Siemens profile. `--version` survives as a hidden alias for the latter.

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
- `SIEMENS_PROTOCOL_REGEN=1 .venv/bin/python -m pytest tests/test_golden.py` — regenerate snapshots
- `pythonpath = ["tests"]` in pyproject lets tests import `conftest` helpers directly
- Verify layout changes with the token-conservation test in `tests/test_pipeline.py`:
  every body token must land in exactly one key, value, or section title. It catches
  silent drops that spot-checking values does not.

### Code Formatting

```bash
# Format code with black (line length: 99)
black .

# Sort imports (configured to match black)
isort .

# Check specific file
black --check src/siemens_protocol/layout/sections.py
```

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
  or a trailing one is read as the last scan's parameters.
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
