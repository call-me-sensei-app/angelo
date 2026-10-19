#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Publishes a release's installers to Cloudflare R2 and proves the public download works.

Runbook: doc/angelo/releasing.rst, "Publishing the installers". The layout (bucket ``angelo-downloads``, served only
at https://downloads.angelo.studio/<version>/) is ``release_downloads.py``'s. From the repository root::

    # Every local check and the plan; no network, no credentials:
    python3 scripts/angelo/publish_installers.py --version 1.0.0 \\
        --dmg dist/macos/Angelo-1.0.0-macos-arm64.dmg --msi dist/windows/Angelo-1.0.0-windows-x64.msi --dry-run

    # Publish (publish_installers.sh fills the credentials from the Keychain):
    scripts/angelo/publish_installers.sh --version 1.0.0 --dmg ... --msi ...

    # Deltas for the macOS app (make_update_delta.py) go up in the same run as their version's installers:
    scripts/angelo/publish_installers.sh --version 1.0.1 --dmg ... --msi ... \\
        --delta dist/deltas/Angelo-1.0.0-to-1.0.1-macos-arm64.delta.tar.xz

    # Verify a published version again, over public HTTPS only (no credentials):
    python3 scripts/angelo/publish_installers.py --version 1.0.0 --dmg ... --msi ... --verify-only

    # The one-time setup check: write, read and delete a probe object, and ask the custom domain for it:
    scripts/angelo/publish_installers.sh --check

Publishing refuses, before anything is sent: a file not named ``Angelo-<version>-<platform>.<dmg|msi>``, a Git LFS
pointer, a disk image that is not signed, stapled and accepted by Gatekeeper as notarized Developer ID (on macOS
only; ``sign_notarize_macos.check_notarized_image``), an MSI without an Authenticode signature (unless
``--allow-unsigned-msi``), an installer that embeds a builder path or credential (``builder_paths.py``: the disk
image is mounted read-only and scanned on macOS; an MSI needs the clean receipt the Windows stage writes for exactly
its bytes, ``<msi>.builder-paths.json``), and any of the version's objects that already exists: a version is published once, all its
installers in one run, and never changed. It uploads the installers, then ``SHA256SUMS`` (written here), each with
``release_downloads.headers`` and ``If-None-Match: *``, then downloads every file from the custom domain and
compares its status, size, headers and SHA-256 with what was sent.

Credentials come only from the environment: ``R2_ACCOUNT_ID``, ``R2_DOWNLOADS_ACCESS_KEY_ID`` and
``R2_DOWNLOADS_SECRET_ACCESS_KEY`` (an Account API token with Object Read & Write on this bucket alone). Nothing
here prints, logs or stores a credential, a signature or a provider's error body. Standard library only.
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import struct
import sys
import tarfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import builder_paths  # noqa: E402
import r2_client  # noqa: E402
import release_checks  # noqa: E402
import release_downloads as layout  # noqa: E402

ENVIRONMENT = ("R2_ACCOUNT_ID", "R2_DOWNLOADS_ACCESS_KEY_ID", "R2_DOWNLOADS_SECRET_ACCESS_KEY")
DELTA_NAME = re.compile(r"[A-Za-z]+-(\d+\.\d+\.\d+)-to-(\d+\.\d+\.\d+)-([a-z0-9-]+)\.delta\.tar\.xz")
DELTA_FORMAT = "angelo-bundle-delta-1"   # update_delta.FORMAT; this tool stays importable without the add-on.
ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")
# One PUT carries at most about 5 GiB on R2; the installers are well under a gigabyte.
MAX_UPLOAD = 5_000_000_000
CHECK_PREFIX = "_check/"
# The website's names for the platforms (angelo-website src/data/release.ts, RELEASE.sha256).
WEBSITE_KEYS = {"macos-arm64": "macos", "windows-x64": "windows"}
# Compound File Binary (MS-CFB), the container of an MSI, and the stream Authenticode adds when it signs one.
CFB_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
CFB_END, CFB_FREE = 0xFFFFFFFE, 0xFFFFFFFF
MSI_SIGNATURE_STREAM = "\x05DigitalSignature"


class PublishError(Exception):
    pass


class Target:
    """One target: in the production configuration the bucket's S3 endpoint and the custom domain. Tests point
    both at a loopback stand-in; the command line never changes them."""

    def __init__(self, endpoint, public_base, bucket=layout.BUCKET, context=None):
        self.endpoint, self.public_base, self.bucket, self.context = endpoint, public_base.rstrip("/"), bucket, context

    @classmethod
    def production(cls, account_id):
        return cls("https://%s.r2.cloudflarestorage.com" % account_id, layout.BASE_URL)


# -- local checks ----------------------------------------------------------------------------------------------

class Item:
    """A file to publish: its object key, local path, size, SHA-256 and headers."""

    def __init__(self, version, name, path, size, sha256, platform=None):
        self.version, self.name, self.path, self.size, self.sha256, self.platform = (
            version, name, path, size, sha256, platform)
        self.key = layout.object_key(version, name)
        self.url = layout.url(version, name)
        self.headers = layout.headers(name)
        self.source = None   # A delta's older version; None for an installer and SHA256SUMS.


def sha256_of(path):
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(r2_client.BLOCK), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def msi_entries(path):
    """The names of every stream and storage in an MSI's directory (MS-CFB section 2.6)."""
    with open(path, "rb") as handle:
        header = handle.read(512)
        if len(header) < 512 or header[:8] != CFB_MAGIC:
            raise PublishError("%s is not a Windows Installer package (no Compound File header)" % path.name)
        shift = struct.unpack_from("<H", header, 0x1E)[0]
        if shift not in (9, 12):
            raise PublishError("%s has an unknown sector size" % path.name)
        size = 1 << shift
        fat_count, first_directory = struct.unpack_from("<II", header, 0x2C)
        first_difat, difat_count = struct.unpack_from("<II", header, 0x44)

        def sector(number):
            handle.seek((number + 1) * size)
            data = handle.read(size)
            if len(data) != size:
                raise PublishError("%s is truncated" % path.name)
            return data

        fat_sectors = [n for n in struct.unpack_from("<109I", header, 0x4C) if n not in (CFB_END, CFB_FREE)]
        next_difat = first_difat
        for _ in range(difat_count):
            entries = struct.unpack("<%dI" % (size // 4), sector(next_difat))
            fat_sectors += [n for n in entries[:-1] if n not in (CFB_END, CFB_FREE)]
            next_difat = entries[-1]
        fat = []
        for number in fat_sectors[:fat_count]:
            fat += struct.unpack("<%dI" % (size // 4), sector(number))
        names, number, seen = [], first_directory, set()
        while number not in (CFB_END, CFB_FREE):
            if number in seen or number >= len(fat):
                raise PublishError("%s has a broken directory chain" % path.name)
            seen.add(number)
            data = sector(number)
            for offset in range(0, size, 128):
                length, kind = struct.unpack_from("<HB", data, offset + 64)
                if kind in (1, 2) and 2 <= length <= 64:  # A storage or a stream.
                    names.append(data[offset:offset + length - 2].decode("utf-16-le", "replace"))
            number = fat[number]
        return names


def check_disk_image(path, runner, platform):
    """Refuse a disk image that is not signed, stapled and accepted by Gatekeeper as notarized; macOS only."""
    if platform != "darwin":
        raise PublishError("the disk image's signature and stapled ticket can only be checked on macOS")
    import sign_notarize_macos  # Imported here: it reads the identity and loads macOS-only helpers.
    runner = runner or sign_notarize_macos.Runner()
    try:
        runner(["codesign", "--verify", "--strict", "--verbose=2", str(path)])
        return sign_notarize_macos.check_notarized_image(path, runner)
    except sign_notarize_macos.ReleaseError as error:
        raise PublishError("%s is not a signed, stapled and notarized disk image: %s" % (path.name, error)) from None


def check_installer(version, platform, path, runner=None, host_platform=sys.platform, allow_unsigned_msi=False,
                    signatures=True):
    """The checked ``Item`` for one installer, or PublishError naming the first problem. ``signatures`` False
    (``--verify-only``, which compares published files with local ones) skips the signature checks."""
    path = Path(path)
    name = layout.installer_name(version, platform)
    if path.name != name:
        raise PublishError("%s must be named %s to be published as %s's %s installer" % (path, name, version,
                                                                                         platform))
    if not path.is_file():
        raise PublishError("%s is not a file" % path)
    with open(path, "rb") as handle:
        head = handle.read(1024)
    if release_checks.is_lfs_pointer(head):
        raise PublishError("%s is a Git LFS pointer, not the installer" % path)
    if signatures and platform == "macos-arm64":
        check_disk_image(path, runner, host_platform)
        try:
            report = builder_paths.scan_disk_image(path, runner)
        except builder_paths.ScanError as error:
            raise PublishError("%s: %s" % (path.name, error)) from None
        if report["hits"]:
            raise PublishError("%s embeds builder paths or credentials:\n%s" % (
                path.name, "\n".join(builder_paths.summary(report)[1:])))
    elif signatures:
        if MSI_SIGNATURE_STREAM not in msi_entries(path):
            if not allow_unsigned_msi:
                raise PublishError("%s has no Authenticode signature: sign it (sign_windows.ps1) or, knowingly, pass "
                                   "--allow-unsigned-msi" % path.name)
            print("warning: publishing %s without an Authenticode signature" % path.name, file=sys.stderr)
        problem = builder_paths.receipt_problem(path)
        if problem:
            raise PublishError(problem)
    digest, size = sha256_of(path)
    if not 0 < size <= MAX_UPLOAD:
        raise PublishError("%s is %d bytes; one upload carries 1 byte to %d" % (path.name, size, MAX_UPLOAD))
    return Item(version, name, path, size, digest, platform)


def check_delta(version, path, platforms):
    """The checked ``Item`` for one delta (``make_update_delta.py``): named for this version, for a platform whose
    installer is in the same run, and a delta whose description goes from its named version to this one."""
    path = Path(path)
    match = DELTA_NAME.fullmatch(path.name)
    if match is None or match.group(2) != version:
        raise PublishError("%s is not named Angelo-<older version>-to-%s-<platform>.delta.tar.xz" % (path.name, version))
    source, platform = match.group(1), match.group(3)
    if path.name != layout.delta_name(source, version, platform):
        raise PublishError("%s must be named %s" % (path.name, layout.delta_name(source, version, platform)))
    if platform not in platforms:
        raise PublishError("%s's installer for %s must be published in the same run" % (path.name, platform))
    try:
        with tarfile.open(path, mode="r|xz") as archive:
            first = archive.next()
            description = json.loads(archive.extractfile(first).read()) if first and first.name == "delta.json" else {}
    except (OSError, tarfile.TarError, ValueError, EOFError, AttributeError):
        description = {}
    if (description.get("format"), description.get("from"), description.get("to")) != (DELTA_FORMAT, source, version):
        raise PublishError("%s is not a %s delta from %s to %s" % (path.name, DELTA_FORMAT, source, version))
    digest, size = sha256_of(path)
    if not 0 < size <= MAX_UPLOAD:
        raise PublishError("%s is %d bytes; one upload carries 1 byte to %d" % (path.name, size, MAX_UPLOAD))
    item = Item(version, path.name, path, size, digest, platform)
    item.source = source
    return item


def plan(version, dmg=None, msi=None, runner=None, host_platform=sys.platform, allow_unsigned_msi=False,
         signatures=True, deltas=()):
    """The checked installers (then deltas) and ``SHA256SUMS`` (bytes) for a version."""
    layout.check_version(version)
    given = [(platform, path) for platform, path in (("macos-arm64", dmg), ("windows-x64", msi)) if path]
    if not given:
        raise PublishError("give the disk image (--dmg), the MSI (--msi) or both")
    items = [check_installer(version, platform, path, runner, host_platform, allow_unsigned_msi, signatures)
             for platform, path in given]
    items += [check_delta(version, path, {platform for platform, _ in given}) for path in deltas]
    sums = layout.sums_text({item.name: item.sha256 for item in items}).encode("ascii")
    return items, sums


# -- the bucket --------------------------------------------------------------------------------------------------

def credentials_from(environ):
    """``(account id, Credentials)`` from the environment; names a missing variable, never shows a value."""
    missing = [name for name in ENVIRONMENT if not environ.get(name, "").strip()]
    if missing:
        raise PublishError("%s not set: run through scripts/angelo/publish_installers.sh, which reads them from the "
                           "Keychain, or set them in the environment" % ", ".join(missing))
    account = environ["R2_ACCOUNT_ID"].strip()
    if not ACCOUNT_ID.fullmatch(account):
        raise PublishError("R2_ACCOUNT_ID is not a Cloudflare account id (32 lower-case hex characters)")
    values = [environ[name].strip() for name in ENVIRONMENT[1:]]
    if any(re.search(r"\s", value) for value in values):
        raise PublishError("an R2 credential contains white space; store it again")
    return account, r2_client.Credentials(*values)


def status_error(action, key, status):
    hint = {401: "the access key is not recognized",
            403: "the token is refused: it needs Object Read & Write on %s" % layout.BUCKET,
            404: "the bucket %s does not exist in this account" % layout.BUCKET}.get(status, "")
    detail = "; " + hint if hint else ""
    return PublishError("%s %s: HTTP %s%s (provider details not shown)" % (action, key, status, detail))


def refuse_existing(bucket, keys):
    """Every key must be absent: a published version is never overwritten."""
    found = []
    for key in keys:
        status, _ = bucket.head(key)
        if status == 200:
            found.append(key)
        elif status != 404:
            raise status_error("HEAD", key, status)
    if found:
        raise PublishError("already published, and never overwritten: %s. A version is published once, with all its "
                           "installers; publish a new version instead" % ", ".join(found))


def upload(bucket, item, data=None):
    headers = dict(item.headers, **{"If-None-Match": "*"})
    print("uploading %s (%.1f MB)" % (item.key, item.size / 1e6), file=sys.stderr)
    status = (bucket.put_bytes(item.key, data, headers) if data is not None
              else bucket.put_file(item.key, item.path, item.size, item.sha256, headers))
    if status == 412:
        raise PublishError("%s appeared while publishing (HTTP 412): it was not overwritten" % item.key)
    if status != 200:
        raise status_error("PUT", item.key, status)


# -- the public download -------------------------------------------------------------------------------------------

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


def verify_public(target, item):
    """The custom domain must serve exactly what was published: status, size, headers and SHA-256."""
    where = "%s/%s" % (target.public_base, item.key)
    status, headers = public_request(target, "HEAD", item.key)
    if status != 200:
        raise PublishError("HEAD %s answered %s, not 200 (is the custom domain connected?)" % (where, status))
    problems = []
    if headers.get("content-length") != str(item.size):
        problems.append("Content-Length %s, expected %d" % (headers.get("content-length"), item.size))
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
    elif sum(received) != item.size or digest.hexdigest() != item.sha256:
        problems.append("the download (%d bytes, SHA-256 %s) is not the file published" % (sum(received),
                                                                                           digest.hexdigest()))
    if problems:
        raise PublishError("%s: %s" % (where, "; ".join(problems)))
    return {"url": where, "size": item.size, "sha256": item.sha256}


def sums_item(version, sums):
    return Item(version, layout.SUMS, None, len(sums), hashlib.sha256(sums).hexdigest())


def summary(version, items, verified, published):
    """What the website (RELEASE.sha256) and the update feed (sign_update_feed.py build) take from this run."""
    return {
        "version": version, "published": published, "verified": verified,
        "sums": layout.url(version, layout.SUMS),
        "website_sha256": {WEBSITE_KEYS[item.platform]: item.sha256 for item in items if item.source is None},
        "feed_downloads": ["%s=%s" % (item.platform, item.path) for item in items if item.source is None],
        "feed_deltas": ["%s=%s=%s" % (item.platform, item.source, item.path) for item in items if item.source is not None],
    }


# -- commands ----------------------------------------------------------------------------------------------------

def publish(arguments, target=None, runner=None, environ=os.environ, host_platform=sys.platform):
    items, sums = plan(arguments.version, arguments.dmg, arguments.msi, runner, host_platform,
                       arguments.allow_unsigned_msi, signatures=not arguments.verify_only,
                       deltas=getattr(arguments, "delta", None) or ())
    sums_entry = sums_item(arguments.version, sums)
    if arguments.dry_run:
        return {"dry_run": True, "bucket": layout.BUCKET,
                "objects": [{"key": item.key, "url": item.url, "size": item.size, "sha256": item.sha256,
                             "headers": item.headers} for item in items + [sums_entry]],
                "sha256sums": sums.decode("ascii")}
    if not arguments.verify_only:
        account, credentials = credentials_from(environ)
        target = target or Target.production(account)
        bucket = r2_client.Bucket(target.endpoint, target.bucket, credentials, context=target.context)
        try:
            refuse_existing(bucket, [item.key for item in items] + [sums_entry.key])
            for item in items:            # SHA256SUMS last: once it exists, the version is complete.
                upload(bucket, item)
            upload(bucket, sums_entry, sums)
        except r2_client.R2Error as error:
            raise PublishError(str(error)) from None
    target = target or Target(None, layout.BASE_URL)
    print("verifying %s/%s/ from the public address" % (target.public_base, arguments.version), file=sys.stderr)
    verified = [verify_public(target, item) for item in items + [sums_entry]]
    return summary(arguments.version, items, verified, published=not arguments.verify_only)


def check(target=None, environ=os.environ, report=print):
    """The setup check; True when every step passed. The probe is a random ``_check/`` key holding a random line,
    stored ``no-store`` so no cache keeps it, and deleted again."""
    account, credentials = credentials_from(environ)
    target = target or Target.production(account)
    bucket = r2_client.Bucket(target.endpoint, target.bucket, credentials, context=target.context)
    key = "%s%s.txt" % (CHECK_PREFIX, uuid.uuid4().hex)
    body = ("angelo downloads check %s\n" % uuid.uuid4().hex).encode("ascii")
    passed = True

    def step(label, run):
        nonlocal passed
        try:
            run()
            report("ok    %s" % label)
        except (PublishError, r2_client.R2Error) as error:
            passed = False
            report("FAIL  %s: %s" % (label, error))

    def write():
        status = bucket.put_bytes(key, body, {"Content-Type": "text/plain", "Cache-Control": "no-store",
                                              "If-None-Match": "*"})
        if status != 200:
            raise status_error("PUT", key, status)

    def read():
        status, data = bucket.get(key)
        if status != 200 or data != body:
            raise status_error("GET", key, status) if status != 200 else PublishError("the content differs")

    def domain():
        received = []
        status, _ = public_request(target, "GET", key, sink=received.append)
        if status != 200 or b"".join(received) != body:
            raise PublishError("%s/%s answered %s%s: connect %s to %s as a custom domain" % (
                target.public_base, key, status, "" if status != 200 else " with other content", layout.HOST,
                layout.BUCKET))

    def delete():
        status = bucket.delete(key)
        if status not in (200, 204):
            raise status_error("DELETE", key, status)
        status, _ = bucket.head(key)
        if status != 404:
            raise status_error("HEAD after delete", key, status)

    step("write a probe object (%s) to %s" % (key, layout.BUCKET), write)
    if passed:
        step("read it back through the S3 API", read)
        step("%s serves it over HTTPS" % target.public_base, domain)
        step("delete it", delete)
    report("\n%s: %s" % (layout.BUCKET, "ready to publish." if passed else "not ready."))
    return passed


def main(argv=None, target=None, runner=None, environ=None, host_platform=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help="MAJOR.MINOR.PATCH, the version the installers carry")
    parser.add_argument("--dmg", help="the stapled disk image, Angelo-<version>-macos-arm64.dmg")
    parser.add_argument("--msi", help="the signed Windows installer, Angelo-<version>-windows-x64.msi")
    parser.add_argument("--delta", action="append", default=[],
                        help="an update delta for this version (make_update_delta.py), published with its installer")
    parser.add_argument("--dry-run", action="store_true", help="check and show the plan; no network, no credentials")
    parser.add_argument("--verify-only", action="store_true",
                        help="upload nothing; check the published files over public HTTPS against these files")
    parser.add_argument("--allow-unsigned-msi", action="store_true",
                        help="publish an MSI without an Authenticode signature (a deliberate exception)")
    parser.add_argument("--check", action="store_true",
                        help="setup check: write, read and delete a _check/ probe and fetch it from the custom domain")
    arguments = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        if arguments.check:
            if arguments.version or arguments.dmg or arguments.msi or arguments.dry_run or arguments.verify_only:
                parser.error("--check takes no other option")
            return 0 if check(target, environ) else 1
        if not arguments.version:
            parser.error("--version is required")
        if arguments.dry_run and arguments.verify_only:
            parser.error("--dry-run and --verify-only exclude each other")
        result = publish(arguments, target, runner, environ, host_platform or sys.platform)
    except (PublishError, layout.LayoutError, release_checks.CheckError, OSError) as error:
        print("publish_installers: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
