"""The local HTTP server behind the GUI.

The GUI is a page served to the user's own browser by a server bound to the
loopback interface. That choice keeps the whole thing to the standard library
-- no toolkit to install, and the same behaviour on Linux, macOS and Windows,
because the part that differs by platform is the browser, which is already
there.

A loopback bind is not on its own a security boundary. Any page the user has
open can send a request to ``http://127.0.0.1``, and this server runs commands
and lists directories. Three things stand in the way:

* a random token minted per session, which every ``/api`` request must echo in
  the ``X-Auth-Token`` header. A cross-origin page cannot read it.
* a ``Host`` header check. A hostile name resolved to ``127.0.0.1`` -- DNS
  rebinding, which defeats the origin check on its own -- arrives with a
  ``Host`` this server does not answer to.
* no CORS headers whatsoever. Requiring a custom header means a cross-origin
  request must be preflighted first, and nothing here approves a preflight.

The token is passed once in the URL that gets opened and then scrubbed from
the address bar by the page, so it does not survive into bookmarks, shell
history or a screenshot of the window.
"""

from __future__ import annotations

import json
import os
import secrets
import socketserver
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import __version__
from .browse import listing, shortcuts
from .commands import build_argv, command_specs, display_command
from .runner import Runner

#: Static files this server will serve, by request path. An explicit map
#: rather than a directory join: a join is how a small server acquires a path
#: traversal bug, and there are only three files.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

#: Largest request body accepted, which is ample for a submitted form and
#: small enough that a runaway client cannot exhaust memory.
MAX_BODY = 1 << 20


def _static_bytes(name: str) -> bytes:
    """Read one packaged static file.

    Parameters
    ----------
    name : str
        A base name from :data:`STATIC`.

    Returns
    -------
    bytes
        The file's contents.
    """
    return files(__package__).joinpath("static", name).read_bytes()


class GuiServer(ThreadingHTTPServer):
    """The HTTP server, carrying the session state its handlers need.

    One server is one session: the token that authorizes the page, the
    directory commands run in, and the single runner they all go through.
    """

    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        """Bind the socket without asking the resolver to name us.

        ``HTTPServer.server_bind`` fills ``server_name`` from
        ``socket.getfqdn(host)`` so that CGI handlers can report it. There are
        none here and nothing reads the attribute -- the ``Host`` check works
        from ``server_address`` -- but the call is a reverse lookup, and on a
        machine whose resolver has no answer for ``127.0.0.1`` it blocks until
        it times out. It runs inside ``serve``, before ``launch`` prints the
        URL, so the one line carrying the session token is held behind a DNS
        timeout and the GUI looks hung with no way into it.

        Returns
        -------
        None
        """
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]

    def __init__(self, address: tuple[str, int], handler: type, cwd: str) -> None:
        """Bind the server and mint this session's token.

        Parameters
        ----------
        address : tuple of (str, int)
            Host and port to bind. Port ``0`` takes whatever is free.
        handler : type
            The request handler class.
        cwd : str
            Directory commands are run in, and where the file picker opens.

        Returns
        -------
        None
        """
        super().__init__(address, handler)
        self.token = secrets.token_urlsafe(32)
        self.cwd = cwd
        self.runner = Runner(cwd)
        self.quit_requested = threading.Event()

    @property
    def url(self) -> str:
        """The address to open, token included.

        Returns
        -------
        str
            A URL carrying the session token as a query parameter.
        """
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}/?token={self.token}"

    def allowed_hosts(self) -> set[str]:
        """Host header values this server answers to.

        Returns
        -------
        set of str
            The bound address and ``localhost``, each with the port, plus the
            bare forms a browser may send when the port is implied.
        """
        host, port = self.server_address[0], self.server_address[1]
        names = {host, "localhost", "127.0.0.1", "[::1]"}
        return {f"{name}:{port}" for name in names} | names


