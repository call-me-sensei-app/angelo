#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A version's published files, their checksum files, the rule for which checksum covers which file, and the
verification over public HTTPS.

Used by ``publish_installers.py``; the layout is ``release_downloads.py``'s. A version's first publish writes
``SHA256SUMS`` for its files; every installer gets ``<installer>.sha256``, listing it and the deltas published with
it (since 1.0.6; before, only a platform added later had one). ``coverage`` is the one rule both views apply: the bucket's (``BucketView``, through
the authenticated S3 API, before anything is written) and the custom domain's (``verify_version``, which needs no
credentials). Standard library only.
"""

import hashlib
import http.client
import sys
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r2_client  # noqa: E402
import release_downloads as layout  # noqa: E402

# A checksum file holds one short line per file; anything larger is not one this tool wrote.
MAX_CHECKSUM_FILE = 1_000_000


class PublishError(Exception):
    pass


class Item:
    """A file to publish or to check: its object key, local path, size, SHA-256 and headers. ``path`` is None for a
    file written from memory (a checksum file) or one only checked on the public domain; ``size`` is None when it
    is only known from the published checksum."""

    def __init__(self, version, name, path, size, sha256, platform=None):
        self.version, self.name, self.path, self.size, self.sha256, self.platform = (
            version, name, path, size, sha256, platform)
        self.key = layout.object_key(version, name)
        self.url = layout.url(version, name)
        self.headers = layout.headers(name)
        self.source = None   # A delta's older version; None for an installer and a checksum file.


def sums_item(version, sums):
    return Item(version, layout.SUMS, None, len(sums), hashlib.sha256(sums).hexdigest())


def checksum_item(installer, deltas=()):
    """``(Item, bytes)``: ``<installer>.sha256``, a ``shasum -a 256`` line for the installer and for each delta
    published with it, so ``shasum -a 256 -c`` checks a download."""
    entries = {item.name: item.sha256 for item in [installer, *deltas]}
    data = layout.sums_text(entries).encode("ascii")
    checksum = Item(installer.version, layout.checksum_name(installer.name), None, len(data),
                    hashlib.sha256(data).hexdigest())
    checksum.entries = entries   # What it lists, for verifying those files when the domain will not serve it.
    return checksum, data


def served(target, version, name):
    """The address ``target`` serves a version's file at (the custom domain in production)."""
    return "%s/%s" % (target.public_base, layout.object_key(version, name))


def public_request(target, method, key, sink=None):
    """``(status, headers)`` of an unauthenticated request to the custom domain; a GET's body goes to ``sink``."""
    link = r2_client.connection(target.public_base, timeout=600, context=target.context)
    prefix = urlsplit(target.public_base).path.rstrip("/")   # Empty for the custom domain.
    try:
        link.request(method, r2_client.canonical_path("%s/%s" % (prefix, key)),
                     headers={"User-Agent": r2_client.USER_AGENT, "Accept-Encoding": "identity"})
        response = link.getresponse()
        while True:
            block = response.read(r2_client.BLOCK)
            if not block:
                break
            if sink is not None and response.status == 200:
                sink(block)
        return response.status, {name.lower(): value for name, value in response.getheaders()}
    except (OSError, http.client.HTTPException) as error:
        reason = r2_client.describe(error)
        raise PublishError("%s %s/%s: no answer (%s)" % (method, target.public_base, key, reason)) from None
    finally:
        link.close()


def _same(header, expected):
    return " ".join((header or "").split()).lower() == expected.lower()


def cached_404(where, what):
    return PublishError("%s answered 404, but it is %s: a 404 the custom domain's cache kept? Purge that URL from "
                        "Cloudflare's cache (releasing.rst, \"Publishing the installers\") and run --verify-only"
                        % (where, what))


