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

Each code object records its source as a path relative to the folder above the compiled root
(``python/lib/python3.13/os.py``, ``scripts/startup/...``), never the absolute path of the machine that built
the app (doc/angelo/releasing.rst, "No builder paths"). Importing from the cache replaces it with the real
location (``importlib``'s ``_fix_co_filename``), so tracebacks still name the installed file.
"""

import importlib.util
import marshal
import os
import py_compile
import sys
import sysconfig

CHECKED_HASH = 0b11


def default_roots(stdlib=None, prefix=None):
    """The interpreter's library and the ``scripts`` folder beside its Python folder (``<version>/scripts``)."""
    stdlib = stdlib or sysconfig.get_path("stdlib")
    prefix = prefix or sys.prefix
    return [stdlib, os.path.join(os.path.dirname(prefix), "scripts")]


def cache_file(source):
    """Python's own location for ``source``'s bytecode with no prefix: ``__pycache__/<name>.<tag>.pyc``."""
    folder, name = os.path.split(source)
    return os.path.join(folder, "__pycache__", "%s.%s.pyc" % (name[:-3], sys.implementation.cache_tag))


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
