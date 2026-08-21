"""Golden JSON snapshots, one per example file.

The point is that a layout or profile change shows up as a reviewable diff
rather than as a silent shift in a few hundred values. Snapshots are stored
without the flattened view, which is a pure function of ``sections`` and is
tested directly in ``test_flatten.py``; that keeps the diffs about the thing
that actually changed.

Regenerate deliberately, and read the diff before committing it::

    SIEMENS_PROTOCOL_REGEN=1 pytest tests/test_golden.py
"""

from __future__ import annotations

import json
import os

import pytest

from conftest import EXAMPLE_FILES, EXAMPLE_IDS, GOLDEN, REPO_ROOT, ParseFixture, requires_examples
from siemens_protocol.model import Protocol

REGENERATE = os.environ.get("SIEMENS_PROTOCOL_REGEN") == "1"


def _snapshot_name(pdf: str) -> str:
    """The snapshot file name for one example, qualified by its release.

    Base names are not unique across the example tree: the same protocol
    exported from two software versions keeps its name in both folders, and
    keying on the base name alone made the two overwrite each other's
    snapshot -- silently, since whichever regenerated last simply won.

    Parameters
    ----------
    pdf : str
        Path to the example PDF.

    Returns
    -------
    str
        ``"<VERSION>-<stem>.json"``, using the parent folder as the version.
    """
    version = os.path.basename(os.path.dirname(pdf))
    return f"{version}-{os.path.splitext(os.path.basename(pdf))[0]}.json"


def _snapshot(protocol: Protocol) -> dict:
    """Serialize a protocol in the form stored on disk.

    Parameters
    ----------
    protocol : Protocol
        The parsed document.

    Returns
    -------
    dict
        The document without its flattened view, and with a repo-relative
        source path, in POSIX form, so snapshots are neither machine- nor
        platform-specific. Without the separator normalization every
        snapshot would fail on Windows on its path alone.
    """
    payload = protocol.to_dict(include_flat=False)
    relative = os.path.relpath(protocol.source_file, REPO_ROOT)
    payload["source_file"] = relative.replace(os.sep, "/")
    return payload


@requires_examples
@pytest.mark.parametrize("pdf,_version", EXAMPLE_FILES, ids=EXAMPLE_IDS)
def test_golden_snapshot(parsed: ParseFixture, pdf: str, _version: str) -> None:
    """The parse matches its stored snapshot, scan by scan.

    Parameters
    ----------
    parsed : callable
        The session-scoped parse fixture.
    pdf : str
        Path to the example.
    _version : str
        Version from the folder name. Unused here.

    Returns
    -------
    None
    """
    snapshot = _snapshot(parsed(pdf).protocol)
    path = os.path.join(GOLDEN, _snapshot_name(pdf))

    if REGENERATE:
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        pytest.skip(f"regenerated {os.path.basename(path)}")

    if not os.path.exists(path):
        pytest.skip(
            f"no snapshot for {os.path.basename(pdf)}; "
            "run with SIEMENS_PROTOCOL_REGEN=1 to create one"
        )

    with open(path, encoding="utf-8") as handle:
        expected = json.load(handle)

    # Compare scan by scan so a failure names the scan that moved.
    assert [s["name"] for s in snapshot["scans"]] == [s["name"] for s in expected["scans"]]
    for got, want in zip(snapshot["scans"], expected["scans"]):
        assert got == want, f"scan {want['name']} differs from its snapshot"
    assert snapshot == expected
