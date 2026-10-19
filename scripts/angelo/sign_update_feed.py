#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Builds, signs and verifies Angelo's update feed (``stable.json``).

The feed format, the release procedure and the key ceremony are in
doc/angelo/updates_and_reports.rst. The production key is an Ed25519 key in
Google Cloud KMS that never leaves it; a local key file (PKCS#8 PEM, as
``openssl genpkey -algorithm ed25519`` writes) is for tests and rehearsals only.
Every signature is verified against the public key before the file is written,
and ``verify`` checks a feed exactly as the app does (``update_feed.open_feed``).

Commands, from the repository root::

    # The payload: title, summary and highlights come from CHANGELOG.md's entry for the version; each
    # platform=FILE download is the installer published at https://downloads.angelo.studio/<version>/<file name>
    # (release_downloads.py), its size and SHA-256 measured from the file.
    python3 scripts/angelo/sign_update_feed.py build --version 1.0.1 --sequence 2 \\
        --download macos-arm64=dist/Angelo-1.0.1-macos-arm64.dmg \\
        --download windows-x64=dist/Angelo-1.0.1-windows-x64.msi \\
        --delta macos-arm64=1.0.0=dist/deltas/Angelo-1.0.0-to-1.0.1-macos-arm64.delta.tar.xz \\
        --previous published/stable.json --out payload.json

    # Sign it (production: Cloud KMS through gcloud; rehearsal: --key-file). The key version's full
    # resource name, projects/<project>/locations/<location>/keyRings/<keyring>/cryptoKeys/<key>/
    # cryptoKeyVersions/<n>, is the release's own; nothing here defaults it.
    python3 scripts/angelo/sign_update_feed.py sign payload.json --key-id 2026-10 \\
        --kms "$ANGELO_UPDATE_FEED_KMS_KEY" --out stable.json

    python3 scripts/angelo/sign_update_feed.py verify stable.json            # against update_keys.json
    python3 scripts/angelo/sign_update_feed.py public-key --kms <version>     # the update_keys.json entry
    python3 scripts/angelo/sign_update_feed.py add-key --key-id 2026-10 --pem update-feed-public.pem
                                                                             # trust it in update_keys.json
    python3 scripts/angelo/sign_update_feed.py keygen --out rehearsal.pem    # a local key, never for production

Standard library only, plus ``gcloud`` on PATH for ``--kms``.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ed25519, update_delta, update_feed  # noqa: E402  (standard library only)
import changelog  # noqa: E402
import release_downloads  # noqa: E402

NOTES_URL = "https://angelo.studio/releases#{version}"
PKCS8_PREFIX = bytes.fromhex("302e020100300506032b657004220420")    # Ed25519 private key, RFC 8410.
SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")              # Ed25519 public key, RFC 8410.
KMS_VERSION = re.compile(r"projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)/keyRings/(?P<keyring>[^/]+)/"
                         r"cryptoKeys/(?P<key>[^/]+)/cryptoKeyVersions/(?P<version>\d+)")


class FeedToolError(Exception):
    pass


# -- keys ------------------------------------------------------------------------------------------------------

def _pem_body(text, label):
    match = re.search(r"-----BEGIN %s-----(.*?)-----END %s-----" % (label, label), text, re.S)
    if not match:
        raise FeedToolError("not a PEM %s" % label)
    return base64.b64decode("".join(match.group(1).split()))


def read_key_file(path):
    """The 32-byte seed of an Ed25519 PKCS#8 PEM file."""
    der = _pem_body(Path(path).read_text(encoding="ascii"), "PRIVATE KEY")
    if len(der) != len(PKCS8_PREFIX) + 32 or not der.startswith(PKCS8_PREFIX):
        raise FeedToolError("%s is not an Ed25519 private key" % path)
    return der[len(PKCS8_PREFIX):]


def key_file_text(seed):
    body = base64.b64encode(PKCS8_PREFIX + seed).decode("ascii")
    return "-----BEGIN PRIVATE KEY-----\n%s\n-----END PRIVATE KEY-----\n" % body


