# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether an app's bundled Angelo add-on is this checkout's (run_native_suites refuses a mismatch).

A native suite runs inside the app, which loads the add-on and startup modules bundled in it, never the
checkout's. Run against an app built before the change under test, or another release's app, a suite tests other
code and reports the result as this checkout's. It has happened: a script-kit case "failed" against an older
app whose kit.row still had the defaults the checkout had removed, and GUI suites ran
against an app built before the change they were meant to test.

So before anything runs, ``check`` compares, file by file (content digests; ``__pycache__``, ``.pyc`` and
``.DS_Store`` ignored):

* every file of ``scripts/addons_core/angelo_agent``, both ways: a module the checkout deleted that the app still
  holds is a mismatch too (an install never deletes it);
* every Python module of ``scripts/startup`` one way (each must be in the app, the same): the app's startup folder
  also holds what the build adds.

A Git LFS pointer (a checkout made with ``GIT_LFS_SKIP_SMUDGE=1``, as every Angelo checkout is) counts as the
file it points to: its ``oid`` is that file's SHA-256, the digest the app's real file gets.

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


def _digest(path):
    """The file's SHA-256, or for a Git LFS pointer the SHA-256 (``oid``) of the file it stands for."""
    if path.stat().st_size < 1024:
        head = path.read_bytes()
        if head.startswith(LFS_POINTER):
            for line in head.decode("ascii", "replace").splitlines():
                if line.startswith("oid sha256:"):
                    return line[len("oid sha256:"):].strip()
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(root, pattern="*"):
    """``{relative path: content digest}`` of the files under ``root`` matching ``pattern``, ignored ones left out."""
    if not root.is_dir():
        return {}
    return {path.relative_to(root).as_posix(): _digest(path) for path in sorted(root.rglob(pattern))
            if path.is_file() and path.name not in IGNORED_NAMES and path.suffix != ".pyc"
            and not IGNORED_DIRS.intersection(path.relative_to(root).parts)}


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


def check(binary, source_root):
    """The comparison of ``binary``'s bundled add-on and startup modules with ``source_root``'s, as a dict.

    ``matches`` is True only when every compared file is the same; ``differing`` names each one that is not
    (``path: reason``), ``source`` and ``app`` give each side's folder and combined digest.
    """
    source_root = Path(source_root)
    scripts = bundled_scripts(binary)
    source = source_files(source_root)
    result = {"source": {"folder": str(source_root), "digest": _combined(source), "files": len(source)},
              "app": {"folder": str(scripts) if scripts else None, "binary": str(binary)}}
    if scripts is None:
        return dict(result, matches=False, differing=["no Angelo add-on bundled with this binary"])
    app = {"addon/" + key: value for key, value in _files(scripts / "addons_core" / "angelo_agent").items()}
    # A startup key is its path under scripts: "startup/<module>".
    app.update({key: _digest(scripts / key) for key in source if key.startswith("startup/") and (scripts / key).is_file()})
    differing = []
    for key in sorted(set(source) | {key for key in app if key.startswith("addon/")}):
        if key not in app:
            differing.append("%s: only in the checkout" % key)
        elif key not in source:
            differing.append("%s: only in the app" % key)
        elif app[key] != source[key]:
            differing.append("%s: differs" % key)
    result["app"].update(digest=_combined(app), files=len(app))
    return dict(result, matches=not differing, differing=differing)


def _sides(result):
    listed = result["differing"][:LISTED]
    more = len(result["differing"]) - len(listed)
    return ("the app's bundled add-on: %s (%s)\nthis checkout's:          %s (%s)\n%s%s" % (
        result["app"]["folder"] or result["app"]["binary"], result["app"].get("digest", "none")[:16],
        result["source"]["folder"], result["source"]["digest"][:16],
        "".join("  - %s\n" % line for line in listed), "  - ... %d more\n" % more if more > 0 else ""))


def refusal(result):
    """The message a mismatch is refused with: both sides, what differs, and the two ways on."""
    return ("Refused: the app would test another add-on than this checkout's, so no suite ran.\n" + _sides(result)
            + "Install this checkout into the app first (cmake --build <build folder> --target install, then remove "
              "any module the checkout deleted), or pass %s to test the app as it is.\n" % OVERRIDE)


def warning(result):
    """The line (and the differences) printed when a run goes ahead on a mismatch with ``OVERRIDE``."""
    return ("WARNING: %s: these suites test the app's bundled add-on, not this checkout's; their results are the "
            "app's.\n" % OVERRIDE + _sides(result))
