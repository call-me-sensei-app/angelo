#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rehearses Linux release signing end to end with a throwaway key: the key ceremony's step 6.

doc/angelo/updates_and_reports.rst, "Linux release signing". Every step is ``sign_linux_release.py``'s own command,
run as the release kit runs it (the split form: the manifest's bytes where the tree is, the signature where the key
is), with a rehearsal key trusted only in a copy of the key list:

    python3 scripts/angelo/rehearse_linux_release.py --out EVIDENCE [--tree STAGE/Angelo-<version>-linux-x64]
        [--version V]

Without ``--tree`` it builds a small stand-in release; with one (lane A's staged tree) it rehearses on a copy,
whose key list gains the rehearsal key: the tree itself is never changed. Steps: keygen, add-key (a copy of the
list), check-keys, manifest --unsigned-out, sign-manifest, the pair placed in the tree, check-tree, pack, minisign,
verify-tarball (and ``minisign -V`` when the program is installed), then the refusals that must happen: a changed
file in the tree, a changed byte in the tarball, another key's manifest. ``EVIDENCE/rehearsal.json`` records every
step's result line; the last line printed is ``PASS rehearsal <evidence>`` or the first step that failed.
Nothing here is ever a release: the key is generated here and the tarball is named ``rehearsal-<name>``.
Standard library only.
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sign_linux_release as tool  # noqa: E402
from sign_linux_release import release_manifest  # noqa: E402

ADDON_KEYS = "5.3/" + tool.KEYS_IN_TREE


class RehearsalError(Exception):
    pass


def stand_in(parent, version):
    """A small release folder: a launcher, an executable, a library link chain, an empty folder, the key list."""
    root = Path(parent) / release_manifest.root_name(version)
    (root / "lib").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "angelo").write_text('#!/bin/sh\nexec "$(dirname "$0")/blender" "$@"\n')
    (root / "blender").write_bytes(os.urandom(64 * 1024))
    (root / "lib/libangelo.so.1.0").write_bytes(os.urandom(32 * 1024))
    os.symlink("libangelo.so.1.0", root / "lib/libangelo.so.1")
    (root / ADDON_KEYS).parent.mkdir(parents=True)
    shutil.copyfile(release_manifest.KEYS_FILE, root / ADDON_KEYS)
    return root


def normalise(root):
    """Folders and executables 0755, other files 0644 (lane A's package does the same before signing)."""
    for folder, folders, files in os.walk(root):
        os.chmod(folder, 0o755)
        for name in files:
            path = os.path.join(folder, name)
            if not os.path.islink(path):
                os.chmod(path, 0o755 if os.access(path, os.X_OK) or name in ("angelo", "blender") else 0o644)


