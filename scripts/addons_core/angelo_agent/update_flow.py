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
  (``update_stage_win``);
* Linux: downloads the release's tarball, unpacks it and checks it is exactly
  the signed release (``update_stage_linux``), and removes the download.

Everything lives in ``<root>/<version>/`` (``root`` is ``<cache>/updates``).
One worker or removal holds a version's folder at a time (``update_hold``):
stopping a worker only asks it to stop (unpacking and verifying run to the
end), so the next preparation of that version waits for it, and a removal
asked for meanwhile happens as it lets go.
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
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import owned_paths
from . import (app_version, update_delta, update_download, update_feed, update_hold, update_install,
               update_stage_linux, update_stage_mac, update_stage_win)
from .update_download import DownloadError, Expected

READY_FILE = "ready.json"
DELTA_FORMATS = (update_delta.FORMAT,)
UNPACKED_FACTOR = {"macos": 4, "windows": 3, "linux": update_stage_linux.UNPACK_FACTOR}   # Free space kept for unpacking, as a multiple of the download.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


# The answers that mean a mirror doesn't hold the file: only these fall back to the feed's own host. Any other
# failure (an interrupted transfer, a network error) keeps the partial download for the mirror to resume next time.
MIRROR_MISSING = frozenset({"http_403", "http_404", "http_410"})


class PrepareError(Exception):
    """``code`` is stable (logs, tests, the card); ``transient`` failures are tried again at a later check."""

    def __init__(self, code, transient=False, message=""):
        super().__init__(message or code)
        self.code, self.transient = code, transient


