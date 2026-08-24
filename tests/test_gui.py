"""Checks for the browser front end.

Three things are worth testing here and nothing else is. The GUI must stay in
step with the command line, since its whole design rests on generating itself
from one description of it; the guards on the loopback server must actually
refuse what they claim to; and the output buffer must hand the browser the
right lines even once a long run has overflowed it, which is the one piece of
arithmetic in the whole front end.

Everything is driven over real HTTP against a real server on a free port, so
what is exercised is the same path the browser takes.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

import pytest

from conftest import EXAMPLE_FILES, REPO_ROOT, requires_examples
from siemens_protocol.cli import build_parser
from siemens_protocol.gui.browse import listing
from siemens_protocol.gui.commands import (
    build_argv,
    command_index,
    command_specs,
    display_command,
)
from siemens_protocol.gui.runner import MAX_LINES, Job, Runner
from siemens_protocol.gui.server import serve

#: How long to wait for a spawned command before calling it hung.
RUN_TIMEOUT = 180.0


@pytest.fixture(scope="module")
def server() -> Iterator[Any]:
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


def request(
    server: Any,
    path: str,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = ...,  # type: ignore[assignment]
    host: str | None = None,
) -> tuple[int, dict]:
    """Make one request to the server and decode its JSON reply.

    Parameters
    ----------
    server : GuiServer
        The server to talk to.
    path : str
        Request path, query string included.
    method : str, optional
        HTTP method. Default ``"GET"``.
    body : dict or None, optional
        A JSON body to send. Default ``None``, meaning none.
    token : str or None, optional
        Value for the ``X-Auth-Token`` header. Defaults to the server's real
        token; ``None`` omits the header entirely.
    host : str or None, optional
        Override for the ``Host`` header. Default ``None``, meaning the one
        urllib would send anyway.

    Returns
    -------
    tuple of (int, dict)
        The status code and the decoded body.
    """
    if token is ...:
        token = server.token
    host_name, port = server.server_address[0], server.server_address[1]
    data = json.dumps(body).encode("utf-8") if body is not None else None
    message = urllib.request.Request(f"http://{host_name}:{port}{path}", data=data, method=method)
    if token is not None:
        message.add_header("X-Auth-Token", token)
    if body is not None:
        message.add_header("Content-Type", "application/json")
    if host is not None:
        message.add_header("Host", host)
    try:
        with urllib.request.urlopen(message, timeout=30) as reply:
            return reply.status, json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def run_command(server: Any, name: str, values: dict) -> tuple[dict, list[str]]:
    """Run one command through the server and collect all of its output.

    Parameters
    ----------
    server : GuiServer
        The server to run on.
    name : str
        The command's name in the GUI specification.
    values : dict
        Field values, as the browser would submit them.

    Returns
    -------
    tuple of (dict, list of str)
        The final snapshot and every line the run produced.

    Raises
    ------
    AssertionError
        If the run could not be started.
    TimeoutError
        If it has not finished within :data:`RUN_TIMEOUT`.
    """
    status, started = request(server, "/api/run", "POST", {"command": name, "values": values})
    assert status == 200, started
    job, since, lines = started["id"], 0, []
    deadline = time.monotonic() + RUN_TIMEOUT
    while time.monotonic() < deadline:
        _, snapshot = request(server, f"/api/job?id={job}&since={since}")
        lines.extend(snapshot["lines"])
        since = snapshot["next"]
        if snapshot["done"]:
            return snapshot, lines
        time.sleep(0.05)
    raise TimeoutError(f"{name} did not finish within {RUN_TIMEOUT}s")


# -- the GUI matches the command line ------------------------------------


def _cli_subparsers() -> dict[str, argparse.ArgumentParser]:
    """The CLI's subcommand parsers, by name.

    Returns
    -------
    dict of str to argparse.ArgumentParser
        Every registered subcommand, taken from the live parser rather than a
        written-out list so that a new subcommand shows up here on its own.
    """
    actions = [
        action
        for action in build_parser()._subparsers._group_actions  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    ]
    return dict(actions[0].choices)


def test_every_cli_subcommand_is_reachable() -> None:
    """The GUI offers every subcommand except the one that launches it.

    This is the check that keeps the front end honest. A subcommand added to
    the CLI and not described in ``gui.commands`` is unreachable from the GUI,
    and nothing else would notice.

    Returns
    -------
    None
    """
    exposed = {command.argv[0] for command in command_specs()}
    # "gui" is excluded on purpose: running it from inside itself would nest a
    # second server inside the first.
    assert set(_cli_subparsers()) - {"gui"} == exposed


def test_vocab_actions_are_all_exposed() -> None:
    """Each of ``vocab``'s three actions has its own form.

    Returns
    -------
    None
    """
    exposed = {command.argv[1] for command in command_specs() if command.argv[0] == "vocab"}
    assert exposed == {"list", "check", "suggest"}


@pytest.mark.parametrize("command", command_specs(), ids=lambda item: item.name)
def test_every_flag_the_gui_offers_is_one_the_cli_takes(command: Any) -> None:
    """No form offers a flag the CLI would reject.

    Parsing the built arguments with the real parser is what proves it: a
    misspelled flag, or one dropped from the CLI, fails here rather than in
    front of a user.

    Parameters
    ----------
    command : Command
        The command specification under test.

    Returns
    -------
    None
    """
    values: dict[str, Any] = {}
    for spec in command.fields:
        if spec.kind == "flag":
            values[spec.name] = True
        elif spec.kind == "choice":
            values[spec.name] = spec.choices[-1]
        elif spec.kind == "pair":
            values[spec.name] = ["left.pdf", "right.pdf"]
        elif spec.kind == "int":
            values[spec.name] = "7"
        elif spec.kind == "list":
            values[spec.name] = "routine,contrast"
        else:
            values[spec.name] = f"{spec.name}.pdf"

    # --flatten and --no-flatten are mutually exclusive in the CLI, and the
    # GUI only ever offers the negative one, so the pair cannot both be set.
    argv = build_argv(command.name, values)
    parsed = build_parser().parse_args(argv)
    assert parsed.command == command.argv[0]


def test_release_choices_come_from_the_registry() -> None:
    """The release drop-down offers exactly what ``--release`` accepts.

    Returns
    -------
    None
    """
    from siemens_protocol.profiles import REGISTRY

    field = next(item for item in command_index()["parse"].fields if item.name == "release")
    assert field.choices == ("auto", *REGISTRY.names())


# -- argument building ----------------------------------------------------


def test_defaults_are_left_off_the_command_line() -> None:
    """A field left at the CLI's own default contributes nothing.

    The command line is shown to the user to be read and retyped, so it must
    not fill up with options that change nothing.

    Returns
    -------
    None
    """
    argv = build_argv("parse", {"input": "a.pdf", "release": "auto", "ocr": "auto", "dpi": "300"})
    assert argv == ["parse", "a.pdf"]


def test_a_list_field_repeats_its_flag() -> None:
    """Comma-separated and newline-separated entries both repeat the flag.

    Returns
    -------
    None
    """
    argv = build_argv("diff", {"left": "a.pdf", "sections": "routine, contrast\ngeometry"})
    assert argv[argv.index("--filter") :] == [
        "--filter",
        "routine",
        "--filter",
        "contrast",
        "--filter",
        "geometry",
    ]


def test_a_required_field_left_empty_is_refused() -> None:
    """An empty required field is reported before anything runs.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="required"):
        build_argv("parse", {})


