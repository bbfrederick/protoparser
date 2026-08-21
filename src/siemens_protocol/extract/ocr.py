"""OCR fallback: rasterize a page and rebuild spans from tesseract words.

The spans produced here have the same shape as the native ones, in the same
coordinate system (PDF points), so layout reconstruction is identical for
both. Two things are unavoidably weaker on this path:

* one span per *word* rather than per table cell, which is why cell text is
  reassembled by x-position rather than trusted per span, and
* no font weight and no usable font size, which is why section-title
  detection falls back to geometry.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
from types import ModuleType

import pymupdf

from .spans import Page, Span

#: Words tesseract is less sure of than this are dropped.
_MIN_CONF = 30.0
#: Lone characters that are almost always an OCR'd table rule.
_RULE_NOISE = frozenset(".,;:'\"|_`~")
_NOISE_CONF = 75.0
#: Ink extent runs under the nominal point size; a rough hint only.
_SIZE_FROM_EXTENT = 1.25


#: Environment variable naming the tesseract binary, for installs that leave
#: it off ``PATH``. The Windows installer is the usual case: it drops the
#: binary under Program Files and adds nothing to ``PATH``.
TESSERACT_ENV = "SIEMENS_PROTOCOL_TESSERACT"

#: Install locations searched when the binary is not on ``PATH``, per
#: platform. Expanded with :func:`os.path.expandvars` before use, so entries
#: may name environment variables.
WELL_KNOWN_TESSERACT: dict[str, tuple[str, ...]] = {
    "win32": (
        r"%ProgramFiles%\Tesseract-OCR\tesseract.exe",
        r"%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe",
        r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
        r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe",
    ),
    "darwin": (
        "/opt/homebrew/bin/tesseract",  # Homebrew on Apple silicon
        "/usr/local/bin/tesseract",  # Homebrew on Intel
        "/opt/local/bin/tesseract",  # MacPorts
    ),
    "linux": (
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/snap/bin/tesseract",
    ),
}

#: How to install tesseract, quoted back when it is missing. A reader on one
#: platform is not helped by the other two platforms' package managers.
INSTALL_HINT: dict[str, str] = {
    "win32": "winget install UB-Mannheim.TesseractOCR (or: choco install tesseract)",
    "darwin": "brew install tesseract",
    "linux": "apt install tesseract-ocr (or: dnf install tesseract)",
}


class OCRUnavailable(RuntimeError):
    """Raised when the tesseract binary or its Python binding is missing."""


def platform_key() -> str:
    """Which set of platform-specific defaults applies here.

    Returns
    -------
    str
        ``"win32"``, ``"darwin"``, or ``"linux"``. Every other Unix falls
        back to the Linux entries, whose paths are the usual ones there too.
    """
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def install_hint() -> str:
    """The install command to suggest on this platform.

    Returns
    -------
    str
        A package-manager command line.
    """
    return INSTALL_HINT[platform_key()]


def _executable(path: str) -> str | None:
    """Accept a candidate path only if it names a runnable file.

    Parameters
    ----------
    path : str
        A candidate binary path, possibly containing environment variables.

    Returns
    -------
    str or None
        The expanded path, or ``None`` if it is not a runnable file.
    """
    # An unset variable is left verbatim by expandvars rather than emptied,
    # so an unexpanded candidate simply fails the isfile check below.
    expanded = os.path.expandvars(os.path.expanduser(path))
    if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
        return expanded
    return None


def find_tesseract(explicit: str | None = None) -> str | None:
    """Locate the tesseract binary on this machine.

    Searched in order: the path given here, the ``SIEMENS_PROTOCOL_TESSERACT``
    environment variable, ``PATH``, then the platform's usual install
    locations. The last step is what makes a stock Windows install work: its
    installer puts tesseract under Program Files and adds nothing to ``PATH``.

    Parameters
    ----------
    explicit : str or None, optional
        A path or command name given by the caller, which takes precedence
        over everything else. Default ``None``.

    Returns
    -------
    str or None
        The binary to run, or ``None`` to leave pytesseract's own default in
        place.

    Raises
    ------
    OCRUnavailable
        If ``explicit`` was given and names nothing runnable. A bad explicit
        path is a mistake worth reporting, not a reason to quietly run some
        other binary.
    """
    if explicit:
        found = _executable(explicit) or shutil.which(explicit)
        if found is None:
            raise OCRUnavailable(f"no tesseract binary at {explicit!r}")
        return found

    from_env = os.environ.get(TESSERACT_ENV)
    if from_env:
        found = _executable(from_env) or shutil.which(from_env)
        if found is None:
            raise OCRUnavailable(f"{TESSERACT_ENV} is set to {from_env!r}, which is not runnable")
        return found

    on_path = shutil.which("tesseract")
    if on_path is not None:
        return on_path

    for candidate in WELL_KNOWN_TESSERACT[platform_key()]:
        found = _executable(candidate)
        if found is not None:
            return found
    return None


def _require_tesseract(tesseract: str | None = None) -> ModuleType:
    """Import pytesseract, point it at a binary, and confirm it runs.

    Parameters
    ----------
    tesseract : str or None, optional
        An explicit binary to use, overriding discovery. Default ``None``.

    Returns
    -------
    ModuleType
        The imported ``pytesseract`` module, with its ``tesseract_cmd``
        already set when discovery found a binary off ``PATH``.

    Raises
    ------
    OCRUnavailable
        If the binding is not installed, or no binary could be found or run.
    """
    try:
        import pytesseract
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OCRUnavailable(
            "pytesseract is not installed; install the OCR extra with "
            "pip install 'siemens-protocol[ocr]', or pass --ocr never"
        ) from exc

    command = find_tesseract(tesseract)
    if command is not None:
        pytesseract.pytesseract.tesseract_cmd = command
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - depends on environment
        raise OCRUnavailable(
            "the tesseract binary was not found; install it with "
            f"{install_hint()}, or set {TESSERACT_ENV} to its path, "
            "or pass --ocr never"
        ) from exc
    return pytesseract


def ocr_page(
    doc: pymupdf.Document,
    number: int,
    dpi: int = 300,
    lang: str = "eng",
    psm: int = 6,
    tesseract: str | None = None,
) -> Page:
    """Rasterize a page and return OCR-derived spans.

    Parameters
    ----------
    doc : pymupdf.Document
        An open document.
    number : int
        Zero-based page index.
    dpi : int, optional
        Rasterization resolution. Default 300.
    lang : str, optional
        Tesseract language pack. Default ``"eng"``.
    psm : int, optional
        Tesseract page segmentation mode. Default 6, a uniform text block.
    tesseract : str or None, optional
        Path to the tesseract binary, overriding discovery. Default ``None``,
        which searches as :func:`find_tesseract` describes.

    Returns
    -------
    Page
        The page with one span per recognized word, in PDF points.

    Raises
    ------
    OCRUnavailable
        If tesseract or its Python binding is unavailable.
    """
    pytesseract = _require_tesseract(tesseract)
    from PIL import Image

    pdf_page = doc[number]
    rect = pdf_page.rect
    scale = dpi / 72.0
    pix = pdf_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes("png")))

    data = pytesseract.image_to_data(
        image,
        lang=lang,
        config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )

    spans: list[Span] = []
    for i, raw_text in enumerate(data["text"]):
        text = (raw_text or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if 0 <= conf < _MIN_CONF:
            continue
        # The table rules rasterize into stray marks that tesseract reads as
        # lone punctuation. Real content is never a single such character, so
        # dropping low-confidence ones keeps borders out of section names.
        if len(text) == 1 and text in _RULE_NOISE and conf < _NOISE_CONF:
            continue
        left, top = data["left"][i], data["top"][i]
        width, height = data["width"][i], data["height"][i]
        x0, y0 = left / scale, top / scale
        x1, y1 = (left + width) / scale, (top + height) / scale
        spans.append(
            Span(
                text=text,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                # Approximate only, and deliberately not trusted for layout
                # decisions: a word box hugs its ink, so "preparation" and
                # "Off" measure very differently inside one 8pt row. Layout
                # code checks ``Span.source`` before believing a size.
                size=round((y1 - y0) * _SIZE_FROM_EXTENT, 2),
                font="",
                bold=False,
                source="ocr",
            )
        )

    return Page(
        number=number,
        width=rect.width,
        height=rect.height,
        spans=spans,
        source="ocr",
        printable_ratio=1.0,
        raw_text="\n".join(s.text for s in spans),
    )