def public_from_pem(text):
    if "PRIVATE KEY-----" in text:
        raise FeedToolError("that is a private key; give the public key (gcloud kms keys versions get-public-key)")
    der = _pem_body(text, "PUBLIC KEY")
    if len(der) != len(SPKI_PREFIX) + 32 or not der.startswith(SPKI_PREFIX):
        raise FeedToolError("the public key is not Ed25519 (is the KMS key EC_SIGN_ED25519?)")
    return der[len(SPKI_PREFIX):]


def public_pem(public):
    """The SubjectPublicKeyInfo PEM of an Ed25519 public key, as Cloud KMS exports it."""
    body = base64.b64encode(SPKI_PREFIX + public).decode("ascii")
    return "-----BEGIN PUBLIC KEY-----\n%s\n-----END PUBLIC KEY-----\n" % body


class LocalSigner:
    """A key file on this computer: tests and rehearsals only."""

    def __init__(self, path):
        self.seed = read_key_file(path)

    def public_key(self):
        return ed25519.public_key(self.seed)

    def sign(self, message):
        return ed25519.sign(self.seed, message)


class KmsSigner:
    """An EC_SIGN_ED25519 key version in Cloud KMS, used through ``gcloud`` (the private key never leaves KMS)."""

    def __init__(self, name, run=subprocess.run):
        match = KMS_VERSION.fullmatch(name)
        if not match:
            raise FeedToolError("--kms needs a full key version name: projects/P/locations/L/keyRings/R/"
                                "cryptoKeys/K/cryptoKeyVersions/N")
        self.parts, self.run = match.groupdict(), run

    def _flags(self):
        p = self.parts
        return ["--project", p["project"], "--location", p["location"], "--keyring", p["keyring"], "--key", p["key"]]

    def _gcloud(self, arguments):
        result = self.run(["gcloud", "kms"] + arguments, capture_output=True, text=True)
        if result.returncode != 0:
            raise FeedToolError("gcloud failed: %s" % (result.stderr or "").strip()[:500])

    def public_key(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "public.pem"
            self._gcloud(["keys", "versions", "get-public-key", self.parts["version"]] + self._flags()
                         + ["--output-file", str(out)])
            return public_from_pem(out.read_text(encoding="ascii"))

    def sign(self, message):
        with tempfile.TemporaryDirectory() as folder:
            data, signature = Path(folder) / "message", Path(folder) / "signature"
            data.write_bytes(message)
            self._gcloud(["asymmetric-sign", "--version", self.parts["version"]] + self._flags()
                         + ["--input-file", str(data), "--signature-file", str(signature)])
            return signature.read_bytes()


def signer(arguments):
    if bool(arguments.key_file) == bool(arguments.kms):
        raise FeedToolError("give exactly one of --key-file (rehearsal) or --kms (production)")
    return LocalSigner(arguments.key_file) if arguments.key_file else KmsSigner(arguments.kms)


# -- the payload ----------------------------------------------------------------------------------------------

def download(spec, version=None):
    """One ``--download``: ``platform=FILE``, ``platform=URL=FILE`` or ``platform=URL=SHA256=SIZE``.

    ``platform=FILE`` names the installer as published on the download host
    (``release_downloads.installer_url``): the file must carry exactly its published name for ``version``. With a
    file, the size and SHA-256 are measured from it. A URL on the download host must be that same address, so a
    feed never points into another version's folder.
    """
    parts = spec.split("=")
    if len(parts) == 2 and version is not None:
        platform, path = parts
        try:
            name = release_downloads.installer_name(version, platform)
        except release_downloads.LayoutError as error:
            raise FeedToolError(str(error)) from None
        if Path(path).name != name:
            raise FeedToolError("%s is published as %s for %s; give that file" % (path, name, version))
        parts = [platform, release_downloads.url(version, name), path]
    if len(parts) == 3:
        platform, url, path = parts
        data = Path(path).read_bytes()
        entry = {"url": url, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    elif len(parts) == 4 and parts[3].isdigit():
        platform, entry = parts[0], {"url": parts[1], "sha256": parts[2].lower(), "size": int(parts[3])}
    else:
        raise FeedToolError("--download is platform=FILE, platform=URL=FILE or platform=URL=SHA256=SIZE: %r" % spec)
    if version is not None and urlsplit(entry["url"]).hostname == release_downloads.HOST:
        try:
            published = release_downloads.installer_url(version, platform)
        except release_downloads.LayoutError as error:
            raise FeedToolError(str(error)) from None
        if entry["url"] != published:
            raise FeedToolError("%s's %s installer is published at %s, not %s"
                                % (version, platform, published, entry["url"]))
    return platform, entry


def delta(spec, version):
    """One ``--delta``: ``platform=FROM=FILE`` (published as ``release_downloads.delta_name``) or
    ``platform=FROM=URL=SHA256=SIZE``; returns ``(platform, entry)``."""
    parts = spec.split("=")
    if len(parts) == 3:
        platform, source, path = parts
        try:
            name = release_downloads.delta_name(source, version, platform)
        except release_downloads.LayoutError as error:
            raise FeedToolError(str(error)) from None
        if Path(path).name != name:
            raise FeedToolError("%s is published as %s; give that file" % (path, name))
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        entry = {"url": release_downloads.url(version, name), "sha256": digest.hexdigest(),
                 "size": os.path.getsize(path)}
    elif len(parts) == 5 and parts[4].isdigit():
        platform, source = parts[0], parts[1]
        entry = {"url": parts[2], "sha256": parts[3].lower(), "size": int(parts[4])}
    else:
        raise FeedToolError("--delta is platform=FROM=FILE or platform=FROM=URL=SHA256=SIZE: %r" % spec)
    entry.update({"from": source, "format": update_delta.FORMAT})
    return platform, entry


def with_deltas(downloads, deltas):
    """``downloads`` (``[(platform, entry)]``) with each ``(platform, delta)`` added to its platform's entry."""
    table = {platform: dict(entry) for platform, entry in downloads}
    for platform, entry in deltas:
        if platform not in table:
            raise FeedToolError("--delta for %s needs that platform's --download too" % platform)
        table[platform].setdefault("deltas", []).append(entry)
    return list(table.items())


def previous_sequence(path):
    """The sequence of the feed published now (read without verification: only its number is used)."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    return json.loads(base64.b64decode(envelope["payload"]))["sequence"]


def build_payload(version, sequence, downloads=(), *, critical=False, minimum="1.0.0", crash_reports="",
                  expires_days=180, now=None, source=changelog.SOURCE, previous=None, automatic_install=True):
    """The payload document, checked with the app's own parser before it is returned."""
    if previous is not None and sequence <= previous:
        raise FeedToolError("--sequence must be greater than the published feed's %d" % previous)
    releases = changelog.load(source)
    entry = next((r for r in releases if r["version"] == version), None)
    if entry is None:
        raise FeedToolError("CHANGELOG.md has no entry for %s" % version)
    if entry["date"] is None:
        raise FeedToolError("CHANGELOG.md still says %s is Unreleased: date it before publishing" % version)
    fields = changelog.feed_fields(releases, version)
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    document = {
        "schema": 1, "channel": "stable", "sequence": sequence,
        "issued": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires": (now + timedelta(days=expires_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minimum_supported": minimum,
        "latest": dict(version=version, released=entry["date"], notes_url=NOTES_URL.format(version=version),
                       critical=critical, downloads=dict(downloads), **fields),
    }
    if not automatic_install:
        document["latest"]["automatic_install"] = False
    if crash_reports:
        document["services"] = {"crash_reports": crash_reports}
    payload = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    try:
        update_feed.parse_payload(payload, now, last_sequence=previous or 0)
    except update_feed.FeedError as error:
        raise FeedToolError("the app would refuse this payload (%s): %s" % (error.code, error)) from None
    return payload


# -- signing and verifying ------------------------------------------------------------------------------------

def sign_payload(payload, key_id, sign_with):
    """The envelope bytes; the signature is verified with the signer's public key before it is returned."""
    if len(payload) > update_feed.MAX_PAYLOAD:
        raise FeedToolError("the payload is larger than %d bytes" % update_feed.MAX_PAYLOAD)
    message = update_feed.SIGNING_CONTEXT + payload
    signature = sign_with.sign(message)
    public = sign_with.public_key()
    if not ed25519.verify(public, message, signature):
        raise FeedToolError("the signature does not verify with the key's public key: nothing was written")
    envelope = {"format": update_feed.FORMAT, "version": 1, "key_id": key_id,
                "payload": base64.b64encode(payload).decode("ascii"),
                "signature": base64.b64encode(signature).decode("ascii")}
    raw = (json.dumps(envelope, indent=2) + "\n").encode("utf-8")
    update_feed.open_feed(raw, {key_id: public})   # Exactly the app's check, with this key.
    return raw


def verify(raw, keys, last_sequence=0, now=None):
    """The parsed ``Feed`` as the app would accept it; raises ``update_feed.FeedError``."""
    return update_feed.open_feed(raw, keys, now, last_sequence)


def key_entry(key_id, public):
    return {"id": key_id, "public_key": base64.b64encode(public).decode("ascii"), "status": "active"}


KEY_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,39}")


def add_key(keys_path, key_id, public):
    """Add ``public`` as an active key to the shipped key list; True when the file changed.

    The step that lets builds verify feeds signed in Cloud KMS: run once per key version, before the
    release build, and commit the file in a reviewed change. Idempotent for the same id and key; refuses
    an id already used for another key, the same key under a second id, and a revoked id (rotation adds
    a new id, never reuses one). The rest of the file (``_about``, other entries, their order) is kept.
    """
    if not KEY_ID.fullmatch(key_id):
        raise FeedToolError("--key-id is 1 to 40 letters, digits, '.', '_' or '-' (for example 2026-10)")
    if len(public) != ed25519.KEY_BYTES:
        raise FeedToolError("an Ed25519 public key is %d bytes" % ed25519.KEY_BYTES)
    path = Path(keys_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("keys")
    if not isinstance(entries, list):
        raise FeedToolError("%s has no 'keys' list" % path)
    encoded = base64.b64encode(public).decode("ascii")
    for entry in entries:
        same_id, same_key = entry.get("id") == key_id, entry.get("public_key") == encoded
        if same_id and same_key and entry.get("status", "active") == "active":
            return False
        if same_id:
            raise FeedToolError("key id %s is already used (%s); a new key version gets a new id" % (
                key_id, "revoked" if entry.get("status") == "revoked" else "for another key"))
        if same_key:
            raise FeedToolError("that key is already listed as %s" % entry.get("id"))
    entries.append(key_entry(key_id, public))
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if update_feed.load_keys(path).get(key_id) != public:
        raise FeedToolError("%s does not load the new key back" % path)
    return True


# -- command line ---------------------------------------------------------------------------------------------

def _write(path, data, private=False):
    target = Path(path)
    if private:
        if target.exists():
            raise FeedToolError("%s exists; a key file is never overwritten" % target)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    else:
        target.write_bytes(data)


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="write the payload for a release")
    build.add_argument("--version", required=True)
    build.add_argument("--sequence", type=int, required=True, help="greater than the published feed's")
    build.add_argument("--download", action="append", default=[],
                       help="platform=FILE (published at %s/<version>/<file name>), platform=URL=FILE or "
                            "platform=URL=SHA256=SIZE" % release_downloads.BASE_URL)
    build.add_argument("--delta", action="append", default=[],
                       help="platform=FROM=FILE (written by make_update_delta.py, published beside the installers) "
                            "or platform=FROM=URL=SHA256=SIZE")
    build.add_argument("--manual-install", action="store_true",
                       help="Angelo must not install this release itself; people are sent to the download")
    build.add_argument("--critical", action="store_true", help="cannot be skipped; the notice says it is important")
    build.add_argument("--minimum-supported", default="1.0.0", help="older versions are told they are unsupported")
    build.add_argument("--crash-reports", default="", help="announce the crash-report endpoint (https, angelo.studio)")
    build.add_argument("--expires-days", type=int, default=180)
    build.add_argument("--previous", help="the feed published now, to check the sequence grows")
    build.add_argument("--out", required=True)
    for name in ("sign", "public-key"):
        command = commands.add_parser(name)
        if name == "sign":
            command.add_argument("payload")
            command.add_argument("--key-id", required=True)
            command.add_argument("--out", required=True)
        else:
            command.add_argument("--key-id", default="KEY-ID")
        command.add_argument("--key-file", help="a local Ed25519 PEM key (tests and rehearsals only)")
        command.add_argument("--kms", help="projects/P/locations/L/keyRings/R/cryptoKeys/K/cryptoKeyVersions/N")
    check = commands.add_parser("verify", help="check a feed exactly as the app does")
    check.add_argument("feed")
    check.add_argument("--keys", default=str(update_feed.KEYS_FILE))
    check.add_argument("--last-sequence", type=int, default=0)
    install = commands.add_parser("add-key", help="trust a public key in the shipped update_keys.json")
    install.add_argument("--key-id", required=True, help="the id feeds signed by this key carry (for example 2026-10)")
    source = install.add_mutually_exclusive_group(required=True)
    source.add_argument("--pem", help="the public key PEM exported with gcloud kms keys versions get-public-key")
    source.add_argument("--kms", help="read the public key from this key version through gcloud instead")
    install.add_argument("--keys", default=str(update_feed.KEYS_FILE))
    keygen = commands.add_parser("keygen", help="a local key for tests and rehearsals (never production)")
    keygen.add_argument("--out", required=True)
    keygen.add_argument("--public-out", help="also write its public key PEM (the form Cloud KMS exports)")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "build":
            previous = previous_sequence(arguments.previous) if arguments.previous else None
            downloads = [download(spec, arguments.version) for spec in arguments.download]
            downloads = with_deltas(downloads, [delta(spec, arguments.version) for spec in arguments.delta])
            payload = build_payload(arguments.version, arguments.sequence, downloads,
                                    critical=arguments.critical, minimum=arguments.minimum_supported,
                                    crash_reports=arguments.crash_reports, expires_days=arguments.expires_days,
                                    previous=previous, automatic_install=not arguments.manual_install)
            _write(arguments.out, payload)
            print("wrote %s (sequence %d)" % (arguments.out, arguments.sequence), file=out)
        elif arguments.command == "sign":
            raw = sign_payload(Path(arguments.payload).read_bytes(), arguments.key_id, signer(arguments))
            _write(arguments.out, raw)
            print("wrote %s, signed by %s and verified" % (arguments.out, arguments.key_id), file=out)
        elif arguments.command == "public-key":
            print(json.dumps(key_entry(arguments.key_id, signer(arguments).public_key()), indent=2), file=out)
        elif arguments.command == "verify":
            feed = verify(Path(arguments.feed).read_bytes(), update_feed.load_keys(arguments.keys), arguments.last_sequence)
            print("valid: sequence %d, latest %s, expires %s, crash reports %s" % (
                feed.sequence, feed.latest.version, feed.expires or "never", feed.crash_reports_url or "not announced"),
                file=out)
        elif arguments.command == "add-key":
            public = (public_from_pem(Path(arguments.pem).read_text(encoding="ascii")) if arguments.pem
                      else KmsSigner(arguments.kms).public_key())
            changed = add_key(arguments.keys, arguments.key_id, public)
            print("%s %s: %s" % ("added" if changed else "already trusted", arguments.key_id,
                                 base64.b64encode(public).decode("ascii")), file=out)
        else:
            seed = os.urandom(32)
            _write(arguments.out, key_file_text(seed).encode("ascii"), private=True)
            if arguments.public_out:
                _write(arguments.public_out, public_pem(ed25519.public_key(seed)).encode("ascii"))
            print(json.dumps(key_entry("REHEARSAL", ed25519.public_key(seed)), indent=2), file=out)
    except (FeedToolError, update_feed.FeedError, changelog.ChangelogError, OSError, ValueError, KeyError) as error:
        print("sign_update_feed: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
