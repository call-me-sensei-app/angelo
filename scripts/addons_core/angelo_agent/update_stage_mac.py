# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""macOS: whether this app can replace itself, and the new bundle, unpacked and verified beside it.

Contract: doc/angelo/updates_and_reports.rst, "Installing updates". The new
bundle comes from the release's own disk image (the file people download, its
SHA-256 checked against the signed feed by ``update_download``) or from a delta
applied to the installed bundle (``update_delta``). Either way it is staged in
the update's folder and accepted only when:

* its bundle identifier is the running app's and its version is the feed's;
* ``codesign --verify --deep --strict`` passes against a requirement taken from
  the running app: the same identifier and, when the running app is signed by
  an Apple developer team, a Developer ID certificate of that same team;
* when Gatekeeper approves the running app (``spctl --assess --type execute``:
  a notarized release), it approves the new one too. Angelo never installs an
  app macOS would call damaged or refuse to open. A build that is not notarized
  (a development or test build) is held to the signature check alone.

An app that cannot replace itself keeps the notify-only behaviour, with the
reason: one started from a disk image or quarantined download location (App
Translocation: a read-only random path), on a read-only volume, or in a folder
the person cannot write (an administrator installed it).

Long tools run at background priority (``taskpolicy -b``). Standard library
only; no bpy. Blocking: call it from a worker thread.
"""

import hashlib
import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

from . import update_delta

TASKPOLICY = "/usr/sbin/taskpolicy"
SPCTL = "/usr/sbin/spctl"
TIMEOUTS = {"attach": 180, "copy": 1800, "verify": 1800, "detach": 120, "inspect": 60}
_TEAM = re.compile(r"^TeamIdentifier=([A-Z0-9]{10})$", re.M)


class StageError(Exception):
    """``code``: mount, copy, layout, bundle_id, version, signature, gatekeeper, io."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def run(args, timeout, background=True):
    """Run a tool; ``CompletedProcess`` with text output. Never raises for a non-zero exit."""
    if background and os.path.exists(TASKPOLICY):
        args = [TASKPOLICY, "-b"] + list(args)
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(args, 255, "", type(error).__name__)


def app_bundle(binary):
    """The ``.app`` holding ``binary`` (``…/Angelo.app/Contents/MacOS/Angelo``), or None."""
    path = Path(binary)
    if len(path.parents) < 3 or path.parent.name != "MacOS" or path.parents[1].name != "Contents":
        return None
    bundle = path.parents[2]
    return bundle if bundle.suffix == ".app" else None


def support(binary):
    """``(reason, app)``: reason "" when this app can replace itself, else ``location`` or ``permission``."""
    app = app_bundle(binary)
    if app is None or not (app / "Contents/Info.plist").is_file():
        return "location", app
    if "/AppTranslocation/" in str(app):
        return "location", app
    try:
        if os.statvfs(app).f_flag & os.ST_RDONLY:
            return "location", app
    except OSError:
        return "location", app
    if not os.access(app.parent, os.W_OK | os.X_OK) or not os.access(app, os.W_OK | os.X_OK):
        return "permission", app
    return "", app


def info(app):
    """The bundle's ``Info.plist`` as a dict ({} when unreadable)."""
    try:
        with open(Path(app) / "Contents/Info.plist", "rb") as handle:
            document = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def team_of(app, runner=run):
    """The Apple developer team that signed ``app``, or "" (ad hoc, unsigned or unreadable)."""
    result = runner(["/usr/bin/codesign", "-dv", "--verbose=2", str(app)], TIMEOUTS["inspect"], False)
    match = _TEAM.search(result.stderr or "")
    return match.group(1) if result.returncode == 0 and match else ""


def requirement(app, runner=run):
    """The code requirement a new bundle must meet, derived from the running ``app``."""
    identifier = info(app).get("CFBundleIdentifier", "")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", identifier):
        raise StageError("bundle_id", "The running app has no usable bundle identifier")
    rule = 'identifier "%s"' % identifier
    team = team_of(app, runner)
    if team:
        rule += ' and anchor apple generic and certificate leaf[subject.OU] = "%s"' % team
    return rule


def approved(bundle, runner=run):
    """True when Gatekeeper would open ``bundle``: notarized, with its ticket stapled or reachable."""
    return runner([SPCTL, "--assess", "--type", "execute", str(bundle)], TIMEOUTS["verify"]).returncode == 0


