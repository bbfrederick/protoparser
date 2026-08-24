"""Checks that keep the tool installable and correct on Linux and Windows.

Nothing here needs a Windows machine: each test drives the platform-dependent
code through the same seam the real platform would, so the Linux and Windows
behaviour is exercised from a development machine of any kind. The three
things that actually differ between platforms are covered:

* where the tesseract binary lives, and how it is found when it is off
  ``PATH`` -- the normal state of a stock Windows install;
* what standard output can encode, since these protocols print multiplication
  signs and superscripts and Windows leaves a redirected stream on the legacy
  code page;
* path separators anywhere a path is written into a file rather than merely
  used.
"""

from __future__ import annotations

import inspect
import io
import ntpath
import os
import posixpath
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import EXAMPLE_FILES, requires_examples
from siemens_protocol.extract import ocr as ocr_module
from siemens_protocol.extract.ocr import (
    INSTALL_HINT,
    TESSERACT_ENV,
    WELL_KNOWN_TESSERACT,
    OCRUnavailable,
    find_tesseract,
    install_hint,
    platform_key,
)
from siemens_protocol.gui import browse as gui_browse
from siemens_protocol.gui import runner as gui_runner
from siemens_protocol.gui import server as gui_server
from siemens_protocol.model import Protocol
from siemens_protocol.pipeline import ParseOptions

#: Characters that reach standard output from real protocol values: a voxel
#: size, a volume unit, and the minus sign the parser accepts as printable.
PROTOCOL_CHARACTERS = "0.5\u00d70.5\u00d710.0 mm\u00b3 \u22125.0 deg"

#: Legacy Windows code pages paired with a character above that each one
#: genuinely cannot encode. ``cp437`` is the OEM page a redirected stream
#: falls back to, and it has neither a multiplication sign nor a superscript
#: three; ``cp1252`` covers those but not the minus sign.
CODE_PAGES = [("cp437", "\u00d7"), ("cp437", "\u00b3"), ("cp1252", "\u2212")]


def _fake_binary(directory: Path | str, name: str) -> str:
    """Create an executable file standing in for the tesseract binary.

    Parameters
    ----------
    directory : path-like or str
        Where to create it.
    name : str
        File name to give it.

    Returns
    -------
    str
        The full path to the created file.
    """
    path = os.path.join(str(directory), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\n")
    os.chmod(path, 0o755)
    return path


# --------------------------------------------------------------------------
# Platform identification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reported,expected",
    [
        ("win32", "win32"),
        ("cygwin", "linux"),
        ("darwin", "darwin"),
        ("linux", "linux"),
        ("freebsd14", "linux"),
        ("sunos5", "linux"),
    ],
)
def test_platform_key_covers_every_platform(
    monkeypatch: pytest.MonkeyPatch, reported: str, expected: str
) -> None:
    """Every ``sys.platform`` maps to a set of defaults, with no gap.

    A missing key would raise ``KeyError`` from the discovery table rather
    than degrade, so the Unixes that are neither Linux nor macOS have to land
    somewhere deliberately.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to report a different platform.
    reported : str
        The value ``sys.platform`` takes.
    expected : str
        The defaults key it should select.

    Returns
    -------
    None
    """
    monkeypatch.setattr(sys, "platform", reported)
    assert platform_key() == expected


def test_every_platform_key_has_defaults() -> None:
    """Both lookup tables answer for every key ``platform_key`` can return.

    Returns
    -------
    None
    """
    keys = {"win32", "darwin", "linux"}
    assert keys <= set(WELL_KNOWN_TESSERACT)
    assert keys <= set(INSTALL_HINT)


@pytest.mark.parametrize(
    "platform,expected",
    [("win32", "winget"), ("darwin", "brew"), ("linux", "apt")],
)
def test_install_hint_names_the_local_package_manager(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: str
) -> None:
    """The suggested command is one the reader's platform can actually run.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to report a different platform.
    platform : str
        The value ``sys.platform`` takes.
    expected : str
        A package manager the hint must name.

    Returns
    -------
    None
    """
    monkeypatch.setattr(sys, "platform", platform)
    assert expected in install_hint()


