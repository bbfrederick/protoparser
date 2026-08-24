"""A cross-platform graphical front end for ``mr-protocol-tool``.

The GUI is a page served to the user's own browser by a small server bound to
the loopback interface. That is what makes it cross-platform without adding a
dependency: the standard library provides the server, and the part that
differs between Linux, macOS and Windows is the browser, which is already
installed. It follows the same reasoning the OCR extra does -- a GUI toolkit
would have put a non-pip install step in front of every user, and on several
platforms a ``python3-tk`` or Homebrew package is exactly that.

It exposes the command line rather than reimplementing it. Every form is
generated from the declarations in :mod:`siemens_protocol.gui.commands`, and
running one spawns the CLI itself, so the two cannot disagree about what a
flag does. The equivalent command line is shown for every run, which makes the
GUI a way to learn the tool rather than a substitute for it.

Start it with ``mr-protocol-gui``, with ``mr-protocol-tool gui``, or with
``python -m siemens_protocol.gui``.
"""

from __future__ import annotations

import argparse
import os

from .server import GuiServer, launch, serve

__all__ = ["GuiServer", "launch", "main", "serve"]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the GUI launcher.

    Returns
    -------
    argparse.ArgumentParser
        A parser for the address to bind and whether to open a browser.
    """
    parser = argparse.ArgumentParser(
        prog="mr-protocol-gui",
        description=(
            "Serve the graphical front end for mr-protocol-tool and open it in "
            "the default browser."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="port to serve on (default: any free port)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "interface to bind (default: 127.0.0.1). Widening this exposes a "
            "server that runs commands as you, so leave it alone unless you "
            "have a reason not to"
        ),
    )
    parser.add_argument(
        "--dir",
        dest="cwd",
        metavar="DIR",
        help="directory commands run in, which relative paths resolve against",
    )
    parser.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        help="print the URL instead of opening a browser",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the GUI launcher.

    Parameters
    ----------
    argv : list of str or None, optional
        Arguments to parse. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        ``0`` once the server has stopped, ``1`` if the directory named by
        ``--dir`` does not exist.
    """
    args = build_parser().parse_args(argv)
    if args.cwd is not None and not os.path.isdir(args.cwd):
        print(f"not a directory: {args.cwd}")
        return 1
    return launch(
        host=args.host,
        port=args.port,
        cwd=args.cwd,
        open_browser=args.open_browser,
    )
