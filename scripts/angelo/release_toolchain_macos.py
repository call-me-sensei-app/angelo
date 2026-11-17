# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The macOS release build's toolchain: pinned, recorded at configure, and unchanged until the build ends.

    release_toolchain_macos.py pin                     before the configure (build_release_macos.sh)
    release_toolchain_macos.py check BUILD             before the configure: a cache from another toolchain goes aside
    release_toolchain_macos.py record BUILD            after the configure: BUILD/angelo-toolchain.json
    release_toolchain_macos.py verify BUILD STAGE      after the build ("end")
    release_toolchain_macos.py link BUILD -- CMD...    CMake's linker launcher: verify ("link"), then run CMD

In the 1.1 rehearsal (2026-11-04) Mac 2's Xcode updated itself from 27.1 to 27.2 during a release build: the configure
had chosen MacOSX27.0.sdk, the update removed it, and every later compile failed. A release build therefore:

* starts only on the pinned Xcode: ANGELO_XCODE_PIN is "<version> <build>" (the release kit sets it), and a different
  Xcode is refused unless ANGELO_XCODE_OVERRIDE gives the reason, which the record keeps;
* records, after the configure, the developer folder, Xcode's version and build and the SDK CMake chose (its path, the
  folder it resolves to and its version);
* refuses to link, and fails at the end, when any of them changed since: the build would mix two toolchains;
* before the next configure, moves the build folder's CMake cache aside (CMakeCache.txt.toolchain-changed) when the
  toolchain it recorded is not this one (another Xcode version, build or developer folder) or its SDK is gone, or, for
  a folder with no record, when the cached CMAKE_OSX_SYSROOT does not exist: CMake keeps OSX_SYSTEM and the sysroot it
  chose, so an Xcode update that removed that SDK left every compile failing ("cstdio file not found", the 1.1
  rehearsal, 2026-11-04). Compiled objects stay; the new toolchain's flags rebuild what they change.

Xcode's version comes from <developer folder>/../version.plist and the SDK's from its SDKSettings.json, read directly,
so the check is cheap enough to run before every link. Standard library only.
"""

import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys

RECORD = "angelo-toolchain.json"
FIELDS = ("developer_dir", "xcode_version", "xcode_build", "sdk_path", "sdk_folder", "sdk_version")


def developer_dir():
    """The developer folder the compilers come from: DEVELOPER_DIR, else xcode-select's."""
    folder = os.environ.get("DEVELOPER_DIR", "")
    if not folder:
        try:
            folder = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            folder = ""
    return folder


def xcode(folder):
    """(version, build) of the Xcode holding ``folder``, or (None, None) (Command Line Tools have no version.plist)."""
    try:
        info = plistlib.loads((Path(folder).parent / "version.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None, None
    return info.get("CFBundleShortVersionString"), info.get("ProductBuildVersion")


def sdk(path):
    """(the folder ``path`` resolves to, its version), each None when the SDK is gone."""
    if not path or not Path(path).is_dir():
        return None, None
    try:
        version = json.loads((Path(path) / "SDKSettings.json").read_text(encoding="utf-8")).get("Version")
    except (OSError, ValueError):
        version = None
    return str(Path(path).resolve()), version


def cached_sysroot(build):
    try:
        text = (Path(build) / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^CMAKE_OSX_SYSROOT:[A-Z]*=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def current(sdk_path):
    folder = developer_dir()
    version, build = xcode(folder)
    sdk_folder, sdk_version = sdk(sdk_path)
    return {"developer_dir": folder, "xcode_version": version, "xcode_build": build, "sdk_path": sdk_path,
            "sdk_folder": sdk_folder, "sdk_version": sdk_version}


def describe(state):
    return "Xcode %s (%s) at %s, SDK %s (%s)" % (state["xcode_version"], state["xcode_build"], state["developer_dir"],
                                               state["sdk_path"] or "none", state["sdk_version"] or "missing")


def pin():
    folder = developer_dir()
    version, build = xcode(folder)
    have = "%s %s" % (version, build)
    wanted = " ".join(os.environ.get("ANGELO_XCODE_PIN", "").split())
    override = os.environ.get("ANGELO_XCODE_OVERRIDE", "").strip()
    print("Toolchain: Xcode %s (%s) at %s" % (version, build, folder or "none"))
    if not wanted:
        print("No Xcode pin given (ANGELO_XCODE_PIN): the release kit pins one; this build records what it used.")
        return 0
    if have == wanted:
        return 0
    if override:
        print("Xcode %s is not the pinned %s; building anyway: ANGELO_XCODE_OVERRIDE=%s" % (have, wanted, override))
        return 0
    print("Xcode %s is not the pinned %s (ANGELO_XCODE_PIN). Install the pinned Xcode, or give the reason in "
          "ANGELO_XCODE_OVERRIDE." % (have, wanted), file=sys.stderr)
    return 1


def record(build):
    state = current(cached_sysroot(build))
    if not state["sdk_folder"]:
        print("The configure chose the SDK %s, which does not exist." % (state["sdk_path"] or "(none)"), file=sys.stderr)
        return 1
    state["override"] = os.environ.get("ANGELO_XCODE_OVERRIDE", "").strip() or None
    (Path(build) / RECORD).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print("Toolchain recorded: " + describe(state))
    return 0


def check(build):
    """Move ``build``'s CMake cache aside when it was configured with another toolchain or a vanished SDK."""
    cache = Path(build) / "CMakeCache.txt"
    if not cache.is_file():
        return 0
    why = ""
    try:
        recorded = json.loads((Path(build) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recorded = None
    if recorded:
        now = current(recorded.get("sdk_path", ""))
        changed = [name for name in ("developer_dir", "xcode_version", "xcode_build") if now[name] != recorded.get(name)]
        if changed:
            why = "configured with %s, now %s" % (describe(recorded), describe(now))
        elif not now["sdk_folder"]:
            why = "its SDK %s is gone" % recorded.get("sdk_path")
    else:
        sysroot = cached_sysroot(build)
        if sysroot and not Path(sysroot).is_dir():
            why = "its cached CMAKE_OSX_SYSROOT %s does not exist" % sysroot
    if why:
        cache.replace(Path(build) / "CMakeCache.txt.toolchain-changed")
        print("The build folder's toolchain changed (%s): its CMake cache moved to CMakeCache.txt.toolchain-changed; "
              "configuring anew." % why)
    return 0


def verify(build, stage):
    try:
        recorded = json.loads((Path(build) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("No toolchain record in %s (%s): configure with build_release_macos.sh first." % (build, stage),
              file=sys.stderr)
        return 1
    now = current(recorded.get("sdk_path", ""))
    changed = [name for name in FIELDS if now.get(name) != recorded.get(name)]
    if changed:
        print("The toolchain changed during the build (%s): configured with %s, now %s (%s changed). Its parts would "
              "mix two toolchains: configure and build again on one toolchain (an Xcode or macOS update must not run "
              "during a release build)." % (stage, describe(recorded), describe(now), ", ".join(changed)),
              file=sys.stderr)
        return 1
    return 0


def main(argv):
    action = argv[0] if argv else ""
    if action == "pin" and len(argv) == 1:
        return pin()
    if action == "check" and len(argv) == 2:
        return check(argv[1])
    if action == "record" and len(argv) == 2:
        return record(argv[1])
    if action == "verify" and len(argv) == 3:
        return verify(argv[1], argv[2])
    if action == "link" and len(argv) >= 4 and argv[2] == "--":
        code = verify(argv[1], "link")
        if code:
            return code
        os.execvp(argv[3], argv[3:])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