def test_windows_candidates_are_executables_under_expandable_roots() -> None:
    """The Windows paths name ``.exe`` files below variable-rooted folders.

    Hard-coding ``C:\\Program Files`` is wrong on a machine with a localized
    or relocated Program Files, so the candidates go through environment
    variables that Windows always defines.

    Returns
    -------
    None
    """
    for candidate in WELL_KNOWN_TESSERACT["win32"]:
        assert candidate.lower().endswith("tesseract.exe")
        assert candidate.startswith("%") and candidate.count("%") >= 2
        assert "\\" in candidate


# --------------------------------------------------------------------------
# Finding the binary
# --------------------------------------------------------------------------


def test_an_explicit_path_wins_over_everything_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A caller-supplied path is used even when ``PATH`` has a tesseract.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to set the environment variable that must lose.
    tmp_path : pathlib.Path
        Temporary directory for the stand-in binary.

    Returns
    -------
    None
    """
    chosen = _fake_binary(tmp_path, "chosen")
    monkeypatch.setenv(TESSERACT_ENV, _fake_binary(tmp_path, "from_env"))
    assert find_tesseract(chosen) == chosen


def test_the_environment_variable_wins_over_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``SIEMENS_PROTOCOL_TESSERACT`` overrides whatever ``PATH`` offers.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to set the variable and stub the ``PATH`` lookup.
    tmp_path : pathlib.Path
        Temporary directory for the stand-in binary.

    Returns
    -------
    None
    """
    wanted = _fake_binary(tmp_path, "from_env")
    monkeypatch.setenv(TESSERACT_ENV, wanted)
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: "/usr/bin/tesseract")
    assert find_tesseract() == wanted


def test_path_wins_over_the_well_known_locations(monkeypatch: pytest.MonkeyPatch) -> None:
    """An install on ``PATH`` is preferred to a guess at a standard folder.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to clear the variable and stub the ``PATH`` lookup.

    Returns
    -------
    None
    """
    monkeypatch.delenv(TESSERACT_ENV, raising=False)
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: "/somewhere/odd/tesseract")
    assert find_tesseract() == "/somewhere/odd/tesseract"


def test_a_windows_install_off_path_is_still_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Program Files install is found although ``PATH`` has nothing.

    This is the case that makes the tool usable on Windows at all: the
    official installer writes the binary to Program Files and adds no ``PATH``
    entry, so a ``PATH``-only lookup reports tesseract missing on a machine
    that has it.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to report Windows, empty ``PATH``, and point the candidate at a
        temporary directory.
    tmp_path : pathlib.Path
        Stands in for Program Files.

    Returns
    -------
    None
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv(TESSERACT_ENV, raising=False)
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: None)
    installed = _fake_binary(tmp_path, "tesseract.exe")
    monkeypatch.setitem(
        WELL_KNOWN_TESSERACT,
        "win32",
        ("%NOT_A_REAL_VARIABLE%\\tesseract.exe", installed),
    )
    assert find_tesseract() == installed


def test_nothing_found_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery reports absence by returning ``None``, not by raising.

    The caller still has pytesseract's own default to fall back on, and it is
    the caller that turns a genuine failure into ``OCRUnavailable`` with the
    platform's install hint attached.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to empty every source of a binary.

    Returns
    -------
    None
    """
    monkeypatch.delenv(TESSERACT_ENV, raising=False)
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: None)
    monkeypatch.setitem(WELL_KNOWN_TESSERACT, platform_key(), ())
    assert find_tesseract() is None


def test_a_bad_explicit_path_is_reported_not_ignored() -> None:
    """A mistyped ``--tesseract`` fails loudly instead of using another binary.

    Falling through to ``PATH`` would run a different program than the one
    asked for, and would do it silently.

    Returns
    -------
    None
    """
    with pytest.raises(OCRUnavailable) as exc:
        find_tesseract("/no/such/place/tesseract")
    assert "/no/such/place/tesseract" in str(exc.value)


def test_a_bad_environment_variable_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale variable says which variable is stale.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to point the variable at nothing.

    Returns
    -------
    None
    """
    monkeypatch.setenv(TESSERACT_ENV, "/gone/tesseract")
    with pytest.raises(OCRUnavailable) as exc:
        find_tesseract()
    assert TESSERACT_ENV in str(exc.value)


