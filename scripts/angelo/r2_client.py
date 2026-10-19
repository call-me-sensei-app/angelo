#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A minimal S3 client for one Cloudflare R2 bucket: AWS Signature Version 4 over ``http.client``.

Only what publishing installers needs: HEAD, PUT (streamed from a file, never chunked), GET and DELETE of one
object, path-style (``https://<account id>.r2.cloudflarestorage.com/<bucket>/<key>``) in R2's region ``auto``.
Every request is signed in its ``Authorization`` header, with the payload's real SHA-256 for a PUT so R2 refuses
a body that changed on the way; this module never builds a presigned URL.

Errors (``R2Error``) name the method, the object key and the HTTP status only: never a credential, a signature,
a request header or the provider's response body, which can echo them. ``publish_installers.py`` uses it.
Standard library only (Python 3.9 or newer).
"""

import hashlib
import hmac
import http.client
import os
import ssl
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
REGION = "auto"            # R2's only region name for SigV4.
SERVICE = "s3"
USER_AGENT = "angelo-publish-installers/1"
UNSIGNED_HEADERS = ("user-agent",)
BLOCK = 1 << 20            # Upload and download block size.
# macOS ships its trust store as this file; Python builds without their own CA bundle can use it.
SYSTEM_CA_FILE = "/etc/ssl/cert.pem"


class R2Error(Exception):
    """A request that failed; ``status`` is the HTTP status, or None when no answer came."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Credentials:
    """An R2 API token's S3 key pair. ``repr`` never shows either value."""

    __slots__ = ("access_key_id", "secret_access_key")

    def __init__(self, access_key_id, secret_access_key):
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key

    def __repr__(self):
        return "Credentials(<not shown>)"


def tls_context():
    """The default verifying context; a Python with no CA certificates of its own falls back to macOS's file."""
    context = ssl.create_default_context()
    if not context.get_ca_certs() and not os.environ.get("SSL_CERT_FILE") and os.path.isfile(SYSTEM_CA_FILE):
        context.load_verify_locations(SYSTEM_CA_FILE)
    return context


def canonical_path(path):
    """S3's canonical URI: every byte but unreserved characters and ``/`` percent-encoded, once."""
    return quote(path, safe="/-_.~")


def _hmac(key, text):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret, date, region=REGION, service=SERVICE):
    key = _hmac(("AWS4" + secret).encode("utf-8"), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def sign(method, path, headers, payload_sha256, credentials, now=None, region=REGION, service=SERVICE):
    """``headers`` (which must hold ``Host``) plus ``x-amz-date``, ``x-amz-content-sha256`` and the SigV4
    ``Authorization`` header. Every header given is signed except ``User-Agent``; ``path`` has no query."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    signed = dict(headers, **{"x-amz-date": stamp, "x-amz-content-sha256": payload_sha256})
    canonical = {name.lower(): " ".join(str(value).split()) for name, value in signed.items()
                 if name.lower() not in UNSIGNED_HEADERS}
    if "host" not in canonical:
        raise ValueError("a signed request needs its Host header")
    names = sorted(canonical)
    lines = "".join("%s:%s\n" % (name, canonical[name]) for name in names)
    request = "\n".join([method, canonical_path(path), "", lines, ";".join(names), payload_sha256])
    scope = "%s/%s/%s/aws4_request" % (stamp[:8], region, service)
    text = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode("utf-8")).hexdigest()])
    signature = hmac.new(signing_key(credentials.secret_access_key, stamp[:8], region, service), text.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    signed["Authorization"] = "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
        credentials.access_key_id, scope, ";".join(names), signature)
    return signed


def describe(error):
    """A transport failure in words that carry no request data: its kind and the system's reason."""
    reason = getattr(error, "reason", None) or getattr(error, "strerror", None)
    return "%s%s" % (type(error).__name__, ": %s" % reason if isinstance(reason, str) and reason else "")


def connection(base_url, timeout, context=None):
    """An ``http.client`` connection to ``base_url``'s host; plain HTTP only for a loopback stand-in (tests)."""
    parts = urlsplit(base_url)
    if parts.scheme == "https":
        return http.client.HTTPSConnection(parts.netloc, timeout=timeout, context=context or tls_context(),
                                           blocksize=BLOCK)
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"):
        return http.client.HTTPConnection(parts.netloc, timeout=timeout, blocksize=BLOCK)
    raise ValueError("only https addresses (or a loopback stand-in) are used: %s" % parts.netloc)


class Bucket:
    """One bucket, addressed path-style at ``endpoint`` (``https://<account id>.r2.cloudflarestorage.com``)."""

    def __init__(self, endpoint, name, credentials, timeout=600, context=None):
        self.endpoint, self.name, self.credentials = endpoint.rstrip("/"), name, credentials
        self.netloc = urlsplit(self.endpoint).netloc
        self.timeout, self.context = timeout, context

    def request(self, method, key, headers=None, body=None, length=0, payload_sha256=EMPTY_SHA256, keep=False):
        """``(status, headers with lower-case names, body)``; the body is read only when ``keep`` (small answers),
        otherwise drained and dropped, so an error body is never kept. Raises R2Error when no answer comes."""
        path = "/%s/%s" % (self.name, key)
        sent = dict(headers or {}, Host=self.netloc)
        if method == "PUT":
            sent["Content-Length"] = str(length)   # Never chunked: S3 needs the length up front.
        sent = sign(method, path, sent, payload_sha256, self.credentials)
        sent["User-Agent"] = USER_AGENT
        link = connection(self.endpoint, self.timeout, self.context)
        try:
            link.request(method, canonical_path(path), body=body, headers=sent)
            response = link.getresponse()
            data = response.read() if keep else b""
            if not keep:
                while response.read(BLOCK):
                    pass
            return response.status, {name.lower(): value for name, value in response.getheaders()}, data
        except (OSError, http.client.HTTPException) as error:
            raise R2Error("%s %s: no answer from R2 (%s)" % (method, key, describe(error))) from None
        finally:
            link.close()

    def head(self, key):
        status, headers, _ = self.request("HEAD", key)
        return status, headers

    def put_file(self, key, path, size, sha256, headers):
        with open(path, "rb") as handle:
            return self.request("PUT", key, headers, body=handle, length=size, payload_sha256=sha256)[0]

    def put_bytes(self, key, data, headers):
        return self.request("PUT", key, headers, body=data, length=len(data),
                            payload_sha256=hashlib.sha256(data).hexdigest())[0]

    def get(self, key):
        """``(status, body)``; the body only for a 200, so an error body never reaches the caller."""
        status, _, data = self.request("GET", key, keep=True)
        return status, data if status == 200 else b""

    def delete(self, key):
        return self.request("DELETE", key)[0]
