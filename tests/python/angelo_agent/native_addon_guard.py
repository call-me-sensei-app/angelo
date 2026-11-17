# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether an app's bundled Angelo add-on is this checkout's (run_native_suites refuses a mismatch).

A native suite runs inside the app, which loads the add-on and startup modules bundled in it; many suites also put
the checkout's ``scripts/addons_core`` first on ``sys.path`` and import its modules (``chosen_tools`` checks
``checkout_modules_loaded``), which then read the assets beside them in the checkout. Run against an app built
before the change under test, or another release's app, a suite tests other code and reports the result as this
checkout's. It has happened: a script-kit case "failed" against an older app whose kit.row still had the defaults
the checkout had removed, and GUI suites ran against an app built before the change they were meant to test.

So before anything runs, ``check`` compares, file by file (content digests; ``__pycache__``, ``.pyc`` and
``.DS_Store`` ignored):

* every file of ``scripts/addons_core/angelo_agent``, both ways: a module the checkout deleted that the app still
  holds is a mismatch too (an install never deletes it);
* every Python module of ``scripts/startup`` one way (each must be in the app, the same): the app's startup folder
  also holds what the build adds.

A Git LFS pointer is never the file it stands for, on either side. In the checkout (one made with
``GIT_LFS_SKIP_SMUDGE=1``) the modules a suite imports read pointer text where their images and .blend files belong:
2.0.0's ``chosen_tools`` drew no base-mesh picture ("not a PNG") from such a checkout while the app held the real
files. ``check`` lists those files as ``pointers`` ("a Git LFS pointer in the checkout, not the file"), and
``refusal`` names them with ``FETCH``, the operator's one-line fetch (LFS is metered: nothing here fetches). In the
app a pointer is only its text: an app overlaid from such a checkout cannot load those files either ("a Git LFS
pointer in the app, not the file").

Standard library only.
"""

import hashlib
from pathlib import Path

ADDON = Path("scripts/addons_core/angelo_agent")
STARTUP = Path("scripts/startup")
IGNORED_DIRS = frozenset({"__pycache__"})
IGNORED_NAMES = frozenset({".DS_Store"})
LISTED = 8   # Differing files named in a message; the report keeps every one.
OVERRIDE = "--allow-addon-mismatch"
FETCH = 'git lfs pull --include "%s/**"' % ADDON.as_posix()   # Run in the checkout: its add-on's LFS files only.


def bundled_scripts(binary):
    """The ``scripts`` folder of the app ``binary`` belongs to that holds Angelo's add-on, or None.

    macOS: ``Angelo.app/Contents/MacOS/Angelo`` -> ``Contents/Resources/<version>/scripts``; Windows and Linux:
    ``<install>/Angelo[.exe]`` -> ``<install>/<version>/scripts``.
    """
    binary = Path(binary)
    for base in (binary.parent.parent / "Resources", binary.parent):
        if base.is_dir():
            for version in sorted(base.iterdir(), reverse=True):
                scripts = version / "scripts"
                if (scripts / "addons_core" / "angelo_agent").is_dir():
                    return scripts
    return None


LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"


def _pointer(path):
    """Whether ``path`` holds a Git LFS pointer's text."""
    if path.stat().st_size >= 1024:
        return False
    with open(path, "rb") as stream:
        return stream.read(len(LFS_POINTER)) == LFS_POINTER


def _digest(path):
    """The file's SHA-256: its own bytes, so a Git LFS pointer's digest is its text's, never the file's."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _paths(root, pattern="*"):
    """``{relative path: path}`` of the files under ``root`` matching ``pattern``, ignored ones left out."""
    if not root.is_dir():
        return {}
    return {path.relative_to(root).as_posix(): path for path in sorted(root.rglob(pattern))
            if path.is_file() and path.name not in IGNORED_NAMES and path.suffix != ".pyc"
            and not IGNORED_DIRS.intersection(path.relative_to(root).parts)}


def _files(root, pattern="*"):
    """``{relative path: content digest}`` of the files under ``root`` matching ``pattern``, ignored ones left out."""
    return {key: _digest(path) for key, path in _paths(root, pattern).items()}


def _combined(files):
    return hashlib.sha256("".join("%s\0%s\n" % item for item in sorted(files.items())).encode()).hexdigest()


def source_files(source_root):
    """``{key: content digest}`` of what the checkout would bundle: its add-on and its startup modules."""
    source_root = Path(source_root)
    files = {"addon/" + key: value for key, value in _files(source_root / ADDON).items()}
    files.update({"startup/" + key: value for key, value in _files(source_root / STARTUP, "*.py").items()})
    return files


def source_digest(source_root):
    """The checkout's combined digest (``check``'s ``source.digest``)."""
    return _combined(source_files(source_root))


def pointers(source_root):
    """The keys (``addon/<path>``) of the checkout's add-on files that are Git LFS pointers, sorted."""
    return ["addon/" + key for key, path in _paths(Path(source_root) / ADDON).items() if _pointer(path)]


