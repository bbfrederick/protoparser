"""Checks on how the package reports its own version.

The version has one source, the git tag, and reaches three places from
there: package metadata, ``siemens_protocol.__version__``, and the
``--version`` flag. What is worth testing is not the number -- it changes
with every release -- but that those three agree, that the number is a
version rather than a placeholder, and that the flag renaming did not break
the older spelling anyone's scripts may still use.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

import siemens_protocol
from siemens_protocol.cli import build_parser, main

#: A version we would be embarrassed to ship: the marker the package falls
#: back to when it can find no version at all.
UNKNOWN = "0.0.0+unknown"


def test_the_package_reports_a_version() -> None:
    """``__version__`` exists and is not the not-installed placeholder.

    Returns
    -------
    None
    """
    assert isinstance(siemens_protocol.__version__, str)
    assert siemens_protocol.__version__
    assert siemens_protocol.__version__ != UNKNOWN


def test_the_version_looks_like_a_version() -> None:
    """The reported version begins with a numeric release segment.

    Guards against the string being a git description (``v0.1.0-3-gabc``) or
    a path or some other stray value: PEP 440 requires it start with digits.

    Returns
    -------
    None
    """
    assert re.match(r"^\d+(\.\d+)*", siemens_protocol.__version__)


def test_installed_metadata_agrees_with_the_module() -> None:
    """Package metadata and ``__version__`` cannot drift apart.

    Both derive from the same build, which is the point of removing the
    hand-maintained literal: there is no second number to forget.

    Returns
    -------
    None
    """
    from importlib.metadata import version

    assert version("siemens-protocol") == siemens_protocol.__version__


def test_the_cli_reports_the_same_version(capsys: pytest.CaptureFixture) -> None:
    """``siemens-protocol --version`` prints what the package reports.

    Parameters
    ----------
    capsys : pytest.CaptureFixture
        Captures the printed version.

    Returns
    -------
    None
    """
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    printed = capsys.readouterr().out.strip()
    assert printed == f"siemens-protocol {siemens_protocol.__version__}"


def test_the_console_script_reports_a_version() -> None:
    """The installed entry point answers ``--version`` as a real process.

    Exercises the packaging rather than the import: a wheel that installs a
    broken entry point passes every in-process test.

    Returns
    -------
    None
    """
    result = subprocess.run(
        [sys.executable, "-m", "siemens_protocol.cli", "--version"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "No module named" in result.stderr:
        pytest.skip("the package is not installed as a runnable module")
    assert siemens_protocol.__version__ in result.stdout


# --------------------------------------------------------------------------
# The release flag, renamed from --version
# --------------------------------------------------------------------------

#: Subcommands that accept a forced release profile.
RELEASE_SUBCOMMANDS = ["parse", "diff", "check", "list"]


@pytest.mark.parametrize("command", RELEASE_SUBCOMMANDS)
def test_release_is_the_visible_spelling(command: str) -> None:
    """``--release`` sets the forced profile on every subcommand that takes one.

    Parameters
    ----------
    command : str
        The subcommand under test.

    Returns
    -------
    None
    """
    argv = {
        "parse": [command, "f.pdf", "--release", "XA60"],
        "diff": [command, "a.pdf", "b.pdf", "--release", "XA60"],
        "check": [command, "f.pdf", "--release", "XA60"],
        "list": [command, "f.pdf", "--release", "XA60"],
    }[command]
    assert build_parser().parse_args(argv).version == "XA60"


@pytest.mark.parametrize("command", RELEASE_SUBCOMMANDS)
def test_the_old_version_spelling_still_works(command: str) -> None:
    """``--version`` keeps working on subcommands, undocumented.

    It was the only spelling until the tool grew a version of its own, so
    scripts and habits still use it. Breaking them to tidy a name would cost
    more than the ambiguity does.

    Parameters
    ----------
    command : str
        The subcommand under test.

    Returns
    -------
    None
    """
    argv = {
        "parse": [command, "f.pdf", "--version", "XA60"],
        "diff": [command, "a.pdf", "b.pdf", "--version", "XA60"],
        "check": [command, "f.pdf", "--version", "XA60"],
        "list": [command, "f.pdf", "--version", "XA60"],
    }[command]
    assert build_parser().parse_args(argv).version == "XA60"


@pytest.mark.parametrize("command", RELEASE_SUBCOMMANDS)
def test_the_release_flag_defaults_to_auto(command: str) -> None:
    """Neither spelling changes the default when omitted.

    Two argparse actions share one destination, so a default applied twice
    is worth confirming rather than assuming.

    Parameters
    ----------
    command : str
        The subcommand under test.

    Returns
    -------
    None
    """
    argv = {
        "parse": [command, "f.pdf"],
        "diff": [command, "a.pdf", "b.pdf"],
        "check": [command, "f.pdf"],
        "list": [command, "f.pdf"],
    }[command]
    assert build_parser().parse_args(argv).version == "auto"


@pytest.mark.parametrize("command", RELEASE_SUBCOMMANDS)
def test_only_the_new_spelling_is_advertised(command: str) -> None:
    """Help text names ``--release`` and stays quiet about the alias.

    Parameters
    ----------
    command : str
        The subcommand under test.

    Returns
    -------
    None
    """
    parser = build_parser()
    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, __import__("argparse")._SubParsersAction)
    ][0]
    help_text = subparsers.choices[command].format_help()
    assert "--release" in help_text
    assert "--version" not in help_text


def test_the_top_level_version_flag_is_not_a_release_profile() -> None:
    """``--version`` before a subcommand reports the tool, not a profile.

    The two meanings sit one level apart, which is the ambiguity the rename
    exists to remove; this pins the distinction down.

    Returns
    -------
    None
    """
    top_level = build_parser().format_help()
    assert "show the tool's version and exit" in top_level
    assert "--release" not in top_level
