#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The GPL source release of one Angelo version: what the website offers next to its installers.

Runbook: doc/angelo/releasing.rst, "Source code". For a release tag (``v`` + ``version.label`` of the
identity at that commit), from the repository root::

    python3 scripts/angelo/source_release.py archive --tag v1.0.0 --out dist/source
    python3 scripts/angelo/source_release.py list --rev HEAD             # dry run: what would go in, no archive
    python3 scripts/angelo/source_release.py fetch-deps --tag v1.0.0 --out dist/deps   # needs the network
    python3 scripts/angelo/source_release.py archive --tag v1.0.0 --out dist/source --dependency-sources dist/deps

``archive`` writes, for version V:

* ``Angelo-V-source.tar.xz``: every file tracked at the tag (Angelo, the Blender source it is built on, the
  build, packaging and release scripts, the dependency build system with its patches) except Blender's
  regression-test data in ``tests/files``, which upstream's source archive also leaves out, under
  ``angelo-V/``, with Git LFS content (icons, fonts, startup files, brushes) taken from this computer's LFS
  store, never the network. It is reproducible: entries are sorted, owned by 0:0 and dated at the commit.
* ``Angelo-V-dependencies.json``: the pinned precompiled library submodules (``lib/*`` commits) and every
  third-party library of ``build_files/build_environment/cmake/versions.cmake`` with its version, license,
  upstream and Blender-mirror download, file name and checksum.
* ``Angelo-V-dependency-sources.tar`` with ``--dependency-sources``: those library source files, each checked
  against its checksum (``fetch-deps`` downloads them from Blender's mirror).
* ``Angelo-V-source-offer.txt``: ``release/text/source_offer.txt`` filled in, the text the installers carry.
* ``SHA256SUMS`` of all of them.

Standard library only. ``GIT_LFS_SKIP_SMUDGE=1`` is set for every Git call; nothing is uploaded.
"""

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_checks  # noqa: E402

VERSIONS = "build_files/build_environment/cmake/versions.cmake"
OFFER = "release/text/source_offer.txt"
MIRROR = "https://projects.blender.org/blender/lib-source/media/branch/blender-v{series}-release/{file}"
LFS_OID = re.compile(rb"oid sha256:([0-9a-f]{64})\nsize (\d+)\n")
# Blender's regression-test data: not used to build or run, and left out of upstream's source archive too
# (build_files/utils/make_source_archive.py). It is the only LFS content most checkouts do not hold.
EXCLUDED = ("tests/files/",)
HASHES = {"MD5": hashlib.md5, "SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


class SourceError(Exception):
    pass


def git(*arguments, data=False):
    result = subprocess.run(["git", "-C", str(ROOT)] + list(arguments), capture_output=True,
                            env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
    if result.returncode != 0:
        raise SourceError("git %s: %s" % (" ".join(arguments[:3]), result.stderr.decode(errors="replace").strip()))
    return result.stdout if data else result.stdout.decode("utf-8")


# -- what is released --------------------------------------------------------------------------------------

def resolve(tag=None, rev=None):
    """``(commit, label, identity, is_release)`` for a release tag, or for any revision as a dry run."""
    if bool(tag) == bool(rev):
        raise SourceError("give exactly one of --tag (a release) or --rev (a dry run)")
    name = "refs/tags/" + tag if tag else rev
    commit = git("rev-parse", "--verify", name + "^{commit}").strip()
    identity = json.loads(git("show", commit + ":scripts/modules/angelo_identity.json"))
    version = identity["version"]["label"]
    if tag and tag != "v" + version:
        raise SourceError("tag %s does not match the identity version %s at %s (expected v%s)"
                          % (tag, version, commit[:12], version))
    return commit, tag or rev, identity, bool(tag)


def blender_version(commit):
    header = git("show", commit + ":source/blender/blenkernel/BKE_blender_version.h")
    numbers = dict(re.findall(r"#define (BLENDER_VERSION(?:_PATCH)?)\s+(\d+)", header))
    full = int(numbers["BLENDER_VERSION"])
    return "%d.%d" % (full // 100, full % 100), "%d.%d.%s" % (full // 100, full % 100, numbers["BLENDER_VERSION_PATCH"])


def tree(commit):
    """``[(mode, kind, object, size, path)]`` of every entry at ``commit``; a submodule is a ``commit``."""
    entries = []
    for record in git("ls-tree", "-r", "-l", "-z", "--full-tree", commit, data=True).split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            mode, kind, obj, size = meta.decode().split()
            entries.append((mode, kind, obj, int(size) if size != "-" else 0,
                            path.decode("utf-8", "surrogateescape")))
    return entries


def lfs_store():
    common = Path(git("rev-parse", "--git-common-dir").strip())
    return (common if common.is_absolute() else ROOT / common) / "lfs/objects"


def lfs_object(pointer, store):
    """The local LFS object a pointer names, checked against its size and SHA-256; None when absent."""
    match = LFS_OID.search(pointer)
    oid, size = match.group(1).decode(), int(match.group(2))
    path = store / oid[:2] / oid[2:4] / oid
    if not path.is_file() or path.stat().st_size != size:
        return None, oid
    if hashlib.sha256(path.read_bytes()).hexdigest() != oid:
        raise SourceError("the local LFS object %s is corrupt" % oid)
    return path, oid


class Blobs:
    """``git cat-file --batch`` for reading thousands of blobs with one process."""

    def __init__(self):
        self.process = subprocess.Popen(["git", "-C", str(ROOT), "cat-file", "--batch"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))

    def read(self, obj):
        self.process.stdin.write(obj.encode() + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if len(header) != 3:
            raise SourceError("git cat-file: %r" % header)
        data = self.process.stdout.read(int(header[2]))
        self.process.stdout.read(1)
        return data

    def close(self):
        self.process.stdin.close()
        self.process.wait()


def plan(commit):
    """What the archive holds, without reading large blobs: ``files`` are ``(path, executable, source, size)``
    where ``source`` is a blob id (``str``), small content (``bytes``) or a local LFS object (``Path``)."""
    store, blobs = lfs_store(), Blobs()
    files, links, submodules, missing, lfs_count = [], [], [], [], 0
    try:
        for mode, kind, obj, size, path in tree(commit):
            if path.startswith(EXCLUDED):
                continue
            if kind == "commit":
                submodules.append({"path": path, "commit": obj})
            elif mode == "120000":
                links.append((path, blobs.read(obj).decode("utf-8", "surrogateescape")))
            elif size < 1024:  # Only a small blob can be a Git LFS pointer.
                data = blobs.read(obj)
                source = data
                if release_checks.is_lfs_pointer(data) and LFS_OID.search(data):
                    lfs_count += 1
                    local, oid = lfs_object(data, store)
                    if local is None:
                        missing.append({"path": path, "oid": oid})
                    else:
                        source, size = local, local.stat().st_size
                files.append((path, mode == "100755", source, size))
            else:
                files.append((path, mode == "100755", obj, size))
    finally:
        blobs.close()
    return {"files": files, "links": links, "submodules": submodules, "lfs_files": lfs_count,
            "lfs_missing": missing}


# -- the dependency manifest ----------------------------------------------------------------------------------

def cmake_sets(text):
    """``{name: value}`` from versions.cmake's ``set()`` calls, with ``${VAR}`` expanded in order."""
    values = {}
    for match in re.finditer(r"^\s*set\((\w+)\s+(\[=\[.*?\]=\]|\"[^\"]*\"|[^\s)]+)[^)]*\)", text, re.S | re.M):
        name, value = match.group(1), match.group(2)
        if value.startswith("[=["):
            value = value[3:-3].strip()
        value = value.strip('"')
        values[name] = re.sub(r"\$\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), value)
    return values


def dependencies(commit, series):
    values = cmake_sets(git("show", "%s:%s" % (commit, VERSIONS)))
    libraries = []
    for key in sorted(k[:-4] for k in values if k.endswith("_URI")):
        if key + "_FILE" not in values:
            continue
        file = values[key + "_FILE"]
        libraries.append({
            "id": key, "name": values.get(key + "_NAME", key.title()), "version": values.get(key + "_VERSION"),
            "license": values.get(key + "_LICENSE", "").replace("SPDX:", ""),
            "homepage": values.get(key + "_HOMEPAGE"), "file": file,
            "hash": {"type": values.get(key + "_HASH_TYPE", ""), "value": values.get(key + "_HASH", "")},
            "upstream_url": values[key + "_URI"], "mirror_url": MIRROR.format(series=series, file=file),
            "build_time_only": values.get(key + "_DEPSBUILDTIMEONLY") or None})
    toolchain = {k[len("RELEASE_"):].lower(): v for k, v in values.items() if k.startswith("RELEASE_")}
    return libraries, toolchain


def manifest(commit, label, identity, contents, is_release):
    series, full = blender_version(commit)
    modules = git("show", commit + ":.gitmodules")
    urls = dict(re.findall(r"path = (\S+)\n\s+url = (\S+)", modules))
    libraries, toolchain = dependencies(commit, series)
    return {
        "schema": 1, "product": identity["product"]["name"], "version": identity["version"]["label"],
        "release": is_release, "revision": label, "commit": commit, "based_on": "Blender " + full,
        "precompiled_libraries": [dict(s, url=urls.get(s["path"])) for s in contents["submodules"]],
        "release_toolchain": toolchain, "libraries": libraries,
        "lfs": {"files": contents["lfs_files"], "missing": contents["lfs_missing"]},
        "excluded": [path + " (regression-test data, as Blender's own source archive)" for path in EXCLUDED],
        "note": ("lib/* are Blender's precompiled libraries at the pinned commits; they are built from the "
                 "library sources listed here with build_files/build_environment in the source archive."),
    }


# -- outputs ---------------------------------------------------------------------------------------------------

def offer_text(identity, blender_full, template):
    """``release/text/source_offer.txt`` with the identity's values, as CMake's configure_file fills it."""
    try:
        values = {"ANGELO_PRODUCT_NAME": identity["product"]["name"],
                  "ANGELO_APP_VERSION": identity["version"]["label"],
                  "ANGELO_BASED_ON_NAME": identity["product"]["based_on"], "ANGELO_BLENDER_BASE_VERSION": blender_full,
                  "ANGELO_URL_SOURCE": identity["urls"]["source"], "ANGELO_VENDOR_NAME": identity["vendor"]["name"],
                  "ANGELO_CONTACT_EMAIL": identity["vendor"]["contact_email"]}
    except KeyError as error:
        raise SourceError("the identity has no %s, which the source offer needs" % error) from None
    unknown = sorted(set(re.findall(r"@(\w+)@", template)) - set(values))
    if unknown:
        raise SourceError("source_offer.txt uses %s, which this tool does not fill" % ", ".join(unknown))
    return re.sub(r"@(\w+)@", lambda m: values[m.group(1)], template)


def write_archive(path, prefix, contents, mtime, info_json, compression):
    """A reproducible tar: sorted entries, owner 0:0, every date the commit's, no host names or paths."""
    blobs = Blobs()
    with contextlib.ExitStack() as stack:
        raw = stack.enter_context(open(path, "wb"))
        stream = stack.enter_context(lzma.open(raw, "wb", preset=6) if compression == "xz"
                                     else gzip.GzipFile(fileobj=raw, mode="wb", mtime=mtime, compresslevel=6))
        archive = stack.enter_context(tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT))
        stack.callback(blobs.close)

        def entry(name):
            info = tarfile.TarInfo(prefix + "/" + name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = mtime, 0, 0, "", ""
            return info

        records = list(contents["files"]) + [("ANGELO_SOURCE.json", False, info_json, len(info_json))]
        for name, executable, source, size in sorted(records, key=lambda record: record[0]):
            info = entry(name)
            info.size, info.mode = size, 0o755 if executable else 0o644
            if isinstance(source, Path):
                with open(source, "rb") as handle:
                    archive.addfile(info, handle)
            else:
                data = source if isinstance(source, bytes) else blobs.read(source)
                archive.addfile(info, io.BytesIO(data))
        for name, target in sorted(contents["links"]):
            info = entry(name)
            info.type, info.linkname, info.mode = tarfile.SYMTYPE, target, 0o777
            archive.addfile(info)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_dependency_file(path, library):
    algorithm = HASHES.get(library["hash"]["type"].upper())
    if algorithm is None:
        raise SourceError("%s: unknown hash type %s" % (library["id"], library["hash"]["type"]))
    digest = algorithm()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest().lower() != library["hash"]["value"].lower():
        raise SourceError("%s does not match its %s checksum in versions.cmake" % (path, library["hash"]["type"]))


def dependency_archive(path, folder, libraries):
    """The library sources from ``folder``, each checked; refuses a missing or altered file."""
    missing = [lib["file"] for lib in libraries if not (Path(folder) / lib["file"]).is_file()]
    if missing:
        raise SourceError("%d library sources are missing from %s (fetch-deps downloads them): %s"
                          % (len(missing), folder, ", ".join(missing[:8])))
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        for library in libraries:
            source = Path(folder) / library["file"]
            check_dependency_file(source, library)
            info = archive.gettarinfo(str(source), arcname="dependency-sources/" + library["file"])
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(source, "rb") as handle:
                archive.addfile(info, handle)


def archive_command(arguments):
    commit, label, identity, is_release = resolve(arguments.tag, arguments.rev)
    contents = plan(commit)
    if contents["lfs_missing"] and not arguments.allow_missing_lfs:
        raise SourceError("%d Git LFS files have no local object (git lfs fetch, then run again): %s" % (
            len(contents["lfs_missing"]), ", ".join(m["path"] for m in contents["lfs_missing"][:8])))
    version, series_full = identity["version"]["label"], blender_version(commit)[1]
    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (identity["product"]["name"], version) + ("" if is_release else "-" + commit[:12])
    info = manifest(commit, label, identity, contents, is_release)
    info_json = (json.dumps(info, indent=2) + "\n").encode("utf-8")
    names = []
    for name, data in ((stem + "-dependencies.json", info_json),
                       (stem + "-source-offer.txt", offer_text(identity, series_full, git(
                           "show", "%s:%s" % (commit, OFFER))).encode("utf-8"))):
        (out / name).write_bytes(data)
        names.append(name)
    compression = arguments.format.rsplit(".", 1)[1]
    archive = out / ("%s-source.%s" % (stem, arguments.format))
    mtime = int(git("show", "-s", "--format=%ct", commit).strip())
    write_archive(archive, "%s-%s" % (identity["product"]["slug"], version), contents, mtime, info_json, compression)
    names.append(archive.name)
    if arguments.dependency_sources:
        target = out / (stem + "-dependency-sources.tar")
        dependency_archive(target, arguments.dependency_sources, [l for l in info["libraries"]
                                                                   if not l["build_time_only"]])
        names.append(target.name)
    sums = "".join("%s  %s\n" % (sha256_file(out / name), name) for name in sorted(names))
    (out / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return {"out": str(out), "files": sorted(names), "commit": commit, "release": is_release,
            "archive_bytes": archive.stat().st_size, "tracked_files": len(contents["files"]),
            "lfs_files": contents["lfs_files"], "lfs_missing": len(contents["lfs_missing"])}


def list_command(arguments):
    commit, label, identity, is_release = resolve(arguments.tag, arguments.rev)
    contents = plan(commit)
    info = manifest(commit, label, identity, contents, is_release)
    top = {}
    for path, _executable, _data, size in contents["files"]:
        top[path.split("/", 1)[0]] = top.get(path.split("/", 1)[0], 0) + size
    return {"commit": commit, "release": is_release, "version": info["version"], "based_on": info["based_on"],
            "files": len(contents["files"]), "links": len(contents["links"]),
            "bytes": sum(f[3] for f in contents["files"]), "lfs_files": contents["lfs_files"],
            "lfs_missing": contents["lfs_missing"][:20], "lfs_missing_count": len(contents["lfs_missing"]),
            "submodules": info["precompiled_libraries"], "libraries": len(info["libraries"]),
            "top_level_bytes": dict(sorted(top.items(), key=lambda item: -item[1]))}


def fetch_command(arguments, opener=urllib.request.urlopen):
    """Download every shipped library's source from Blender's mirror into ``--out``, checking each checksum."""
    commit, label, identity, is_release = resolve(arguments.tag, arguments.rev)
    libraries = [l for l in dependencies(commit, blender_version(commit)[0])[0] if not l["build_time_only"]]
    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    fetched = 0
    for library in libraries:
        target = out / library["file"]
        if not target.is_file():
            partial = target.with_name(target.name + ".part")
            with opener(library["mirror_url"], timeout=1800) as response, open(partial, "wb") as handle:
                for block in iter(lambda: response.read(1 << 20), b""):
                    handle.write(block)
            partial.replace(target)
            fetched += 1
        check_dependency_file(target, library)
    return {"out": str(out), "libraries": len(libraries), "downloaded": fetched}


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("archive", "list", "fetch-deps"):
        command = commands.add_parser(name)
        command.add_argument("--tag", help="the release tag, v<version>")
        command.add_argument("--rev", help="any revision, for a dry run (the outputs carry its commit)")
        if name != "list":
            command.add_argument("--out", required=True)
        if name == "archive":
            command.add_argument("--format", choices=("tar.xz", "tar.gz"), default="tar.xz")
            command.add_argument("--dependency-sources", help="a folder of library sources (fetch-deps)")
            command.add_argument("--allow-missing-lfs", action="store_true",
                                 help="keep pointers for LFS files with no local object (dry runs only)")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "archive":
            if arguments.tag and arguments.allow_missing_lfs:
                raise SourceError("a release archive needs every LFS file; --allow-missing-lfs is for dry runs")
            result = archive_command(arguments)
        elif arguments.command == "list":
            result = list_command(arguments)
        else:
            result = fetch_command(arguments)
    except (SourceError, release_checks.CheckError, OSError, KeyError, ValueError) as error:
        print("source_release: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
