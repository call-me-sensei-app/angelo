# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A release ships only what the commit tracks (release_checks.py ``source-clean`` and ``tracked-scripts``).

A build installs whole source folders (``scripts/``, the Cycles add-on and its kernel sources), so any file in the
source tree that the commit does not track would ship: on 2026-10-19 the 1.0.1 Windows MSI carried an untracked
``script_routing.py`` and two theme presets left on a build machine's disk by an earlier extraction. Two checks close
that class for every file, not for those names:

* ``source_clean(source, commit)``: the tree is a git work tree at exactly ``commit`` (when given): no untracked,
  non-ignored file and no tracked file modified or deleted. Run before a release build compiles.
* ``untracked_shipped(scripts, source)``: every file of a built bundle's ``scripts`` folder (bytecode excluded) is a
  file the commit tracks at the same path, or a built or relocated file one of ``BUILT`` derives from a tracked
  source; anything else is listed. Run on the installed bundle and on an MSI's unpacked payload.

Standard library only.
"""

import re
import subprocess
from pathlib import Path, PurePosixPath

BYTECODE = (".pyc", ".pyo")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"
# Cycles' precompiled GPU kernels (installed into addons_core/cycles/lib by intern/cycles/kernel/device/*/CMakeLists.txt,
# zstd-compressed): each binary's name says which tracked kernel source it is compiled from.
_GPU_KERNEL = re.compile(r"kernel_(?:(?P<cuda>sm_\d+\.cubin|compute_\d+\.ptx)|(?P<hip>gfx\w+\.fatbin)|"
                         r"(?P<hiprt>rt_gfx\w+\.hipfb)|optix(?P<optix>(?:_\w+?)?)\.ptx)\.zst")
_OPTIX_ELSEWHERE = {"_osl_services": "intern/cycles/kernel/osl/services_optix.cu"}   # cycles_optix_kernel_add's source


def _gpu_kernel_sources(name):
    match = _GPU_KERNEL.fullmatch(name)
    if not match:
        return []
    device = "intern/cycles/kernel/device/"
    if match.group("cuda"):
        return [device + "cuda/kernel.cu"]
    if match.group("hip"):
        return [device + "hip/kernel.cpp"]
    if match.group("hiprt"):
        return [device + "hiprt/kernel.cpp"]
    variant = match.group("optix")
    return [_OPTIX_ELSEWHERE.get(variant, device + "optix/kernel%s.cu" % variant)]


# Built or relocated files under scripts/: (prefix under scripts/, rule, reason). A rule maps the shipped file's path
# below the prefix to the tracked sources that justify it; the file is accepted only if one of them is tracked.
BUILT = (
    ("addons_core/cycles/lib/", _gpu_kernel_sources,
     "Cycles' precompiled CUDA, OptiX, HIP and HIP RT kernels, compiled from the tracked kernel source each names"),
    ("addons_core/cycles/license/", lambda rest: ["intern/cycles/doc/license/" + rest],
     "Cycles' licence texts, installed from intern/cycles/doc/license"),
    ("addons_core/cycles/shader/", lambda rest: (["intern/cycles/kernel/osl/shaders/" + rest[:-4] + ".osl"]
                                                 if rest.endswith(".oso") else
                                                 ["intern/cycles/kernel/osl/shaders/" + rest]),
     "Cycles' OSL shaders: .oso compiled from the tracked .osl of the same name, headers and sources as tracked"),
    ("addons_core/cycles/source/", lambda rest: ["intern/cycles/" + rest],
     "Cycles kernel sources for run-time compilation, installed from intern/cycles"),
    ("addons_core/cycles/", lambda rest: ["intern/cycles/blender/addon/" + rest] if "/" not in rest else [],
     "the Cycles add-on, installed from intern/cycles/blender/addon"),
)
RELOCATED = {   # Shipped path under scripts/ -> the tracked file CMake installs there under another name or folder.
    "addons_core/cycles/source/kernel/device/optix/services_optix.cu": "intern/cycles/kernel/osl/services_optix.cu",
}
# Third-party files installed from the pinned precompiled libraries (LIBDIR): accepted only byte-identical to the
# library's own copy. (shipped prefix under scripts/, folder under LIBDIR, reason)
FROM_LIBRARY = (
    ("addons_core/cycles/shader/", "osl/share/OSL/shaders",
     "OSL's standard shader headers, from the pinned OSL library"),
    ("addons_core/cycles/source/kernel/device/hiprt/hiprt/", "hiprt/include/hiprt",
     "HIP RT's device headers for run-time compilation, from the pinned HIP RT library (HIPRT_INCLUDE_DIR)"),
)
BUILT_FILES = {
    "addons_core/io_scene_gltf2/" + name: "the glTF add-on's draco/meshopt bridge library, built by CMake"
    for stem in ("bf_intern_draco_bridge", "bf_intern_meshopt_bridge")
    for name in ("lib%s.dylib" % stem, "lib%s.so" % stem, "%s.dll" % stem)
}


def _git(source, *args):
    return subprocess.run(["git", "-C", str(source), "-c", "core.quotepath=false"] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def is_lfs_pointer(data):
    """True for the text Git LFS leaves in place of a file it has not fetched or checked out."""
    return len(data) < 1024 and data.startswith(LFS_POINTER)


def pointer_problems(source, paths):
    """The files of ``paths`` (relative to ``source``) that must hold their content but are Git LFS pointers.

    git status cannot see this: it runs a pointer through the LFS clean filter unchanged, so a pointer equals the
    commit's entry. A build installs whatever the tree holds, so such a file would ship as its stub.
    """
    found = []
    for rel in paths:
        path = Path(source, rel)
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 1024:
            with open(path, "rb") as handle:
                if is_lfs_pointer(handle.read(1024)):
                    found.append(rel)
    if not found:
        return []
    return ["%d file(s) the build needs real are Git LFS pointers: %s" % (
        len(found), ", ".join(found[:8]) + (", ..." if len(found) > 8 else ""))]


def source_clean(source, commit=None, ignore_submodules=False):
    """Problems that keep ``source`` from being exactly the commit; empty when it is.

    ``ignore_submodules``: leave submodules (the pinned libraries, a repository of their own that the build machine
    brings to its pin separately) out of the comparison, as source_send.py does for every run on that machine.
    """
    head = _git(source, "rev-parse", "HEAD")
    if head.returncode != 0:
        return ["%s is not a git work tree: %s" % (source, head.stderr.strip())]
    problems = []
    if commit and not head.stdout.strip().startswith(commit.strip()):
        problems.append("HEAD is %s, not the commit sent (%s)" % (head.stdout.strip()[:12], commit.strip()[:12]))
    status = _git(source, "status", "--porcelain", "--untracked-files=all", "--ignored=no",
                  *(["--ignore-submodules=all"] if ignore_submodules else []))
    if status.returncode != 0:
        return problems + ["git status failed: %s" % status.stderr.strip()]
    lines = status.stdout.splitlines()
    for label, picked in (("git does not track", [line[3:] for line in lines if line.startswith("?? ")]),
                          ("differ from the commit (modified, deleted or renamed)",
                           [line[3:] for line in lines if not line.startswith("?? ")])):
        if picked:
            problems.append("%d file(s) %s would be built from: %s" % (
                len(picked), label, ", ".join(picked[:8]) + (", ..." if len(picked) > 8 else "")))
    return problems


def tracked(source, rev="HEAD"):
    listing = _git(source, "ls-tree", "-r", "--name-only", rev)
    if listing.returncode != 0:
        raise RuntimeError("git ls-tree failed: %s" % listing.stderr.strip())
    return set(listing.stdout.splitlines())


def _from_library(path, rel, libdir):
    """True when ``rel`` is a FROM_LIBRARY file whose bytes equal the pinned library's own copy."""
    if libdir is None:
        return False
    for prefix, folder, _reason in FROM_LIBRARY:
        if rel.startswith(prefix):
            original = Path(libdir, folder, rel[len(prefix):])
            if original.is_file() and original.read_bytes() == Path(path).read_bytes():
                return True
    return False


def untracked_shipped(scripts, source, rev="HEAD", known=None, libdir=None):
    """Files in a bundle's ``scripts`` folder that no tracked source accounts for (sorted, relative to it).

    ``libdir``: the pinned precompiled-library folder the build used (FROM_LIBRARY files must match it byte for byte).
    """
    known = tracked(source, rev) if known is None else known
    found = []
    for path in sorted(Path(scripts).rglob("*")):
        if not path.is_file() or path.suffix in BYTECODE or "__pycache__" in path.parts:
            continue
        rel = PurePosixPath(path.relative_to(scripts)).as_posix()
        if "scripts/" + rel in known or rel in BUILT_FILES or RELOCATED.get(rel) in known:
            continue
        if _from_library(path, rel, libdir):
            continue
        for prefix, rule, _reason in BUILT:
            if rel.startswith(prefix):
                if any(candidate in known for candidate in rule(rel[len(prefix):])):
                    break
        else:
            found.append(rel)
    return found