def test_half_a_pair_is_refused() -> None:
    """``--against`` takes two values or none.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="both"):
        build_argv("vocab-check", {"against": ["only-one.pdf", ""]})


def test_a_non_numeric_integer_is_refused() -> None:
    """A numeric field rejects text before it reaches ``argparse``.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="whole number"):
        build_argv("parse", {"input": "a.pdf", "dpi": "lots"})


def test_a_path_with_spaces_is_quoted_for_display() -> None:
    """The displayed command line survives being pasted into a shell.

    Returns
    -------
    None
    """
    shown = display_command(build_argv("parse", {"input": "my protocols/scan 1.pdf"}))
    assert shown == "siemens-protocol-tool parse 'my protocols/scan 1.pdf'"


# -- the output buffer ----------------------------------------------------


def test_a_snapshot_returns_only_what_the_client_has_not_seen() -> None:
    """Polling from a position returns the lines after it, and no others.

    Returns
    -------
    None
    """
    job = Job(id=1, argv=[], display="", cwd=".")
    for index in range(5):
        job._append(f"line {index}")  # noqa: SLF001
    assert job.snapshot(0)["lines"] == [f"line {index}" for index in range(5)]
    assert job.snapshot(3)["lines"] == ["line 3", "line 4"]
    assert job.snapshot(5)["lines"] == []


