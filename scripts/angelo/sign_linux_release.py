#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Signs and verifies a Linux release: its file manifest, its download's minisign signature, and the key list.

Contract and key ceremony: doc/angelo/updates_and_reports.rst, "Linux release signing". The production key is the
Ed25519 key ``linux-release`` in Google Cloud KMS (key ring ``angelo-release``, project ``<project>``), separate
from the update feed's key; a local PKCS#8 PEM key (``keygen``) is for tests and rehearsals only. Every signature is
verified against the key's public key before anything is written, and every check here is the app's own
(``release_manifest.check_tree``) or minisign's.

Commands, from the repository root (``--kms`` takes the key version's full name; ``--key-file`` a rehearsal key)::

    # Trust a key: the public key the ceremony exported. Writes linux_release_keys.json and the minisign public key
    # doc/angelo/linux_release_keys/<id>.pub (the downloads page shows it). Refuses the update feed's key.
    python3 scripts/angelo/sign_linux_release.py add-key --key-id linux-2026-11 --pem linux-release-public.pem
    python3 scripts/angelo/sign_linux_release.py check-keys            # release-ready: an active key is shipped

    # The release tree, final (stripped, nothing added later), before it is packed: writes the signed manifest.
    python3 scripts/angelo/sign_linux_release.py manifest --tree dist/Angelo-2.1.0-linux-x64 --version 2.1.0 \\
        --key-id linux-2026-11 --kms "$ANGELO_LINUX_RELEASE_KMS_KEY"
    # Or split, when the build machine has no KMS rights: the bytes there, the signature where the key is, both into
    # the tree (check-tree proves the pair matches the tree).
    python3 scripts/angelo/sign_linux_release.py manifest --tree T --version 2.1.0 --unsigned-out M   # build VM
    python3 scripts/angelo/sign_linux_release.py sign-manifest M --key-id linux-2026-11 --kms ...      # signing Mac
    #   then copy M and its angelo-release-manifest.sig to T as angelo-release-manifest.json / .sig
    python3 scripts/angelo/sign_linux_release.py check-tree --tree dist/Angelo-2.1.0-linux-x64 --version 2.1.0

    # The packed download: <file>.minisig in minisign's default form (Ed25519 over the file's BLAKE2b-512).
    python3 scripts/angelo/sign_linux_release.py minisign dist/Angelo-2.1.0-linux-x64.tar.xz \\
        --key-id linux-2026-11 --kms "$ANGELO_LINUX_RELEASE_KMS_KEY"
    python3 scripts/angelo/sign_linux_release.py verify-tarball dist/Angelo-2.1.0-linux-x64.tar.xz --version 2.1.0

Each command ends with one fixed result line the release kit binds to (PASS check-keys, MANIFEST, SIGNED manifest,
PASS check-tree, SIGNED minisig, PASS verify-tarball); the PASS lines end with ``keys <sha256>``, the key list they
compared against, so the kit can prove a production step used the reviewed list. Otherwise exit 1 with
``sign_linux_release: <reason>``. Every step that checks a key list takes the same ``--keys`` (default: this
checkout's ``linux_release_keys.json``; a rehearsal passes its copy everywhere).

Standard library only, plus ``gcloud`` on PATH for ``--kms`` and, when present, ``minisign`` for a second check.
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ed25519, release_manifest, update_feed, update_stage_linux  # noqa: E402
import sign_update_feed as feed_tool  # noqa: E402

PUBLIC_KEYS_DIR = ROOT / "doc/angelo/linux_release_keys"
KEYS_IN_TREE = "scripts/addons_core/angelo_agent/" + release_manifest.KEYS_FILE.name
MINISIGN_SUFFIX = ".minisig"
# A release may unpack to at most this many times its download, with room to spare under the updater's own stop.
MAX_RATIO = 0.8 * update_stage_linux.UNPACK_FACTOR
PREHASHED = release_manifest.PREHASHED   # The only signature form made and accepted.
PUBLIC_ALGORITHM = b"Ed"
# One implementation of the download's signature check: the app's own (release_manifest).
keynum, read_minisig, check_minisig, verify_minisig, blake2b_file = (
    release_manifest.keynum, release_manifest.read_minisig, release_manifest.check_minisig,
    release_manifest.verify_minisig, release_manifest.blake2b_file)
_CHUNK = 1024 * 1024


class ReleaseToolError(Exception):
    pass


# -- minisign ------------------------------------------------------------------------------------------------------


def keynum_text(public):
    """The key number as minisign prints it (hex of the little-endian integer)."""
    return "%016X" % int.from_bytes(keynum(public), "little")


def minisign_public_key(key_id, public):
    """The text of a minisign public key file for ``public``."""
    body = base64.b64encode(PUBLIC_ALGORITHM + keynum(public) + public).decode("ascii")
    return "untrusted comment: Angelo Linux release key %s (minisign key %s)\n%s\n" % (key_id, keynum_text(public),
                                                                                       body)


def read_minisign_public_key(text):
    """The Ed25519 public key in a minisign public key file's text."""
    lines = text.splitlines()
    if len(lines) < 2 or not lines[0].startswith("untrusted comment:"):
        raise ReleaseToolError("not a minisign public key file")
    raw = base64.b64decode(lines[1].strip(), validate=True)
    if len(raw) != 42 or raw[:2] != PUBLIC_ALGORITHM:
        raise ReleaseToolError("not an Ed25519 minisign public key")
    return raw[10:]



def minisign_signature(path, key_id, sign_with, now=None):
    """The ``.minisig`` text for the file at ``path``: minisign's default (prehashed) form, its trusted comment
    naming the file and the key. Both signatures are verified with the key's public key before it is returned."""
    public = sign_with.public_key()
    name = Path(path).name
    if "\t" in name or "\n" in name:
        raise ReleaseToolError("the file name has a tab or line break: %r" % name)
    signature = sign_with.sign(blake2b_file(path))
    comment = "timestamp:%d\tfile:%s\thashed\tkey:%s" % (int(now if now is not None else time.time()), name, key_id)
    global_signature = sign_with.sign(signature + comment.encode("utf-8"))
    text = "untrusted comment: signature from Angelo Linux release key %s\n%s\ntrusted comment: %s\n%s\n" % (
        key_id, base64.b64encode(PREHASHED + keynum(public) + signature).decode("ascii"), comment,
        base64.b64encode(global_signature).decode("ascii"))
    verify_minisig(path, text, {key_id: public})
    return text





def minisign_tool_check(path, public, runner=subprocess.run):
    """Also check with the ``minisign`` program when it is on PATH: True when it verified, None when absent."""
    program = shutil.which("minisign")
    if program is None:
        return None
    result = runner([program, "-Vm", str(path), "-x", str(path) + MINISIGN_SUFFIX, "-P",
                     minisign_public_key("check", public).splitlines()[1], "-q"], capture_output=True, text=True)
    if result.returncode != 0:
        raise ReleaseToolError("minisign refused %s: %s" % (Path(path).name, (result.stderr or "").strip()[:300]))
    return True


# -- keys ----------------------------------------------------------------------------------------------------------

def add_key(key_id, public, keys_path=release_manifest.KEYS_FILE, public_dir=PUBLIC_KEYS_DIR,
            feed_keys=update_feed.KEYS_FILE):
    """Trust ``public`` for Linux releases: an entry in the key list and its minisign public key file. Refuses the
    update feed's key (each key signs one thing, so either can be rotated or revoked alone); otherwise the rules of
    ``sign_update_feed.add_key``. True when the key list changed."""
    try:
        changed = feed_tool.add_key(keys_path, key_id, public, separate_from=feed_keys)
    except feed_tool.FeedToolError as error:
        raise ReleaseToolError(str(error)) from None
    target = Path(public_dir) / ("%s.pub" % key_id)
    text = minisign_public_key(key_id, public)
    if target.exists() and target.read_text(encoding="ascii") != text:
        raise ReleaseToolError("%s exists with another key" % target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="ascii")
    return changed


def active_keys(keys_path):
    keys = release_manifest.load_keys(keys_path)
    if not keys:
        raise ReleaseToolError("%s trusts no key: run the key ceremony and add-key before a Linux release build"
                               % keys_path)
    return keys


def signing_key(key_id, sign_with, keys_path):
    """The public key of ``sign_with``, which must be listed active as ``key_id`` in ``keys_path``."""
    public = sign_with.public_key()
    if active_keys(keys_path).get(key_id) != public:
        raise ReleaseToolError("%s does not trust this key as %s: a release signed with it would never install"
                               % (keys_path, key_id))
    return public


# -- the manifest --------------------------------------------------------------------------------------------------

def check_keys_in_tree(entries, tree, keys_path=release_manifest.KEYS_FILE):
    """The release carries exactly one Linux key list, the reviewed one (``keys_path``, byte for byte, with an
    active key): every installed copy trusts that list for its own updates, so a rehearsal key must never ship."""
    found = [path for path, entry in entries.items() if path.endswith(KEYS_IN_TREE) and entry["type"] == "file"]
    if len(found) != 1:
        raise ReleaseToolError("the release must carry exactly one %s; found %d" % (KEYS_IN_TREE, len(found)))
    active_keys(keys_path)
    if entries[found[0]]["sha256"] != file_sha256(keys_path):
        raise ReleaseToolError("the release's %s is not the reviewed list %s" % (found[0], keys_path))
    return found[0]


def unsigned_manifest(tree, version, keys_path=release_manifest.KEYS_FILE):
    """``(manifest bytes, entries)`` of the final release ``tree``: what the release key signs. Needs no key, so a
    build machine without KMS rights writes it, and the signing machine signs exactly those bytes."""
    tree = Path(tree)
    entries = release_manifest.describe_tree(tree)
    check_keys_in_tree(entries, tree, keys_path)
    return release_manifest.manifest_bytes(entries, version), len(entries)


def sign_manifest(manifest, key_id, sign_with, keys_path=release_manifest.KEYS_FILE):
    """The signature file's bytes for ``manifest`` (as ``unsigned_manifest`` wrote it), verified with the key's
    public key, which ``keys_path`` must trust as ``key_id``."""
    public = signing_key(key_id, sign_with, keys_path)
    try:
        document = json.loads(manifest)
        release_manifest.parse_manifest(manifest, document.get("version"), document.get("platform"))
    except (ValueError, AttributeError) as error:
        raise ReleaseToolError("not a release manifest: %s" % error) from None
    signature = sign_with.sign(release_manifest.signed_message(manifest))
    if not ed25519.verify(public, release_manifest.signed_message(manifest), signature):
        raise ReleaseToolError("the signature does not verify with the key's public key: nothing was written")
    return release_manifest.signature_bytes(key_id, signature)


def place(tree, manifest, signature):
    """Put a manifest and its signature at the top of ``tree`` (read-only for others, as the release requires)."""
    for name, data in ((release_manifest.MANIFEST_NAME, manifest), (release_manifest.SIGNATURE_NAME, signature)):
        target = Path(tree) / name
        target.write_bytes(data)
        os.chmod(target, 0o644)


def write_manifest(tree, version, key_id, sign_with, keys_path=release_manifest.KEYS_FILE):
    """Write the signed manifest of ``tree`` and check the tree with it, as the updater will (one machine with both
    the tree and the key; the split form is ``unsigned_manifest`` then ``sign_manifest``)."""
    manifest, count = unsigned_manifest(tree, version, keys_path)
    place(tree, manifest, sign_manifest(manifest, key_id, sign_with, keys_path))
    release_manifest.check_tree(tree, version, {key_id: sign_with.public_key()})
    return count


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    """A file's SHA-256, as the feed and the downloads' .sha256 list it."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_tree(tree, version, keys_path=release_manifest.KEYS_FILE):
    keys = active_keys(keys_path)
    key_id = release_manifest.check_tree(tree, version, keys)
    check_keys_in_tree(release_manifest.describe_tree(tree), tree, keys_path)
    return key_id


# -- the tarball ---------------------------------------------------------------------------------------------------

def _sha256_member(archive, member):
    digest = hashlib.sha256()
    handle = archive.extractfile(member)
    for chunk in iter(lambda: handle.read(_CHUNK), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _member_entry(member, digest):
    if member.mode & 0o7000 or (not member.issym() and member.mode & 0o022):
        raise release_manifest.ManifestError("bad_mode", "%s has a set-id bit or is writable by others" % member.name)
    if member.isdir():
        return {"type": "dir"}
    if member.issym():
        return {"type": "link", "target": member.linkname}
    if member.isreg():
        return {"type": "file", "size": member.size, "sha256": digest,
                "exec": bool(member.mode & 0o100)}
    raise release_manifest.ManifestError("bad_type", "%s is not a file, folder or link" % member.name)


def check_tarball(path, version, keys, keys_list_sha256=None, totals=None):
    """The key id when the tarball holds exactly the signed release ``version``: one top folder, every member as its
    manifest lists it, and (``keys_list_sha256``) the reviewed key list. Read without extracting anything."""
    root = release_manifest.root_name(version)
    actual, own, top = {}, {}, False
    with tarfile.open(path, "r:xz") as archive:
        for member in archive:
            name = member.name[2:] if member.name.startswith("./") else member.name
            name = name.rstrip("/")
            if name == root:
                if not member.isdir():
                    raise release_manifest.ManifestError("bad_type", "%s is not a folder" % root)
                _member_entry(member, "")   # The top folder's mode, by the same rule as every member's.
                top = True
                continue
            if not name.startswith(root + "/"):
                raise release_manifest.ManifestError("unexpected", "%s is outside %s/" % (member.name, root))
            relative = release_manifest.check_path(name[len(root) + 1:])
            if relative in release_manifest.OWN_FILES and member.isreg():
                if relative in own:
                    raise release_manifest.ManifestError("malformed", "%s is in the tarball twice" % relative)
                if member.size > release_manifest.MAX_MANIFEST:
                    raise release_manifest.ManifestError("too_large", "%s is too large" % relative)
                own[relative] = archive.extractfile(member).read()
                continue
            if relative in actual or relative in release_manifest.OWN_FILES:
                raise release_manifest.ManifestError("malformed", "%s is in the tarball twice" % relative)
            if member.islnk():
                # tar stores a file with several names once, then each other name as a hard link to it: unpacked,
                # it is that same file, which the manifest lists under every name.
                target = member.linkname[2:] if member.linkname.startswith("./") else member.linkname
                source = actual.get(target[len(root) + 1:]) if target.startswith(root + "/") else None
                if source is None or source["type"] != "file":
                    raise release_manifest.ManifestError("bad_link", "%s links to a file outside the release" % relative)
                actual[relative] = dict(source)
                continue
            actual[relative] = _member_entry(member, _sha256_member(archive, member) if member.isreg() else "")
            if totals is not None:
                totals["bytes"] = totals.get("bytes", 0) + max(member.size, 0)
            if actual[relative]["type"] == "link" and not release_manifest.link_inside(relative, member.linkname):
                raise release_manifest.ManifestError("bad_link", "%s points outside the release" % relative)
    if not top:   # Its mode would be left to the umask of whoever unpacks it.
        raise release_manifest.ManifestError("layout", "The tarball has no entry for its top folder %s/" % root)
    if set(own) != set(release_manifest.OWN_FILES):
        raise release_manifest.ManifestError("unsigned", "The tarball has no signed manifest")
    key_id = release_manifest.verify_signature(own[release_manifest.MANIFEST_NAME],
                                               own[release_manifest.SIGNATURE_NAME], keys)
    release_manifest.compare(release_manifest.parse_manifest(own[release_manifest.MANIFEST_NAME], version), actual)
    if keys_list_sha256 is not None:
        lists = [entry for path, entry in actual.items() if path.endswith(KEYS_IN_TREE) and entry["type"] == "file"]
        if len(lists) != 1 or lists[0]["sha256"] != keys_list_sha256:
            raise ReleaseToolError("the tarball does not carry the reviewed %s" % KEYS_IN_TREE)
    return key_id


def verify_tarball(path, version, keys_path=release_manifest.KEYS_FILE, runner=subprocess.run):
    """Everything a release-ready check needs of the download: its ``.minisig`` (this tool's check and minisign's,
    when installed) and the signed manifest inside it, against the shipped key list."""
    keys = active_keys(keys_path)
    signature = Path(str(path) + MINISIGN_SUFFIX)
    if not signature.is_file():
        raise ReleaseToolError("%s has no %s beside it" % (Path(path).name, signature.name))
    key_id = verify_minisig(path, signature.read_text(encoding="utf-8"), keys)
    tool = minisign_tool_check(path, keys[key_id], runner)
    totals = {}
    manifest_key = check_tarball(path, version, keys, file_sha256(keys_path), totals)
    ratio = totals.get("bytes", 0) / max(os.path.getsize(path), 1)
    if ratio > MAX_RATIO:
        raise ReleaseToolError("%s unpacks to %.1f times its size; the updater stops at %d times "
                               "(update_stage_linux.UNPACK_FACTOR): keep releases under %.1f" % (
                                   Path(path).name, ratio, update_stage_linux.UNPACK_FACTOR, MAX_RATIO))
    return key_id, manifest_key, tool, ratio


# -- command line --------------------------------------------------------------------------------------------------

def _signer_arguments(command):
    command.add_argument("--key-id", required=True, help="the id the key list trusts this key as")
    command.add_argument("--key-file", help="a local Ed25519 PEM key (tests and rehearsals only)")
    command.add_argument("--kms", help="projects/P/locations/L/keyRings/R/cryptoKeys/K/cryptoKeyVersions/N")
    command.add_argument("--keys", default=str(release_manifest.KEYS_FILE), help="the key list that must trust it")


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("add-key", help="trust a public key in linux_release_keys.json")
    install.add_argument("--key-id", required=True, help="for example linux-2026-11")
    source = install.add_mutually_exclusive_group(required=True)
    source.add_argument("--pem", help="the public key PEM exported with gcloud kms keys versions get-public-key")
    source.add_argument("--kms", help="read the public key from this key version through gcloud instead")
    install.add_argument("--keys", default=str(release_manifest.KEYS_FILE))
    install.add_argument("--public-dir", default=str(PUBLIC_KEYS_DIR))
    for name in ("check-keys",):
        commands.add_parser(name).add_argument("--keys", default=str(release_manifest.KEYS_FILE))
    keygen = commands.add_parser("keygen", help="a local key for tests and rehearsals (never production)")
    keygen.add_argument("--out", required=True)
    keygen.add_argument("--public-out", required=True)
    write = commands.add_parser("manifest", help="write the signed manifest of a final release tree, or with "
                                                 "--unsigned-out only the bytes to sign elsewhere")
    write.add_argument("--tree", required=True)
    write.add_argument("--version", required=True)
    write.add_argument("--unsigned-out", help="write the manifest here (outside the tree) and sign nothing")
    write.add_argument("--key-id")
    write.add_argument("--key-file", help="a local Ed25519 PEM key (tests and rehearsals only)")
    write.add_argument("--kms", help="projects/P/locations/L/keyRings/R/cryptoKeys/K/cryptoKeyVersions/N")
    write.add_argument("--keys", default=str(release_manifest.KEYS_FILE), help="the key list that must trust it")
    signed = commands.add_parser("sign-manifest", help="sign a manifest written with manifest --unsigned-out")
    signed.add_argument("manifest")
    signed.add_argument("--out", help="the signature file (default: angelo-release-manifest.sig beside it)")
    _signer_arguments(signed)
    check = commands.add_parser("check-tree", help="check a release tree exactly as the updater does")
    check.add_argument("--tree", required=True)
    check.add_argument("--version", required=True)
    check.add_argument("--keys", default=str(release_manifest.KEYS_FILE))
    sign = commands.add_parser("minisign", help="write <file>.minisig")
    sign.add_argument("file")
    _signer_arguments(sign)
    tarball = commands.add_parser("verify-tarball", help="the download's .minisig and the signed manifest inside")
    tarball.add_argument("file")
    tarball.add_argument("--version", required=True)
    tarball.add_argument("--keys", default=str(release_manifest.KEYS_FILE))
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "add-key":
            public = (feed_tool.public_from_pem(Path(arguments.pem).read_text(encoding="ascii")) if arguments.pem
                      else feed_tool.KmsSigner(arguments.kms).public_key())
            changed = add_key(arguments.key_id, public, arguments.keys, arguments.public_dir)
            print("%s %s: %s (minisign key %s)" % ("added" if changed else "already trusted", arguments.key_id,
                                                  base64.b64encode(public).decode("ascii"), keynum_text(public)),
                  file=out)
        elif arguments.command == "check-keys":
            print("PASS check-keys %s keys %s" % (",".join(sorted(active_keys(arguments.keys))),
                                                  file_sha256(arguments.keys)), file=out)
        elif arguments.command == "keygen":
            seed = os.urandom(32)
            feed_tool._write(arguments.out, feed_tool.key_file_text(seed).encode("ascii"), private=True)
            Path(arguments.public_out).write_text(feed_tool.public_pem(ed25519.public_key(seed)), encoding="ascii")
            print("wrote %s (rehearsal only) and %s" % (arguments.out, arguments.public_out), file=out)
        elif arguments.command == "manifest" and arguments.unsigned_out:
            if arguments.key_id or arguments.key_file or arguments.kms:
                raise ReleaseToolError("--unsigned-out signs nothing: give no key")
            if Path(arguments.unsigned_out).resolve().is_relative_to(Path(arguments.tree).resolve()):
                raise ReleaseToolError("--unsigned-out must be outside the tree, which the manifest describes")
            manifest, count = unsigned_manifest(arguments.tree, arguments.version, arguments.keys)
            Path(arguments.unsigned_out).write_bytes(manifest)
            print("MANIFEST %s entries %d version %s" % (sha256_hex(manifest), count, arguments.version), file=out)
        elif arguments.command == "manifest":
            if not arguments.key_id:
                raise ReleaseToolError("manifest needs --key-id and a key, or --unsigned-out")
            write_manifest(arguments.tree, arguments.version, arguments.key_id, feed_tool.signer(arguments),
                           arguments.keys)
            manifest = (Path(arguments.tree) / release_manifest.MANIFEST_NAME).read_bytes()
            print("SIGNED manifest %s key %s" % (sha256_hex(manifest), arguments.key_id), file=out)
        elif arguments.command == "sign-manifest":
            manifest = Path(arguments.manifest).read_bytes()
            signature = sign_manifest(manifest, arguments.key_id, feed_tool.signer(arguments), arguments.keys)
            target = Path(arguments.out or Path(arguments.manifest).with_name(release_manifest.SIGNATURE_NAME))
            target.write_bytes(signature)
            print("SIGNED manifest %s key %s" % (sha256_hex(manifest), arguments.key_id), file=out)
        elif arguments.command == "check-tree":
            key_id = check_tree(arguments.tree, arguments.version, arguments.keys)
            manifest = (Path(arguments.tree) / release_manifest.MANIFEST_NAME).read_bytes()
            print("PASS check-tree %s version %s manifest %s key %s keys %s" % (
                Path(arguments.tree).name, arguments.version, sha256_hex(manifest), key_id,
                file_sha256(arguments.keys)), file=out)
        elif arguments.command == "minisign":
            signer = feed_tool.signer(arguments)
            signing_key(arguments.key_id, signer, arguments.keys)
            text = minisign_signature(arguments.file, arguments.key_id, signer)
            Path(arguments.file + MINISIGN_SUFFIX).write_text(text, encoding="utf-8")
            print("SIGNED minisig %s sha256 %s key %s" % (Path(arguments.file).name,
                                                          file_sha256(arguments.file), arguments.key_id), file=out)
        else:
            key_id, manifest_key, tool, ratio = verify_tarball(arguments.file, arguments.version, arguments.keys)
            print("PASS verify-tarball %s sha256 %s key %s manifest-key %s minisign %s ratio %.2f keys %s" % (
                Path(arguments.file).name, file_sha256(arguments.file), key_id, manifest_key,
                "verified" if tool else "absent", ratio, file_sha256(arguments.keys)), file=out)
    except (ReleaseToolError, release_manifest.ManifestError, feed_tool.FeedToolError, OSError, ValueError,
            tarfile.TarError) as error:
        print("sign_linux_release: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
