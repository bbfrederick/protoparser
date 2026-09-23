"""The low-level readers never import the high-level layer.

``analysis/`` -- comparison, cataloging, generation, reporting -- operates on
a document a low-level reader already produced. Two independent producers of
that document (``model.Scan.to_dict`` for a parsed PDF, and
``exar/inspect.py`` hand-building the equivalent dict for an archive) used to
be the package's clearest low-level/high-level entanglement: a fix to one had
no guarantee of reaching the other. ``analysis/archive_view.py`` replaced the
second producer, and this test is what keeps a future change from growing a
third one somewhere under the low-level tree -- ``exar/``, ``extract/``,
``layout/``, ``profiles/``, ``split.py``, ``pipeline.py`` -- rather than
routing through ``model.py`` the way ``archive_view.py`` does.

The sweep is static (``ast``, not an import trace at run time) so it also
catches a deferred, function-local import -- the shape both of the two
existing cross-layer reaches (``analysis/diff.py``'s and
``analysis/summary.py``'s deferred imports of ``exar.patch``/``exar.build``,
which run the other direction and are not what this test guards) already
took, and which a plain ``grep`` for a module-level ``import`` line would
miss.
"""

from __future__ import annotations

import ast
import os
import tempfile

#: Package-relative paths swept for an upward import. Each is either a
#: subpackage directory or a single top-level module.
LOW_LEVEL_PATHS = ("exar", "extract", "layout", "profiles", "split.py", "pipeline.py")


def _analysis_imports(path: str) -> list[tuple[int, str]]:
    """Every import in one file that reaches into ``analysis``.

    Parameters
    ----------
    path : str
        A ``.py`` file to parse.

    Returns
    -------
    list of tuple
        ``(line, spelling)`` for each offending import, module-level or
        nested inside a function -- :func:`ast.walk` does not distinguish,
        which is the point.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "analysis" or module.startswith("analysis."):
                found.append((node.lineno, f"from {'.' * node.level}{module} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "analysis" or ".analysis." in f".{alias.name}.":
                    found.append((node.lineno, f"import {alias.name}"))
    return found


def test_the_low_level_readers_never_import_analysis() -> None:
    """A PDF or archive reader must not import the manipulation layer.

    Every field the low-level tree reads is a fact about the file format;
    everything in ``analysis/`` is a judgement made from those facts
    (a verdict, a diff, a report). The one legitimate bridge is
    ``analysis/archive_view.py``, which is on the ``analysis`` side of the
    boundary by construction -- it imports ``model`` and ``exar``, never the
    reverse -- so this test does not exempt it or anything else; nothing
    under :data:`LOW_LEVEL_PATHS` is allowed to import ``analysis`` at all.

    Returns
    -------
    None
    """
    package_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "siemens_protocol"
    )
    offenders: list[str] = []
    for relative in LOW_LEVEL_PATHS:
        target = os.path.join(package_root, relative)
        if os.path.isfile(target):
            files = [target]
        else:
            files = [
                os.path.join(folder, name)
                for folder, _dirs, names in os.walk(target)
                for name in sorted(names)
                if name.endswith(".py")
            ]
        for path in sorted(files):
            for line, spelling in _analysis_imports(path):
                offenders.append(f"{os.path.relpath(path, package_root)}:{line}: {spelling}")
    assert offenders == []


def test_the_sweep_actually_catches_a_violation() -> None:
    """The detector fires on a deferred import, not only a module-level one.

    Without this, a bug in :func:`_analysis_imports` that only looked at
    module-level imports would leave :func:`test_the_low_level_readers_never_import_analysis`
    passing for the wrong reason -- exactly the shape the two real deferred
    imports in ``analysis/diff.py`` and ``analysis/summary.py`` have, and
    exactly the shape a regression here would most plausibly take.

    Returns
    -------
    None
    """
    sample = "def f():\n" "    from ..analysis.sequences import identify\n" "    return identify\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(sample)
        path = handle.name
    try:
        found = _analysis_imports(path)
    finally:
        os.unlink(path)
    assert found == [(2, "from ..analysis.sequences import ...")]