@dataclass(frozen=True)
class Ready:
    """A verified update, waiting for a restart."""

    version: str
    family: str          # macos, windows, linux
    payload: str         # macOS: the staged bundle; Windows: the installer; Linux: the checked release folder.
    work: str            # This update's folder.
    install_root: str    # macOS: the installed bundle; Windows and Linux: the install folder.
    fingerprint: str     # macOS and Linux: the stage's fingerprint; Windows: the installer's SHA-256.
    method: str          # delta or full
    gatekeeper: str = ""  # macOS: "1" when Gatekeeper approves the running app, so the installer requires it again.
    download_sha256: str = ""  # Linux: the feed's SHA-256 of the tarball this stage came from.
    key_id: str = ""           # Linux: the release key that signed the stage's manifest.


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
                 mac=update_stage_mac, win=update_stage_win, log=print, mirror=None, linux=update_stage_linux):
        self.root, self.current, self.platform = Path(root), current, platform
        if mirror is None:   # The machine policy's UpdateFeedURL mirror also serves the installers (install_source).
            from . import managed_policy
            policy = managed_policy.current()
            mirror = "" if policy.update_feed_url == managed_policy.INVALID else policy.update_feed_url
        self.mirror = mirror
        self.binary, self.upgrade_code = str(binary), upgrade_code
        self.family = update_install.family(platform)
        # Looked up now, not when the module loaded, so a fixture can stand in for the download.
        self.fetch = fetch or update_download.fetch
        self.mac, self.win, self.linux, self.log = mac, win, linux, log

    def support(self):
        """``(reason, install root)``; reason "" when this install can update itself."""
        if self.family == "macos":
            reason, app = self.mac.support(self.binary)
            return reason, str(app or "")
        if self.family == "windows":
            reason, root = self.win.support(self.binary, self.upgrade_code)
            return reason, str(root)
        if self.family == "linux":
            reason, root = self.linux.support(self.binary, self.current)
            return reason, str(root or "")
        return "platform", ""

    def work(self, version):
        """``<root>/<version>``. ``version`` comes from Python state a script can change in place (an offer, a
        ``Ready``): only one folder name that reads as a version is taken, so no removal or stage reaches another
        folder, Angelo's private ones included (``ValueError``; p10c-1-update)."""
        if app_version.parse(version) is None:
            raise ValueError("Not one update version: %r" % (version,))
        return self.root / owned_paths.entry_name(version)

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
        work = self.work(release.version)
        try:
            with update_hold.hold(work, cancel):
                ready = self._prepare_held(release, download, work, install_root, cancel, limit, progress)
        except update_hold.Stopped:
            raise PrepareError("download:cancelled", transient=True) from None
        if not (work / READY_FILE).is_file():
            raise PrepareError("removed", transient=True)   # Removed as this worker let go: not this install's to use.
        return ready

    def _prepare_held(self, release, download, work, install_root, cancel, limit, progress):
        owned_paths.require_writable(work)
        known = self.recall(release.version, download)   # Also what a stopped worker before this one finished.
        if known is not None:
            return known
        work.mkdir(parents=True, exist_ok=True)
        # What an earlier attempt derived goes; a partial or finished download stays, so it resumes or is reused.
        (work / READY_FILE).unlink(missing_ok=True)
        for derived in ("staged", "staging"):
            shutil.rmtree(work / derived, ignore_errors=True)
        options = dict(cancel=cancel, limit=limit, progress=progress)
        if self.family == "macos":
            ready = self._prepare_mac(release, download, work, install_root, options)
        elif self.family == "linux":
            ready = self._prepare_linux(release, download, work, install_root, options)
        else:
            ready = self._prepare_windows(release, download, work, install_root, options)
        (work / READY_FILE).write_text(json.dumps(asdict(ready), indent=2), encoding="utf-8")
        return ready

    def _fetch(self, entry, work, fallback, reserve, options):
        target = work / _file_name(entry.url, fallback)
        source = update_feed.install_source(entry.url, self.mirror)   # The policy's mirror serves it, when set.
        try:
            return self.fetch(Expected(source, entry.sha256, entry.size), target, reserve=reserve, **options)
        except DownloadError as error:
            if source == entry.url or error.code not in MIRROR_MISSING:
                raise PrepareError("download:" + error.code, error.transient) from None
            # A mirror that copies only stable.json: the feed's own host, with the same checks.
            self.log("Update mirror could not serve %s (%s); downloading from Angelo's host"
                     % (_file_name(entry.url, fallback), error.code))
        try:
            return self.fetch(Expected(entry.url, entry.sha256, entry.size), target, reserve=reserve, **options)
        except DownloadError as error:
            raise PrepareError("download:" + error.code, error.transient) from None

    def _prepare_mac(self, release, download, work, app, options):
        owned_paths.require_writable(work)
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
        owned_paths.require_writable(work)
        reserve = UNPACKED_FACTOR["windows"] * download.size
        installer = self._fetch(download, work, "update.msi", reserve, options)
        try:
            self.win.check_installer(installer, self.binary)
        except update_stage_win.InstallerError as error:
            Path(installer).unlink(missing_ok=True)
            raise PrepareError("verify:" + error.code) from None
        return Ready(release.version, "windows", str(installer), str(work), root, download.sha256, "full")

    def _prepare_linux(self, release, download, work, root, options):
        owned_paths.require_writable(work)
        reserve = UNPACKED_FACTOR["linux"] * download.size
        tarball = self._fetch(download, work, "update.tar.xz", reserve, options)
        try:
            signature = self._linux_signature(download.url)
            staged, key_id = self.linux.stage(tarball, release.version, work, signature=signature,
                                              name=_file_name(download.url, "update.tar.xz"))
        except update_stage_linux.StageError as error:
            transient = error.code == "signature_fetch" or error.code.startswith("signature_http_")
            if not transient:
                Path(tarball).unlink(missing_ok=True)
            raise PrepareError("verify:" + error.code, transient) from None   # Offline: the download stays.
        Path(tarball).unlink(missing_ok=True)
        return Ready(release.version, "linux", str(staged), str(work), root, self.linux.fingerprint(staged), "full",
                     download_sha256=download.sha256, key_id=key_id)

    def _linux_signature(self, url):
        """The tarball's ``.minisig``, from the policy's mirror when set (the feed's own host when the mirror
        doesn't hold it), as the tarball itself."""
        signature_url = url + ".minisig"
        source = update_feed.install_source(signature_url, self.mirror)
        try:
            return self.linux.fetch_signature(source)
        except update_stage_linux.StageError as error:
            if source == signature_url or error.code not in ("signature_http_403", "signature_http_404",
                                                              "signature_http_410"):
                raise
        return self.linux.fetch_signature(signature_url)

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
        if download is not None and ready.family == "linux" and ready.download_sha256 != download.sha256:
            return None   # The feed names another tarball for this version now (a respin): never the old stage.
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
        # Held to what staging made, whatever the ``Ready`` (Python state) says now: its version's own folder under
        # the updates folder, this platform, and the one payload there (p10c-1-update).
        try:
            work = self.work(ready.version)
        except ValueError:
            return False
        payload = Path(ready.payload)
        staged = (work / "staged" / Path(ready.install_root).name if ready.family == "macos"
                  else self.linux.staged_path(work, ready.version) if ready.family == "linux" else None)
        if (ready.family != self.family or Path(ready.work) != work
                or (payload != staged if staged is not None else payload.parent != work)
                or not _same_path(ready.install_root, self.support()[1])):
            return False
        try:
            if ready.family == "macos":
                return payload.is_dir() and self.mac.fingerprint(payload) == ready.fingerprint
            if ready.family == "linux":   # And still signed by a key this build trusts now (revocation reaches it).
                return (payload.is_dir() and not payload.is_symlink()
                        and self.linux.fingerprint(payload) == ready.fingerprint
                        and self.linux.staged_key(payload) == ready.key_id)
            return payload.is_file() and _sha256(payload) == ready.fingerprint
        except (OSError, update_stage_linux.release_manifest.ManifestError):
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
        """Remove every update folder except ``keep`` (versions); returns the names removed now. A folder a worker
        holds goes as that worker lets go (one another Angelo holds stays, for a later clean)."""
        removed = []
        for folder in sorted(self.root.glob("*")) if self.root.is_dir() else ():
            if folder.name not in keep and folder.is_dir() and self._remove(folder):
                removed.append(folder.name)
        return removed

    def forget(self, version, unless=None):
        """Remove one version's folder, whatever it holds (the person chose not to take that version); a worker
        still writing there removes it as it stops. ``unless``: asked right before removing (the person changed
        their mind meanwhile). Returns the names removed now."""
        try:
            folder = self.work(version)
        except ValueError:
            return []   # Not a version's name: nothing of the updates folder's.
        return [version] if folder.is_dir() and self._remove(folder, unless) else []

    def _remove(self, folder, unless=None):
        owned_paths.require_writable(folder)
        def remove():
            owned_paths.require_writable(folder)
            if self.family == "macos":
                self.mac.release_mount(folder / "mount")   # A disk image a quit left attached.
            shutil.rmtree(folder, ignore_errors=True)
        return update_hold.remove(folder, remove, unless)

    def free(self):
        """Free bytes where updates are stored (for the card's message)."""
        try:
            return shutil.disk_usage(self.root if self.root.exists() else self.root.parent).free
        except OSError:
            return 0


