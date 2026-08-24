"""Running the command line tool on behalf of the browser.

The GUI shells out to ``python -m siemens_protocol.cli`` rather than calling
:func:`siemens_protocol.cli.main` in a thread. Two reasons, both practical.
``main`` writes to the process-wide ``sys.stdout``, so capturing it in-process
would mean mutating global state while the HTTP server is serving on another
thread. And a subprocess can be stopped: parsing a directory of protocols can
run for a long time, and a GUI that cannot cancel it is worse than the command
line it is meant to be friendlier than.

Standard output and standard error are merged into one pipe deliberately. The
CLI writes JSON to the first and per-file summaries and warnings to the second,
and a warning is only useful next to the file that produced it.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import IO, Sequence

#: How long :meth:`Job.stop` waits for a terminated child before killing it.
STOP_GRACE_SECONDS = 3.0

#: Lines kept for one run. This has to clear the largest thing the tool
#: legitimately prints, which is ``parse --stdout`` on a big protocol: the
#: longest shipped example is a little over thirty thousand lines of JSON, and
#: a batch run emits one such dump per file. The cap is a backstop against a
#: run that never stops producing output, not a display limit, so it is set
#: well above anything a real protocol reaches.
MAX_LINES = 200000


@dataclass
class Job:
    """One run of the command line tool, and the output it has produced.

    A job is created already running. The browser polls :meth:`snapshot` for
    whatever has arrived since the line it last saw, which is what makes the
    output pane fill in as the tool works rather than all at once at the end.

    Attributes
    ----------
    id : int
        Identifier the browser quotes when polling.
    argv : list of str
        Arguments passed to the tool, without the interpreter that runs it.
    display : str
        The equivalent command line, for showing and for copying.
    cwd : str
        Directory the child was started in, which is what relative paths in
        ``argv`` resolve against.
    """

    id: int
    argv: list[str]
    display: str
    cwd: str
    _lines: list[str] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)
    _returncode: int | None = field(default=None, repr=False)
    _cancelled: bool = field(default=False, repr=False)
    _dropped: int = field(default=0, repr=False)

    def _append(self, line: str) -> None:
        """Record one line of output, discarding the oldest once full.

        Parameters
        ----------
        line : str
            A line of merged standard output and standard error, without its
            trailing newline.

        Returns
        -------
        None
        """
        with self._lock:
            self._lines.append(line)
            excess = len(self._lines) - MAX_LINES
            if excess > 0:
                del self._lines[:excess]
                # Counting what was discarded is what keeps the client's
                # position meaningful. It polls with the number of lines it
                # has seen, which is an absolute count; without this the list
                # index it maps to would shift under it on every drop and the
                # client would silently skip output.
                self._dropped += excess

    def _pump(self, stream: IO[str]) -> None:
        """Read the child's merged output until it closes, then reap it.

        Parameters
        ----------
        stream : IO[str]
            The child's combined stdout and stderr pipe.

        Returns
        -------
        None
        """
        try:
            for line in stream:
                self._append(line.rstrip("\n"))
        finally:
            stream.close()
            process = self._process
            if process is not None:
                self._returncode = process.wait()
            self._done.set()

    def start(self, argv: Sequence[str]) -> None:
        """Spawn the child process and begin collecting its output.

        Parameters
        ----------
        argv : Sequence of str
            The full command, interpreter included.

        Returns
        -------
        None
        """
        environment = dict(os.environ)
        # The child's output is a pipe, and Python leaves a redirected stream
        # on the platform's default encoding -- which on Windows cannot encode
        # the multiplication signs and superscripts these protocols print.
        environment["PYTHONIOENCODING"] = "utf-8"
        # Without this the child buffers its output in block-sized chunks and
        # the pane sits empty until it exits, defeating the point of polling.
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            self._process = subprocess.Popen(
                list(argv),
                cwd=self.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self._append(f"could not start {argv[0]}: {exc}")
            self._returncode = 127
            self._done.set()
            return
        threading.Thread(target=self._pump, args=(self._process.stdout,), daemon=True).start()

    def stop(self) -> None:
        """Ask the child to stop, and insist if it does not.

        Returns
        -------
        None
        """
        process = self._process
        if process is None or process.poll() is not None:
            return
        self._cancelled = True
        process.terminate()
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()

    @property
    def done(self) -> bool:
        """Whether the child has exited and its output is complete.

        Returns
        -------
        bool
            ``True`` once the pipe has closed and the child been reaped.
        """
        return self._done.is_set()

    def snapshot(self, since: int = 0) -> dict:
        """Report the output produced after a given line.

        Parameters
        ----------
        since : int, optional
            Number of lines the caller has already seen. Default ``0``.

        Returns
        -------
        dict
            ``lines`` is what follows that point, ``next`` the count to quote
            next time, ``done`` whether the run has finished, ``returncode``
            the child's exit status once it has, ``cancelled`` whether it was
            stopped rather than finishing, and ``dropped`` how many early
            lines were discarded to bound memory.
        """
        with self._lock:
            dropped = self._dropped
            total = dropped + len(self._lines)
            # ``since`` counts lines the client has seen, which is absolute;
            # translate it into an index into what is still held.
            start = min(max(0, since - dropped), len(self._lines))
            lines = self._lines[start:]
        return {
            "id": self.id,
            "lines": lines,
            "next": total,
            "dropped": dropped,
            "done": self.done,
            "returncode": self._returncode,
            "cancelled": self._cancelled,
            "display": self.display,
        }


class Runner:
    """Runs one command at a time, on behalf of every browser tab.

    One at a time is a deliberate limit rather than an implementation
    shortcut: these commands read and write files the user names, and letting
    two of them race over the same output path is a way to lose work. Starting
    a run while one is going stops the old one first.

    """

    def __init__(self, cwd: str) -> None:
        """Create a runner with nothing running yet.

        Parameters
        ----------
        cwd : str
            Directory child processes are started in, so that relative paths
            in the GUI mean what they would mean in a shell opened there.

        Returns
        -------
        None
        """
        self.cwd = cwd
        self._ids = itertools.count(1)
        self._current: Job | None = None
        self._lock = threading.Lock()

    @staticmethod
    def command_line(argv: Sequence[str]) -> list[str]:
        """Build the full command, interpreter included.

        Invoking the module rather than the ``siemens-protocol-tool`` script means
        the GUI runs the code it was installed alongside, even in an
        environment whose scripts directory is not on ``PATH``.

        Parameters
        ----------
        argv : Sequence of str
            Tool arguments, subcommand first.

        Returns
        -------
        list of str
            The interpreter, ``-m``, the CLI module, then ``argv``.
        """
        return [sys.executable, "-m", "siemens_protocol.cli", *argv]

    def start(self, argv: Sequence[str], display: str) -> Job:
        """Stop whatever is running and start this instead.

        Parameters
        ----------
        argv : Sequence of str
            Tool arguments, subcommand first.
        display : str
            The equivalent command line, for showing to the user.

        Returns
        -------
        Job
            The newly started job.
        """
        with self._lock:
            if self._current is not None and not self._current.done:
                self._current.stop()
            job = Job(id=next(self._ids), argv=list(argv), display=display, cwd=self.cwd)
            self._current = job
        job.start(self.command_line(argv))
        return job

    def current(self) -> Job | None:
        """The most recently started job, running or finished.

        Returns
        -------
        Job or None
            ``None`` before anything has been run.
        """
        with self._lock:
            return self._current

    def stop(self) -> bool:
        """Stop the running job, if there is one.

        Returns
        -------
        bool
            ``True`` if a job was running and has been asked to stop.
        """
        job = self.current()
        if job is None or job.done:
            return False
        job.stop()
        return True
