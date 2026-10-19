# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The GPL source release (scripts/angelo/source_release.py) and the written offer the installers carry.

Dependency-free apart from Git. A small throwaway repository stands in for the real one: a release tag, a
Git LFS pointer with its object in the local store, a symbolic link, an executable, a library submodule and
Blender's test data. Covered: the tag must be ``v`` plus the identity version at that commit; LFS content
comes from the local store and a missing object refuses a release; ``tests/files`` is left out as upstream's
archive does; entries are reproducible (same bytes twice); the manifest names the submodule commits and every
library with its checksum and download; library sources are checked against their checksums; and the offer
text is rendered completely, by this tool and from the variables CMake defines. The real repository's
``versions.cmake`` parses into the manifest. ``doc/angelo/releasing.rst`` has the dry run on the real tree.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import source_release as tool  # noqa: E402

VERSIONS = """
set(ZLIB_VERSION 1.3.1)
set(ZLIB_URI https://github.com/madler/zlib/releases/download/v${ZLIB_VERSION}/zlib-${ZLIB_VERSION}.tar.gz)
set(ZLIB_HASH %s)
set(ZLIB_HASH_TYPE MD5)
set(ZLIB_FILE zlib-${ZLIB_VERSION}.tar.gz)
set(ZLIB_LICENSE SPDX:Zlib)
set(ZLIB_COPYRIGHT [=[
Copyright (c) 1995 (with a parenthesis) Jean-loup Gailly.
]=])
set(NASM_VERSION 2.16)
set(NASM_URI https://www.nasm.us/pub/nasm/releasebuilds/${NASM_VERSION}/nasm-${NASM_VERSION}.tar.xz)
set(NASM_HASH abc)
set(NASM_HASH_TYPE SHA256)
set(NASM_FILE nasm-${NASM_VERSION}.tar.xz)
set(NASM_DEPSBUILDTIMEONLY "Blender ships the produced artifact")
set(RELEASE_GCC_VERSION 14.2)
"""
ZLIB_SOURCE = b"zlib source bytes"
LIB_COMMIT = "a76ef917b4849ba2b1b1deb1a643e131a884a63b"


def git(repo, *arguments):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"] + list(arguments),
                          check=True, capture_output=True, text=True).stdout


class RepoCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        repo = self.repo = self.folder / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        for relative in ("scripts/modules/angelo_identity.json", "source/blender/blenkernel/BKE_blender_version.h",
                         tool.OFFER):
            (repo / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, repo / relative)
        files = {
            tool.VERSIONS: VERSIONS % hashlib.md5(ZLIB_SOURCE).hexdigest(),
            ".gitmodules": '[submodule "lib/macos_arm64"]\n\tpath = lib/macos_arm64\n'
                           "\turl = https://projects.blender.org/blender/lib-macos_arm64.git\n",
            "source/main.c": "int main(void) { return 0; }\n",
            "tests/files/big.blend": "regression data\n",
        }
        for relative, text in files.items():
            (repo / relative).parent.mkdir(parents=True, exist_ok=True)
            (repo / relative).write_text(text)
        self.asset = b"\x89PNG real splash bytes"
        oid = hashlib.sha256(self.asset).hexdigest()
        pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:%s\nsize %d\n" % (oid, len(self.asset))
        (repo / "release/datafiles").mkdir(parents=True)
        (repo / "release/datafiles/splash.png").write_text(pointer)
        store = repo / ".git/lfs/objects" / oid[:2] / oid[2:4]
        store.mkdir(parents=True)
        (store / oid).write_bytes(self.asset)
        self.store_object = store / oid
        build = repo / "build.sh"
        build.write_text("#!/bin/sh\n")
        build.chmod(0o755)
        os.symlink("main.c", repo / "source/link.c")
        git(repo, "add", "-A")
        git(repo, "update-index", "--add", "--cacheinfo", "160000,%s,lib/macos_arm64" % LIB_COMMIT)
        git(repo, "commit", "-q", "-m", "release")
        self.version = json.loads((repo / "scripts/modules/angelo_identity.json").read_text())["version"]["label"]
        git(repo, "tag", "v" + self.version)
        git(repo, "tag", "v9.9.9")
        self.saved_root, tool.ROOT = tool.ROOT, repo
        self.addCleanup(setattr, tool, "ROOT", self.saved_root)

    def run_tool(self, *argv):
        out, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(errors):
            code = tool.main(list(argv), out=out)
        return code, out.getvalue(), errors.getvalue()