def test_a_bare_command_name_is_resolved_through_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--tesseract tesseract-5`` resolves like a command, not only a path.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to stub the ``PATH`` lookup.

    Returns
    -------
    None
    """
    monkeypatch.setattr(
        ocr_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name else None
    )
    assert find_tesseract("tesseract-5") == "/usr/bin/tesseract-5"


def test_the_discovered_binary_is_handed_to_pytesseract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Discovery is pointless unless pytesseract is told what it found.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to install a stand-in pytesseract module.
    tmp_path : pathlib.Path
        Temporary directory for the stand-in binary.

    Returns
    -------
    None
    """
    inner = SimpleNamespace(tesseract_cmd="tesseract")
    fake = SimpleNamespace(pytesseract=inner, get_tesseract_version=lambda: "5.4.0")
    monkeypatch.setitem(sys.modules, "pytesseract", fake)

    wanted = _fake_binary(tmp_path, "tesseract")
    assert ocr_module._require_tesseract(wanted) is fake
    assert inner.tesseract_cmd == wanted


def test_an_unrunnable_binary_reports_how_to_install_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure names this platform's installer and the override.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to install a pytesseract whose version query fails.

    Returns
    -------
    None
    """

    def explode() -> str:
        """Stand in for a binary that cannot be run.

        Returns
        -------
        str
            Never returns.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("not found")

    inner = SimpleNamespace(tesseract_cmd="tesseract")
    fake = SimpleNamespace(pytesseract=inner, get_tesseract_version=explode)
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv(TESSERACT_ENV, raising=False)
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: None)
    monkeypatch.setitem(WELL_KNOWN_TESSERACT, "linux", ())

    with pytest.raises(OCRUnavailable) as exc:
        ocr_module._require_tesseract()
    message = str(exc.value)
    assert "apt install tesseract-ocr" in message
    assert TESSERACT_ENV in message


# --------------------------------------------------------------------------
# Output encoding
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code_page,character", CODE_PAGES)
def test_protocol_characters_survive_a_legacy_code_page(
    monkeypatch: pytest.MonkeyPatch, code_page: str, character: str
) -> None:
    """Redirected output on Windows encodes the values a protocol contains.

    A Windows console reports UTF-8, but a *redirected* stream falls back to
    the legacy code page. Printing a parsed voxel size to a file therefore
    raised ``UnicodeEncodeError`` while the same command to a terminal
    worked -- a failure that depends on redirection rather than on content,
    which is the hard kind to diagnose.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to substitute a code-page-backed stream for stdout.
    code_page : str
        A legacy Windows encoding.
    character : str
        A character real protocols print that this code page cannot encode.

    Returns
    -------
    None
    """
    from siemens_protocol.cli import use_utf8_output

    # The control: without the fix this stream is where the run dies.
    with pytest.raises(UnicodeEncodeError):
        character.encode(code_page)

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding=code_page, newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    use_utf8_output()
    sys.stdout.write(PROTOCOL_CHARACTERS)
    sys.stdout.flush()
    assert PROTOCOL_CHARACTERS in buffer.getvalue().decode("utf-8")