def test_an_overflowing_run_does_not_skip_lines() -> None:
    """Once the cap drops old lines, a poll still resumes where it left off.

    ``since`` counts lines the client has seen, which keeps counting up, while
    the buffer's indices shift down every time the front is trimmed. Confusing
    the two makes the browser skip output silently, which looks like the tool
    printed less than it did.

    Returns
    -------
    None
    """
    job = Job(id=1, argv=[], display="", cwd=".")
    total = MAX_LINES + 500
    for index in range(total):
        job._append(f"line {index}")  # noqa: SLF001

    snapshot = job.snapshot(0)
    assert snapshot["dropped"] == 500
    assert snapshot["next"] == total
    # The oldest surviving line is the first one returned, and the newest is
    # the last: nothing in between has been skipped or repeated.
    assert snapshot["lines"][0] == "line 500"
    assert snapshot["lines"][-1] == f"line {total - 1}"
    assert len(snapshot["lines"]) == MAX_LINES

    # A client that had seen the first thousand resumes at line 1000, not at
    # whatever happens to sit at index 1000 of the trimmed buffer.
    assert job.snapshot(1000)["lines"][0] == "line 1000"
    # A client whose position has been dropped entirely gets what is left,
    # rather than an error or a silent gap.
    assert job.snapshot(10)["lines"][0] == "line 500"


def test_the_cap_clears_the_largest_thing_the_tool_prints() -> None:
    """The buffer holds a full ``parse --stdout`` dump of a real protocol.

    The cap exists to bound memory, not to truncate normal output. When it was
    set below what a real protocol prints, the JSON shown in the browser was
    missing its own opening brace.

    Returns
    -------
    None
    """
    assert MAX_LINES > 100000


# -- the server's guards --------------------------------------------------


def test_the_page_and_its_assets_are_served(server: Any) -> None:
    """The three static files load, and nothing else does.

    Returns
    -------
    None
    """
    host, port = server.server_address[0], server.server_address[1]
    for path, needle in (
        ("/", b"<title>MR Protocol Tool"),
        ("/app.js", b"X-Auth-Token"),
        ("/app.css", b"--accent"),
    ):
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=10) as reply:
            assert reply.status == 200
            assert needle in reply.read()

    # Static files are served from a fixed map rather than a path join, which
    # is what stops a request climbing out of the package directory.
    status, _ = request(server, "/../../etc/passwd", token=None)
    assert status == 404


def test_the_api_needs_the_session_token(server: Any) -> None:
    """A request without the token, or with the wrong one, is refused.

    Any page in the user's browser can reach a loopback port, and this API
    runs commands, so the token is what separates this page from every other.

    Returns
    -------
    None
    """
    assert request(server, "/api/spec", token=None)[0] == 401
    assert request(server, "/api/spec", token="not the token")[0] == 401
    assert request(server, "/api/run", "POST", {"command": "versions"}, token=None)[0] == 401
    assert request(server, "/api/spec")[0] == 200


def test_an_unexpected_host_header_is_refused(server: Any) -> None:
    """A request naming a host this server does not answer to is refused.

    This is what closes DNS rebinding, where a name the attacker controls is
    repointed at ``127.0.0.1`` so their page becomes same-origin with this one
    and the token is no longer out of reach.

    Returns
    -------
    None
    """
    assert request(server, "/api/spec", host="evil.example.com")[0] == 403
    assert request(server, "/api/spec", host="evil.example.com:80")[0] == 403


def test_the_spec_describes_every_command(server: Any) -> None:
    """The specification the browser renders covers the whole tool.

    Returns
    -------
    None
    """
    status, spec = request(server, "/api/spec")
    assert status == 200
    assert [item["name"] for item in spec["commands"]] == [item.name for item in command_specs()]
    assert spec["cwd"] == REPO_ROOT
    assert spec["sep"] == os.sep
    assert spec["shortcuts"]


def test_the_preview_builds_the_same_command_that_would_run(server: Any) -> None:
    """Previewing and running share one argument builder.

    Returns
    -------
    None
    """
    status, preview = request(
        server,
        "/api/preview",
        "POST",
        {"command": "parse", "values": {"input": "examples", "ocr": "always"}},
    )
    assert status == 200
    assert preview["display"] == "siemens-protocol-tool parse examples --ocr always"
    assert preview["argv"] == build_argv("parse", {"input": "examples", "ocr": "always"})