class ArchiveTests(RepoCase):
    def test_a_release_archive_is_complete_reproducible_and_summed(self):
        out = self.folder / "out"
        code, text, errors = self.run_tool("archive", "--tag", "v" + self.version, "--out", str(out), "--format", "tar.gz")
        self.assertEqual(code, 0, errors)
        stem = "Angelo-%s" % self.version
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, sorted([stem + "-source.tar.gz", stem + "-dependencies.json",
                                        stem + "-source-offer.txt", "SHA256SUMS"]))
        for line in (out / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), digest)
        prefix = "angelo-%s/" % self.version
        with tarfile.open(out / (stem + "-source.tar.gz")) as archive:
            members = {m.name[len(prefix):]: m for m in archive.getmembers()}
            self.assertNotIn("tests/files/big.blend", members, "regression-test data is left out")
            self.assertEqual(archive.extractfile(members["release/datafiles/splash.png"]).read(), self.asset)
            self.assertEqual(members["build.sh"].mode, 0o755)
            self.assertEqual(members["source/main.c"].mode, 0o644)
            self.assertTrue(members["source/link.c"].issym())
            self.assertEqual(members["source/link.c"].linkname, "main.c")
            self.assertEqual({(m.uid, m.gid, m.uname) for m in members.values()}, {(0, 0, "")})
            self.assertEqual(len({m.mtime for m in members.values()}), 1, "dated at the commit")
            embedded = json.loads(archive.extractfile(members["ANGELO_SOURCE.json"]).read())
        manifest = json.loads((out / (stem + "-dependencies.json")).read_text())
        self.assertEqual(embedded, manifest)
        self.assertTrue(manifest["release"])
        self.assertEqual(manifest["precompiled_libraries"], [{
            "path": "lib/macos_arm64", "commit": LIB_COMMIT, "url": "https://projects.blender.org/blender/lib-macos_arm64.git"}])
        zlib = next(lib for lib in manifest["libraries"] if lib["id"] == "ZLIB")
        self.assertEqual((zlib["version"], zlib["file"], zlib["license"]), ("1.3.1", "zlib-1.3.1.tar.gz", "Zlib"))
        self.assertEqual(zlib["upstream_url"], "https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz")
        self.assertTrue(zlib["mirror_url"].endswith("/blender-v5.2-release/zlib-1.3.1.tar.gz"))
        self.assertIsNone(zlib["build_time_only"])
        self.assertTrue(next(lib for lib in manifest["libraries"] if lib["id"] == "NASM")["build_time_only"])
        self.assertEqual(manifest["release_toolchain"], {"gcc_version": "14.2"})
        first = (out / (stem + "-source.tar.gz")).read_bytes()
        again = self.folder / "again"
        self.assertEqual(self.run_tool("archive", "--tag", "v" + self.version, "--out", str(again), "--format",
                                       "tar.gz")[0], 0)
        self.assertEqual((again / (stem + "-source.tar.gz")).read_bytes(), first, "byte-for-byte reproducible")

    def test_refusals(self):
        code, _text, errors = self.run_tool("archive", "--tag", "v9.9.9", "--out", str(self.folder / "x"))
        self.assertEqual(code, 1)
        self.assertIn("does not match the identity version", errors)
        self.assertEqual(self.run_tool("archive", "--out", str(self.folder / "x"))[0], 1, "a tag or a revision")
        self.store_object.unlink()
        code, _text, errors = self.run_tool("archive", "--tag", "v" + self.version, "--out", str(self.folder / "x"))
        self.assertEqual(code, 1)
        self.assertIn("release/datafiles/splash.png", errors)
        code, _text, errors = self.run_tool("archive", "--tag", "v" + self.version, "--allow-missing-lfs",
                                            "--out", str(self.folder / "x"))
        self.assertIn("for dry runs", errors)
        code, text, errors = self.run_tool("archive", "--rev", "HEAD", "--allow-missing-lfs", "--out",
                                           str(self.folder / "dry"), "--format", "tar.gz")
        self.assertEqual(code, 0, errors)
        self.assertFalse(json.loads(text)["release"])
        self.assertEqual(json.loads(text)["lfs_missing"], 1)

    def test_library_sources_are_checked_against_their_checksums(self):
        sources = self.folder / "deps"
        sources.mkdir()
        argv = ("archive", "--tag", "v" + self.version, "--out", str(self.folder / "out"), "--format", "tar.gz",
                "--dependency-sources", str(sources))
        self.assertIn("missing", self.run_tool(*argv)[2])
        (sources / "zlib-1.3.1.tar.gz").write_bytes(b"altered")
        self.assertIn("does not match", self.run_tool(*argv)[2])
        (sources / "zlib-1.3.1.tar.gz").write_bytes(ZLIB_SOURCE)
        shutil.rmtree(self.folder / "out")
        code, _text, errors = self.run_tool(*argv)
        self.assertEqual(code, 0, errors)
        with tarfile.open(self.folder / "out" / ("Angelo-%s-dependency-sources.tar" % self.version)) as archive:
            self.assertEqual(archive.getnames(), ["dependency-sources/zlib-1.3.1.tar.gz"], "build-time-only tools stay out")

    def test_fetch_checks_what_it_downloads(self):
        served = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(url, timeout):
            served[url] = timeout
            return Response(ZLIB_SOURCE)

        arguments = type("A", (), {"tag": "v" + self.version, "rev": None, "out": str(self.folder / "deps")})
        result = tool.fetch_command(arguments, opener=opener)
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(list(served), ["https://projects.blender.org/blender/lib-source/media/branch/"
                                        "blender-v5.2-release/zlib-1.3.1.tar.gz"])