def test_a_stream_that_cannot_be_reconfigured_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output redirected into a plain object does not crash the run.

    Test capture and some embedding hosts replace ``sys.stdout`` with an
    object that has no ``reconfigure``, and the CLI must not care.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to substitute a bare stream for stdout.

    Returns
    -------
    None
    """
    from siemens_protocol.cli import use_utf8_output

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    use_utf8_output()  # must not raise


#: Calls that read as ``open(`` but open no file, so declare no encoding.
#: ``subprocess.Popen`` is deliberately absent: it does choose an encoding for
#: its pipes, and one that forgets to is exactly the bug this test is for.
NOT_FILE_OPENS = ("pymupdf.open(", "Image.open(", "webbrowser.open(")


def _whole_call(lines: list[str], start: int) -> str:
    """Join a call that begins on one line and may end on a later one.

    Checking a single line misses an argument list broken across several,
    which is how ``subprocess.Popen`` is normally written and how a long
    ``open`` ends up once the formatter has wrapped it.

    Parameters
    ----------
    lines : list of str
        Every line of the source file.
    start : int
        Zero-based index of the line the call begins on.

    Returns
    -------
    str
        The lines from ``start`` up to the one where the parentheses opened on
        ``start`` are closed again, joined together. Falls back to the rest of
        the file if they never balance, which cannot happen in source that
        parses but keeps this from running off the end.
    """
    depth = 0
    collected: list[str] = []
    for line in lines[start:]:
        collected.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            break
    return "".join(collected)


def test_every_file_the_package_opens_declares_an_encoding() -> None:
    """No source file relies on the platform's default text encoding.

    ``open`` without ``encoding=`` reads UTF-8 on Linux and macOS and the
    legacy code page on Windows, so an omission here is a bug that only
    appears on one platform and only for non-ASCII content.

    Returns
    -------
    None
    """
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    offenders: list[str] = []
    for folder, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
            for number, line in enumerate(lines, start=1):
                if "open(" not in line or any(known in line for known in NOT_FILE_OPENS):
                    continue
                call = _whole_call(lines, number - 1)
                if "encoding=" not in call and '"rb"' not in call:
                    offenders.append(f"{path}:{number}")
    assert offenders == []


# --------------------------------------------------------------------------
# Path separators
# --------------------------------------------------------------------------


def test_golden_snapshots_record_posix_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """A snapshot's source path is separator-independent.

    The path is written into a file that is compared byte for byte across
    machines, so a Windows run would otherwise fail every snapshot on the
    separator alone, before comparing a single parameter.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to make the path machinery behave as it does on Windows.

    Returns
    -------
    None
    """
    import test_golden

    monkeypatch.setattr(os, "sep", "\\")
    monkeypatch.setattr(
        os.path, "relpath", lambda path, start=None: "examples\\VE11C\\R01_Mindfulness.pdf"
    )
    protocol = Protocol(source_file="C:\\work\\examples\\VE11C\\R01_Mindfulness.pdf")
    snapshot = test_golden._snapshot(protocol)
    assert snapshot["source_file"] == "examples/VE11C/R01_Mindfulness.pdf"


# --------------------------------------------------------------------------
# The GUI
# --------------------------------------------------------------------------


def test_the_picker_stops_climbing_at_a_root_on_either_platform() -> None:
    """A root has no parent, whether it is ``/``, ``C:\\`` or a UNC share.

    Getting this wrong is invisible on POSIX and confusing on Windows.
    Stripping the trailing separator turns ``C:\\`` into ``C:``, which looks
    like a parent -- and a bare ``C:`` is resolved relative to the current
    directory *on that drive*, so the picker's Up button would jump somewhere
    unrelated rather than stopping.

    Returns
    -------
    None
    """
    assert gui_browse._parent("C:\\", ntpath) is None
    assert gui_browse._parent("\\\\server\\share", ntpath) is None
    assert gui_browse._parent("C:\\protocols", ntpath) == "C:\\"
    assert gui_browse._parent("C:\\protocols\\XA30", ntpath) == "C:\\protocols"

    assert gui_browse._parent("/", posixpath) is None
    assert gui_browse._parent("/protocols", posixpath) == "/"
    assert gui_browse._parent("/protocols/XA30/", posixpath) == "/protocols"


def test_the_picker_reports_paths_the_current_platform_can_reopen() -> None:
    """Every path a listing hands back can be listed again as it stands.

    The browser sends a path straight back to be browsed or run, so a listing
    that returned anything the platform could not resolve -- a mixed
    separator, a relative fragment -- would break on the round trip.

    Returns
    -------
    None
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = gui_browse.listing(os.path.join(root, "src"))

    assert os.path.isabs(result["path"])
    assert gui_browse.listing(result["parent"])["path"] == os.path.dirname(result["path"])
    for entry in result["entries"]:
        assert os.path.isabs(entry["path"])
        assert os.path.exists(entry["path"])
        if entry["dir"]:
            assert gui_browse.listing(entry["path"])["path"] == entry["path"]