def test_a_bad_form_is_reported_before_anything_runs(server: Any) -> None:
    """Validation failures come back as a message, not a started process.

    Returns
    -------
    None
    """
    status, payload = request(server, "/api/preview", "POST", {"command": "parse", "values": {}})
    assert status == 400
    assert "required" in payload["error"]

    status, payload = request(server, "/api/run", "POST", {"command": "nope", "values": {}})
    assert status == 400
    assert "unknown command" in payload["error"]


# -- browsing -------------------------------------------------------------


def test_browsing_lists_directories_whatever_the_filter_says() -> None:
    """A filter for PDFs must not hide the folders the PDFs are in.

    Returns
    -------
    None
    """
    result = listing(os.path.join(REPO_ROOT, "examples"), (".pdf",))
    assert [entry["name"] for entry in result["entries"]] == ["VB17A", "VE11C", "XA30", "XA60"]
    assert all(entry["dir"] for entry in result["entries"])
    assert result["parent"] == REPO_ROOT


@requires_examples
def test_browsing_filters_files_by_suffix() -> None:
    """Only files matching the filter are offered.

    Returns
    -------
    None
    """
    folder = os.path.dirname(EXAMPLE_FILES[0][0])
    result = listing(folder, (".pdf",))
    files = [entry for entry in result["entries"] if not entry["dir"]]
    assert files
    assert all(entry["name"].lower().endswith(".pdf") for entry in files)

    assert not [entry for entry in listing(folder, (".zzz",))["entries"] if not entry["dir"]]


def test_browsing_a_file_lists_the_directory_holding_it() -> None:
    """Pointing the picker at a file opens where that file lives.

    Returns
    -------
    None
    """
    result = listing(os.path.join(REPO_ROOT, "pyproject.toml"))
    assert result["path"] == REPO_ROOT


def test_browsing_something_that_is_not_a_directory_is_refused() -> None:
    """A path that does not exist is an error rather than an empty listing.

    Returns
    -------
    None
    """
    with pytest.raises(ValueError, match="not a directory"):
        listing(os.path.join(REPO_ROOT, "no", "such", "place"))


# -- running --------------------------------------------------------------


def test_a_command_runs_and_its_output_arrives(server: Any) -> None:
    """The simplest command runs to completion and its output comes back.

    Returns
    -------
    None
    """
    snapshot, lines = run_command(server, "versions", {})
    assert snapshot["returncode"] == 0
    assert snapshot["done"]
    assert any("XA60" in line for line in lines)


def test_a_failing_command_reports_its_message_and_status(server: Any) -> None:
    """A failure is shown rather than swallowed.

    Standard error is merged into standard output on purpose: the message
    naming the file is only useful beside the file it refers to.

    Returns
    -------
    None
    """
    snapshot, lines = run_command(server, "parse", {"input": "no-such-file.pdf"})
    assert snapshot["returncode"] != 0
    assert any("no-such-file.pdf" in line for line in lines)


@requires_examples
def test_a_real_protocol_parses_to_complete_json(server: Any) -> None:
    """``parse --stdout`` returns JSON the browser can show whole.

    Returns
    -------
    None
    """
    pdf = EXAMPLE_FILES[0][0]
    snapshot, lines = run_command(server, "parse", {"input": pdf, "stdout": True, "quiet": True})
    assert snapshot["returncode"] == 0
    payload = json.loads("\n".join(lines))
    assert payload["scans"]


@requires_examples
def test_listing_a_protocol_works_through_the_gui(server: Any) -> None:
    """The ``list`` command produces its table.

    Returns
    -------
    None
    """
    snapshot, lines = run_command(server, "list", {"input": EXAMPLE_FILES[0][0]})
    assert snapshot["returncode"] == 0
    assert len(lines) > 2


def test_starting_a_run_supersedes_the_previous_one(server: Any) -> None:
    """Only one command runs at a time.

    These commands write files the user names, so two of them racing over one
    output path is a way to lose work.

    Returns
    -------
    None
    """
    runner = Runner(REPO_ROOT)
    first = runner.start(["versions"], "siemens-protocol-tool versions")
    second = runner.start(["versions"], "siemens-protocol-tool versions")
    assert runner.current() is second
    assert second.id != first.id
    deadline = time.monotonic() + 30
    while not second.done and time.monotonic() < deadline:
        time.sleep(0.05)
    assert second.done


