# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Downloading an update file in the background: resumable, bounded and verified.

Contract: doc/angelo/updates_and_reports.rst, "Installing updates". The file's
size and SHA-256 come from the signed feed (``update_feed.Download``), so the
bytes are trusted only once both match; until then they live in
``<name>.part`` and are never opened as an installer. An interrupted download
resumes with ``Range`` and ``If-Range`` (the server's validator from the first
answer), so a file that changed on the server starts over instead of being
spliced. Nothing beyond the announced size is ever written, and a finished
file whose digest differs is deleted.

``limit()`` is asked between chunks for a byte rate (None: no limit): the
updater lowers it while an agent turn runs, so a large download never competes
with the agent for the connection. ``cancel`` (a ``threading.Event``) stops at
the next chunk and keeps the partial file for a later resume.

Standard library only; no bpy. Blocking: call it from a worker thread.
"""

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import app_http
from .app_http import NetworkError

CHUNK = 256 * 1024
READ_TIMEOUT = 30
PROGRESS_INTERVAL = 0.25


class DownloadError(Exception):
    """``code``: offline, timeout, tls, protocol, bad_url, http_<status>, incomplete, size, digest, disk_space,
    cancelled, io."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code

    @property
    def transient(self):
        """True when trying again later may succeed (network trouble, a busy server, a cancelled run)."""
        return (self.code in {"offline", "timeout", "tls", "protocol", "incomplete", "cancelled", "io", "http_429"}
                or self.code.startswith("http_5"))


@dataclass(frozen=True)
class Expected:
    """What the signed feed says the file is."""

    url: str
    sha256: str
    size: int


def _digest_of(path, limit=None):
    digest, done = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while limit is None or done < limit:
            chunk = handle.read(CHUNK if limit is None else min(CHUNK, limit - done))
            if not chunk:
                break
            digest.update(chunk)
            done += len(chunk)
    return digest, done


def verified(path, expected):
    """True when ``path`` is exactly the expected file (size first, then SHA-256)."""
    try:
        if os.path.getsize(path) != expected.size:
            return False
        return _digest_of(path)[0].hexdigest() == expected.sha256
    except OSError:
        return False


def _meta_path(part):
    return part.with_name(part.name + ".json")


def _read_meta(part, expected):
    """The stored validator of a partial file, when it belongs to this exact download."""
    try:
        meta = json.loads(_meta_path(part).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(meta, dict) or meta.get("sha256") != expected.sha256 or meta.get("url") != expected.url:
        return ""
    validator = meta.get("validator")
    return validator if isinstance(validator, str) and len(validator) <= 200 else ""


def _write_meta(part, expected, validator):
    _meta_path(part).write_text(json.dumps({"url": expected.url, "sha256": expected.sha256, "size": expected.size,
                                            "validator": validator}), encoding="utf-8")


def _discard(part):
    for path in (part, _meta_path(part)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _validator(headers):
    """A strong ETag, else Last-Modified: what ``If-Range`` may carry (a weak ETag may not)."""
    etag = headers.get("etag", "")
    if etag and not etag.startswith("W/"):
        return etag
    return headers.get("last-modified", "")


def _content_range_start(headers):
    """The first byte of a ``206``'s ``Content-Range: bytes <start>-<end>/<total>``, or None."""
    value = headers.get("content-range", "")
    if not value.startswith("bytes "):
        return None
    try:
        return int(value[6:].split("-", 1)[0])
    except ValueError:
        return None


class Throttle:
    """Sleeps between chunks so the average rate stays at ``limit()`` bytes a second (None: no limit)."""

    def __init__(self, limit, clock=time.monotonic, sleep=time.sleep):
        self.limit, self.clock, self.sleep = limit, clock, sleep
        self.window_start, self.window_bytes = clock(), 0

    def account(self, count):
        rate = self.limit() if self.limit else None
        now = self.clock()
        if not rate:
            self.window_start, self.window_bytes = now, 0
            return
        self.window_bytes += count
        ahead = self.window_bytes / rate - (now - self.window_start)
        if ahead > 0:
            self.sleep(min(ahead, 2.0))
        if now - self.window_start > 5.0:   # A short window: a new limit applies within seconds.
            self.window_start, self.window_bytes = self.clock(), 0


def fetch(expected, target, *, stream=app_http.stream, progress=None, cancel=None, limit=None, clock=time.monotonic,
          sleep=time.sleep, reserve=0):
    """Download ``expected`` to ``target`` and verify it; returns ``target``. Raises ``DownloadError``.

    An existing ``target`` that verifies is reused without a request. ``reserve`` is extra free space
    (bytes) the caller needs afterwards (unpacking), checked before the first byte is written.
    """
    target = Path(target)
    if target.exists() and verified(target, expected):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    validator = _read_meta(part, expected) if have else ""
    if have > expected.size or (have and not validator):
        _discard(part)
        have = 0
    if shutil.disk_usage(target.parent).free < expected.size - have + reserve:
        raise DownloadError("disk_space", "Not enough free space for the update")
    headers = {"Accept-Encoding": "identity"}
    if have:
        headers.update({"Range": "bytes=%d-" % have, "If-Range": validator})
    try:
        status, response_headers, body = stream(expected.url, headers=headers, timeout=READ_TIMEOUT)
    except NetworkError as error:
        raise DownloadError(error.kind, str(error)) from None
    try:
        if status == 206 and have and _content_range_start(response_headers) == have:
            digest, _ = _digest_of(part)
            mode = "ab"
        elif status == 200:
            digest, have, mode = hashlib.sha256(), 0, "wb"
            _write_meta(part, expected, _validator(response_headers))
        elif status == 416 and have == expected.size:
            digest, mode = _digest_of(part)[0], None   # Already complete: only the check is left.
        else:
            if status in {206, 416}:
                _discard(part)   # A range answer that doesn't continue this file: start over next time.
            raise DownloadError("http_%d" % status, "The download server answered %d" % status)
        if mode is not None:
            have = _copy(body, part, mode, digest, have, expected, progress, cancel, limit, clock, sleep)
    finally:
        try:
            body.close()
        except Exception:   # Closing a broken connection must not hide the real outcome.
            pass
    if have != expected.size:
        raise DownloadError("incomplete", "The download ended early")   # The partial file resumes next time.
    if digest.hexdigest() != expected.sha256:
        _discard(part)
        raise DownloadError("digest", "The downloaded file is not the release's file")
    os.replace(part, target)
    _discard(part)
    return target


def _copy(body, part, mode, digest, have, expected, progress, cancel, limit, clock, sleep):
    throttle = Throttle(limit, clock, sleep)
    last_report = 0.0
    try:
        with open(part, mode) as handle:
            while have < expected.size:
                if cancel is not None and cancel.is_set():
                    raise DownloadError("cancelled", "The download was stopped")
                try:
                    chunk = body.read(min(CHUNK, expected.size - have + 1))
                except Exception as error:   # Socket, TLS and protocol errors while reading the body.
                    failure = app_http.read_failure(error)
                    raise DownloadError(failure.kind, str(failure)) from None
                if not chunk:
                    break
                if have + len(chunk) > expected.size:
                    handle.close()
                    _discard(part)
                    raise DownloadError("size", "The server sent more than the release's size")
                handle.write(chunk)
                digest.update(chunk)
                have += len(chunk)
                throttle.account(len(chunk))
                now = clock()
                if progress is not None and (now - last_report >= PROGRESS_INTERVAL or have == expected.size):
                    last_report = now
                    progress(have, expected.size)
    except OSError as error:
        raise DownloadError("io", "The update could not be written: %s" % type(error).__name__) from None
    return have