RECOVERY_FILE = "recovery.json"   # Linux: the fallback's kept folder and note, as Angelo chose them.
_NOTE_LIMIT = 64 * 1024


def _clean(text):
    """``text`` as a path when it is absolute and already normal (no ``.`` or ``..`` part, no doubled slash), else
    None: recorded and installer-written paths are compared as strings, so ``<app>/..`` never matches its parent."""
    if not isinstance(text, str) or not text or "\0" in text or not os.path.isabs(text):
        return None
    return Path(text) if os.path.normpath(text) == text and not text.startswith("//") else None


def record_recovery(work, install_root, version, kept, note, note_text):
    """Before the Linux hand-over: the exact kept-folder and note paths Angelo chose (``update_card.linux_recovery``),
    the install folder's device and inode with the version it holds (a rename keeps both), and the hash of the
    note's bytes, in a file of the update's folder the installer never writes. Cleanup deletes only what matches
    all of it, never by translated names. Refuses paths that are not normal or not beside the install."""
    owned_paths.require_writable(work)
    root = _clean(str(install_root))
    if root is None or any(_clean(str(path)) is None or Path(path).parent != root.parent for path in (kept, note)):
        raise ValueError("the recovery paths must be normal paths beside the install")
    status = os.lstat(root)
    (Path(work) / RECOVERY_FILE).write_text(json.dumps({
        "root": str(root), "kept": str(kept), "note": str(note), "device": status.st_dev, "inode": status.st_ino,
        "version": version, "note_sha256": hashlib.sha256(os.fsencode(note_text)).hexdigest()}), encoding="utf-8")


def _recovery(work, install_root):
    """The record, when it is whole and was made for ``install_root``; else None."""
    try:
        document = json.loads(_read_own(Path(work) / RECOVERY_FILE, _NOTE_LIMIT))   # Never blocks on a FIFO.
        root, kept, note = (_clean(document[key]) for key in ("root", "kept", "note"))
        fields = (document["device"], document["inode"], document["version"], document["note_sha256"])
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None
    if (None in (root, kept, note) or str(root) != str(install_root) or kept.parent != root.parent
            or note.parent != root.parent or not all(type(value) is int for value in fields[:2])
            or not all(type(value) is str for value in fields[2:])):
        return None
    return dict(zip(("root", "kept", "note", "device", "inode", "version", "note_sha256"), (root, kept, note) + fields))


