"""Checks for the JavaScript the browser runs.

``tests/test_gui.py`` proves the server, the command specification, the
argument builder and the output buffer. None of that touches ``app.js``, which
is the half of the GUI a user actually operates -- so this file executes it.

There is no browser involved and no dependency to install. ``app.js`` is run
under ``node:vm`` against the small DOM in ``tests/frontend/dom.mjs``, wired to
a real server on a free port, and driven the way a person would drive it:
clicking tabs, typing in fields, opening the picker, pressing Run. What it
renders is compared against what the server sent it rather than against
expectations written out here, so adding a release or an example folder does
not touch this file.

The harness runs once for the whole module and reports each check as a line of
JSON. The tests below group those results, and every one of them asserts a
minimum count as well as the absence of failures: a check that disappears
would otherwise turn its test green by leaving nothing to assert.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Iterator

import pytest

from conftest import REPO_ROOT, requires_examples
from siemens_protocol.gui.server import serve

#: Where the harness and its DOM shim live.
FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

#: Static files the harness reads directly, so that a renamed element id in
#: the markup fails a check instead of being invisible.
STATIC = os.path.join(REPO_ROOT, "src", "siemens_protocol", "gui", "static")

#: Ceiling for the whole harness. It spawns two child runs of the tool, so it
#: is generous; the point is to fail rather than wedge the suite.
HARNESS_TIMEOUT = 600.0

#: Node ships with every supported platform's CI image and with most developer
#: machines, but it is not a dependency of this package, so its absence skips
#: rather than fails. CI asserts these tests actually ran -- a skip reads like
#: a pass in the summary otherwise.
NODE = shutil.which("node")

pytestmark = [
    requires_examples,
    pytest.mark.skipif(NODE is None, reason="node is not installed"),
]


@pytest.fixture(scope="module")
def frontend_server() -> Iterator[Any]:
    """A GUI server on a free port, serving the repository root.

    Yields
    ------
    GuiServer
        A running server, shut down when the module's tests finish.
    """
    running = serve(port=0, cwd=REPO_ROOT)
    try:
        yield running
    finally:
        running.runner.stop()
        running.shutdown()
        running.server_close()


@pytest.fixture(scope="module")
def frontend(frontend_server: Any) -> dict[str, list[dict]]:
    """Run the browser harness once and group what it reported.

    Parameters
    ----------
    frontend_server : GuiServer
        The server the page is driven against.

    Returns
    -------
    dict of str to list of dict
        Every result the harness emitted, keyed by its group. Each result has
        ``name``, ``ok`` and a ``detail`` that is populated only on failure.
    """
    host, port = frontend_server.server_address[0], frontend_server.server_address[1]
    finished = subprocess.run(
        [
            NODE,
            os.path.join(FRONTEND, "checks.mjs"),
            f"http://{host}:{port}",
            frontend_server.token,
            os.path.join(STATIC, "app.js"),
            os.path.join(STATIC, "index.html"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=HARNESS_TIMEOUT,
    )
    grouped: dict[str, list[dict]] = {}
    for line in finished.stdout.splitlines():
        if not line.startswith("{"):
            continue
        result = json.loads(line)
        grouped.setdefault(result["group"], []).append(result)
    if not grouped:  # pragma: no cover - only when node itself fails to start
        raise AssertionError(
            f"the harness reported nothing (exit {finished.returncode})\n{finished.stderr[-4000:]}"
        )
    return grouped


def must_pass(frontend: dict[str, list[dict]], group: str, least: int) -> None:
    """Assert that one group of checks ran and that all of them passed.

    Parameters
    ----------
    frontend : dict of str to list of dict
        The harness results, as grouped by the ``frontend`` fixture.
    group : str
        Which group to examine.
    least : int
        How many checks that group must contain. Asserting a floor is what
        stops a deleted or skipped check from turning this into a test that
        passes because it examined nothing.

    Returns
    -------
    None
    """
    found = frontend.get(group, [])
    assert len(found) >= least, f"{group}: expected at least {least} checks, got {len(found)}"
    bad = [item for item in found if not item["ok"]]
    assert not bad, "\n".join(
        f"{group}: {item['name']}\n    {json.dumps(item['detail'])[:600]}" for item in bad
    )


def test_the_harness_ran_every_check(frontend: dict[str, list[dict]]) -> None:
    """The browser harness reached its end rather than dying part way.

    Checked first, and separately: a harness that throws after ten green
    checks leaves output indistinguishable from a clean ten-check run.

    Returns
    -------
    None
    """
    must_pass(frontend, "harness", 1)


def test_the_page_starts_itself_up(frontend: dict[str, list[dict]]) -> None:
    """The page loads, reaches the server and draws itself from the answer.

    Includes the token scrub: the token arrives in the URL and must not stay
    in the address bar, where a screenshot or a copied link would carry it.

    Returns
    -------
    None
    """
    must_pass(frontend, "startup", 5)


def test_the_tabs_are_generated_from_the_specification(frontend: dict[str, list[dict]]) -> None:
    """Tabs, their order and their selection follow the spec the server sent.

    Returns
    -------
    None
    """
    must_pass(frontend, "tabs", 3)


def test_every_command_renders_its_whole_form(frontend: dict[str, list[dict]]) -> None:
    """Each command's fields, choices, help and summary all reach the page.

    This is the check that makes ``gui/commands.py`` the single description of
    the CLI's surface in practice and not only in principle: a field added
    there must appear in the browser without anyone editing the markup.

    Returns
    -------
    None
    """
    must_pass(frontend, "forms", 6)


def test_the_previewed_command_line_tracks_the_form(frontend: dict[str, list[dict]]) -> None:
    """What the page shows is what the server would build from the same form.

    The preview is compared against a fresh ``/api/preview`` call made with
    the values the page is holding, so this fails if the page ever starts
    composing the line itself instead of asking.

    Returns
    -------
    None
    """
    must_pass(frontend, "preview", 6)


def test_the_file_picker_navigates_and_selects(frontend: dict[str, list[dict]]) -> None:
    """Opening, listing, descending, climbing, typing a path and choosing.

    The listing is compared against ``/api/browse`` for the same directory
    rather than against a written-out set of names, so a new example folder
    does not touch this test.

    Returns
    -------
    None
    """
    must_pass(frontend, "picker", 12)


def test_a_command_runs_from_the_page(frontend: dict[str, list[dict]]) -> None:
    """Run streams output, reports the exit status and re-enables itself.

    The keyboard shortcut is covered here too, since it is a second way into
    the same path and the only one with no visible control to click.

    Returns
    -------
    None
    """
    must_pass(frontend, "run", 6)


def test_the_page_asks_only_for_elements_that_exist(frontend: dict[str, list[dict]]) -> None:
    """No lookup missed, and nothing threw while the page was driven.

    Renaming an element id in ``index.html`` without renaming it in ``app.js``
    breaks the page silently in a browser -- the handler throws inside a
    listener and the button simply stops working. The shim records every
    missed lookup instead.

    Returns
    -------
    None
    """
    must_pass(frontend, "page", 2)


def test_the_toolbar_controls_work(frontend: dict[str, list[dict]]) -> None:
    """Copy reaches the clipboard and Quit ends the session.

    Returns
    -------
    None
    """
    must_pass(frontend, "controls", 2)


def test_quit_reaches_the_server_and_not_only_the_page(
    frontend: dict[str, list[dict]], frontend_server: Any
) -> None:
    """Pressing Quit asks the server to stop, rather than only redrawing.

    The page replaces its own body when Quit is pressed, which looks right
    whether or not the request behind it arrived. ``launch`` waits on this
    event, so it is what actually ends the session.

    Returns
    -------
    None
    """
    assert frontend["controls"], "the harness never reached the Quit check"
    assert frontend_server.quit_requested.is_set()
