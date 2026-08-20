# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`siemens-protocol` parses Siemens MR protocol PDF exports into hierarchical JSON
(one entry per scan, sections of key/value parameters, plus a flattened view that
flags parameters printed inconsistently across sections). Supports VE11C and XA60.
See `Design.md` for the design and `README.md` for usage.

### Environment

```bash
.venv/bin/python -m pytest          # always use .venv, not system python3
.venv/bin/pip install -e ".[dev]"   # pymupdf, pytesseract, pillow, pytest, black, isort
```

- `import pymupdf`, not `import fitz` (deprecated alias, emits a warning)
- OCR path needs the `tesseract` binary on PATH (`brew install tesseract`)

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

- `Design.md` says XA60 exports are scrambled CID needing OCR. All 7 current examples
  (both releases) have a clean native text layer, so none takes the OCR path. The
  fallback is built and tested; exercise it with `--ocr always`, not by "fixing" it.
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