def check(staged, version, app, runner=run):
    """Raise ``StageError`` unless ``staged`` is ``version`` of this app, signed as this app is."""
    new, running = info(staged), info(app)
    if not new or new.get("CFBundleIdentifier") != running.get("CFBundleIdentifier"):
        raise StageError("bundle_id", "The update is not this app")
    if new.get("CFBundleShortVersionString") != version:
        raise StageError("version", "The update's bundle is not version %s" % version)
    executable = new.get("CFBundleExecutable")
    if not isinstance(executable, str) or not (Path(staged) / "Contents/MacOS" / executable).is_file():
        raise StageError("layout", "The update has no executable")
    result = runner(["/usr/bin/codesign", "--verify", "--deep", "--strict", "-R=" + requirement(app, runner),
                     str(staged)], TIMEOUTS["verify"])
    if result.returncode != 0:
        raise StageError("signature", "The update's signature is not this app's: %s" % (result.stderr or "")[-300:])
    if approved(app, runner) and not approved(staged, runner):
        raise StageError("gatekeeper", "macOS would not open the update (it is not notarized)")


def from_disk_image(image, version, work, app, runner=run):
    """Unpack the app from the release's disk image into ``work/staged``, verify it; returns the bundle."""
    work = Path(work)
    staged = work / "staged" / Path(app).name
    temporary = work / "staging"
    mount = work / "mount"
    shutil.rmtree(temporary, ignore_errors=True)
    shutil.rmtree(work / "staged", ignore_errors=True)
    release_mount(mount, runner)   # Left attached by a session that quit while it was unpacking.
    mount.mkdir(parents=True, exist_ok=True)
    attached = runner(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-noverify",
                       "-mountpoint", str(mount), str(image)], TIMEOUTS["attach"])
    if attached.returncode != 0:
        raise StageError("mount", "The disk image could not be opened")
    try:
        source = _bundle_in(mount, info(app).get("CFBundleIdentifier"))
        if source is None:
            raise StageError("layout", "The disk image does not hold this app")
        temporary.mkdir(parents=True)
        copied = runner(["/usr/bin/ditto", str(source), str(temporary / source.name)], TIMEOUTS["copy"])
        if copied.returncode != 0:
            raise StageError("copy", "The app could not be copied out of the disk image")
    finally:
        if runner(["/usr/bin/hdiutil", "detach", str(mount)], TIMEOUTS["detach"], False).returncode != 0:
            runner(["/usr/bin/hdiutil", "detach", "-force", str(mount)], TIMEOUTS["detach"], False)
    return _accept(temporary / source.name, staged, version, app, runner)


def release_mount(mount, runner=run):
    """Detach a disk image still attached at ``mount`` (True when one was)."""
    if not os.path.ismount(mount):
        return False
    runner(["/usr/bin/hdiutil", "detach", "-force", str(mount)], TIMEOUTS["detach"], False)
    return True


def _bundle_in(folder, identifier):
    """The one ``.app`` at the top of ``folder`` whose bundle identifier is ``identifier`` (the app may be renamed
    where it is installed), or None."""
    found = [path for path in sorted(Path(folder).glob("*.app")) if not path.is_symlink() and path.is_dir()
             and info(path).get("CFBundleIdentifier") == identifier]
    return found[0] if len(found) == 1 else None


def from_delta(package, from_version, version, work, app, runner=run):
    """Rebuild the new bundle from the installed ``app`` and a delta, verify it; returns the bundle."""
    work = Path(work)
    staged = work / "staged" / Path(app).name
    temporary = work / "staging"
    shutil.rmtree(temporary, ignore_errors=True)
    shutil.rmtree(work / "staged", ignore_errors=True)
    update_delta.apply(package, app, temporary / Path(app).name, from_version, version)
    return _accept(temporary / Path(app).name, staged, version, app, runner)


def _accept(candidate, staged, version, app, runner):
    """Verify ``candidate`` and move it to ``staged`` (named as the installed app, whatever the image called it)."""
    try:
        check(candidate, version, app, runner)
        staged.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate, staged)
    finally:
        shutil.rmtree(candidate.parent, ignore_errors=True)
    return staged


def fingerprint(bundle):
    """A cheap digest of a staged bundle's layout (names, types, sizes, modes, times): it changes when anything
    in the folder is removed, added or rewritten after verification, so a damaged stage is never installed."""
    digest = hashlib.sha256()
    for path, entry in sorted(update_delta.tree(bundle, hashes=False).items()):
        try:
            mtime = os.lstat(Path(bundle) / path).st_mtime_ns
        except OSError:
            mtime = -1
        digest.update(repr((path, sorted(entry.items()), mtime)).encode("utf-8"))
    return digest.hexdigest()


def other_instances(app, executable, own_pid, runner=run):
    """Process ids of other copies of this app running from the same bundle (they would break in a swap)."""
    target = str(Path(app) / "Contents/MacOS" / executable)
    result = runner(["/bin/ps", "-axo", "pid=,comm="], TIMEOUTS["inspect"], False)
    pids = []
    for line in (result.stdout or "").splitlines():
        pid, _, command = line.strip().partition(" ")
        if command.strip() == target and pid.isdigit() and int(pid) != own_pid:
            pids.append(int(pid))
    return pids
