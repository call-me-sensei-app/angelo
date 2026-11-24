#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Linux release package: ``Angelo-<version>-linux-x64.tar.xz``, Blender's own Linux format.

doc/angelo/releasing.rst, "Linux package". Three steps, so the release's file manifest can be signed between them
(the signing key never comes to the build machine)::

    linux_package.py stage --install <build>/install --out STAGE     # STAGE/Angelo-<v>-linux-x64/
    sign_linux_release.py manifest --tree STAGE/Angelo-<v>-linux-x64 ...   # the manifest and its signature
    linux_package.py pack  --stage STAGE --out DIST [--debug-symbols <build>/debug-symbols]
    linux_package.py check DIST/Angelo-<v>-linux-x64.tar.xz      # linux_package_checks.py

``stage`` copies a portable install (``WITH_INSTALL_PORTABLE``: ``blender``, the ``angelo`` launcher, the desktop
file and icons, ``lib/`` and the version folder) into one top folder named after the package, leaving out bytecode
Python wrote at run time (the build's own hash-based bytecode stays: ``release_checks.is_build_bytecode``). The tree is
final from then on: ``sign_linux_release.py manifest`` (the updater's own format, signed in Cloud KMS) lists every file,
and an update is accepted only when every file matches it, so nothing may change inside the tree after signing.

``pack`` makes the same bytes from the same staged tree on any machine: entries sorted by path, every timestamp the
commit's time (``SOURCE_DATE_EPOCH``), owner root with no names, modes 0755 (folders and executables) or 0644, symbolic
links kept as links, and ``xz`` with a fixed preset, block size and thread count. It refuses a tree whose signed
manifest is missing or does not verify (``sign_linux_release.py check-tree``) unless ``--unsigned`` (a development or
CI package, which its receipt says), and writes nothing into the tree. ``--debug-symbols`` also packs the build's split
symbols as ``<package>.debug.tar.xz`` the same way: kept with the release kit for crash reports, never published.

Standard library plus the ``xz`` command (every Linux distribution has it). No ``bpy``.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import release_checks  # noqa: E402  (beside this file; standard library only)

IDENTITY = ROOT / "scripts/modules/angelo_identity.json"
ARCH = "x64"
STAGE_RECEIPT = "stage-receipt.json"
PACK_RECEIPT = "pack-receipt.json"
# What the portable install must hold at its top (source/creator/CMakeLists.txt, WITH_INSTALL_PORTABLE).
EXECUTABLE = "blender"
LAUNCHER = "angelo"
DESKTOP_FILE = "angelo.desktop"
ICONS = ("angelo.svg", "angelo-symbolic.svg")
# xz: one fixed setting, so the archive's bytes depend only on its contents (the thread count and block size
# decide how xz splits the stream; both are pinned).
XZ_ARGS = ("xz", "--format=xz", "--check=crc64", "-9", "--threads=8", "--block-size=67108864", "--stdout")


class PackageError(Exception):
    pass


def package_name(identity=IDENTITY):
    """``Angelo-<version>-linux-x64``: the product name and version label of ``angelo_identity.json``."""
    data = json.loads(Path(identity).read_text(encoding="utf-8"))
    return "%s-%s-linux-%s" % (data["product"]["name"], data["version"]["label"], ARCH)


# -- the signed manifest (lane B: scripts/angelo/sign_linux_release.py; the updater verifies the same way) ---------

SIGNER = ROOT / "scripts/angelo/sign_linux_release.py"
MANIFEST_NAME = "angelo-release-manifest.json"
SIGNATURE_NAME = "angelo-release-manifest.sig"


def version_label(identity=IDENTITY):
    return json.loads(Path(identity).read_text(encoding="utf-8"))["version"]["label"]


def verify_tree(top, keys=None):
    """Why the signed manifest of ``top`` does not hold (empty: it verifies against the shipped Linux release keys, or
    ``keys`` for a rehearsal, and every file matches it): ``sign_linux_release.py check-tree``."""
    if not SIGNER.is_file():
        return ["%s is missing: this source cannot verify a signed Linux release" % SIGNER.relative_to(ROOT)]
    argv = [sys.executable, str(SIGNER), "check-tree", "--tree", str(top), "--version", version_label()]
    result = subprocess.run(argv + (["--keys", str(keys)] if keys else []), capture_output=True, text=True)
    if result.returncode == 0:
        return []
    return [line for line in (result.stderr + result.stdout).splitlines() if line.strip()] or [
        "sign_linux_release.py check-tree exited %d" % result.returncode]


def signature_problems(top, verify=verify_tree):
    """Why the staged ``top`` cannot ship as a signed release (empty: it can)."""
    top = Path(top)
    missing = [name for name in (MANIFEST_NAME, SIGNATURE_NAME) if not (top / name).is_file()]
    if missing:
        return ["%s is unsigned (%s missing): sign it with sign_linux_release.py manifest (doc/angelo/releasing.rst, "
                "\"Linux package\") or pass --unsigned for a development package" % (top.name, ", ".join(missing))]
    return verify(top)


# -- stage ---------------------------------------------------------------------------------------------------

def install_problems(install):
    """Why ``install`` is not a portable Linux install (empty: it is)."""
    install = Path(install)
    problems = []
    for name in (EXECUTABLE, LAUNCHER):
        path = install / name
        if not path.is_file() or not os.access(path, os.X_OK):
            problems.append("%s is missing or not executable" % path)
    for name in (DESKTOP_FILE,) + ICONS:
        if not (install / name).is_file():
            problems.append("%s is missing" % (install / name))
    if not (install / "lib").is_dir():
        problems.append("%s/lib (the bundled libraries) is missing" % install)
    return problems


def _ignore_runtime_bytecode(folder, names):
    """``shutil.copytree``'s ignore: bytecode Python wrote at run time (the build's own stays)."""
    ignored = set()
    if os.path.basename(folder) == "__pycache__":
        ignored = {name for name in names if not release_checks.is_build_bytecode(os.path.join(folder, name))}
    return ignored


def normalise_modes(top):
    """0755 for folders and owner-executable files, 0644 for every other file: no set-id bits and nothing group- or
    other-writable, exactly the modes the tar records. Runs before the manifest is signed, which lists each file's
    executable bit and refuses any other mode. Links keep their own (unused) modes."""
    for folder, dirs, files in os.walk(top):
        os.chmod(folder, 0o755)
        for name in files:
            path = os.path.join(folder, name)
            if not os.path.islink(path):
                os.chmod(path, 0o755 if os.lstat(path).st_mode & 0o100 else 0o644)


def _drop_empty_caches(top):
    for folder, dirs, files in os.walk(top, topdown=False):
        if os.path.basename(folder) == "__pycache__" and not os.listdir(folder):
            os.rmdir(folder)


def commit_info(source=ROOT):
    """(commit, commit time as a Unix time) of ``source``; ``SOURCE_DATE_EPOCH`` overrides the time."""
    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        epoch = subprocess.run(["git", "-C", str(source), "show", "-s", "--format=%ct", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
    return commit, int(epoch)


def stage(install, out, name=None, commit=None, epoch=None):
    """Copy the portable install ``install`` into ``out/<name>``; returns the receipt."""
    problems = install_problems(install)
    if problems:
        raise PackageError("not a portable Linux install:\n  " + "\n  ".join(problems))
    name = name or package_name()
    out = Path(out)
    top = out / name
    if top.exists():
        raise PackageError("%s exists: stage into an empty folder" % top)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(install, top, symlinks=True, ignore=_ignore_runtime_bytecode)
    _drop_empty_caches(top)
    normalise_modes(top)
    if commit is None or epoch is None:
        found_commit, found_epoch = commit_info()
        commit = commit or found_commit
        epoch = found_epoch if epoch is None else epoch
    receipt = {
        "package": name,
        "commit": commit,
        "source_date_epoch": epoch,
        "files": sum(len(files) for _folder, _dirs, files in os.walk(top)),
    }
    (out / STAGE_RECEIPT).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


# -- pack ----------------------------------------------------------------------------------------------------

def _entries(top):
    """Every path under ``top`` (``top`` itself first), sorted by its archive name, as (archive name, path)."""
    base = top.parent
    found = [top]
    for folder, dirs, files in os.walk(top):
        for entry in dirs + files:
            found.append(Path(folder) / entry)
    return sorted(((path.relative_to(base).as_posix(), path) for path in found), key=lambda item: item[0])


def _tar_info(arcname, path, epoch):
    info = tarfile.TarInfo(arcname)
    stat = path.lstat()
    info.mtime = epoch
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if path.is_symlink():
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(path)
        info.mode = 0o777
    elif path.is_dir():
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
    elif path.is_file():
        info.size = stat.st_size
        info.mode = 0o755 if stat.st_mode & 0o100 else 0o644
    else:
        raise PackageError("%s is neither a file, a folder nor a link: a package holds nothing else" % path)
    return info


def write_tar(top, stream, epoch):
    """Write the reproducible uncompressed tar of ``top`` to the binary ``stream``."""
    with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as archive:
        for arcname, path in _entries(Path(top)):
            info = _tar_info(arcname, path, epoch)
            if info.isreg():
                with open(path, "rb") as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)


def _xz_tar(top, tarball, epoch):
    """Write the reproducible ``tarball`` (.tar.xz) of the folder ``top``; returns its SHA-256."""
    partial = tarball.with_name(tarball.name + ".partial")
    with open(partial, "wb") as handle:
        xz = subprocess.Popen(XZ_ARGS, stdin=subprocess.PIPE, stdout=handle)
        try:
            write_tar(top, xz.stdin, epoch)
        finally:
            xz.stdin.close()
        if xz.wait() != 0:
            raise PackageError("xz failed with exit code %d" % xz.returncode)
    os.replace(partial, tarball)
    digest = _sha256(tarball)
    tarball.with_name(tarball.name + ".sha256").write_text("%s  %s\n" % (digest, tarball.name), encoding="utf-8")
    return digest


def pack(stage_dir, out, unsigned=False, verify=None, debug_symbols=None, keys=None):
    """Pack ``stage_dir`` (``stage``'s output) into ``out/<package>.tar.xz``; returns the receipt."""
    stage_dir, out = Path(stage_dir), Path(out)
    receipt = json.loads((stage_dir / STAGE_RECEIPT).read_text(encoding="utf-8"))
    top = stage_dir / receipt["package"]
    if not unsigned:
        problems = signature_problems(top, verify or (lambda tree: verify_tree(tree, keys)))
        if problems:
            raise PackageError("\n".join(problems))
    stray = sorted(entry.name for entry in stage_dir.iterdir() if entry.name not in (receipt["package"], STAGE_RECEIPT))
    if stray:
        raise PackageError("%s holds %s beside the package: stage into an empty folder" % (stage_dir, ", ".join(stray)))
    out.mkdir(parents=True, exist_ok=True)
    tarball = out / (receipt["package"] + ".tar.xz")
    digest = _xz_tar(top, tarball, receipt["source_date_epoch"])
    result = dict(receipt, tarball=tarball.name, sha256=digest, size=tarball.stat().st_size, signed=not unsigned)
    if debug_symbols:
        # The symbols under a top folder of their own name, so unpacking them never mixes with an install.
        with tempfile.TemporaryDirectory(prefix=".angelo-pack-", dir=str(out)) as scratch:
            symbols_top = Path(scratch) / (receipt["package"] + ".debug")
            shutil.copytree(debug_symbols, symbols_top, symlinks=True)
            debug_tarball = out / (receipt["package"] + ".debug.tar.xz")
            result["debug_symbols"] = {"tarball": debug_tarball.name,
                                       "sha256": _xz_tar(symbols_top, debug_tarball, receipt["source_date_epoch"])}
    (out / PACK_RECEIPT).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Angelo's Linux release package (tar.xz).")
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser("stage", help="copy a portable install into a package folder and list its files")
    staging.add_argument("--install", required=True, help="the build's portable install (<build>/install)")
    staging.add_argument("--out", required=True, help="an empty folder")
    packing = commands.add_parser("pack", help="make the reproducible tar.xz of a staged, signed package")
    packing.add_argument("--stage", required=True)
    packing.add_argument("--out", required=True)
    packing.add_argument("--unsigned", action="store_true", help="a development or CI package (the receipt says so)")
    packing.add_argument("--debug-symbols", help="the build's debug-symbols folder: also pack <package>.debug.tar.xz")
    packing.add_argument("--keys", help="a rehearsal's copy of linux_release_keys.json holding its own key")
    commands.add_parser("check", add_help=False, help="the package's release checks (linux_package_checks.py)")
    commands.add_parser("name", help="print the package name this source builds")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["check"]:
        import linux_package_checks  # noqa: E402  (beside this file)
        return linux_package_checks.main(argv[1:])
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "name":
            print(package_name())
        elif arguments.command == "stage":
            receipt = stage(arguments.install, arguments.out)
            print("staged %s (%d files); sign it with sign_linux_release.py manifest --tree %s"
                  % (receipt["package"], receipt["files"], Path(arguments.out) / receipt["package"]))
        else:
            receipt = pack(arguments.stage, arguments.out, unsigned=arguments.unsigned,
                           debug_symbols=arguments.debug_symbols, keys=arguments.keys)
            print("packed %s (%d bytes, sha256 %s, %s)" % (receipt["tarball"], receipt["size"], receipt["sha256"],
                                                          "signed" if receipt["signed"] else "UNSIGNED"))
    except (PackageError, OSError, subprocess.CalledProcessError) as error:
        print("linux package: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