def verify_public(target, item, ours=False):
    """The custom domain must serve exactly ``item``: status, size, headers and SHA-256. With ``item.size`` None
    (a file known only by its published checksum), the size is the one the HEAD answer gives. ``ours``: this run
    wrote it, so a 404 is named as a cached one."""
    where = "%s/%s" % (target.public_base, item.key)
    status, headers = public_request(target, "HEAD", item.key)
    if status == 404 and ours:
        raise cached_404("HEAD " + where, "a file this run wrote")
    if status != 200:
        raise PublishError("HEAD %s answered %s, not 200 (is the custom domain connected?)" % (where, status))
    problems, size = [], item.size
    if size is None:
        length = headers.get("content-length") or ""
        size = int(length) if length.isdigit() and int(length) > 0 else None
        if size is None:
            problems.append("Content-Length %r is not a size" % length)
    elif headers.get("content-length") != str(size):
        problems.append("Content-Length %s, expected %d" % (headers.get("content-length"), size))
    media_type = (headers.get("content-type") or "").split(";")[0].strip()
    for field, actual, expected in (("Content-Type", media_type, item.headers["Content-Type"]),
                                    ("Cache-Control", headers.get("cache-control"), item.headers["Cache-Control"]),
                                    ("Content-Disposition", headers.get("content-disposition"),
                                     item.headers["Content-Disposition"])):
        if not _same(actual, expected):
            problems.append("%s %r, expected %r" % (field, actual, expected))
    digest, received = hashlib.sha256(), []

    def take(block):
        digest.update(block)
        received.append(len(block))

    status, _ = public_request(target, "GET", item.key, sink=take)
    if status != 200:
        problems.append("GET answered %s" % status)
    elif sum(received) != size or digest.hexdigest() != item.sha256:
        problems.append("the download (%d bytes, SHA-256 %s) is not the file published" % (sum(received),
                                                                                           digest.hexdigest()))
    if problems:
        raise PublishError("%s: %s" % (where, "; ".join(problems)))
    return {"url": where, "size": size, "sha256": item.sha256}


# -- the coverage rule -----------------------------------------------------------------------------------------

def coverage(version, sums, owns, exists=None):
    """The one coverage rule, applied to the bucket's view before any write and to the public view when verifying.

    ``sums`` is ``SHA256SUMS``'s entries (None when absent), ``owns`` ``{installer: entries}`` of every
    ``<installer>.sha256`` that exists, ``exists(name)`` whether an object is in the bucket; the public view passes
    no ``exists`` (it never asks for a file no checksum lists) and finds a missing listed file by downloading it.
    An ``<installer>.sha256`` lists its installer and only deltas of that platform to this version; every file is
    covered at most once, except that ``SHA256SUMS`` and an installer's own file may list it with the same checksum; with ``exists``, every listed file exists and every installer that exists is covered.
    ``(claims, problems, leftovers)``: ``{file: sha256}`` of every file covered, each problem verification would
    reject, and the names a publish cut off part way left behind (to delete before publishing again)."""
    platforms = {layout.installer_name(version, platform): platform for platform in layout.INSTALLERS}
    sources = [(layout.SUMS, sums or {}, None)] + [(layout.checksum_name(name), entries, name)
                                                   for name, entries in sorted(owns.items())]
    claims, source_of, problems, leftovers = {}, {}, [], []
    for source, entries, installer in sources:
        if installer is not None:
            strays = sorted(name for name in entries if name != installer
                            and not layout.is_delta_of(name, version, platforms[installer]))
            if installer not in entries or strays:
                problems.append("%s must list %s and only its platform's deltas to %s%s" % (
                    source, installer, version, "; it lists %s" % ", ".join(strays) if strays else ""))
                continue
        for name, digest in sorted(entries.items()):
            if name not in source_of:
                source_of[name], claims[name] = source, digest
            elif source_of[name] != layout.SUMS or claims[name] != digest:
                # A first publish lists its files in SHA256SUMS and in each installer's own .sha256: the same
                # checksum twice is that layout; anything else is a file published twice or a damaged list.
                problems.append("%s is covered twice, by %s and by %s%s" % (
                    name, source_of[name], source, "" if claims[name] == digest else ", with different checksums"))
    if exists is None:
        return claims, problems, leftovers
    missing = sorted(name for name in (sums or {}) if not exists(name))
    if missing:
        problems.append("%s lists %s, which is not published" % (layout.SUMS, ", ".join(missing)))
    for source, entries, installer in sources[1:]:
        missing = sorted(name for name in entries if not exists(name))
        if missing:
            problems.append("%s lists %s, which is not published: a publish stopped part way" % (
                source, ", ".join(missing)))
            leftovers += [source] + sorted(name for name in entries if name not in missing)
    for name in sorted(platforms):
        if name not in claims and exists(name):
            problems.append("%s is published without a checksum file: a publish stopped part way" % name)
            leftovers.append(name)
    return claims, problems, leftovers


