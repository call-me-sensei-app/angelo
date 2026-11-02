# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library's downloads: the index, thumbnails and item files, into a local cache (doc/angelo/library.rst).

Everything comes from public links and carries no credential (``app_http``: the system proxy, Angelo's trust roots,
https only). On top of that, for untrusted bytes:

* **Hosts.** Every link is https on a ToonLab host (``library_catalog``); file downloads never follow a redirect.
* **Index.** ``latest.json`` (at most 64 KB) names the version, the index's link, size and SHA-256; the index is
  downloaded only when the version changed, must match both, and is at most 64 MB compressed and 256 MB
  decompressed. A failed refresh keeps the last good index.
* **Files.** Each file streams into a temporary name in the item's own folder, stops at its declared size (and the
  hard ``MAX_FILE_BYTES``), must end at exactly that size and hash to its SHA-256 (else its MD5), and only then is
  renamed into place. A cached file is hashed again before every use, so a file changed on disk is downloaded anew.
* **Names.** An item's folder is named by a digest of its id and file list, never by catalog text; a file's name is
  checked by ``library_catalog.safe_name`` and its resolved path must stay inside that folder.
* **Size and time.** The file cache is trimmed to ``CACHE_BYTES`` (oldest use first, links never followed) after each
  download, sparing every item folder someone holds a ``Lease`` on (from its request until its import ends, so a
  file waiting for its main-thread import is never deleted under it); thumbnails to ``THUMB_CACHE_BYTES`` every 200 downloads. A file download ends past ``MIN_SECONDS`` plus
  its size at ``MIN_RATE``, and stops when its cancel event is set. Thumbnails are at most 2 MB, PNG, JPEG or WebP,
  and at most ``MAX_THUMB_SIDE`` pixels a side, read from the header before anything decodes them.
