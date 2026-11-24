#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Linux release build's record and debug-symbol split (``build_release_linux.sh``).

``write BUILD``, after the configure: ``BUILD/angelo-release-build.json``, the Linux twin of the Windows build's
record: commit, libraries and their commit, compiler and glibc, the GPU SDK versions (``gpu-versions.json``, which
``linux_gpu_toolkits.py`` read from the SDKs themselves) or the GPU parts left out, the agent runtime, and every
release feature read back from ``CMakeCache.txt``. A release feature that is off fails here, before compiling.
``release_checks.py gpu-sdks --record`` judges the SDK versions against Blender's pins.

``strip BUILD``, after the install: every ELF file the build compiled (``BUILD/install``, not the pinned libraries, which
upstream ships as they are) keeps its symbols in ``BUILD/debug-symbols/<path>.debug`` (``objcopy --only-keep-debug``)
and ships stripped with a ``.gnu_debuglink`` naming that file (a bare file name, no path). The symbols stay with the
release kit for crash reports (offline ``eu-addr2line``) and never ship.

``built BUILD --seconds N``: records the compile's duration (the machine-time receipt) in the record.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from linux_package_checks import is_elf  # noqa: E402

RECORD = "angelo-release-build.json"
# Release features that blender_release.cmake turns on for Linux x86-64 (the GPU ones unless left out on purpose).
FEATURES = (
    "WITH_ALEMBIC", "WITH_AUDASPACE", "WITH_BULLET", "WITH_CODEC_FFMPEG", "WITH_CODEC_SNDFILE", "WITH_CYCLES",
    "WITH_EMBREE", "WITH_CYCLES_OSL", "WITH_CYCLES_PATH_GUIDING", "WITH_DRACO", "WITH_FFTW3", "WITH_FREESTYLE",
    "WITH_HARU", "WITH_IMAGE_OPENJPEG", "WITH_IMAGE_WEBP", "WITH_INTERNATIONAL", "WITH_IO_FBX", "WITH_IO_PLY",
    "WITH_IO_STL", "WITH_IO_WAVEFRONT_OBJ", "WITH_MANIFOLD", "WITH_MATERIALX", "WITH_HYDRA", "WITH_MOD_FLUID",
    "WITH_MOD_OCEANSIM", "WITH_MOD_REMESH", "WITH_NANOVDB", "WITH_OPENAL", "WITH_OPENIMAGEDENOISE",
    "WITH_OPENSUBDIV", "WITH_OPENVDB", "WITH_OPENVDB_BLOSC", "WITH_POTRACE", "WITH_PYTHON_INSTALL",
    "WITH_QUADRIFLOW", "WITH_RUBBERBAND", "WITH_TBB", "WITH_TBB_MALLOC_PROXY", "WITH_USD", "WITH_XR_OPENXR",
    "WITH_BLENDER_THUMBNAILER", "WITH_CYCLES_DEVICE_CUDA", "WITH_CYCLES_DEVICE_HIP", "WITH_VULKAN_BACKEND",
    "WITH_JACK", "WITH_PULSEAUDIO", "WITH_GHOST_X11", "WITH_GHOST_WAYLAND", "WITH_GHOST_XDND", "WITH_X11_XINPUT",
    "WITH_INSTALL_PORTABLE",
)
GPU_FEATURES = {"cuda": ("WITH_CYCLES_CUDA_BINARIES",), "optix": ("WITH_CYCLES_DEVICE_OPTIX",),
                "hip": ("WITH_CYCLES_HIP_BINARIES",), "hiprt": ("WITH_CYCLES_DEVICE_HIPRT",),
                "oneapi": ("WITH_CYCLES_DEVICE_ONEAPI", "WITH_CYCLES_ONEAPI_BINARIES"), "dlss": ("WITH_DLSS",)}