class BucketView:
    """A version as the bucket holds it, through the authenticated S3 API (no cache in between): its checksum
    files read, and each object's existence asked once and remembered, for ``coverage``."""

    def __init__(self, bucket, version):
        self.bucket, self.version, self.known = bucket, version, {}

    def key(self, name):
        return layout.object_key(self.version, name)

    def exists(self, name):
        if name not in self.known:
            status, _ = self.bucket.head(self.key(name))
            if status not in (200, 404):
                raise status_error("HEAD", self.key(name), status)
            self.known[name] = status == 200
        return self.known[name]

    def checksums(self, name):
        """The entries of the checksum file ``name``; None when it does not exist."""
        status, data = self.bucket.get(self.key(name))
        if status not in (200, 404):
            raise status_error("GET", self.key(name), status)
        self.known[name] = status == 200
        if status == 404:
            return None
        try:
            return layout.parse_sums(data.decode("ascii"))
        except (UnicodeDecodeError, layout.LayoutError) as error:
            raise PublishError("%s: %s" % (self.key(name), error)) from None

    def owns(self):
        """``{installer: entries}`` of every platform's ``<installer>.sha256`` in the bucket."""
        found = {}
        for platform in layout.INSTALLERS:
            installer = layout.installer_name(self.version, platform)
            entries = self.checksums(layout.checksum_name(installer))
            if entries is not None:
                found[installer] = entries
        return found

    def absent(self):
        return {name for name, present in self.known.items() if not present}


def status_error(action, key, status):
    hint = {401: "the access key is not recognized",
            403: "the token is refused: it needs Object Read & Write on %s" % layout.BUCKET,
            404: "the bucket %s does not exist in this account" % layout.BUCKET}.get(status, "")
    detail = "; " + hint if hint else ""
    return PublishError("%s %s: HTTP %s%s (provider details not shown)" % (action, key, status, detail))


def refuse_rejected(view, version, sums, outcome):
    """PublishError naming every problem, and every object a cut-off publish left, when the bucket's view of the
    version is a state its verification would reject (``coverage``)."""
    _, problems, leftovers = coverage(version, sums, view.owns(), view.exists)
    if sums is None:
        problems.insert(0, "%s is not published" % layout.SUMS)
    if problems:
        cut_off = ("\nA publish cut off part way left %s: delete exactly these, then publish again"
                   % ", ".join(view.key(name) for name in leftovers)) if leftovers else ""
        raise PublishError("%s is not in a state its verification accepts, %s:\n  %s%s" % (
            version, outcome, "\n  ".join(problems), cut_off))


def check_written(bucket, version):
    """After a run's writes, the version as the bucket now holds it (a fresh view, so another publish that ran at
    the same time is seen) must be a state its verification accepts; the names it shows absent."""
    view = BucketView(bucket, version)
    refuse_rejected(view, version, view.checksums(layout.SUMS),
                    "after this run's writes (did another publish of it run at the same time?)")
    return view.absent()


def stopped_writing(bucket, version, error, sent):
    """The PublishError for a run whose write ``error`` stopped it (a 412 when another publish won a key, or any
    other failure): the error, every key this run wrote before it (``sent``), and, best effort, every problem and
    leftover the bucket now shows. Nothing raised while looking replaces the original error."""
    message = str(error)
    if sent:
        message += "\nThis run wrote %s before it stopped" % ", ".join(sent)
    try:
        check_written(bucket, version)
    except (PublishError, r2_client.R2Error) as found:
        message += "\n%s" % found
    except Exception as unexpected:   # Best effort only: never hide the write's own error.
        message += "\n(the bucket could not be read again: %s)" % type(unexpected).__name__
    return PublishError(message)