* **No redirects** for any request: a 3xx is an answer, never followed (the library's objects are served directly).

Standard library only; no bpy. Blocking: call from worker threads.
"""

import hashlib
import json
import os
import shutil
import threading
import time
import weakref
import zlib
from pathlib import Path

from . import app_http, library_catalog as catalog, team_library_files

LATEST_URL = "https://assets.toonlab.io/catalog/v1/latest.json"   # ToonLab's catalog export.
URL_VARIABLE = "ANGELO_LIBRARY_URL"    # Another latest.json (tests, a development mirror on this computer).
MAX_LATEST = 64 * 1024
MAX_INDEX = 64 * 1024 * 1024
MAX_INDEX_JSON = 64 * 1024 * 1024   # Four times the first index (8,800 items, 14 MB): room to grow, no more.
MAX_THUMB = 2 * 1024 * 1024
CACHE_BYTES = 4 * 1024 ** 3
THUMB_CACHE_BYTES = 256 * 1024 ** 2
CHUNK = 1 << 20
MIN_SECONDS, MIN_RATE = 60.0, 64 * 1024   # A file's download may take 60 s plus its size at 64 KB/s.
MAX_THUMB_SIDE = 2048
TIMEOUT = 30
_lock = threading.Lock()
_leases = {}   # item folder (str) -> holders; guarded by _lock, which the trim holds while it removes.


class FetchError(RuntimeError):
    """A download that failed; ``code`` is the closed refusal code (``EXPIRED`` in transit, ``UNSUPPORTED`` refused,
    ``LIMIT_EXCEEDED`` too large, ``CANCELLED`` stopped). The message never holds a link."""

    def __init__(self, message, code="EXPIRED"):
        super().__init__(message)
        self.refusal_code = code


def latest_url():
    return os.environ.get(URL_VARIABLE) or LATEST_URL


def loopback_ok():
    """http on this computer is accepted only when the latest link itself was pointed there (tests, development)."""
    return catalog.allowed_url(latest_url(), loopback_ok=True) and not latest_url().startswith("https://")


def _get(url, limit, what, hosts=catalog.FILE_HOSTS):
    if not catalog.allowed_url(url, loopback_ok(), hosts):
        raise FetchError("The library's %s isn't on a ToonLab host; it wasn't used." % what, "UNSUPPORTED")
    try:
        response = app_http.request("GET", url, timeout=TIMEOUT, limit=limit, follow=False)
    except app_http.NetworkError as error:
        raise FetchError("Couldn't reach the Angelo Library (%s); check your network." % error.kind,
                         "LIMIT_EXCEEDED" if error.kind == app_http.TOO_LARGE else "EXPIRED") from None
    if response.status != 200:
        raise FetchError("The Angelo Library answered HTTP %d for its %s." % (response.status, what))
    return response.body


# -- the index ------------------------------------------------------------------------------------------------

def read_local(folder):
    """The cached index's document, or None (missing, or no longer matching its record)."""
    record_path = Path(folder) / "index.json"
    try:
        record = json.loads(record_path.read_text())
        data = (Path(folder) / record["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            return None
        return _decode(data)
    except (OSError, ValueError, KeyError, TypeError, FetchError):
        return None


def _decode(packed):
    try:
        stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = stream.decompress(packed, MAX_INDEX_JSON + 1)
        if len(raw) > MAX_INDEX_JSON or stream.unconsumed_tail:
            raise FetchError("The library index is larger than Angelo reads.", "LIMIT_EXCEEDED")
        document = json.loads(raw.decode("utf-8"))
    except (zlib.error, ValueError, UnicodeError, RecursionError):
        raise FetchError("The library index couldn't be read.", "UNSUPPORTED") from None
    if not isinstance(document, dict):
        raise FetchError("The library index couldn't be read.", "UNSUPPORTED")
    return document


def refresh(folder, current_version=None):
    """Download the index when ``latest.json`` names another version; returns the document, or None when the cached
    one is current. Writes ``index.json`` (the record) and the index file atomically; never leaves a partial file."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        latest = json.loads(_get(latest_url(), MAX_LATEST, "version list").decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise FetchError("The library's version list couldn't be read.", "UNSUPPORTED") from None
    if not isinstance(latest, dict) or latest.get("schema") != catalog.SCHEMA:
        raise FetchError("This Angelo is too old for the current library; update Angelo.", "UNSUPPORTED")
    version, url, size, sha256 = (latest.get(k) for k in ("version", "url", "size", "sha256"))
    if (not isinstance(version, str) or type(size) is not int or not 0 < size <= MAX_INDEX
            or not isinstance(sha256, str) or len(sha256) != 64):
        raise FetchError("The library's version list is malformed.", "UNSUPPORTED")
    if version == current_version:
        return None
    packed = _get(url, size, "index")
    if len(packed) != size or hashlib.sha256(packed).hexdigest() != sha256:
        raise FetchError("The downloaded library index doesn't match its checksum; the last one stays.", "UNSUPPORTED")
    document = _decode(packed)
    if document.get("version") != version:
        raise FetchError("The library index isn't the version announced; the last one stays.", "UNSUPPORTED")
    try:   # Checked before it replaces the last good index: an unusable one is never kept.
        catalog.parse(document, loopback_ok())
    except catalog.IndexRefused:
        raise FetchError("The downloaded library index isn't usable; the last one stays.", "UNSUPPORTED") from None
    name = "index-%s.json.gz" % hashlib.sha256(version.encode()).hexdigest()[:16]
    _atomic(folder / name, packed)
    _atomic(folder / "index.json", json.dumps({"version": version, "file": name, "sha256": sha256}).encode())
    for old in folder.glob("index-*.json.gz"):
        if old.name != name:
            old.unlink(missing_ok=True)
    return document


def _atomic(path, data):
    temporary = path.with_name(".%s.%d.part" % (path.name, threading.get_ident()))
    temporary.write_bytes(data)
    os.replace(temporary, path)


# -- item files -----------------------------------------------------------------------------------------------

def item_folder(root, item, files):
    """``<root>/files/<digest of id and file list>``: a changed item never reuses its old files."""
    key = json.dumps([item["id"], [(f["name"], f.get("sha256") or f.get("md5"), f["size"]) for f in files]])
    return Path(root) / "files" / hashlib.sha256(key.encode()).hexdigest()[:32]


def _inside(folder, name):
    if not catalog.safe_name(name):
        raise FetchError("A library file has an unusable name; it wasn't downloaded.", "UNSUPPORTED")
    path = (folder / name).resolve()
    if not str(path).startswith(str(folder.resolve()) + os.sep):
        raise FetchError("A library file's name leaves its folder; it wasn't downloaded.", "UNSUPPORTED")
    return path


def _digests(path):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def _matches(path, file):
    try:
        if path.stat().st_size != file["size"]:
            return False
    except OSError:
        return False
    sha, md5 = _digests(path)
    return sha == file["sha256"] if "sha256" in file else md5 == file["md5"]


def _download(url, path, file, cancel):
    if not catalog.allowed_url(url, loopback_ok()):
        raise FetchError("A library file isn't on a ToonLab host; it wasn't downloaded.", "UNSUPPORTED")
    try:
        status, headers, body = app_http.stream(url, timeout=TIMEOUT, follow=False)
    except app_http.NetworkError as error:
        raise FetchError("Couldn't download from the Angelo Library (%s); check your network." % error.kind) from None
    temporary = path.with_name(".%s.%d.part" % (path.name, threading.get_ident()))
    try:
        final = getattr(body, "geturl", lambda: url)()
        if status != 200:
            raise FetchError("The Angelo Library answered HTTP %d for a file; try again later." % status)
        if not catalog.allowed_url(final, loopback_ok()):
            raise FetchError("A library file was redirected off ToonLab's hosts; it wasn't downloaded.", "UNSUPPORTED")
        if (headers.get("content-encoding") or "identity").lower() != "identity":
            raise FetchError("The library sent an encoded file; it wasn't used.", "UNSUPPORTED")
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) != file["size"]:
            raise FetchError("A library file's size doesn't match the index; it wasn't used.", "UNSUPPORTED")
        sha, md5, written = hashlib.sha256(), hashlib.md5(), 0
        deadline = time.monotonic() + MIN_SECONDS + file["size"] / MIN_RATE   # A dripping server never holds a fetch.
        with open(temporary, "xb") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise FetchError("The download was stopped.", "CANCELLED")
                if time.monotonic() > deadline:
                    raise FetchError("The library download is too slow; try again later.")
                try:
                    chunk = body.read(CHUNK)
                except Exception as error:   # noqa: BLE001 (any read failure is a failed transfer)
                    raise FetchError("The download was interrupted (%s); try again."
                                     % app_http.read_failure(error).kind) from None
                if not chunk:
                    break
                written += len(chunk)
                if written > file["size"]:
                    raise FetchError("A library file is larger than the index says; it wasn't used.", "UNSUPPORTED")
                handle.write(chunk)
                sha.update(chunk)
                md5.update(chunk)
        if written != file["size"]:
            raise FetchError("A library download ended early; try again.")
        if (sha.hexdigest() != file["sha256"]) if "sha256" in file else (md5.hexdigest() != file["md5"]):
            raise FetchError("A library file doesn't match its checksum; it wasn't used.", "UNSUPPORTED")
        os.replace(temporary, path)
    finally:
        body.close()
        temporary.unlink(missing_ok=True)


class Lease:
    """Holds an item's cache folder against the trim, from its request until its import ends. ``release`` is
    idempotent; a lease nobody releases ends when it is collected."""

    def __init__(self, folder):
        self.folder = str(folder)
        with _lock:
            _leases[self.folder] = _leases.get(self.folder, 0) + 1
        self._end = weakref.finalize(self, _unhold, self.folder)

    def release(self):
        self._end()


def _unhold(folder):
    with _lock:
        left = _leases.get(folder, 0) - 1
        if left > 0:
            _leases[folder] = left
        else:
            _leases.pop(folder, None)


def held():
    """``{folder: holders}`` of the leases now held (for tests and diagnostics)."""
    with _lock:
        return dict(_leases)


def fetch_files(root, item, files, cancel=None, progress=None):
    """Every file of ``files`` checked in the item's cache folder (downloaded when missing or changed); returns
    ``{name: path}``. ``progress(done_bytes, total_bytes)`` is called from this thread. The folder is held while
    this runs; a caller that imports later holds its own ``Lease`` from before this call until the import ends."""
    folder = item_folder(root, item, files)
    lease = Lease(folder)
    try:
        return _fetch_into(root, folder, files, cancel, progress)
    finally:
        lease.release()


def _fetch_into(root, folder, files, cancel, progress):
    if folder.is_symlink():
        folder.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    total = sum(f["size"] for f in files)
    done, paths = 0, {}
    for file in files:
        if file["size"] > catalog.MAX_FILE_BYTES:
            raise FetchError("A library file is larger than Angelo imports.", "LIMIT_EXCEEDED")
        path = _inside(folder, file["name"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not _matches(path, file):
            path.unlink(missing_ok=True)
            _download(file["url"], path, file, cancel)
        done += file["size"]
        if progress is not None:
            progress(done, total)
        paths[file["name"]] = path
    now = time.time()
    os.utime(folder, (now, now))   # The trim keeps recently used items.
    trim(Path(root) / "files", CACHE_BYTES)
    return paths


def cached(root, item, files):
    """Whether every file is already in the cache at its size (no hashing: a hint for the interface)."""
    folder = item_folder(root, item, files)
    try:
        return all((folder / f["name"]).stat().st_size == f["size"] for f in files)
    except OSError:
        return False


def trim(folder, limit):
    """Remove the least recently used entries of ``folder`` until it holds at most ``limit`` bytes, never one under a
    ``Lease``. Links are removed as links and never followed: nothing outside the cache is counted or deleted."""
    with _lock:
        entries = []
        try:
            listed = list(os.scandir(folder))
        except OSError:
            return
        for entry in listed:
            try:
                stat = entry.stat(follow_symlinks=False)
                size = _tree_size(entry.path) if entry.is_dir(follow_symlinks=False) else stat.st_size
                entries.append((stat.st_mtime, size, Path(entry.path)))
            except OSError:
                continue
        total = sum(size for _, size, _ in entries)
        for _mtime, size, entry in sorted(entries, key=lambda e: e[0]):
            if total <= limit:
                break
            if str(entry) in _leases:
                continue
            _remove(entry)
            total -= size


def _tree_size(path):
    total = 0
    for top, folders, names in os.walk(path):   # os.walk never descends into a linked folder.
        for name in names:
            try:
                total += os.lstat(os.path.join(top, name)).st_size
            except OSError:
                pass
    return total


def _remove(entry):
    if entry.is_symlink() or not entry.is_dir():
        entry.unlink(missing_ok=True)
    else:
        shutil.rmtree(entry, ignore_errors=True)   # Removes links inside as links.


# -- thumbnails -----------------------------------------------------------------------------------------------

def thumbnail(root, url):
    """The thumbnail's cached file (downloaded when missing), or None when it can't be had."""
    folder = Path(root) / "thumbs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".img")
    if path.is_file():
        os.utime(path)
        return path
    try:
        data = _get(url, MAX_THUMB, "preview", catalog.FILE_HOSTS)
    except FetchError:
        return None
    try:   # Format and pixel bounds from the header, before the main thread ever decodes it.
        if team_library_files.sniff(data[:65536]) not in team_library_files.IMAGE_FORMATS:
            return None
        width, height = team_library_files.check_image(data, "The preview")
    except team_library_files.Refused:
        return None
    if max(width, height) > MAX_THUMB_SIDE:
        return None
    _atomic(path, data)
    _thumbs_written[0] += 1
    if _thumbs_written[0] % 200 == 0:
        trim(folder, THUMB_CACHE_BYTES)
    return path


_thumbs_written = [0]
