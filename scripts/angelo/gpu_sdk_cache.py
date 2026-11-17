#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Does a build folder's configure take each GPU SDK from the folder the build was given, at the version judged?

``gpu_sdk_pins.py`` judges the versions the release script reads from the folders it gives CMake. What compiles is
what CMake resolved, and a build folder keeps what CMake found under a toolkit's folder (OptiX's headers, HIP's
compiler, DLSS's headers) and never looks again when another folder is given. On 2026-11-15 the first configure on
the pinned toolkits passed the pin check and compiled with OptiX 9.1.0's headers and ROCm 6.4's ``hipcc``, left in
the folder's ``CMakeCache.txt`` by earlier configures. The release build calls this around its configure::

    python gpu_sdk_cache.py --build <folder> --used <file> --stage before|after
    <file>: {"options": ["-DOPTIX_ROOT_DIR=...", ...], "versions": {"optix": "9.0.0", "cuda": ["12.9.1", ...], ...}}

``before``: a CUDA toolkit without ``nvvm/bin/cicc``, which ``nvcc`` runs to compile a kernel, stops the build in
seconds (CUDA 13 ships it as a component of its own). Then a folder last configured with other options or versions,
or with none recorded (``angelo-toolkits.json``), has its cache moved aside (``CMakeCache.txt.toolkits-changed``), so
CMake looks for everything again; compiled objects stay.

``after``: the cache CMake wrote is read, and the build stops, naming each entry, when

* an entry named for a toolkit (``OPTIX_``, ``HIP_``, ``DLSS_``, ``OCLOC_`` and the ``IGC_`` ocloc loads) holds a
  path outside the folder given for that toolkit;
* any entry, whatever its name, holds a path in another version of a folder that was given: beside it, in a folder
  of the same name but for its digits (``CUDA/v12.8`` beside ``CUDA/v12.9``, ``ROCm/6.4`` beside ``ROCm/7.1``). CUDA
  is judged this way alone: FindCUDA finds helper libraries outside the toolkit by design;
* what an entry resolved does not report the version judged: ``optix.h`` in ``OPTIX_INCLUDE_DIR``,
  ``hip_version.h`` of ``HIP_HIPCC_EXECUTABLE``'s toolkit, ``version.json`` of each ``CUDA<major>_NVCC_EXECUTABLE``'s
  toolkit, the file version of ``ocloc.exe`` (or ``igc64.dll``) in ``OCLOC_INSTALL_DIR``, the folder of
  ``DLSS_INCLUDE_DIR``; or the configure resolved none for an SDK that was judged.