class Handler(BaseHTTPRequestHandler):
    """Serves the page and the small JSON API behind it."""

    server: GuiServer  # narrowed from BaseHTTPRequestHandler's BaseServer
    server_version = f"siemens-protocol-gui/{__version__}"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the per-request log.

        The terminal that launched the GUI is where the user watches for the
        URL and for a real failure; a line per poll would bury both.

        Parameters
        ----------
        format : str
            Unused printf-style format.
        *args : Any
            Unused format arguments.

        Returns
        -------
        None
        """
        return

    # -- plumbing ---------------------------------------------------------

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        """Send one complete response.

        Parameters
        ----------
        status : http.HTTPStatus
            Status code to send.
        body : bytes
            The response body.
        content_type : str
            Value for the ``Content-Type`` header.

        Returns
        -------
        None
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This page is served to itself and to nothing else, so it needs no
        # framing, no sniffing and no referrer leaking the token onward.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        """Send a JSON response.

        Parameters
        ----------
        payload : Any
            Anything :mod:`json` can serialize.
        status : http.HTTPStatus, optional
            Status code. Default ``200``.

        Returns
        -------
        None
        """
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        """Send a JSON error the page can display.

        Parameters
        ----------
        status : http.HTTPStatus
            Status code to send.
        message : str
            Text shown to the user.

        Returns
        -------
        None
        """
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        """Whether the request's ``Host`` is one this server answers to.

        Rejecting an unexpected ``Host`` is what closes DNS rebinding, where a
        name the attacker controls is repointed at ``127.0.0.1`` so their page
        becomes same-origin with this one.

        Returns
        -------
        bool
            ``True`` if the header is present and recognized.
        """
        host = (self.headers.get("Host") or "").strip().lower()
        return host in {name.lower() for name in self.server.allowed_hosts()}

    def _authorized(self) -> bool:
        """Whether this request carries the session token.

        Returns
        -------
        bool
            ``True`` if ``X-Auth-Token`` matches, compared in constant time.
        """
        offered = self.headers.get("X-Auth-Token") or ""
        return secrets.compare_digest(offered, self.server.token)

    def _body(self) -> dict:
        """Read and decode a JSON request body.

        Returns
        -------
        dict
            The decoded object, or an empty mapping for an empty body.

        Raises
        ------
        ValueError
            If the body is oversized, is not valid JSON, or is not an object.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request body too large")
        if length <= 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

    # -- routing ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler dispatches to
        """Serve a static file or answer a read-only API request.

        Returns
        -------
        None
        """
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return

        parsed = urlparse(self.path)
        route = parsed.path

        if route in STATIC:
            name, content_type = STATIC[route]
            self._send(HTTPStatus.OK, _static_bytes(name), content_type)
            return

        if not route.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {route}")
            return

        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED, "missing or invalid session token")
            return

        query = parse_qs(parsed.query)
        if route == "/api/spec":
            self._json(self._spec())
        elif route == "/api/browse":
            self._browse(query)
        elif route == "/api/job":
            self._job(query)
        else:
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {route}")

    def do_POST(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler dispatches to
        """Start a run, stop one, or shut the server down.

        Returns
        -------
        None
        """
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return
        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED, "missing or invalid session token")
            return

        route = urlparse(self.path).path
        try:
            payload = self._body()
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        if route == "/api/run":
            self._run(payload, start=True)
        elif route == "/api/preview":
            self._run(payload, start=False)
        elif route == "/api/stop":
            self._json({"stopped": self.server.runner.stop()})
        elif route == "/api/quit":
            self.server.runner.stop()
            self._json({"quitting": True})
            self.server.quit_requested.set()
        else:
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {route}")

    # -- endpoints --------------------------------------------------------

    def _spec(self) -> dict:
        """Everything the page needs to render itself.

        Returns
        -------
        dict
            The command specification, the working directory, the picker's
            shortcuts and the tool's version.
        """
        return {
            "version": __version__,
            "cwd": self.server.cwd,
            "sep": os.sep,
            "commands": [command.to_dict() for command in command_specs()],
            "shortcuts": shortcuts(self.server.cwd),
        }

    def _browse(self, query: dict[str, list[str]]) -> None:
        """Answer a directory listing request.

        Parameters
        ----------
        query : dict of str to list of str
            Parsed query string. ``path`` is the directory, defaulting to the
            working directory; ``accept`` is a comma-separated suffix filter.

        Returns
        -------
        None
        """
        path = (query.get("path") or [self.server.cwd])[0]
        raw = (query.get("accept") or [""])[0]
        accept = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
        try:
            self._json(listing(path, accept))
        except ValueError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))

    def _job(self, query: dict[str, list[str]]) -> None:
        """Answer a poll for a running job's output.

        Parameters
        ----------
        query : dict of str to list of str
            Parsed query string. ``since`` is the number of lines already
            seen, and ``id`` the job being polled.

        Returns
        -------
        None
        """
        job = self.server.runner.current()
        if job is None:
            self._json({"id": 0, "lines": [], "next": 0, "done": True, "returncode": None})
            return
        try:
            since = int((query.get("since") or ["0"])[0])
            wanted = int((query.get("id") or [str(job.id)])[0])
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "since and id must be whole numbers")
            return
        # A poll for a job that has been superseded restarts from the top,
        # so a second tab does not silently show a stale run's output.
        self._json(job.snapshot(since if wanted == job.id else 0))

    def _run(self, payload: dict, start: bool) -> None:
        """Build a command from a submitted form, and optionally run it.

        Previewing and running share this one path on purpose. The command
        line shown as the user types is then produced by the same code that
        builds the arguments actually passed to the tool, so the preview
        cannot drift into describing a command the Run button would not send.

        Parameters
        ----------
        payload : dict
            ``command`` names the command, ``values`` maps field names to
            submitted values.
        start : bool
            Whether to start the command. ``False`` builds and returns the
            command line without running anything.

        Returns
        -------
        None
        """
        name = payload.get("command")
        values = payload.get("values") or {}
        if not isinstance(name, str) or not isinstance(values, dict):
            self._error(HTTPStatus.BAD_REQUEST, "expected a command name and its values")
            return
        try:
            argv = build_argv(name, values)
        except KeyError:
            self._error(HTTPStatus.BAD_REQUEST, f"unknown command: {name}")
            return
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        display = display_command(argv)
        if not start:
            self._json({"display": display, "argv": argv})
            return

        job = self.server.runner.start(argv, display)
        self._json({"id": job.id, "display": job.display, "cwd": job.cwd})


