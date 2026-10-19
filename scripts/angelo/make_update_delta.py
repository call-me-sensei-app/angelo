#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Writes an update delta: the package that turns one published Angelo.app into the next.

Format and rules: ``scripts/addons_core/angelo_agent/update_delta.py`` (the app's
side, which this tool reuses) and doc/angelo/updates_and_reports.rst, "Deltas".
Both apps are the final, signed (and stapled) bundles, read from their
published disk images or from folders; nothing in them is changed. The delta is
applied to a copy of the old app and compared with the new one before it is
written, so a delta that would not rebuild the new app byte for byte is never
published. From the repository root, on the Mac that signed the release::

    python3 scripts/angelo/make_update_delta.py --platform macos-arm64 \\
        --from-version 1.0.0 --from dist/1.0.0/Angelo-1.0.0-macos-arm64.dmg \\
        --to-version 1.0.1 --to dist/macos/Angelo-1.0.1-macos-arm64.dmg --out dist/deltas

It prints the ``--delta`` argument for ``sign_update_feed.py build``. Standard
library only (``hdiutil`` for disk images).
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import update_delta  # noqa: E402  (standard library only)
import release_downloads  # noqa: E402

PATCH_MIN_SIZE = 256 * 1024     # Smaller files go whole: a patch saves little and costs a member.
PATCH_MAX_SHARE = 0.5           # A patch is used only when it keeps at most half of the file as new bytes.


class DeltaToolError(Exception):
    pass


@contextlib.contextmanager
def app_from(source, run=subprocess.run):
    """The ``.app`` in ``source``: a folder as is, or a disk image mounted read-only for the duration."""
    source = Path(source)
    if source.suffix == ".app" and source.is_dir():
        yield source
        return
    if source.suffix != ".dmg":
        raise DeltaToolError("%s is neither an .app folder nor a .dmg" % source)
    mount = Path(tempfile.mkdtemp(prefix="angelo-delta-mount-"))
    run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount), str(source)],
        check=True, capture_output=True)
    try:
        apps = [p for p in mount.iterdir() if p.suffix == ".app" and p.is_dir() and not p.is_symlink()]
        if len(apps) != 1:
            raise DeltaToolError("%s holds %d apps, not one" % (source, len(apps)))
        yield apps[0]
    finally:
        if run(["hdiutil", "detach", str(mount)], capture_output=True).returncode != 0:
            run(["hdiutil", "detach", "-force", str(mount)], capture_output=True)
        shutil.rmtree(mount, ignore_errors=True)


def bundle_version(app):
    info = app / "Contents/Info.plist"
    if not info.exists():
        return None
    with open(info, "rb") as handle:
        return plistlib.load(handle).get("CFBundleShortVersionString")


def plan(old_app, new_app):
    """``(entries, members)``: the new bundle's description and ``{member: (kind, source)}`` to store."""
    old, new = update_delta.tree(old_app), update_delta.tree(new_app)
    if any(entry["type"] == "other" for entry in new.values()):
        raise DeltaToolError("the new app holds something other than folders, files and links")
    by_digest = {}
    for path, entry in sorted(old.items()):
        if entry["type"] == "file":
            by_digest.setdefault(entry["sha256"], path)
    entries, members = {}, {}
    for path, entry in new.items():
        entry = dict(entry)
        if entry["type"] == "file":
            entry["from"] = _source(path, entry, old, by_digest, old_app, new_app, members)
        entries[path] = entry
    return entries, members


def _source(path, entry, old, by_digest, old_app, new_app, members):
    previous = old.get(path)
    if previous and previous["type"] == "file" and previous["sha256"] == entry["sha256"]:
        return {"kind": "same", "path": path}
    if entry["sha256"] in by_digest:
        return {"kind": "copy", "path": by_digest[entry["sha256"]]}
    if previous and previous["type"] == "file" and entry["size"] >= PATCH_MIN_SIZE:
        buffer = io.BytesIO()
        literal = update_delta.write_patch(old_app / path, new_app / path, buffer)
        if literal <= PATCH_MAX_SHARE * entry["size"]:
            member = "patches/%d" % sum(1 for kind, _ in members.values() if kind == "patch")
            members[member] = ("patch", buffer.getvalue())
            return {"kind": "patch", "path": path, "member": member}
    member = "blobs/" + entry["sha256"]
    members.setdefault(member, ("blob", new_app / path))
    return {"kind": "blob", "member": member}