A clean cache becomes the folder's record. Standard library only.
"""

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

CACHE_NAME, CACHE_ASIDE, TOOLKIT_RECORD = "CMakeCache.txt", "CMakeCache.txt.toolkits-changed", "angelo-toolkits.json"
# A toolkit's own cache entries start with the first word of the option that names its folder (OPTIX_ROOT_DIR,
# OPTIX_INCLUDE_DIR). On Windows ocloc loads the Graphics Compiler from its own folder (Cycles' external_libs.cmake).
ALSO_NAMED = {"OCLOC": ("IGC",)}
NVCC = re.compile(r"^CUDA\d*_NVCC_EXECUTABLE$")
LABELS = {"optix": "OptiX SDK", "hip": "HIP SDK", "cuda": "CUDA Toolkit", "ocloc": "ocloc", "dlss": "DLSS SDK"}


def _path(value):
    """An absolute path as paths compare (forward slashes, no trailing one, a drive's path in lower case), or None."""
    text = value.strip().strip('"').replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:(/|$)", text):
        return text.lower()
    return text if text.startswith("/") else None


def _inside(path, folder):
    return path == folder or path.startswith(folder + "/")


def cache_entries(cache_text):
    """``(name, value)`` of every entry of a CMake cache."""
    for line in cache_text.splitlines():
        match = re.match(r"^([^#/:=\s][^:=\s]*):[A-Z]*=(.*)$", line)
        if match:
            yield match.group(1), match.group(2)


def option_paths(options):
    """``(name, path as compared, as given)`` of the ``-D`` options whose value is an absolute path."""
    found = []
    for option in options:
        match = re.match(r"^-D([A-Za-z0-9_]+)=(.*)$", option)
        if match and _path(match.group(2)):
            found.append((match.group(1), _path(match.group(2)), match.group(2).strip()))
    return found


def toolkit_roots(options):
    """``{first word: [(folder as compared, as given)]}`` of the options that name a toolkit's folder, but CUDA's."""
    roots = {}
    for name, folder, shown in option_paths(options):
        word = name.split("_", 1)[0]
        if "_" in name and not word.startswith("CUDA"):
            for also in (word,) + ALSO_NAMED.get(word, ()):
                roots.setdefault(also, []).append((folder, shown))
    return roots


def _other_version(path, folder):
    """Whether ``path`` lies in another version of what is installed at ``folder``: beside the last part of
    ``folder`` that holds a digit, in a folder of the same name but for its digits."""
    def shape(name):
        return re.sub(r"[\d.]+", "#", name)
    parts, given = path.split("/"), folder.split("/")
    for index in range(len(given) - 1, 0, -1):
        if re.search(r"\d", given[index]):
            return (len(parts) > index and parts[:index] == given[:index] and parts[index] != given[index]
                    and shape(parts[index]) == shape(given[index]))
    return False


def cache_strays(cache_text, options):
    """Lines for the cache's paths that lie outside the folder given for their toolkit, or in another version of a
    folder that was given."""
    named, given, strays = toolkit_roots(options), [(folder, shown) for _n, folder, shown in option_paths(options)], []
    for name, value in cache_entries(cache_text):
        word = name.split("_", 1)[0]
        for item in re.split(r'[;\[\]"]', value):
            path = _path(item)
            if not path:
                continue
            if "_" in name and word in named and not any(_inside(path, folder) for folder, _shown in named[word]):
                strays.append("%s = %s (given: %s)"
                              % (name, item.strip(), ", ".join(shown for _folder, shown in named[word])))
            elif not any(_inside(path, folder) for folder, _shown in given):
                other = [shown for folder, shown in given if _other_version(path, folder)]
                if other:
                    strays.append("%s = %s (another version of what was given: %s)" % (name, item.strip(), other[0]))
    return strays


def _define(header, name):
    try:
        match = re.search(r"^#define %s\s+(\d+)" % re.escape(name), Path(header).read_text(errors="replace"), re.M)
    except OSError:
        return None
    return int(match.group(1)) if match else None


def _optix(include):
    number = _define(Path(include, "optix.h"), "OPTIX_VERSION")
    return None if number is None else "%d.%d.%d" % (number // 10000, number % 10000 // 100, number % 100)


def _hip(hipcc):
    header = Path(hipcc).parent.parent / "include/hip/hip_version.h"
    parts = [_define(header, "HIP_VERSION_" + part) for part in ("MAJOR", "MINOR", "PATCH")]
    return None if None in parts[:2] else ".".join(str(part) for part in parts if part is not None)


def _cuda(nvcc):
    try:
        text = (Path(nvcc).parent.parent / "version.json").read_text(encoding="utf-8-sig")
        version = json.loads(text)["cuda"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return str(version) if version else None


def file_version(path):
    """A Windows binary's file version as the release script names ocloc's: the last two of its four fields (the
    whole of another shape), or None when the file names none. Only the version resource's ``FileVersion`` text is
    read, as the release script reads it (``VersionInfo.FileVersion``), so the two readers cannot disagree. The
    entry gives its value's length, so an entry without a value reads as none, not as the entry after it."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    key = "FileVersion".encode("utf-16-le") + b"\0\0"
    at = data.find(key)
    if at < 6:
        return None
    characters = struct.unpack_from("<H", data, at - 4)[0]   # The value's length, its closing NUL included.
    start = at + len(key)
    if data[start:start + 2] == b"\0\0":   # Padding to a multiple of four bytes.
        start += 2
    text = data[start:start + 2 * max(characters - 1, 0)].decode("utf-16-le", errors="replace")
    fields = re.findall(r"\d+", text)
    if not any(int(field) for field in fields):
        return None
    return "%s.%s" % (fields[2], fields[3]) if len(fields) >= 4 else ".".join(fields)


def _ocloc(folder):
    return file_version(Path(folder, "ocloc.exe")) or file_version(Path(folder, "igc64.dll"))


def _dlss(include):
    name = Path(include).parent.name
    return name[len("dlss-sdk-"):] if name.startswith("dlss-sdk-") else None


def has_cicc(nvcc):
    return any((Path(nvcc).parent.parent / "nvvm/bin" / name).is_file() for name in ("cicc.exe", "cicc"))


READERS = (("optix", re.compile(r"^OPTIX_INCLUDE_DIR$"), _optix), ("hip", re.compile(r"^HIP_HIPCC_EXECUTABLE$"), _hip),
           ("cuda", NVCC, _cuda), ("ocloc", re.compile(r"^OCLOC_INSTALL_DIR$"), _ocloc),
           ("dlss", re.compile(r"^DLSS_INCLUDE_DIR$"), _dlss))


def resolved_problems(cache_text, versions):
    """Lines for what the configure resolved that does not report the version judged for its SDK."""
    problems, entries = [], list(cache_entries(cache_text))
    for sdk, pattern, reader in READERS:
        judged = versions.get(sdk)
        if not judged or judged == "off":
            # Left out of this build on purpose (-AllowMissing records nothing, a release without DLSS records
            # "off", as gpu_sdk_pins.py reads it): no version was judged, so no entry is expected.
            continue
        wanted = [str(version) for version in (judged if isinstance(judged, list) else [judged])]
        found = [(name, value.strip()) for name, value in entries if pattern.match(name) and _path(value)]
        if not found:
            problems.append("the configure resolved no %s although %s %s was judged"
                            % (pattern.pattern.strip("^$"), LABELS[sdk], " and ".join(wanted)))
        seen = set()
        for name, value in found:
            version = reader(value)
            seen.add(version)
            if version not in wanted:
                problems.append("%s = %s: %s there, this build judged %s"
                                % (name, value, "%s %s" % (LABELS[sdk], version) if version else "no version to read",
                                   " and ".join(wanted)))
            if sdk == "cuda" and not has_cicc(value):
                problems.append("%s = %s: no nvvm/bin/cicc in this toolkit, which nvcc runs to compile a kernel"
                                % (name, value))
        if sdk == "cuda":   # One toolkit for each pinned major version: each must be some nvcc's.
            problems += ["no nvcc of the configure belongs to %s %s, which was judged" % (LABELS[sdk], version)
                         for version in wanted if found and version not in seen]
    return problems


def toolkit_record(used):
    """The text a build folder keeps of the toolkits its cache was configured with."""
    return json.dumps({"options": sorted(used.get("options") or []), "versions": used.get("versions") or {}},
                      indent=1, sort_keys=True) + "\n"


def cache_stage(build, used, stage, out=print):
    """``before`` or ``after`` a configure of ``build`` (the module's text); the exit status."""
    build, options = Path(build), used.get("options") or []
    cache, record, wanted = build / CACHE_NAME, build / TOOLKIT_RECORD, toolkit_record(used)
    if stage == "before":
        incomplete = [shown for name, _folder, shown in option_paths(options)
                      if NVCC.match(name) and not has_cicc(shown)]
        for nvcc in incomplete:
            out("gpu-sdks: no nvvm/bin/cicc in the CUDA toolkit of %s, which nvcc runs to compile a kernel: install "
                "the toolkit's nvvm component" % nvcc)
        if incomplete or not cache.is_file():
            return 1 if incomplete else 0
        have = record.read_text(encoding="utf-8") if record.is_file() else None
        if have != wanted:
            os.replace(cache, build / CACHE_ASIDE)
            out("gpu-sdks: %s was last configured with %s: its CMake cache moved to %s, so CMake looks for each again"
                % (build, "other GPU toolkits" if have else "GPU toolkits it has no record of", CACHE_ASIDE))
        return 0
    if not cache.is_file():
        out("gpu-sdks: no %s in %s: nothing was configured" % (CACHE_NAME, build))
        return 1
    text = cache.read_text(encoding="utf-8", errors="replace")
    found = cache_strays(text, options) + resolved_problems(text, used.get("versions") or {})
    if found:
        out("gpu-sdks: this configure of %s does not take every GPU SDK from the folder given for it, at the version "
            "judged:" % build)
        for line in found:
            out("gpu-sdks:   " + line)
        return 1
    record.write_text(wanted, encoding="utf-8")
    out("gpu-sdks: the configure takes each GPU SDK from the folder given for it, at the version judged")
    return 0


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", required=True, help="the build folder")
    parser.add_argument("--used", required=True, help="a JSON file: {\"options\": [...], \"versions\": {...}}")
    parser.add_argument("--stage", required=True, choices=("before", "after"))
    arguments = parser.parse_args(argv)
    try:
        used = json.loads(Path(arguments.used).read_text(encoding="utf-8-sig"))
        return cache_stage(arguments.build, used, arguments.stage, out)
    except (OSError, ValueError) as error:
        out("gpu-sdks: %s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