def test_the_picker_offers_drive_letters_only_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows needs drive roots; a single ``/`` reaches everything elsewhere.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to present each platform to the same code.

    Returns
    -------
    None
    """
    monkeypatch.setattr(gui_browse.os.path, "exists", lambda path: path in ("C:\\", "D:\\"))

    monkeypatch.setattr(gui_browse.sys, "platform", "win32")
    assert gui_browse._drive_roots() == ["C:\\", "D:\\"]

    for platform in ("linux", "darwin"):
        monkeypatch.setattr(gui_browse.sys, "platform", platform)
        assert gui_browse._drive_roots() == []


def test_the_gui_gives_its_child_an_encoding_windows_can_print_with() -> None:
    """A command run from the GUI writes UTF-8 whatever the code page says.

    The child's output is a pipe, which is the case where Windows leaves the
    stream on the legacy code page -- and these protocols print multiplication
    signs and superscripts that it cannot encode. ``use_utf8_output`` fixes the
    child's own streams, but only once it is running; setting the variable is
    what covers a failure before that, such as a traceback from argument
    parsing.

    Returns
    -------
    None
    """
    source = inspect.getsource(gui_runner.Job.start)
    assert 'environment["PYTHONIOENCODING"] = "utf-8"' in source
    assert 'encoding="utf-8"' in source
    assert 'errors="replace"' in source


def test_the_gui_runs_the_package_it_was_installed_beside() -> None:
    """Commands go through the interpreter, not a script on ``PATH``.

    ``siemens-protocol-tool`` is only on ``PATH`` while the environment is
    activated, and on Windows it lands in ``Scripts`` rather than ``bin``.
    Invoking the module through ``sys.executable`` sidesteps both.

    Returns
    -------
    None
    """
    command = gui_runner.Runner(".").command_line(["versions"])
    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "siemens_protocol.cli"]


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


@requires_examples
def test_the_tesseract_option_reaches_the_ocr_call(
    capsys: pytest.CaptureFixture, tmp_path: Path
) -> None:
    """``--tesseract`` is threaded from the command line to discovery.

    Proved by giving a path that cannot exist and seeing it quoted back: only
    the OCR path can produce that message, so the option must have arrived.

    Parameters
    ----------
    capsys : pytest.CaptureFixture
        Captures the error output.
    tmp_path : pathlib.Path
        Somewhere to write the JSON that will never be produced.

    Returns
    -------
    None
    """
    from siemens_protocol.cli import main

    pdf = EXAMPLE_FILES[0][0]
    code = main(
        [
            "parse",
            pdf,
            "--ocr",
            "always",
            "--tesseract",
            os.path.join(str(tmp_path), "nowhere", "tesseract"),
            "--out",
            os.path.join(str(tmp_path), "out.json"),
        ]
    )
    assert code == 1
    assert "nowhere" in capsys.readouterr().err


def test_parse_options_carries_the_binary_path() -> None:
    """The option survives as a field rather than being dropped in the CLI.

    Returns
    -------
    None
    """
    assert ParseOptions().tesseract is None
    assert ParseOptions(tesseract="/opt/tesseract").tesseract == "/opt/tesseract"