# -- the public view -------------------------------------------------------------------------------------------

def read_checksums(target, version, name, expected=None):
    """``({file: sha256}, record)`` from the published checksum file ``name`` (``SHA256SUMS`` or an
    ``<installer>.sha256``), its headers checked, and its bytes too when ``expected`` (the Item this run wrote) is
    given; ``(None, None)`` when it is not published and nothing was expected."""
    key, received = layout.object_key(version, name), []

    def take(block):
        received.append(block)
        if sum(map(len, received)) > MAX_CHECKSUM_FILE:
            raise PublishError("%s is larger than any checksum file" % served(target, version, name))

    status, _ = public_request(target, "GET", key, sink=take)
    if status == 404 and expected is None:
        return None, None
    if status == 404:
        raise cached_404("GET " + served(target, version, name), "the checksum file this run wrote")
    if status != 200:
        raise PublishError("GET %s/%s answered %s, not 200 (is the custom domain connected?)" % (
            target.public_base, key, status))
    data = b"".join(received)
    record = verify_public(target, expected or Item(version, name, None, len(data), hashlib.sha256(data).hexdigest()))
    try:
        return layout.parse_sums(data.decode("ascii")), record
    except (UnicodeDecodeError, layout.LayoutError) as error:
        raise PublishError("%s: %s" % (served(target, version, name), error)) from None


def verify_version(target, version, local=(), written=(), absent=()):
    """Check ``version`` on the public domain; ``(verified, installers)``: a record per file checked and
    ``{platform: sha256}`` of every installer it has. ``SHA256SUMS`` must be published; each platform's
    ``<installer>.sha256`` is read when it exists, and the domain is asked for an installer or a delta only when a
    checksum file lists it. Every listed file and every ``local`` Item must be covered by exactly one checksum
    file (``coverage``) and be served as it says; a local file must also be the published one. ``written`` holds
    the checksum files' Items this run wrote, served byte for byte; ``absent`` the names the bucket showed absent,
    never asked of the domain, whose cache would keep the 404 for a file published later. A 404 for a file this
    run wrote is named as a cached one. Every problem is reported together, as one PublishError."""
    written = {item.name: item for item in written}
    local = {item.name: item for item in local}
    sums, record = read_checksums(target, version, layout.SUMS, written.get(layout.SUMS))
    if sums is None:
        raise PublishError("%s is not published: %s answered 404" % (version, served(target, version, layout.SUMS)))
    verified, problems, owns, installers = [record], [], {}, {}
    platforms = {layout.installer_name(version, platform): platform for platform in layout.INSTALLERS}
    for installer in sorted(platforms):
        own = layout.checksum_name(installer)
        if own in absent and own not in written:
            continue
        try:
            entries, record = read_checksums(target, version, own, written.get(own))
        except PublishError as error:
            problems.append(str(error))   # One this run wrote: its files are still checked against what it sent.
            entries, record = getattr(written.get(own), "entries", None), None
        if entries is not None:
            owns[installer] = entries
            verified += [record] if record else []
    claims, found, _ = coverage(version, sums, owns)
    problems += found
    for name in sorted(set(claims) | set(local)):
        where = served(target, version, name)
        try:
            if name not in claims:
                raise PublishError("%s is not published (no checksum file covers it)" % where)
            mine = local.get(name)
            if mine is not None and mine.sha256 != claims[name]:
                raise PublishError("%s: the published checksum %s is not the local file's (%s)" % (
                    where, claims[name], mine.sha256))
            # A publishing run (it wrote checksum files) wrote every local file too.
            verified.append(verify_public(target, mine or Item(version, name, None, None, claims[name]),
                                          ours=mine is not None and bool(written)))
            if name in platforms:
                installers[platforms[name]] = claims[name]
        except (PublishError, layout.LayoutError) as error:
            problems.append(str(error))
    if problems:
        raise PublishError("\n  ".join(["%s is not as published:" % version] + problems))
    return verified, installers