class OfferAndManifestTests(unittest.TestCase):
    def test_the_offer_is_complete_and_every_placeholder_is_a_cmake_variable(self):
        template = (ROOT / tool.OFFER).read_text(encoding="utf-8")
        identity = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))
        loader = (ROOT / "build_files/cmake/angelo_identity.cmake").read_text(encoding="utf-8")
        for name in set(re.findall(r"@(\w+)@", template)):
            defined = (name.startswith("ANGELO_URL_") and name[len("ANGELO_URL_"):].lower() in identity["urls"]) or \
                re.search(r"(_angelo_get|set)\(%s\b" % name, loader)
            self.assertTrue(defined, "%s is not defined by angelo_identity.cmake" % name)
        text = tool.offer_text(identity, "5.2.2", template)
        self.assertNotRegex(text, r"@\w+@")
        for needed in (identity["urls"]["source"], identity["vendor"]["contact_email"], identity["vendor"]["name"],
                       "three years", "Angelo %s" % identity["version"]["label"], "Blender 5.2.2"):
            self.assertIn(needed, text)
        with self.assertRaises(tool.SourceError):
            tool.offer_text(identity, "5.2.2", template + "@ANGELO_SOMETHING_NEW@")
        self.assertIn("source_offer.txt", (ROOT / "release/text/copyright.txt").read_text(encoding="utf-8"))
        cmake = (ROOT / "source/creator/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("${CMAKE_BINARY_DIR}/release/text/source_offer.txt\n  )", cmake, "both installers carry it")

    def test_the_real_dependency_list_parses(self):
        libraries, toolchain = tool.dependencies("HEAD", "5.2")
        self.assertGreater(len(libraries), 100)
        for library in libraries:
            self.assertTrue(library["version"] and library["file"] and library["hash"]["value"], library["id"])
            self.assertIn(library["hash"]["type"].upper(), tool.HASHES, library["id"])
            self.assertNotIn("${", library["upstream_url"] + library["file"], library["id"])
        self.assertIn("gcc_version", toolchain)


if __name__ == "__main__":
    unittest.main()