def _read_own(path, limit):
    """A regular file's bytes, never through a link and never blocking, or None: a cleanup reader never raises on
    what it finds on disk (an exception would end the update timer that runs cleanup at launch)."""
    manifest = update_stage_linux.release_manifest
    try:
        return manifest.read_own_file(os.path.dirname(path), os.path.basename(path), limit)
    except (manifest.ManifestError, OSError):
        return None


def _release_version(folder):
    manifest = update_stage_linux.release_manifest
    try:
        return json.loads(manifest.read_own_file(folder, manifest.MANIFEST_NAME, manifest.MAX_MANIFEST))["version"]
    except (manifest.ManifestError, OSError, TypeError, ValueError, KeyError, RecursionError):
        return None


def _runs(root):
    return os.path.isfile(os.path.join(root, update_stage_linux.release_manifest.MANIFEST_NAME))   # Never raises.


def forget_note(work, install_root):
    """Remove the Linux fallback's recovery note Angelo recorded, once the app at ``install_root`` runs again (after
    an install, and after an attempt that stopped before it moved anything): its instructions no longer hold. Only
    the recorded ``.txt`` beside the install whose bytes are exactly the note Angelo wrote, naming both folders."""
    record, root = _recovery(work, install_root), Path(install_root)
    note = record and record["note"]
    if not note or note.suffix != ".txt" or not _runs(root):
        return False
    data = _read_own(note, _NOTE_LIMIT)
    kept = os.fsencode(str(record["kept"]))   # Angelo's note names both, the install folder also on its own:
    if (data is None or hashlib.sha256(data).hexdigest() != record["note_sha256"] or kept not in data
            or os.fsencode(str(root)) not in data.replace(kept, b"")):
        return False
    try:
        note.unlink(missing_ok=True)
    except OSError:   # A sticky shared folder, a read-only or stale mount: kept, and cleanup goes on.
        return False
    return True


def _is_kept_release(record, previous):
    """``previous`` is exactly the install folder Angelo recorded, renamed: same path, device, inode and version,
    a version older than the one now running."""
    try:
        status = os.lstat(previous)
    except OSError:
        return False
    older, running = record and app_version.parse(record["version"]), app_version.parse(app_version.current())
    return (record is not None and previous == record["kept"] and stat.S_ISDIR(status.st_mode)
            and (status.st_dev, status.st_ino) == (record["device"], record["inode"])
            and None not in (older, running) and older < running and _release_version(previous) == record["version"])


def remove_previous(work, install_root):
    """After a successful install: delete the previous app the macOS or Linux installer kept (``previous.txt``).

    Only a folder the installer names that way is removed: ``<work>/previous.app``, ``<work>/previous``, a folder of
    ``<work>/staged`` (Linux, an exchange whose last move did not happen), beside the app ``.<name>.previous-<n>`` or
    ``.<name>.update-<n>``, or exactly the kept folder Angelo recorded (``_is_kept_release``); never the installed
    app or a path that is not normal. Also the installer's pid-named leftovers and the recorded note
    (``forget_note``). Each step is independent and none raises. Called at the first start of the installed
    version, also for ``interrupted`` (a stop after the new folder was in place).
    """
    root = Path(install_root)
    removed = forget_note(work, root)
    for leftover in update_stage_linux.installer_leftovers(root):
        shutil.rmtree(leftover, ignore_errors=True)
        removed = True
    try:
        previous = _clean(_read_own(Path(work) / "previous.txt", _NOTE_LIMIT).decode("utf-8").strip())
    except (AttributeError, UnicodeDecodeError):   # None (missing, not a file) or not UTF-8.
        return removed
    if previous is None:
        return removed
    pid_named, name = update_stage_linux.pid_named, root.name
    beside = previous.parent == root.parent and (
        pid_named(previous.name, "." + name + ".previous-") or pid_named(previous.name, "." + name + ".update-")
        or _is_kept_release(_recovery(work, root), previous))
    named = (previous in (Path(work) / "previous.app", Path(work) / "previous")
             or previous.parent == Path(work) / "staged" or beside)
    if named and previous != root and os.path.isdir(previous) and not os.path.islink(previous):
        shutil.rmtree(previous, ignore_errors=True)
        return True
    return removed
