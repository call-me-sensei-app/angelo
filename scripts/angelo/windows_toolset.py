#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which compiler and linker built a Windows release, read from the built app itself.

The Windows release builds with clang-cl and lld-link (Visual Studio 2022's ClangCL toolset), as official Blender's
Windows release does (``doc/angelo/native_editor_build.rst``, "Toolset: clang-cl")::

    python release_checks.py windows-toolset --binary <build>\\bin\\Release\\Angelo.exe [--pe <image>...] [--report R]

runs the executable in the background to read ``bpy.app.build_cflags`` and ``build_cxxflags`` and reads the PE
headers of the images the build linked (the executable and each ``--pe``). A build is clang-cl's when:

* each compile-flag string passes options through clang-cl's driver (``/clang:-...``): ``platform_win32.cmake``
  adds them for a Clang compiler only, and cl.exe rejects the switch;
* each carries Blender's SIMD baseline for Clang (``macros.cmake``, ``get_compiler_simd_flags``):
  ``-march=x86-64-v2`` or a higher level, where MSVC gets no ``-march`` at all;
* no image carries the "Rich" header, which Microsoft's link.exe writes into every image it links and lld-link
  never does.

``--flags-json FILE`` reads recorded flags (``{"build_cflags": ..., "build_cxxflags": ...}``) instead of running the
executable, for another host. Standard library only; prints each problem and exits 1 when there is one.
"""

import argparse
import json
import os
import re
import struct
import subprocess
import sys

MARKER = "ANGELO_BUILD_FLAGS "
FLAG_NAMES = ("build_cflags", "build_cxxflags", "build_linkflags")
COMPILE_FLAGS = ("build_cflags", "build_cxxflags")
# One line, single quotes only (it travels as one argument of a Windows command line), and no comprehension, which
# would not see names bound by the expression itself in a namespace other than the module's.
READ_FLAGS = ("import bpy, json; print(%r + json.dumps(dict(zip(%r, map(lambda value: value.decode('utf-8', 'replace') "
              "if isinstance(value, bytes) else str(value), (bpy.app.build_cflags, bpy.app.build_cxxflags, "
              "bpy.app.build_linkflags))))), flush=True)" % (MARKER, FLAG_NAMES))
PASSTHROUGH = re.compile(r"(?:^|\s)/clang:-\S")
SIMD = re.compile(r"(?:^|\s)[-/]march=x86-64-v(\d+)(?=\s|$)")
MINIMUM_SIMD_LEVEL = 2


class ToolsetError(Exception):
    pass


def flag_problems(flags):
    """What in the reported compile flags says the build is not clang-cl's (an empty list when it is)."""
    problems = []
    for name in COMPILE_FLAGS:
        value = flags.get(name)
        if not isinstance(value, str) or not value.strip():
            problems.append("bpy.app.%s is not reported" % name)
            continue
        if not PASSTHROUGH.search(value):
            problems.append("bpy.app.%s passes nothing through clang-cl's driver (no /clang:-... option): "
                            "not a clang-cl build" % name)
        levels = [int(level) for level in SIMD.findall(value)]
        if not levels:
            problems.append("bpy.app.%s has no -march=x86-64-vN, Blender's SIMD baseline for Clang" % name)
        elif max(levels) < MINIMUM_SIMD_LEVEL:
            problems.append("bpy.app.%s targets x86-64-v%d, below Blender's baseline x86-64-v%d"
                            % (name, max(levels), MINIMUM_SIMD_LEVEL))
    return problems


def rich_header(data):
    """True when a PE image carries link.exe's Rich header, False when it has none, None when ``data`` is no PE."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset < 0x40 or offset + 4 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        return None
    # The Rich header sits between the DOS stub and the PE header, ending in "Rich" and its XOR key.
    return b"Rich" in data[0x40:offset]


def image_problems(paths):
    """``(problems, facts)`` for the images: each must be a PE image without link.exe's Rich header."""
    problems, facts = [], []
    for path in paths:
        try:
            with open(path, "rb") as handle:
                found = rich_header(handle.read(4096))
        except OSError as error:
            problems.append("%s cannot be read: %s" % (path, error))
            continue
        facts.append({"path": str(path), "rich_header": found})
        if found is None:
            problems.append("%s is not a PE image" % path)
        elif found:
            problems.append("%s carries the Rich header Microsoft's link.exe writes: not linked by lld-link" % path)
    return problems, facts


def read_flags(binary, timeout=600):
    """The build flags the executable reports, from a background run of it (no user settings, no bytecode)."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        done = subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr", READ_FLAGS],
                              capture_output=True, text=True, errors="replace", timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolsetError("%s could not report its build flags: %s" % (binary, error))
    for line in done.stdout.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    tail = " / ".join((done.stdout + done.stderr).strip().splitlines()[-4:])
    raise ToolsetError("%s reported no build flags (exit %s): %s" % (binary, done.returncode, tail))


def check(flags, images):
    """The report: the flags, each image's Rich-header fact, the problems and the verdict."""
    problems = flag_problems(flags)
    more, facts = image_problems(images)
    problems += more
    return {"toolset": "not clang-cl" if problems else "clang-cl", "flags": flags, "images": facts,
            "problems": problems}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="release_checks.py windows-toolset",
                                     description="Refuse a Windows build that clang-cl and lld-link did not make.")
    parser.add_argument("--binary", help="the built executable: run to report its flags, and checked as an image")
    parser.add_argument("--pe", action="append", default=[], help="another image the build linked (repeatable)")
    parser.add_argument("--flags-json", help="recorded flags instead of running --binary")
    parser.add_argument("--report", help="write the report here (JSON)")
    arguments = parser.parse_args(argv)
    if not arguments.binary and not arguments.flags_json:
        parser.error("give --binary or --flags-json")
    images = list(dict.fromkeys(([arguments.binary] if arguments.binary else []) + arguments.pe))
    try:
        if arguments.flags_json:
            with open(arguments.flags_json, encoding="utf-8-sig") as handle:
                flags = json.load(handle)
        else:
            flags = read_flags(arguments.binary)
    except (ToolsetError, OSError, ValueError) as error:
        report = {"toolset": "unknown", "flags": None, "images": [], "problems": [str(error)]}
    else:
        report = check(flags, images)
    if arguments.report:
        with open(arguments.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    for problem in report["problems"]:
        print("windows toolset: %s" % problem, file=sys.stderr)
    if not report["problems"]:
        print("windows toolset: clang-cl and lld-link (%d image(s) checked)" % len(report["images"]))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
