#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Is a release built with the GPU SDK versions Blender pins for its own release?

Blender names the SDKs of its release builds in ``build_files/buildbot/config/blender_version.cmake``: OptiX, one
CUDA toolkit per major version, HIP, ocloc and DLSS. A different version can change what a person's machine needs
(found on 2026-11-14: a build made with OptiX SDK 9.1 where Blender pins 9.0.0 had no OptiX on NVIDIA drivers below
590, which Blender's own 5.3 release supports). This module is the one reader of that file and the one judge::

    python gpu_sdk_pins.py pins  --source <tree> [--platform windows|linux|macos]
    python gpu_sdk_pins.py check --source <tree> --used <file>      (before configuring: {"versions": {}, "left_out": []})
    python gpu_sdk_pins.py check --source <tree> --record <angelo-release-build.json>
    python release_checks.py gpu-sdks --source <tree> --record <angelo-release-build.json>

A version used must equal its pin exactly: newer is refused like older, each pinned CUDA toolkit must be there and
no other, and a version known with less precision than its pin is not a match. A pinned name this module does not
know is a problem too, so a new SDK cannot go unjudged. A part the build leaves out on purpose (``-AllowMissing``, a
release without DLSS) is not judged: the build's record already says it is not Blender's release.

An exception is an entry of ``gpu_sdk_exceptions.json`` beside this file: ``sdk``, ``pin``, ``used`` ("none" for a
pinned toolkit that is not used at all, or for a pin of "none" when a toolkit Blender does not pin is used),
``reason``, ``approved_by`` and ``approved_on``. It covers exactly that pin and that version, so a new pin or
another version voids it, and one that covers nothing in the build is a problem.

The versions judged here are read from the folders the build gives CMake. That the configure then takes each SDK
from that folder, at that version, is ``gpu_sdk_cache.py``'s part. Standard library only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

PIN_FILE = "build_files/buildbot/config/blender_version.cmake"
EXCEPTIONS_FILE = "scripts/angelo/gpu_sdk_exceptions.json"
SINGLE = {"DLSS_VERSION": "dlss", "OPTIX_VERSION": "optix", "HIP_VERSION": "hip", "OCLOC_VERSION": "ocloc"}
CUDA = re.compile(r"^CUDA(\d+)_VERSION$")
CUDA_DEFAULT = "DEFAULT_CUDA_MAJOR_VERSION"
LABELS = {"dlss": "DLSS SDK", "optix": "OptiX SDK", "cuda": "CUDA Toolkit", "hip": "HIP SDK", "ocloc": "ocloc"}
PLATFORMS = {"windows": {"WIN32"}, "linux": {"UNIX"}, "macos": {"UNIX", "APPLE"}}
KNOWN_CONDITIONS = {"WIN32", "UNIX", "APPLE", "HAIKU", "MSVC", "CMAKE_HOST_WIN32", "CMAKE_HOST_UNIX", "CMAKE_HOST_APPLE"}
LEFT_OUT_NAMES = {"oneapi": "ocloc"}   # the release script's -AllowMissing names, where they differ
FIELDS = ("sdk", "pin", "used", "reason", "approved_by", "approved_on")
NONE = "none"


class PinError(Exception):
    pass


def _condition(text, true):
    """A CMake condition of platform names, AND, OR, NOT and parentheses, for one platform."""
    tokens = re.findall(r"\(|\)|[A-Za-z_][A-Za-z0-9_]*", text)
    if "".join(tokens) != re.sub(r"\s+", "", text):
        raise PinError("a condition this reader cannot read: if(%s)" % text.strip())
    position = 0

    def atom():
        nonlocal position
        if position >= len(tokens):
            raise PinError("an unfinished condition: if(%s)" % text.strip())
        token = tokens[position]
        position += 1
        if token == "NOT":
            return not atom()
        if token == "(":
            value = either()
            if position >= len(tokens) or tokens[position] != ")":
                raise PinError("an unclosed parenthesis: if(%s)" % text.strip())
            position += 1
            return value
        if token not in KNOWN_CONDITIONS:
            raise PinError("a condition this reader does not know (%s): if(%s)" % (token, text.strip()))
        return token in true

    def both():
        nonlocal position
        value = atom()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            value = atom() and value
        return value

    def either():
        nonlocal position
        value = both()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            value = both() or value
        return value

    value = either()
    if position != len(tokens):
        raise PinError("a condition this reader cannot read: if(%s)" % text.strip())
    return value


def read_pins(text, platform):
    """``{"sdks": {name: version, "cuda": [versions]}, "cuda_default_major": n, "unknown": [names]}`` for a platform."""
    if platform not in PLATFORMS:
        raise PinError("no such platform: %s" % platform)
    true = PLATFORMS[platform]
    sdks, cuda, default, unknown = {}, {}, None, []
    branches = []   # one [taken already, active now] per open if()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.match(r"^(\w+)\s*\((.*)\)$", line)
        if not match:
            raise PinError("a line this reader cannot read: %s" % raw.strip())
        command, arguments = match.group(1).lower(), match.group(2)
        outer = all(branch[1] for branch in branches)
        if command == "if":
            value = outer and _condition(arguments, true)
            branches.append([value, value])
        elif command in ("elseif", "else", "endif"):
            if not branches:
                raise PinError("%s() without if(): %s" % (command, raw.strip()))
            if command == "endif":
                branches.pop()
                continue
            branch = branches[-1]
            enclosing = all(other[1] for other in branches[:-1])
            value = enclosing and not branch[0] and (command == "else" or _condition(arguments, true))
            branch[0], branch[1] = branch[0] or value, value
        elif command == "set" and outer:
            words = arguments.split()
            if len(words) < 2 or not words[0].endswith("_VERSION"):
                continue
            name, value = words[0], words[1].strip('"')
            if name in SINGLE:
                sdks[SINGLE[name]] = value
            elif CUDA.match(name):
                cuda[int(CUDA.match(name).group(1))] = value
            elif name == CUDA_DEFAULT:
                default = int(value)
            else:
                unknown.append(name)
    if branches:
        raise PinError("an if() without endif()")
    if cuda:
        sdks["cuda"] = [cuda[major] for major in sorted(cuda)]
    return {"sdks": sdks, "cuda_default_major": default, "unknown": unknown}


def source_pins(source, platform="windows"):
    return read_pins((Path(source) / PIN_FILE).read_text(encoding="utf-8"), platform)


def source_exceptions(source):
    path = Path(source) / EXCEPTIONS_FILE
    if not path.is_file():
        return []
    entries = json.loads(path.read_text(encoding="utf-8-sig")).get("exceptions")
    if not isinstance(entries, list):
        raise PinError("%s has no \"exceptions\" list" % EXCEPTIONS_FILE)
    return entries


def exception_problems(entry):
    """What an exception lacks (an empty list when it says what, why, who and when)."""
    if not isinstance(entry, dict):
        return ["an exception that is not an object: %r" % (entry,)]
    what = "the exception for %s (pin %s, used %s)" % tuple(str(entry.get(key) or "?").strip() for key in FIELDS[:3])
    problems = ["%s has no %s" % (what, field) for field in FIELDS
                if not isinstance(entry.get(field), str) or not entry[field].strip()]
    if not problems and entry["sdk"] not in LABELS:
        problems.append("%s names an SDK this check does not know" % what)
    return problems


def _major(version):
    return version.split(".", 1)[0]


def _differences(pins, used, left_out):
    """``(sdk, pin, used version, sentence)`` for every pinned SDK the build differs in."""
    found = []
    for name in sorted(pins["sdks"]):
        if name in left_out:
            continue
        pin, value = pins["sdks"][name], used.get(name)
        label = LABELS[name]
        if value is None or value == [] or (isinstance(value, str) and not value.strip()):
            found.append((name, pin if isinstance(pin, str) else ", ".join(pin), NONE,
                          "the build names no %s version (Blender pins %s)" % (label, pin if isinstance(pin, str) else " and ".join(pin))))
            continue
        if name == "dlss" and value == "off":
            continue   # A release without DLSS: the record says so, and nothing of the SDK is compiled in.
        if name != "cuda":
            value = str(value).strip()
            if value != pin:
                found.append((name, pin, value, "%s %s is not the %s Blender pins for its release" % (label, value, pin)))
            continue
        versions = [str(item).strip() for item in ([value] if isinstance(value, str) else value)]
        paired = set()
        for wanted in pin:
            if wanted in versions:
                continue
            same = next((item for item in versions if item not in pin and _major(item) == _major(wanted)), None)
            if same is None:
                found.append((name, wanted, NONE, "%s %s, which Blender pins for its release, is not used" % (label, wanted)))
            else:
                paired.add(same)
                found.append((name, wanted, same, "%s %s is not the %s Blender pins for its release" % (label, same, wanted)))
        for item in versions:
            if item not in pin and item not in paired:
                found.append((name, NONE, item, "%s %s is used, and Blender pins no such toolkit" % (label, item)))
    return found


def judge(pins, used, exceptions, left_out=()):
    """``(problems, covered)``: what stops the build, and the differences an approved exception covers."""
    left_out = {LEFT_OUT_NAMES.get(name, name) for name in left_out}
    problems = ["%s pins %s, which this check does not know: teach scripts/angelo/gpu_sdk_pins.py what it is before "
                "building a release" % (PIN_FILE, name) for name in pins["unknown"]]
    complete = []
    for entry in exceptions:
        lacking = exception_problems(entry)
        problems.extend(lacking)
        if not lacking:
            complete.append(entry)
    spent, covered = set(), []
    for name, pin, value, sentence in _differences(pins, used, left_out):
        index = next((i for i, entry in enumerate(complete)
                      if (entry["sdk"], entry["pin"].strip(), entry["used"].strip()) == (name, pin, value)), None)
        if index is None:
            problems.append("%s (%s): install the pinned version, or record an approved exception in %s"
                            % (sentence, PIN_FILE, EXCEPTIONS_FILE))
        else:
            spent.add(index)
            entry = complete[index]
            covered.append("%s, under an approved exception: %s (%s, %s)"
                           % (sentence, entry["reason"].strip(), entry["approved_by"].strip(), entry["approved_on"].strip()))
    for index, entry in enumerate(complete):
        if index not in spent and entry["sdk"] not in left_out:
            problems.append("an exception in %s covers nothing in this build (%s: pin %s, used %s): remove it, or "
                            "correct it" % (EXCEPTIONS_FILE, entry["sdk"], entry["pin"], entry["used"]))
    return problems, covered


def problems(pins, used, exceptions, left_out=()):
    return judge(pins, used, exceptions, left_out)[0]


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("pins", "check"):
        command = commands.add_parser(name)
        command.add_argument("--source", required=True, help="the source tree")
        command.add_argument("--platform", default="windows", choices=sorted(PLATFORMS))
    check = commands.choices["check"]
    check.add_argument("--used", help="a JSON file: {\"versions\": {...}, \"left_out\": [...]}")
    check.add_argument("--record", help="a build's angelo-release-build.json")
    arguments = parser.parse_args(argv)
    try:
        pins = source_pins(arguments.source, arguments.platform)
        if arguments.command == "pins":
            out(json.dumps(pins, indent=1))
            return 0
        if bool(arguments.used) == bool(arguments.record):
            parser.error("check takes --used or --record")
        data = json.loads(Path(arguments.used or arguments.record).read_text(encoding="utf-8-sig"))
        if arguments.record:
            versions, left_out = data.get("toolkit_versions"), data.get("allowed_missing") or []
            if not isinstance(versions, dict):
                out("gpu-sdks: %s has no toolkit_versions: the build did not record the exact GPU SDK versions"
                    % Path(arguments.record).name)
                return 1
        else:
            versions, left_out = data.get("versions") or {}, data.get("left_out") or []
        found, covered = judge(pins, versions, source_exceptions(arguments.source), left_out)
    except (OSError, ValueError, PinError) as error:
        out("gpu-sdks: %s" % error)
        return 1
    for line in found:
        out("gpu-sdks: " + line)
    for line in covered:
        out("gpu-sdks: " + line)
    if not found:
        out("gpu-sdks: every GPU SDK of this build is the version Blender pins%s"
            % (" or an approved exception" if covered else ""))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
