# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Downloads from and uploads to the team library's short-lived storage links.

The console answers a download or upload request with a presigned link
(Cloudflare R2 in production; ``/api/dev-storage/...`` on a development
console). A link is its own credential, so these requests carry **no**
Authorization header, cookie or token. Rules match ``team_http``: https with
verified TLS, plain http only to a loopback host, no redirects (a 3xx is a
failure, never followed), no proxies, timeouts on every socket operation, and
the same trust roots as the console (``team_http.tls_context``: certifi when the
interpreter has none, plus a managed policy's extra CA certificates).

Downloads stream into a file the caller owns, stop at a hard byte cap, check
the declared length and, when the library verified one on upload, the SHA-256,
and can be cancelled between chunks. Uploads stream a
file with exactly the headers the console signed.

Standard library only; no bpy. Called only on worker threads.
"""

import hashlib
import http.client
from urllib.parse import urlsplit

from .team_endpoints import is_loopback
from .team_http import USER_AGENT, tls_context

TIMEOUT = 30
CHUNK = 1024 * 1024
_FORBIDDEN_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


class TransferError(RuntimeError):
    """A storage transfer that failed; ``str`` is a fixed sentence without the link.

    ``refusal_code`` is its closed code (``refusal_codes``): ``UNSUPPORTED`` when
    a link or delivered file is refused, ``LIMIT_EXCEEDED`` over a size or rate
    limit, ``EXPIRED`` when the short-lived attempt failed in transit and a new
    request may try again, ``CANCELLED`` when stopped. Transfers run on workers
    without Blender data, so none of these changed a scene.
    """

    def __init__(self, message, code):
        super().__init__(message)
        self.refusal_code = code


class Cancelled(TransferError):
    def __init__(self, message):
        super().__init__(message, "CANCELLED")


def _connection(url, timeout):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or not parsed.hostname:
        raise TransferError("The team library returned an unusable storage link.", "UNSUPPORTED")
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout,
                                                 context=tls_context())
    elif parsed.scheme == "http" and is_loopback(parsed.hostname):
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    else:
        raise TransferError("The team library returned a storage link that isn't https; it wasn't used.", "UNSUPPORTED")
    return connection, (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")


def download(url, destination, *, limit, expected=None, sha256=None, cancel=None, timeout=TIMEOUT):
    """Write the object at ``url`` to ``destination`` (a new file); returns the byte count.

    ``limit``: refuse anything larger. ``expected``: the library's declared
    size, which the stored object must match exactly. ``sha256``: the
    lowercase hex digest storage checked when the file arrived (the current
    console's ``LibraryItemDto.sha256``); the bytes must hash to it.
    """
    digest = hashlib.sha256() if sha256 else None
    connection, path = _connection(url, timeout)
    try:
        try:
            connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            response = connection.getresponse()
        except (OSError, http.client.HTTPException):
            raise TransferError("Couldn't download the file from team storage; check your network and try again.",
                                "EXPIRED") from None
        if response.status != 200:
            raise TransferError("Team storage refused the download (HTTP %d); the link may have expired. Try again."
                                % response.status, "EXPIRED")
        if (response.getheader("Content-Encoding") or "identity").lower() != "identity":
            raise TransferError("Team storage sent an encoded file; it wasn't used.", "UNSUPPORTED")
        declared = response.getheader("Content-Length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            raise TransferError("The file is larger than Angelo imports (%d MB at most)." % (limit // (1024 * 1024)),
                                "LIMIT_EXCEEDED")
        if expected is not None and declared is not None and int(declared) != expected:
            raise TransferError("The stored file's size doesn't match the library; it wasn't imported.", "UNSUPPORTED")
        written = 0
        with open(destination, "xb") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("The download was stopped.")
                try:
                    chunk = response.read(CHUNK)
                except (OSError, http.client.HTTPException):
                    raise TransferError("The download was interrupted; try again.", "EXPIRED") from None
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise TransferError("The file is larger than Angelo imports (%d MB at most)." % (limit // (1024 * 1024)),
                                        "LIMIT_EXCEEDED")
                handle.write(chunk)
                if digest is not None:
                    digest.update(chunk)
        if expected is not None and written != expected:
            raise TransferError("The download ended early or doesn't match the library's size; try again.", "EXPIRED")
        if digest is not None and digest.hexdigest() != sha256:
            raise TransferError("The downloaded file doesn't match the checksum the library verified; it wasn't imported.",
                                "UNSUPPORTED")
        return written
    finally:
        connection.close()


def fetch(url, *, limit, timeout=TIMEOUT):
    """A small object (a thumbnail) in memory, at most ``limit`` bytes."""
    connection, path = _connection(url, timeout)
    try:
        try:
            connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            response = connection.getresponse()
            data = response.read(limit + 1) if response.status == 200 else b""
        except (OSError, http.client.HTTPException):
            raise TransferError("Couldn't load the preview from team storage.", "EXPIRED") from None
        if response.status != 200 or not data or len(data) > limit:
            raise TransferError("The preview isn't available.", "UNSUPPORTED")
        return data
    finally:
        connection.close()


def upload(url, headers, source, size, *, cancel=None, timeout=TIMEOUT):
    """PUT ``size`` bytes of the file ``source`` with exactly the signed ``headers``."""
    signed = {str(k): str(v) for k, v in (headers or {}).items()}
    if any(name.lower() in _FORBIDDEN_HEADERS for name in signed):
        raise TransferError("The upload link asked for credentials; nothing was uploaded.", "UNSUPPORTED")
    connection, path = _connection(url, timeout)
    try:
        try:
            connection.putrequest("PUT", path, skip_accept_encoding=True)
            for name, value in dict(signed, **{"Content-Length": str(size), "User-Agent": USER_AGENT}).items():
                connection.putheader(name, value)
            connection.endheaders()
            sent = 0
            with open(source, "rb") as handle:
                while sent < size:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("The upload was stopped; nothing was added to the library.")
                    chunk = handle.read(min(CHUNK, size - sent))
                    if not chunk:
                        raise TransferError("The file changed while uploading; nothing was added to the library.", "EXPIRED")
                    connection.send(chunk)
                    sent += len(chunk)
            response = connection.getresponse()
            response.read(65536)
        except (OSError, http.client.HTTPException):
            raise TransferError("The upload was interrupted; nothing was added to the library. Try again.",
                                "EXPIRED") from None
        if not 200 <= response.status < 300:
            raise TransferError("Team storage refused the upload (HTTP %d); nothing was added to the library."
                                % response.status, "EXPIRED")
    finally:
        connection.close()