def check(binary, source_root):
    """The comparison of ``binary``'s bundled add-on and startup modules with ``source_root``'s, as a dict.

    ``matches`` is True only when every compared file is the same and no side holds a Git LFS pointer in its place;
    ``differing`` names each one that is not (``path: reason``), ``pointers`` the checkout's add-on files that are
    pointers, ``source`` and ``app`` give each side's folder and combined digest.
    """
    source_root = Path(source_root)
    scripts = bundled_scripts(binary)
    source = source_files(source_root)
    in_checkout = pointers(source_root)
    result = {"source": {"folder": str(source_root), "digest": _combined(source), "files": len(source)},
              "app": {"folder": str(scripts) if scripts else None, "binary": str(binary)}, "pointers": in_checkout}
    if scripts is None:
        return dict(result, matches=False, differing=["no Angelo add-on bundled with this binary"])
    app = {"addon/" + key: value for key, value in _files(scripts / "addons_core" / "angelo_agent").items()}
    # A startup key is its path under scripts: "startup/<module>".
    app.update({key: _digest(scripts / key) for key in source
                if key.startswith("startup/") and (scripts / key).is_file()})
    differing = []
    for key in sorted(set(source) | {key for key in app if key.startswith("addon/")}):
        if key not in app:
            differing.append("%s: only in the checkout" % key)
        elif key not in source:
            differing.append("%s: only in the app" % key)
        elif key in in_checkout:
            differing.append("%s: a Git LFS pointer in the checkout, not the file" % key)
        else:
            path = scripts / ("addons_core/angelo_agent/" + key[len("addon/"):] if key.startswith("addon/") else key)
            if _pointer(path):
                differing.append("%s: a Git LFS pointer in the app, not the file" % key)
            elif app[key] != source[key]:
                differing.append("%s: differs" % key)
    result["app"].update(digest=_combined(app), files=len(app))
    return dict(result, matches=not differing, differing=differing)


def _listed(lines):
    shown = lines[:LISTED]
    return "".join("  - %s\n" % line for line in shown) + (
        "  - ... %d more\n" % (len(lines) - len(shown)) if len(lines) > len(shown) else "")


def _sides(result):
    return ("the app's bundled add-on: %s (%s)\nthis checkout's:          %s (%s)\n%s" % (
        result["app"]["folder"] or result["app"]["binary"], result["app"].get("digest", "none")[:16],
        result["source"]["folder"], result["source"]["digest"][:16], _listed(result["differing"])))


def pointer_refusal(result):
    """The message a checkout holding Git LFS pointers in its add-on is refused with, or None when it holds none.

    No override runs past it: the modules a suite imports from the checkout would read pointer text, whichever
    add-on the app holds. Fetching is the operator's (``FETCH``); nothing here fetches."""
    found = result.get("pointers") or []
    if not found:
        return None
    return ("Refused: %d file(s) of this checkout's add-on are Git LFS pointers, not the files, so no suite ran.\n"
            "The suites import this checkout's modules, which would read the pointer text (%s).\n%s"
            "Fetch them in the checkout, then run again:\n  %s\n" % (
                len(found), result["source"]["folder"], _listed(found), FETCH))


def refusal(result):
    """The message a mismatch is refused with: both sides, what differs, and the ways on."""
    return pointer_refusal(result) or (
        "Refused: the app would test another add-on than this checkout's, so no suite ran.\n" + _sides(result)
        + "Install this checkout into the app first (cmake --build <build folder> --target install, then remove "
          "any module the checkout deleted), or pass %s to test the app as it is.\n" % OVERRIDE)


def warning(result):
    """The line (and the differences) printed when a run goes ahead on a mismatch with ``OVERRIDE``."""
    return ("WARNING: %s: these suites test the app's bundled add-on, not this checkout's; their results are the "
            "app's.\n" % OVERRIDE + _sides(result))
