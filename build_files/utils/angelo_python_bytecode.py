# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Precompile every module of the bundled Python library and of Blender's scripts, inside the installation.

Angelo's own interpreter keeps its bytecode in a per-user folder (doc/angelo/releasing.rst, "Nothing is written
inside the app"), and the bundled ``sitecustomize.py`` sends a child interpreter's there too. But a child can
bypass ``sitecustomize`` (pip's isolated build environment puts its own first, so does any ``sitecustomize`` on
``PYTHONPATH``; a venv made from the bundled Python; ``-S``; ``-W``, ``-X dev`` or a user ``.pth`` file import
modules before it), and the shipped commands (``scripts/angelo``) import the add-on. With every module already
compiled, such a child finds valid bytecode and writes none.

The install step (``build_files/cmake/angelo_python_bytecode.cmake``) runs this file with the installed
interpreter itself, so the bytecode is that interpreter's own::

    <installed python> -I -B angelo_python_bytecode.py [folder ...]

``-B`` keeps this script's own imports from writing anything; ``py_compile`` writes regardless. Without
folders: the interpreter's library (``site-packages`` included) and the ``scripts`` folder beside its Python.
The files are checked hash-based ``.pyc`` (PEP 552): valid whatever time an installer gives the sources, checked
against the source on import (an edited source is compiled again), and recognisable as build output
(``release_checks.is_build_bytecode``; the macOS signer keeps them). A module whose bytecode already matches its
source is skipped, so an incremental install compiles only what changed. A file that cannot compile (Python 2
test data) is listed: it cannot be imported either, so nothing ever writes bytecode for it.

The modules an interpreter imports while it starts (``encodings``, ``site``, ``.pth`` imports such as
``_distutils_hack``, ``sitecustomize``) run before ``sitecustomize`` can choose a bytecode folder or turn writing off,
so under ``-O``/``-OO`` (which read ``<name>.<tag>.opt-1.pyc``/``opt-2``) they would write next to their sources.
``startup_sources`` asks the installed interpreter which modules those are, and they are also compiled at both
optimization levels (``precompile_optimized``); a module imported later goes to the bytecode folder or writes none.
A child of Angelo that sees its environment gets the bytecode folder before it starts (``sitecustomize.py`` exports
``PYTHONPYCACHEPREFIX``); these files cover an interpreter that cannot see it (a replaced environment, ``-E``/``-I``,
a run by hand), for 82 files of about 1.7 MB. Other levels are not shipped.