def write(out, from_version, to_version, entries, members, preset=9):
    """The ``.tar.xz`` delta: ``delta.json`` first, then every member."""
    description = json.dumps({"format": update_delta.FORMAT, "from": from_version, "to": to_version,
                              "entries": entries}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with tarfile.open(out, "w:xz", preset=preset, format=tarfile.PAX_FORMAT) as archive:
        _add(archive, update_delta.MANIFEST, io.BytesIO(description), len(description))
        for member, (kind, source) in members.items():
            if kind == "patch":
                _add(archive, member, io.BytesIO(source), len(source))
            else:
                with open(source, "rb") as handle:
                    _add(archive, member, handle, os.path.getsize(source))


def _add(archive, name, stream, size):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, 0o644, 0
    archive.addfile(info, stream)


def check(delta, old_app, from_version, to_version, entries):
    """Rebuild the new app from the old one with the delta, exactly as the app does, and compare."""
    with tempfile.TemporaryDirectory(prefix="angelo-delta-check-") as work:
        out = Path(work) / old_app.name
        update_delta.apply(delta, old_app, out, from_version, to_version)
        problem = update_delta.compare(out, entries)
        if problem:
            raise DeltaToolError("the delta does not rebuild the new app: " + problem)


def make(platform, from_version, from_source, to_version, to_source, out_dir, verify=True, run=subprocess.run):
    """Write the delta into ``out_dir`` under its published name; returns ``(path, report)``."""
    for version in (from_version, to_version):
        release_downloads.check_version(version)
    name = release_downloads.delta_name(from_version, to_version, platform)
    out = Path(out_dir) / name
    if out.exists():
        raise DeltaToolError("%s already exists" % out)
    with app_from(from_source, run) as old_app, app_from(to_source, run) as new_app:
        for app, version in ((old_app, from_version), (new_app, to_version)):
            found = bundle_version(app)
            if found is not None and found != version:
                raise DeltaToolError("%s is version %s, not %s" % (app, found, version))
        entries, members = plan(old_app, new_app)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        partial = out.with_name(out.name + ".part")
        write(partial, from_version, to_version, entries, members)
        if verify:
            check(partial, old_app, from_version, to_version, entries)
        os.replace(partial, out)
        new_size = sum(e["size"] for e in entries.values() if e["type"] == "file")
    kinds = {}
    for entry in entries.values():
        if entry["type"] == "file":
            kinds[entry["from"]["kind"]] = kinds.get(entry["from"]["kind"], 0) + 1
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    report = {"delta": str(out), "size": out.stat().st_size, "sha256": digest, "new_app_bytes": new_size,
              "files": kinds, "verified": verify,
              "feed_argument": "--delta %s=%s=%s" % (platform, from_version, out)}
    return out, report


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--platform", required=True, choices=sorted(release_downloads.DELTAS))
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--from", dest="from_source", required=True, help="the older app: .dmg or .app")
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--to", dest="to_source", required=True, help="the new app: .dmg or .app")
    parser.add_argument("--out", required=True, help="folder for the delta")
    parser.add_argument("--no-verify", action="store_true", help="skip rebuilding the new app (tests only)")
    arguments = parser.parse_args(argv)
    try:
        _, report = make(arguments.platform, arguments.from_version, arguments.from_source, arguments.to_version,
                         arguments.to_source, arguments.out, verify=not arguments.no_verify)
    except (DeltaToolError, update_delta.DeltaError, release_downloads.LayoutError, OSError,
            subprocess.CalledProcessError) as error:
        print("make_update_delta: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