def cache_flags(build):
    flags = {}
    for line in (Path(build) / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace").splitlines():
        name, sep, value = line.partition(":BOOL=")
        if sep and name.startswith("WITH_"):
            flags[name] = value.strip()
    return flags


def feature_problems(flags, allowed_missing):
    wanted = list(FEATURES)
    for part, names in GPU_FEATURES.items():
        if part not in allowed_missing:
            wanted += names
    problems = []
    for name in wanted:
        if name not in flags:
            problems.append("%s is not an option of this tree (renamed upstream?)" % name)
        elif flags[name] != "ON":
            problems.append("%s is %s after the configure; a release turns it on" % (name, flags[name]))
    return problems, {name: flags.get(name) for name in sorted(wanted)}


def _out(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write(build, source, libdir, allowed_missing, agent_runtime):
    build = Path(build)
    problems, features = feature_problems(cache_flags(build), allowed_missing)
    versions_file = build / "gpu-versions.json"
    record = {
        "kind": "angelo-linux-release-build",
        "source_commit": _out(["git", "-C", str(source), "rev-parse", "HEAD"]),
        "libraries": str(libdir),
        "libraries_commit": _out(["git", "-C", str(libdir), "rev-parse", "HEAD"]),
        "libraries_clean": _out(["git", "-C", str(libdir), "status", "--porcelain", "--untracked-files=no"]) == "",
        "compiler": _out(["gcc", "--version"]).splitlines()[0] if _out(["gcc", "--version"]) else None,
        "glibc": os.confstr("CS_GNU_LIBC_VERSION"),
        "toolkit_versions": json.loads(versions_file.read_text()) if versions_file.is_file() else {},
        "allowed_missing": allowed_missing,
        "release_equivalent": not allowed_missing and not problems,
        "features": features,
        "agent_runtime": str(agent_runtime),
        "built_seconds": None,
    }
    (build / RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return problems


def compiled_elf_files(install, runtime_folder=None):
    """ELF files under ``install`` the build made: everything but ``lib/`` (the pinned libraries), the bundled
    Python's own files, which come from the pinned libraries too, and ``runtime_folder``, the agent runtime's folder
    (agent_runtime.json's linux ``folder``, named by the build), which ships byte for byte as it was built and checked.
    A build without the runtime names none."""
    install = Path(install)
    runtime = Path(runtime_folder).parts if runtime_folder else ()
    for folder, dirs, files in os.walk(install):
        rel = Path(folder).relative_to(install)
        if rel.parts[:1] == ("lib",) or "python" in rel.parts[:2] or (runtime and rel.parts[:len(runtime)] == runtime):
            dirs[:] = []
            continue
        for name in sorted(files):
            path = Path(folder) / name
            if not path.is_symlink() and is_elf(path):
                yield path


def strip(build, runtime_folder=None):
    build = Path(build).resolve()
    install, symbols = build / "install", build / "debug-symbols"
    if runtime_folder and not (install / runtime_folder).is_dir():
        # The runtime went somewhere else: it would be stripped like the build's own files.
        raise RuntimeError("the agent runtime folder %s is not in %s: the strip would alter the runtime's bytes"
                           % (runtime_folder, install))
    symbols.mkdir(parents=True, exist_ok=True)   # split.json is written even when nothing needed splitting
    split = []
    for path in compiled_elf_files(install, runtime_folder):
        rel = path.relative_to(install)
        debug = symbols / (rel.as_posix() + ".debug")
        sections = subprocess.run(["readelf", "-S", "-W", str(path)], capture_output=True, text=True, check=True).stdout
        if ".gnu_debuglink" in sections:
            # Split by an earlier run (an incremental build installs unchanged files as they were): its symbols are
            # already in debug-symbols, and splitting the stripped file again would overwrite them with nothing.
            if not debug.is_file():
                raise RuntimeError("%s is stripped but %s is missing: rebuild it" % (rel, debug))
            split.append(rel.as_posix())
            continue
        debug.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["objcopy", "--only-keep-debug", str(path), str(debug)], check=True)
        subprocess.run(["strip", "--strip-debug", "--strip-unneeded", str(path)], check=True)
        # The link records only the symbol file's name (objcopy keeps the base name and its CRC); debuggers look for it
        # beside the binary or under a debug folder.
        subprocess.run(["objcopy", "--add-gnu-debuglink=" + str(debug), str(path)], check=True)
        split.append(rel.as_posix())
    (symbols / "split.json").write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    return split


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    writing = commands.add_parser("write")
    writing.add_argument("build")
    writing.add_argument("--source", required=True)
    writing.add_argument("--libdir", required=True)
    writing.add_argument("--allowed-missing", default="[]", help="a JSON list of GPU parts left out")
    writing.add_argument("--agent-runtime", required=True)
    stripping = commands.add_parser("strip")
    stripping.add_argument("build")
    stripping.add_argument("--runtime-folder", help="the agent runtime's folder in the install: never stripped")
    built = commands.add_parser("built")
    built.add_argument("build")
    built.add_argument("--seconds", type=int, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "write":
        problems = write(arguments.build, arguments.source, arguments.libdir,
                         json.loads(arguments.allowed_missing), arguments.agent_runtime)
        for problem in problems:
            print("release features: %s" % problem, file=sys.stderr)
        return 1 if problems else 0
    if arguments.command == "strip":
        split = strip(arguments.build, arguments.runtime_folder)
        print("debug symbols: %d files split into %s/debug-symbols" % (len(split), arguments.build))
        return 0
    path = Path(arguments.build) / RECORD
    record = json.loads(path.read_text(encoding="utf-8"))
    record["built_seconds"] = arguments.seconds
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