def pack(root, tarball):
    """A tar.xz with one top folder, sorted, owned by 0:0 (the shape lane A's pack writes)."""
    def owner(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info
    with tarfile.open(tarball, "w:xz") as archive:
        archive.add(root, arcname=root.name, filter=owner)
    return tarball


def rehearse(out, version, tree=None, runner=subprocess.run):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise RehearsalError("%s is not empty" % out)
    out.mkdir(parents=True, exist_ok=True)
    steps = []

    def step(name, argv, expect_ok=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stderr(stderr):
            code = tool.main(argv, out=stdout)
        line = (stdout.getvalue().strip().splitlines() or [""])[-1]
        steps.append({"step": name, "ok": code == 0, "expected_ok": expect_ok, "line": line,
                      "error": stderr.getvalue().strip()[:500]})
        if (code == 0) != expect_ok:
            raise RehearsalError("%s: %s" % (name, stderr.getvalue().strip() or line or "refused nothing"))
        return line

    key, public, keys = out / "rehearsal.pem", out / "rehearsal-public.pem", out / "linux_release_keys.json"
    shutil.copyfile(release_manifest.KEYS_FILE, keys)
    step("keygen", ["keygen", "--out", str(key), "--public-out", str(public)])
    step("add-key", ["add-key", "--key-id", "rehearsal", "--pem", str(public), "--keys", str(keys),
                     "--public-dir", str(out / "public-keys")])
    step("check-keys", ["check-keys", "--keys", str(keys)])
    stage = out / "stage"
    if tree is None:
        root = stand_in(stage, version)
    else:
        root = stage / Path(tree).name
        shutil.copytree(tree, root, symlinks=True)
        version = Path(tree).name[len("Angelo-"):-len("-linux-x64")]
        for name in release_manifest.OWN_FILES:
            (root / name).unlink(missing_ok=True)
    found = [path for path in root.rglob("linux_release_keys.json") if path.as_posix().endswith(tool.KEYS_IN_TREE)]
    if len(found) != 1:
        raise RehearsalError("the tree carries %d key lists" % len(found))
    shutil.copyfile(keys, found[0])   # The copy trusts the rehearsal key, as a real release's list trusts the real one.
    normalise(root)
    manifest = out / "angelo-release-manifest.json"
    step("manifest --unsigned-out", ["manifest", "--tree", str(root), "--version", version,
                                     "--unsigned-out", str(manifest), "--keys", str(keys)])
    step("sign-manifest", ["sign-manifest", str(manifest), "--key-id", "rehearsal", "--key-file", str(key),
                           "--keys", str(keys)])
    tool.place(root, manifest.read_bytes(), (out / release_manifest.SIGNATURE_NAME).read_bytes())
    step("check-tree", ["check-tree", "--tree", str(root), "--version", version, "--keys", str(keys)])
    dist = out / "dist"
    dist.mkdir()
    tarball = pack(root, dist / ("%s.tar.xz" % root.name))
    step("minisign", ["minisign", str(tarball), "--key-id", "rehearsal", "--key-file", str(key), "--keys", str(keys)])
    step("verify-tarball", ["verify-tarball", str(tarball), "--version", version, "--keys", str(keys)])
    program = shutil.which("minisign")
    if program:
        done = runner([program, "-Vm", str(tarball), "-p", str(out / "public-keys/rehearsal.pub")],
                      capture_output=True, text=True)
        steps.append({"step": "minisign -V (program)", "ok": done.returncode == 0, "expected_ok": True,
                      "line": (done.stdout or "").strip().splitlines()[0] if done.stdout else "", "error": done.stderr[:300]})
        if done.returncode:
            raise RehearsalError("minisign -V refused the tarball: %s" % done.stderr.strip())
    # The refusals: each on its own copy, the signed release left as it is.
    changed = out / "refusals/changed-tree" / root.name
    shutil.copytree(root, changed, symlinks=True)
    target = next(path for path in sorted(changed.rglob("*")) if path.is_file() and not path.is_symlink()
                  and path.name not in release_manifest.OWN_FILES)
    target.write_bytes(target.read_bytes() + b"\0")
    step("refused: a changed file", ["check-tree", "--tree", str(changed), "--version", version, "--keys", str(keys)],
         expect_ok=False)
    damaged = out / "refusals" / tarball.name
    damaged.write_bytes(tarball.read_bytes()[:-1] + bytes([tarball.read_bytes()[-1] ^ 1]))
    shutil.copyfile(str(tarball) + tool.MINISIGN_SUFFIX, str(damaged) + tool.MINISIGN_SUFFIX)
    step("refused: a changed byte in the tarball", ["verify-tarball", str(damaged), "--version", version,
                                                   "--keys", str(keys)], expect_ok=False)
    opened = out / "refusals/group-writable-top" / root.name
    shutil.copytree(root, opened, symlinks=True)
    os.chmod(opened, 0o775)
    step("refused: a top folder a group may write", ["manifest", "--tree", str(opened), "--version", version,
                                                    "--unsigned-out", str(out / "refusals/opened.json"),
                                                    "--keys", str(keys)], expect_ok=False)
    topless = out / "refusals" / ("no-top-entry-" + tarball.name)
    with tarfile.open(tarball, "r:xz") as source, tarfile.open(topless, "w:xz") as archive:
        for member in source:
            if member.name.rstrip("/") != root.name:   # Every member but the top folder's own.
                archive.addfile(member, source.extractfile(member) if member.isreg() else None)
    (Path(str(topless) + tool.MINISIGN_SUFFIX)).write_text(
        tool.minisign_signature(topless, "rehearsal", tool.feed_tool.LocalSigner(key)), encoding="utf-8")
    step("refused: a tarball without its top folder's entry", ["verify-tarball", str(topless), "--version", version,
                                                               "--keys", str(keys)], expect_ok=False)
    other = out / "refusals/other.pem"
    step("keygen (another key)", ["keygen", "--out", str(other), "--public-out", str(out / "refusals/other.pub.pem")])
    step("refused: another key signs", ["sign-manifest", str(manifest), "--key-id", "rehearsal", "--key-file",
                                        str(other), "--keys", str(keys), "--out", str(out / "refusals/other.sig")],
         expect_ok=False)
    evidence = out / "rehearsal.json"
    evidence.write_text(json.dumps({"kind": "angelo-linux-signing-rehearsal", "version": version,
                                    "tree": "stand-in" if tree is None else Path(tree).name,
                                    "tarball": tarball.name, "steps": steps}, indent=1) + "\n", encoding="utf-8")
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="an empty folder for the evidence")
    parser.add_argument("--tree", help="a staged release folder Angelo-<version>-linux-x64 (rehearsed on a copy)")
    parser.add_argument("--version", default="0.0.1", help="the stand-in's version (without --tree)")
    arguments = parser.parse_args(argv)
    try:
        evidence = rehearse(arguments.out, arguments.version, arguments.tree)
    except (RehearsalError, OSError, tarfile.TarError, StopIteration) as error:
        print("FAIL rehearsal: %s" % error)
        return 1
    print("PASS rehearsal %s" % evidence)
    return 0


if __name__ == "__main__":
    sys.exit(main())
