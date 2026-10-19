#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The macOS disk image of a signed Angelo.app, with the styled Finder window, checked before it is kept.

Used by ``sign_notarize_macos.py``. The window is laid out by ``release/darwin/dmg_window.applescript.in``
(the same script CPack runs with ``ANGELO_DMG_STYLED=ON``; doc/angelo/app_identity.rst, "The disk image
window"), and the result is read back from the volume's ``.DS_Store``: Finder stores the window's bounds
and the icons' positions there asynchronously, and on a large image it has been seen to keep a stale
window size, so the layout is applied again (at most three times) until the stored layout is the one the
script asks for. An image whose layout never matches is refused, not shipped.

Standard library only; macOS only (hdiutil, osascript, Finder).
"""

import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import release_checks  # noqa: E402

DMG_WINDOW_SCRIPT = ROOT / "release/darwin/dmg_window.applescript.in"
DMG_BACKGROUND = ROOT / "release/darwin/background.tif"
# Where the writable image is made and styled. Finder's background alias in the shipped .DS_Store records that
# image's path, so it must name no user: /tmp, never $TMPDIR (/var/folders/<per-user id>/T).
WORK_ROOT = "/tmp"
LAYOUT_ATTEMPTS = 3
ReleaseError = release_checks.CheckError


def window_script(executable):
    """The Finder layout script with the bundle's name filled in, as CMake's configure_file would."""
    text = DMG_WINDOW_SCRIPT.read_text(encoding="utf-8").replace("@ANGELO_MACOS_EXECUTABLE@", executable)
    if re.search(r"@[A-Z_]+@", text):
        raise ReleaseError("dmg_window.applescript.in has a placeholder this tool does not fill")
    return text


def expected_layout(script):
    """``{"size": (w, h), "icon_size": n, "icons": {name: (x, y)}}`` as the script sets them."""
    left, top, right, bottom = map(int, re.search(r"set the bounds to \{(\d+), (\d+), (\d+), (\d+)\}",
                                                  script).groups())
    icons = {name: (int(x), int(y)) for name, x, y in
             re.findall(r'set position of item "([^"]+)" to \{(\d+), (\d+)\}', script)}
    icon_size = int(re.search(r"set icon size of viewOptions to (\d+)", script).group(1))
    return {"size": (right - left, bottom - top), "icon_size": icon_size, "icons": icons}


def _blob(data, record):
    index = data.find(record + b"blob")
    if index < 0:
        return None
    length = struct.unpack(">I", data[index + 8:index + 12])[0]
    return data[index + 12:index + 12 + length]


def stored_layout(ds_store):
    """The volume window Finder stored: window size, icon size, background and icon positions.

    Reads only the three records the window script writes (``bwsp``, ``icvp``, ``Iloc``) from the
    ``.DS_Store`` B-tree's records, which hold each name as UTF-16 followed by the record code.
    """
    data = Path(ds_store).read_bytes()
    window, view = _blob(data, b"bwsp"), _blob(data, b"icvp")
    if window is None or view is None:
        return None
    bounds = plistlib.loads(window).get("WindowBounds", "")
    numbers = [int(n) for n in re.findall(r"-?\d+", bounds)]
    options = plistlib.loads(view)
    icons = {}
    for match in re.finditer(rb"\x00\x00\x00(.)((?:\x00[\x20-\x7e])+)Ilocblob\x00\x00\x00\x10(.{8})", data, re.S):
        name = match.group(2).decode("utf-16-be")
        if len(name) == ord(match.group(1)):
            icons[name] = struct.unpack(">II", match.group(3)[:8])
    return {"size": tuple(numbers[2:4]) if len(numbers) == 4 else None, "icon_size": int(options.get("iconSize", 0)),
            "background": options.get("backgroundType") == 2 and bool(options.get("backgroundImageAlias")),
            "icons": icons}


def layout_problems(stored, expected):
    if stored is None:
        return ["Finder stored no window layout"]
    problems = []
    if stored["size"] != expected["size"]:
        problems.append("window %s, expected %s" % (stored["size"], expected["size"]))
    if stored["icon_size"] != expected["icon_size"]:
        problems.append("icon size %s, expected %s" % (stored["icon_size"], expected["icon_size"]))
    if not stored["background"]:
        problems.append("no background picture")
    for name, position in expected["icons"].items():
        if stored["icons"].get(name) != position:
            problems.append("%s at %s, expected %s" % (name, stored["icons"].get(name), position))
    return problems


def attach(image, runner, *options):
    plist = plistlib.loads(runner(["hdiutil", "attach", str(image), "-noverify", "-noautoopen", "-plist"]
                                  + list(options)).stdout.encode())
    mounted = [e for e in plist["system-entities"] if e.get("mount-point")]
    if len(mounted) != 1:
        raise ReleaseError("hdiutil attached %d volumes for %s" % (len(mounted), image))
    return plist["system-entities"][0]["dev-entry"], Path(mounted[0]["mount-point"])


def detach(device, runner):
    for attempt in range(6):
        if runner(["hdiutil", "detach", device] + (["-force"] if attempt == 5 else []), check=False).returncode == 0:
            return
        time.sleep(2)
    raise ReleaseError("could not detach %s" % device)


def style_window(mount, volume, executable, work, runner, settle=30):
    """Run the window script until Finder's stored layout matches it; returns the stored layout."""
    script_text = window_script(executable)
    expected = expected_layout(script_text)
    script = work / "dmg_window.applescript"
    script.write_text(script_text, encoding="utf-8")
    problems = []
    for _attempt in range(LAYOUT_ATTEMPTS):
        try:
            runner(["osascript", str(script), volume], timeout=300)
        except (ReleaseError, subprocess.TimeoutExpired) as error:
            raise ReleaseError("styling the window through Finder failed (allow this terminal to control Finder "
                               "in System Settings > Privacy & Security > Automation, or pass --unstyled-dmg): %s"
                               % error) from None
        deadline = time.monotonic() + settle
        while True:   # Finder writes .DS_Store on its own schedule.
            stored = stored_layout(mount / ".DS_Store") if (mount / ".DS_Store").is_file() else None
            problems = layout_problems(stored, expected)
            if not problems or time.monotonic() > deadline:
                break
            time.sleep(2)
        if not problems:
            return dict(stored, attempts=_attempt + 1)
    raise ReleaseError("Finder did not keep the disk image window layout: %s" % "; ".join(problems))


def image_megabytes(content):
    """The writable image's size for ``content``: every file's allocated 4 KiB blocks plus a block of catalog per
    entry, then a tenth and 128 MiB more for HFS+'s journal and trees. The bundled agent runtime made the app 1.1 GB
    in about 6,000 files, and the content's byte total plus 64 MiB no longer fit ("No space left on device"); the
    compressed image drops the free space, so the margin costs nothing in the download.
    """
    entries = [path for path in content.rglob("*") if not path.is_symlink()]
    allocated = sum(-(-path.stat().st_size // 4096) * 4096 for path in entries if path.is_file()) + 4096 * len(entries)
    megabytes = allocated // 2 ** 20
    return megabytes + megabytes // 10 + 128


def build(app, out, work, identity, styled, runner):
    """The compressed disk image of the signed ``app``, checked mounted; returns (path, layout or None)."""
    if not styled:
        print("warning: building the disk image without its styled window (--unstyled-dmg)", file=sys.stderr)
    volume = identity["bundle"]["macos_dmg_volume_name"]
    final = out / ("%s-%s-macos-arm64.dmg" % (identity["product"]["name"], identity["version"]["label"]))
    if final.exists():
        raise ReleaseError("%s exists" % final)
    if Path("/Volumes", volume).exists():
        raise ReleaseError("a volume named %s is mounted; eject it first (Finder addresses the image by name)"
                           % volume)
    content = work / "dmg-root"
    content.mkdir(parents=True)
    runner(["ditto", str(app), str(content / app.name)])
    os.symlink("/Applications", content / "Applications")
    if styled:
        (content / ".background").mkdir()
        shutil.copyfile(release_checks.real_file(DMG_BACKGROUND), content / ".background/background.tif")
    writable = work / "writable.dmg"
    runner(["hdiutil", "create", "-volname", volume, "-fs", "HFS+", "-format", "UDRW", "-size",
            "%dm" % image_megabytes(content), "-srcfolder", str(content), str(writable)])
    device, mount = attach(writable, runner, "-readwrite")
    layout = None
    try:
        if mount != Path("/Volumes", volume):
            raise ReleaseError("the image mounted at %s, not /Volumes/%s" % (mount, volume))
        if styled:
            layout = style_window(mount, volume, identity["bundle"]["macos_executable"], work, runner)
        runner(["sync"])
    finally:
        detach(device, runner)
    runner(["hdiutil", "convert", str(writable), "-format", "UDZO", "-imagekey", "zlib-level=9", "-o", str(final)])
    runner(["hdiutil", "verify", str(final)])
    check_contents(final, app.name, styled, work, runner)
    return final, layout


def check_contents(image, app_name, styled, work, runner, gatekeeper=False):
    """Mount read-only, out of Finder's sight, and check what a person will see and run."""
    mount = work / ("mounted-%d" % len(runner.log))
    mount.mkdir()
    device, _ = attach(image, runner, "-readonly", "-nobrowse", "-mountpoint", str(mount))
    try:
        if os.readlink(mount / "Applications") != "/Applications":
            raise ReleaseError("the image's Applications link is wrong")
        if styled:
            if not (mount / ".background/background.tif").is_file():
                raise ReleaseError("the image has no window background")
            expected = expected_layout(window_script(Path(app_name).stem))
            problems = layout_problems(stored_layout(mount / ".DS_Store") if (mount / ".DS_Store").is_file()
                                       else None, expected)
            if problems:
                raise ReleaseError("the compressed image lost its window layout: %s" % "; ".join(problems))
        runner(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(mount / app_name)])
        if gatekeeper:   # A notarized release: the app inside carries its own ticket and Gatekeeper accepts it.
            runner(["xcrun", "stapler", "validate", str(mount / app_name)])
            assessed = runner(["spctl", "--assess", "--type", "execute", "-vv", str(mount / app_name)])
            return (assessed.stderr or assessed.stdout).strip().splitlines()
    finally:
        detach(device, runner)
    return None
