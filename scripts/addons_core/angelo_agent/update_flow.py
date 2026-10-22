# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Preparing an update in the background: from the feed's offer to an installer that is ready to run.

Contract: doc/angelo/updates_and_reports.rst, "Installing updates". For an
offer the feed allows to install automatically, on an install that can update
itself (``support``), ``Preparer.prepare``:

* macOS: downloads the delta from the running version when the feed lists one,
  rebuilds the new bundle from it, and falls back to the release's disk image
  when anything about the delta fails; the bundle is staged and verified
  (``update_stage_mac``) and the downloaded file removed;
* Windows: downloads the release's MSI and checks its signer
  (``update_stage_win``).

Everything lives in ``<root>/<version>/`` (``root`` is ``<cache>/updates``).
``ready.json`` records a finished preparation with a fingerprint of what was
verified, so a ready update survives a restart (``recall``) and is checked
again, cheaply, right before it is installed (``still_valid``). Nothing here
quits or installs: ``update_restart`` does that on the main thread.

Standard library only; no bpy. ``prepare`` blocks: call it from a worker thread.
"""

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import (app_version, update_delta, update_download, update_feed, update_install, update_stage_mac,
               update_stage_win)
from .update_download import DownloadError, Expected

READY_FILE = "ready.json"
DELTA_FORMATS = (update_delta.FORMAT,)
UNPACKED_FACTOR = {"macos": 4, "windows": 3}   # Free space kept for unpacking, as a multiple of the download.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


class PrepareError(Exception):
    """``code`` is stable (logs, tests, the card); ``transient`` failures are tried again at a later check."""

    def __init__(self, code, transient=False, message=""):
        super().__init__(message or code)
        self.code, self.transient = code, transient


@dataclass(frozen=True)
class Ready:
    """A verified update, waiting for a restart."""

    version: str
    family: str          # macos, windows
    payload: str         # macOS: the staged bundle; Windows: the installer.
    work: str            # This update's folder.
    install_root: str    # macOS: the installed bundle; Windows: the install folder.
    fingerprint: str     # macOS: update_stage_mac.fingerprint; Windows: the installer's SHA-256.
    method: str          # delta or full
    gatekeeper: str = ""  # macOS: "1" when Gatekeeper approves the running app, so the installer requires it again.


def _file_name(url, fallback):
    name = Path(urlsplit(url).path).name
    return name if _NAME.fullmatch(name or "") else fallback


def _same_path(a, b):
    def norm(path):
        return os.path.normcase(os.path.abspath(str(path))).rstrip("\\/")
    return bool(a) and bool(b) and norm(a) == norm(b)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Preparer:
    """One install's preparation. ``binary`` is the running executable (``bpy.app.binary_path``)."""

    def __init__(self, root, current, platform, binary, upgrade_code="", *, fetch=None,
                 mac=update_stage_mac, win=update_stage_win, log=print):
        self.root, self.current, self.platform = Path(root), current, platform
        self.binary, self.upgrade_code = str(binary), upgrade_code
        self.family = update_install.family(platform)
        # Looked up now, not when the module loaded, so a fixture can stand in for the download.
        self.fetch = fetch or update_download.fetch
        self.mac, self.win, self.log = mac, win, log

    def support(self):
        """``(reason, install root)``; reason "" when this install can update itself."""
        if self.family == "macos":
            reason, app = self.mac.support(self.binary)
            return reason, str(app or "")
        if self.family == "windows":
            reason, root = self.win.support(self.binary, self.upgrade_code)
            return reason, str(root)
        return "platform", ""

    def work(self, version):
        return self.root / version

    def prepare(self, release, download, cancel=None, limit=None, progress=None):
        """A ``Ready`` for ``release``; raises ``PrepareError``. ``download`` is this platform's feed entry."""
        reason, install_root = self.support()
        if reason:
            raise PrepareError("unsupported:" + reason)
        if download is None:
            raise PrepareError("no_download")
        if not update_feed.installable(download.url):
            raise PrepareError("unsupported:host")   # Offered as a download only (update_feed.INSTALL_HOSTS).
        if app_version.parse(release.version) <= app_version.parse(self.current):
            raise PrepareError("not_newer")   # Never a downgrade, whatever a stored feed says.
        known = self.recall(release.version, download)
        if known is not None:
            return known
        work = self.work(release.version)
        work.mkdir(parents=True, exist_ok=True)
        # What an earlier attempt derived goes; a partial or finished download stays, so it resumes or is reused.
        (work / READY_FILE).unlink(missing_ok=True)
        for derived in ("staged", "staging"):
            shutil.rmtree(work / derived, ignore_errors=True)
        options = dict(cancel=cancel, limit=limit, progress=progress)
        if self.family == "macos":
            ready = self._prepare_mac(release, download, work, install_root, options)
        else:
            ready = self._prepare_windows(release, download, work, install_root, options)
        (work / READY_FILE).write_text(json.dumps(asdict(ready), indent=2), encoding="utf-8")
        return ready

    def _fetch(self, entry, work, fallback, reserve, options):
        target = work / _file_name(entry.url, fallback)
        try:
            return self.fetch(Expected(entry.url, entry.sha256, entry.size), target, reserve=reserve, **options)
        except DownloadError as error:
            raise PrepareError("download:" + error.code, error.transient) from None

    def _prepare_mac(self, release, download, work, app, options):
        reserve = UNPACKED_FACTOR["macos"] * download.size
        delta = download.delta_from(self.current, DELTA_FORMATS)
        if delta is not None and not update_feed.installable(delta.url):
            delta = None
        staged, method = None, "full"
        if delta is not None:
            package = None
            try:
                package = self._fetch(delta, work, "update.delta.tar.xz", reserve, options)
                staged, method = self.mac.from_delta(package, self.current, release.version, work, app), "delta"
            except PrepareError as error:
                if error.transient:
                    raise   # Offline or stopped: the full download would fail the same way; try again later.
                self.log("Angelo update: the delta was not usable (%s); downloading the full update" % error.code)
            except (update_delta.DeltaError, update_stage_mac.StageError) as error:
                self.log("Angelo update: the delta was not usable (%s); downloading the full update" % error.code)
            finally:
                if package is not None:
                    Path(package).unlink(missing_ok=True)
        if staged is None:
            image = self._fetch(download, work, "update.dmg", reserve, options)
            try:
                staged = self.mac.from_disk_image(image, release.version, work, app)
            except update_stage_mac.StageError as error:
                raise PrepareError("verify:" + error.code) from None
            finally:
                Path(image).unlink(missing_ok=True)
        return Ready(release.version, "macos", str(staged), str(work), app, self.mac.fingerprint(staged), method,
                     "1" if self.mac.approved(app) else "")

    def _prepare_windows(self, release, download, work, root, options):
        reserve = UNPACKED_FACTOR["windows"] * download.size
        installer = self._fetch(download, work, "update.msi", reserve, options)
        try:
            self.win.check_installer(installer, self.binary)
        except update_stage_win.InstallerError as error:
            Path(installer).unlink(missing_ok=True)
            raise PrepareError("verify:" + error.code) from None
        return Ready(release.version, "windows", str(installer), str(work), root, download.sha256, "full")

    def recall(self, version, download=None):
        """The ``Ready`` an earlier session left for ``version``, when it is still exactly what was verified."""
        try:
            document = json.loads((self.work(version) / READY_FILE).read_text(encoding="utf-8"))
            ready = Ready(**{key: str(document[key]) for key in Ready.__dataclass_fields__})
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if ready.version != version or ready.family != self.family or Path(ready.work) != self.work(version):
            return None
        if not _same_path(ready.install_root, self.support()[1]):
            return None   # Prepared for another copy of Angelo that shares this cache folder.
        if download is not None and ready.family == "windows" and ready.fingerprint != download.sha256:
            return None
        if not self.still_valid(ready):
            return None
        if ready.family == "windows":
            # The signer check again, as this build runs it: an installer an earlier build staged under other rules
            # is prepared again (and refused there) rather than installed unchecked.
            try:
                self.win.check_installer(ready.payload, self.binary)
            except update_stage_win.InstallerError:
                return None
        return ready

    def still_valid(self, ready):
        """Cheap re-check before installing: this install's, and the staged bundle unchanged or the installer's
        digest. The installer verifies the signature (macOS) or the digest (Windows) again right before it swaps."""
        payload = Path(ready.payload)
        if Path(ready.work) not in payload.parents or not _same_path(ready.install_root, self.support()[1]):
            return False
        try:
            if ready.family == "macos":
                return payload.is_dir() and self.mac.fingerprint(payload) == ready.fingerprint
            return payload.is_file() and _sha256(payload) == ready.fingerprint
        except OSError:
            return False

    def results(self):
        """``[(version, result)]`` the installer left (``update_install.read_result``), oldest first."""
        found = []
        for folder in sorted(self.root.glob("*")) if self.root.is_dir() else ():
            result = update_install.read_result(folder) if app_version.parse(folder.name) else None
            if result is not None:
                found.append((folder.name, result))
        return found

    def clean_older(self, version):
        """Remove the folders of versions older than ``version`` (and anything that is not a version)."""
        newest = app_version.parse(version)
        keep = {f.name for f in self.root.glob("*") if (app_version.parse(f.name) or (0,)) >= newest} \
            if self.root.is_dir() else set()
        return self.clean(keep=keep)

    def clean(self, keep=()):
        """Remove every update folder except ``keep`` (versions); returns the removed names."""
        removed = []
        for folder in sorted(self.root.glob("*")) if self.root.is_dir() else ():
            if folder.name in keep or not folder.is_dir():
                continue
            if self.family == "macos":
                self.mac.release_mount(folder / "mount")   # A disk image a quit left attached.
            shutil.rmtree(folder, ignore_errors=True)
            if not folder.exists():
                removed.append(folder.name)
        return removed

    def free(self):
        """Free bytes where updates are stored (for the card's message)."""
        try:
            return shutil.disk_usage(self.root if self.root.exists() else self.root.parent).free
        except OSError:
            return 0


def remove_previous(work, install_root):
    """After a successful install: delete the previous bundle the macOS installer kept (``previous.txt``).

    Only a folder the installer names that way is removed (``<work>/previous.app`` or
    ``.<name>.previous-<n>`` beside the app), and never the installed app itself.
    """
    try:
        previous = Path((Path(work) / "previous.txt").read_text(encoding="utf-8").strip())
    except OSError:
        return False
    named = (previous == Path(work) / "previous.app"
             or (previous.parent == Path(install_root).parent and previous.name.startswith("." + Path(install_root).name
                                                                                           + ".previous-")))
    if named and previous != Path(install_root) and previous.is_dir() and not previous.is_symlink():
        shutil.rmtree(previous, ignore_errors=True)
        return True
    return False