Each code object records its source as a path relative to the folder above the compiled root
(``python/lib/python3.13/os.py``, ``scripts/startup/...``), never the absolute path of the machine that built
the app (doc/angelo/releasing.rst, "No builder paths"). Importing from the cache replaces it with the real
location (``importlib``'s ``_fix_co_filename``), so tracebacks still name the installed file.
"""

import importlib.util
import json
import marshal
import os
import py_compile
import subprocess
import sys
import sysconfig

CHECKED_HASH = 0b11


def default_roots(stdlib=None, prefix=None):
    """The interpreter's library and the ``scripts`` folder beside its Python folder (``<version>/scripts``)."""
    stdlib = stdlib or sysconfig.get_path("stdlib")
    prefix = prefix or sys.prefix
    return [stdlib, os.path.join(os.path.dirname(prefix), "scripts")]


def cache_file(source, optimization=0):
    """Python's own location for ``source``'s bytecode with no prefix: ``__pycache__/<name>.<tag>.pyc``, or
    ``<name>.<tag>.opt-<n>.pyc`` for an optimization level."""
    folder, name = os.path.split(source)
    level = ".opt-%d" % optimization if optimization else ""
    return os.path.join(folder, "__pycache__", "%s.%s%s.pyc" % (name[:-3], sys.implementation.cache_tag, level))


# Run by the installed interpreter as a person's run of it starts (``-I`` keeps the environment and user site out;
# ``-B`` writes nothing): the source of every module loaded before the command runs.
_STARTUP_PROBE = ("import json, sys; print(json.dumps(sorted({m.__file__ for m in list(sys.modules.values()) "
                  "if isinstance(getattr(m, '__file__', None), str) and m.__file__.endswith('.py')})))")


def startup_sources(roots, python=None, run=subprocess.run):
    """The ``.py`` files below ``roots`` that ``python`` (default: this interpreter) imports while it starts."""
    result = run([python or sys.executable, "-I", "-B", "-c", _STARTUP_PROBE], capture_output=True, text=True,
                 timeout=120, check=True)
    # normcase: Windows paths compare without case (a drive letter or folder the probe spells differently).
    inside = [os.path.normcase(os.path.join(os.path.realpath(root), "")) for root in roots]
    return sorted(path for path in json.loads(result.stdout)
                  if any(os.path.normcase(os.path.realpath(path)).startswith(root) for root in inside))


def precompile_optimized(sources, anchor, levels=(1, 2)):
    """Checked hash-based ``opt-1``/``opt-2`` bytecode for ``sources``; ``{"compiled": [...], "current": n}``."""
    report = {"compiled": [], "current": 0}
    for source in sources:
        with open(source, "rb") as handle:
            data = handle.read()
        dfile = display_name(source, anchor)
        for level in levels:
            cfile = cache_file(source, level)
            if up_to_date(cfile, data, dfile):
                report["current"] += 1
                continue
            py_compile.compile(source, cfile=cfile, dfile=dfile, doraise=True, optimize=level,
                               invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
            report["compiled"].append(cfile)
    return report


def display_name(source, anchor):
    """The path a code object records for ``source``: relative to ``anchor``, with ``/`` separators."""
    return os.path.relpath(source, anchor).replace(os.sep, "/")


def up_to_date(cfile, data, dfile=None):
    """True when ``cfile`` is this interpreter's checked hash-based bytecode of exactly ``data``, recording
    ``dfile`` as its source when one is given."""
    try:
        with open(cfile, "rb") as handle:
            header = handle.read(16)
            if not (len(header) == 16 and header[:4] == importlib.util.MAGIC_NUMBER
                    and int.from_bytes(header[4:8], "little") == CHECKED_HASH
                    and header[8:] == importlib.util.source_hash(data)):
                return False
            if dfile is None:
                return True
            code = marshal.loads(handle.read())
    except (OSError, EOFError, ValueError, TypeError):
        return False
    return getattr(code, "co_filename", None) == dfile


def precompile(root, anchor=None):
    """Compile every ``.py`` below ``root``, recording paths relative to ``anchor`` (default: the folder
    above ``root``); ``{"compiled": [...], "current": n, "failed": [(path, error)]}``."""
    anchor = anchor or os.path.dirname(os.path.abspath(root))
    report = {"compiled": [], "current": 0, "failed": []}
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name != "__pycache__")
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            source = os.path.join(folder, name)
            with open(source, "rb") as handle:
                data = handle.read()
            cfile = cache_file(source)
            dfile = display_name(source, anchor)
            if up_to_date(cfile, data, dfile):
                report["current"] += 1
                continue
            try:
                py_compile.compile(source, cfile=cfile, dfile=dfile, doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
            except py_compile.PyCompileError as error:
                report["failed"].append((source, error.exc_type_name))
                continue
            report["compiled"].append(source)
    return report


def main(argv=None):
    given = sys.argv[1:] if argv is None else argv
    roots = given or default_roots()
    # The default roots record paths from the version folder (``python/lib/...``, ``scripts/...``).
    anchor = None if given else os.path.dirname(sys.prefix)
    for root in roots:
        if not os.path.isdir(root):
            print("angelo_python_bytecode: %s is not a folder" % root, file=sys.stderr)
            return 1
        report = precompile(root, anchor)
        print("angelo_python_bytecode: %s: %d compiled, %d current, %d cannot compile%s" % (
            root, len(report["compiled"]), report["current"], len(report["failed"]),
            (" (" + ", ".join(os.path.relpath(path, root) for path, _error in report["failed"][:5]) + ")")
            if report["failed"] else ""))
    if not given:   # The installed interpreter's own startup modules, for -O and -OO (module docstring).
        sources = startup_sources(roots)
        report = precompile_optimized(sources, anchor or os.path.dirname(sys.prefix))
        print("angelo_python_bytecode: %d startup modules at -O and -OO: %d compiled, %d current" % (
            len(sources), len(report["compiled"]), report["current"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