def test_a_run_can_be_stopped(server: Any) -> None:
    """A long run stops when asked, and says that it was stopped.

    Returns
    -------
    None
    """
    runner = Runner(REPO_ROOT)
    job = runner.start(["parse", "examples"], "siemens-protocol-tool parse examples")
    time.sleep(0.5)
    assert runner.stop() is True
    deadline = time.monotonic() + 30
    while not job.done and time.monotonic() < deadline:
        time.sleep(0.05)
    assert job.done
    assert job.snapshot()["cancelled"] is True
    assert runner.stop() is False


def test_a_command_that_cannot_be_started_is_reported() -> None:
    """A missing interpreter becomes a message, not a hung job.

    Returns
    -------
    None
    """
    job = Job(id=1, argv=[], display="", cwd=".")
    job.start(["definitely-not-a-real-binary-xyz"])
    deadline = time.monotonic() + 10
    while not job.done and time.monotonic() < deadline:
        time.sleep(0.05)
    snapshot = job.snapshot()
    assert snapshot["done"]
    assert snapshot["returncode"] == 127
    assert "could not start" in snapshot["lines"][0]


def test_binding_never_waits_on_the_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server binds without asking for a name it does not use.

    ``HTTPServer.server_bind`` fills ``server_name`` from
    ``socket.getfqdn(host)``. Nothing here reads it -- the ``Host`` check works
    from ``server_address`` -- but it is a reverse lookup, and a machine whose
    resolver has no answer for ``127.0.0.1`` blocks on it until it times out.
    That happens inside ``serve``, before ``launch`` prints the URL carrying
    the session token, so the GUI appears to hang with no way into it. macOS CI
    is where this showed up; the resolver is the platform difference, so the
    test removes the resolver instead of the platform.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to make any reverse lookup fail loudly rather than merely be slow.

    Returns
    -------
    None
    """
    asked: list[tuple] = []

    def refuse(*args: Any, **kwargs: Any) -> str:
        """Stand in for a resolver, recording the call and refusing it.

        Parameters
        ----------
        *args : Any
            Whatever the caller passed; only that it was called matters.
        **kwargs : Any
            Likewise.

        Returns
        -------
        str
            Never returns.

        Raises
        ------
        AssertionError
            Always.
        """
        asked.append(args)
        raise AssertionError(f"server_bind asked the resolver to name {args}")

    monkeypatch.setattr(socket, "getfqdn", refuse)
    running = serve(port=0, cwd=REPO_ROOT)
    try:
        assert not asked
        host, port = running.server_address[0], running.server_address[1]
        assert host == "127.0.0.1"
        # The guard that actually needs a name still has one.
        assert f"{host}:{port}" in running.allowed_hosts()
    finally:
        running.runner.stop()
        running.shutdown()
        running.server_close()


def test_the_launcher_prints_its_url_before_it_exits() -> None:
    """The URL appears immediately, even when output is redirected.

    With ``--no-browser`` this line is the only way in, and it carries the
    session token, so it cannot wait. Python block-buffers a stream that is
    not a terminal -- which is what happens when the GUI is piped to a log or
    a pager -- so without an explicit flush the line is held until the server
    exits, by which time it is useless.

    The read is done on a thread with a deadline rather than by calling
    ``readline`` directly: an unflushed stream makes that block forever, and a
    test that hangs wedges the run instead of failing it.

    Returns
    -------
    None
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "siemens_protocol.cli", "gui", "--no-browser"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    first: list[str] = []
    reader = threading.Thread(target=lambda: first.append(process.stdout.readline()), daemon=True)
    try:
        # A pipe rather than a terminal, which is the case the flush is for.
        assert not process.stdout.isatty()
        reader.start()
        reader.join(timeout=20)
        assert first, "the launcher printed nothing within 20s: its output is being buffered"
        assert "serving on http://127.0.0.1:" in first[0]
        assert "token=" in first[0]
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged child
            process.kill()
            process.wait(timeout=15)


def test_the_runner_invokes_the_installed_package(server: Any) -> None:
    """Commands run through the module, not through a script on ``PATH``.

    The GUI must run the code it was installed beside, which is not
    guaranteed to be what ``siemens-protocol-tool`` resolves to in the shell.

    Returns
    -------
    None
    """
    assert Runner(".").command_line(["versions"]) == [
        sys.executable,
        "-m",
        "siemens_protocol.cli",
        "versions",
    ]