def serve(host: str = "127.0.0.1", port: int = 0, cwd: str | None = None) -> GuiServer:
    """Start the GUI server without blocking.

    Parameters
    ----------
    host : str, optional
        Interface to bind. Default ``"127.0.0.1"``, and there is no good
        reason to widen it: the server runs commands as the user.
    port : int, optional
        Port to bind. Default ``0``, which takes whatever is free and avoids
        colliding with whatever else the user has on a memorable port.
    cwd : str or None, optional
        Directory commands run in. Defaults to the process's own.

    Returns
    -------
    GuiServer
        A server already serving on a background thread.
    """
    server = GuiServer((host, port), Handler, cwd or os.getcwd())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def launch(
    host: str = "127.0.0.1",
    port: int = 0,
    cwd: str | None = None,
    open_browser: bool = True,
) -> int:
    """Serve the GUI and wait until it is shut down.

    Parameters
    ----------
    host : str, optional
        Interface to bind. Default ``"127.0.0.1"``.
    port : int, optional
        Port to bind, or ``0`` for any free one. Default ``0``.
    cwd : str or None, optional
        Directory commands run in. Defaults to the process's own.
    open_browser : bool, optional
        Whether to open the URL in the default browser. Default ``True``.

    Returns
    -------
    int
        ``0`` once the server has stopped, whether from the page's Quit
        button or from an interrupt at the terminal.
    """
    server = serve(host=host, port=port, cwd=cwd)
    # Flushed explicitly: with --no-browser this URL is the only way in, and
    # Python block-buffers a redirected stream, so piping the GUI to a log or
    # a pager would otherwise hold the line back until the server exits.
    print(f"siemens-protocol-tool GUI serving on {server.url}", flush=True)
    print("Press Ctrl-C to stop.", flush=True)
    if open_browser:
        webbrowser.open(server.url)
    try:
        while not server.quit_requested.wait(timeout=0.5):
            pass
    except KeyboardInterrupt:
        print("")
    finally:
        server.runner.stop()
        server.shutdown()
        server.server_close()
    print("GUI stopped.", flush=True)
    return 0
