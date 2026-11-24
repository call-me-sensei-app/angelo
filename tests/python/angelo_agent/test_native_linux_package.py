# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

"""The Linux release package (scripts/angelo/linux_package.py, linux_package_checks.py, and their use of elf_info.py;
doc/angelo/releasing.rst, "Linux package"): the ELF reader, the glibc floor and search-path checks, staging,
the reproducible tar.xz, and the launcher and desktop files the install ships."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import hashlib
import json
import os
import shutil
import struct
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import elf_info  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
import linux_package  # noqa: E402
import linux_package_checks  # noqa: E402


POSIX_ONLY = unittest.skipUnless(os.name == "posix", environment_skip(
    "the Linux package keeps symbolic links and executable bits, which this host's file system cannot hold"))
NEEDS_XZ = unittest.skipUnless(shutil.which("xz"), environment_skip("the xz command packs the tar.xz"))


DT_NULL, DT_NEEDED, DT_RPATH, DT_RUNPATH = 0, 1, 15, 29
SHT_DYNAMIC, SHT_GNU_VERNEED = 6, 0x6FFFFFFE


def make_elf(needed=(), runpath="", rpath="", version_needs=None, elf_type=3):
    """A minimal 64-bit little-endian ELF file with a .dynamic and a .gnu.version_r section (no code)."""
    version_needs = version_needs or {}
    strings = bytearray(b"\0")

    def add(text):
        offset = len(strings)
        strings.extend(text.encode() + b"\0")
        return offset

    dynamic = bytearray()
    for name in needed:
        dynamic += struct.pack("<qQ", DT_NEEDED, add(name))
    if rpath:
        dynamic += struct.pack("<qQ", DT_RPATH, add(rpath))
    if runpath:
        dynamic += struct.pack("<qQ", DT_RUNPATH, add(runpath))
    dynamic += struct.pack("<qQ", DT_NULL, 0)
    verneed = bytearray()
    files = list(version_needs.items())
    for index, (file_name, versions) in enumerate(files):
        aux = bytearray()
        for vindex, version in enumerate(versions):
            aux += struct.pack("<IHHII", 0, 0, 2 + vindex, add(version), 16 if vindex + 1 < len(versions) else 0)
        size = 16 + len(aux)
        verneed += struct.pack("<HHIII", 1, len(versions), add(file_name), 16, size if index + 1 < len(files) else 0)
        verneed += aux
    header_size = 64
    dyn_off = header_size
    ver_off = dyn_off + len(dynamic)
    str_off = ver_off + len(verneed)
    sh_off = str_off + len(strings)
    sections = [
        struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        struct.pack("<IIQQQQIIQQ", 0, SHT_DYNAMIC, 0, 0, dyn_off, len(dynamic), 3, 0, 8, 16),
        struct.pack("<IIQQQQIIQQ", 0, SHT_GNU_VERNEED, 0, 0, ver_off, len(verneed), 3, len(files), 8, 0),
        struct.pack("<IIQQQQIIQQ", 0, 3, 0, 0, str_off, len(strings), 0, 0, 1, 0),
    ]
    ident = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    header = ident + struct.pack("<HHIQQQIHHHHHH", elf_type, 62, 1, 0, 0, sh_off, 0, 64, 0, 0, 64, len(sections), 3)
    return bytes(header) + bytes(dynamic) + bytes(verneed) + bytes(strings) + b"".join(sections)


GOOD_ELF = make_elf(needed=["libc.so.6", "libm.so.6"], runpath="$ORIGIN/lib",
                    version_needs={"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.28"], "libm.so.6": ["GLIBC_2.27"]})


class FakeSigner:
    """Stands in for lane B's sign_linux_release.py: ``sign`` lists every file with its SHA-256 and writes a
    signature; ``verify`` (check-tree) refuses a tree whose files differ from the list or whose signature is wrong."""

    @staticmethod
    def _files(top):
        listed = {}
        for folder, _dirs, files in os.walk(top):
            for name in files:
                path = Path(folder) / name
                rel = path.relative_to(top).as_posix()
                if rel in (linux_package.MANIFEST_NAME, linux_package.SIGNATURE_NAME) or path.is_symlink():
                    continue
                listed[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return listed

    @classmethod
    def sign(cls, top):
        manifest = json.dumps(cls._files(top), sort_keys=True).encode()
        (Path(top) / linux_package.MANIFEST_NAME).write_bytes(manifest)
        (Path(top) / linux_package.SIGNATURE_NAME).write_text(hashlib.sha256(manifest).hexdigest())

    @classmethod
    def verify(cls, top):
        manifest = (Path(top) / linux_package.MANIFEST_NAME).read_bytes()
        if (Path(top) / linux_package.SIGNATURE_NAME).read_text() != hashlib.sha256(manifest).hexdigest():
            return ["the signature does not verify"]
        return [] if json.loads(manifest) == cls._files(top) else ["the files differ from the manifest"]


def write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    os.chmod(path, mode)
    return path


def hash_pyc(path, hash_based):
    flags = 1 if hash_based else 0
    write(path, b"\x00\x00\r\n" + flags.to_bytes(4, "little") + bytes(8))


def fake_install(folder):
    """A portable Linux install as the CMake install writes it (WITH_INSTALL_PORTABLE)."""
    folder = Path(folder)
    write(folder / "blender", GOOD_ELF, 0o755)
    write(folder / "angelo", "#!/bin/sh\nexec \"$(dirname \"$0\")/blender\" \"$@\"\n", 0o755)
    write(folder / "angelo.desktop", "[Desktop Entry]\nName=Angelo\nExec=blender %f\nIcon=angelo\n")
    write(folder / "angelo.svg", "<svg/>")
    write(folder / "angelo-symbolic.svg", "<svg/>")
    write(folder / "lib/libfoo.so.1", make_elf(needed=["libc.so.6"], runpath="$ORIGIN",
                                                version_needs={"libc.so.6": ["GLIBC_2.17"]}))
    os.symlink("libfoo.so.1", folder / "lib/libfoo.so")
    write(folder / "5.3/scripts/startup/mod.py", "x = 1\n")
    hash_pyc(folder / "5.3/scripts/startup/__pycache__/mod.cpython-313.pyc", hash_based=True)
    hash_pyc(folder / "5.3/scripts/startup/__pycache__/other.cpython-313.pyc", hash_based=False)
    hash_pyc(folder / "5.3/scripts/modules/__pycache__/gone.cpython-313.pyc", hash_based=False)
    return folder


class ElfReader(unittest.TestCase):
    """The package checks read ELF files through elf_info.py (lane C's reader, the one every Linux check uses)."""

    def test_reads_needed_search_paths_and_versions(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = write(Path(scratch) / "a.so", make_elf(
                needed=["libc.so.6", "libstdc++.so.6"], rpath="/home/builder/lib", runpath="$ORIGIN/lib:$ORIGIN/..",
                version_needs={"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.34"], "libstdc++.so.6": ["GLIBCXX_3.4.21"]}))
            info = elf_info.read(path)
        self.assertEqual(info.needed, ("libc.so.6", "libstdc++.so.6"))
        self.assertEqual(info.runpaths, ("/home/builder/lib", "$ORIGIN/lib", "$ORIGIN/.."))
        self.assertIn(("libc.so.6", "GLIBC_2.34"), info.versions)
        self.assertEqual(elf_info.glibc_versions(info)[-1], "2.34")

    def test_newer_compares_as_versions_not_text(self):
        self.assertIsNone(linux_package_checks._newer({"GLIBCXX_3.4.9", "GLIBCXX_3.4.25"}, "GLIBCXX_", "3.4.25"))
        self.assertEqual(linux_package_checks._newer({"GLIBCXX_3.4.9", "GLIBCXX_3.4.30"}, "GLIBCXX_", "3.4.25"),
                         "3.4.30")

    def test_other_files_are_not_elf(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = write(Path(scratch) / "x", "#!/bin/sh\n")
            self.assertFalse(linux_package_checks.is_elf(path))
            self.assertIsNone(elf_info.read(path))


class ElfChecks(unittest.TestCase):
    def check(self, files):
        with tempfile.TemporaryDirectory() as scratch:
            for name, data in files.items():
                write(Path(scratch) / name, data, 0o755)
            return linux_package_checks.elf_problems(scratch)

    def test_floor_holds(self):
        self.assertEqual(self.check({"blender": GOOD_ELF}), [])

    def test_newer_glibc_from_any_glibc_library_is_refused(self):
        for library in ("libc.so.6", "libm.so.6", "libpthread.so.0"):
            problems = self.check({"blender": make_elf(needed=[library], version_needs={library: ["GLIBC_2.34"]})})
            self.assertEqual(len(problems), 1, library)
            self.assertIn("glibc 2.34", problems[0])

    def test_newer_libstdcxx_is_refused_unless_bundled(self):
        newer = make_elf(needed=["libstdc++.so.6"], version_needs={"libstdc++.so.6": ["GLIBCXX_3.4.30", "CXXABI_1.3.13"]})
        problems = self.check({"blender": newer})
        self.assertEqual(len(problems), 2)
        self.assertEqual(self.check({"blender": newer, "lib/libstdc++.so.6": make_elf()}), [])

    def test_glibc_private_is_refused(self):
        problems = self.check({"blender": make_elf(needed=["libc.so.6"],
                                                   version_needs={"libc.so.6": ["GLIBC_2.17", "GLIBC_PRIVATE"]})})
        self.assertEqual(len(problems), 1)
        self.assertIn("GLIBC_PRIVATE", problems[0])

    @POSIX_ONLY
    def test_links_stay_inside_the_package(self):
        with tempfile.TemporaryDirectory() as scratch:
            top = Path(scratch) / "top"
            write(top / "lib/libfoo.so.1", "x")
            os.symlink("libfoo.so.1", top / "lib/libfoo.so")
            self.assertEqual(linux_package_checks.link_problems(top), [])
            os.symlink("/usr/lib/libbar.so", top / "lib/libbar.so")
            os.symlink("../../outside", top / "lib/escape")
            self.assertEqual(len(linux_package_checks.link_problems(top)), 2)

    def test_search_paths_must_be_origin_relative(self):
        problems = self.check({"lib/a.so": make_elf(rpath="/opt/build/lib"),
                               "lib/b.so": make_elf(runpath="$ORIGIN:/usr/local/lib")})
        self.assertEqual(len(problems), 2)
        self.assertTrue(all("must start with $ORIGIN" in problem for problem in problems))


@POSIX_ONLY
@NEEDS_XZ
class Staging(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="angelo-linux-pkg-test-"))
        self.addCleanup(shutil.rmtree, self.scratch)

    def stage(self, name="Angelo-9.9.9-linux-x64", install=None, out=None):
        install = install or fake_install(self.scratch / "install")
        return linux_package.stage(install, out or self.scratch / "stage", name=name, commit="0" * 40,
                                   epoch=1763600000)

    def pack(self, stage="stage", dist="dist", **options):
        return linux_package.pack(self.scratch / stage, self.scratch / dist, verify=FakeSigner.verify, **options)

    def test_stage_keeps_build_bytecode_and_drops_runtime_bytecode(self):
        receipt = self.stage()
        top = self.scratch / "stage" / receipt["package"]
        self.assertTrue((top / "5.3/scripts/startup/__pycache__/mod.cpython-313.pyc").is_file())
        self.assertFalse((top / "5.3/scripts/startup/__pycache__/other.cpython-313.pyc").exists())
        self.assertFalse((top / "5.3/scripts/modules/__pycache__").exists())
        self.assertTrue((top / "lib/libfoo.so").is_symlink())
        self.assertEqual(linux_package_checks.bytecode_problems(top), [])
        self.assertFalse((top / linux_package.MANIFEST_NAME).exists())   # signing writes it, after staging

    def test_stage_normalises_modes_before_signing(self):
        install = fake_install(self.scratch / "install")
        os.chmod(install / "angelo.svg", 0o664)
        os.chmod(install / "blender", 0o4775)                 # set-id and group-writable never ship
        os.chmod(install / "5.3/scripts/startup", 0o775)
        top = self.scratch / "stage" / self.stage(install=install)["package"]
        self.assertEqual(top.joinpath("angelo.svg").stat().st_mode & 0o7777, 0o644)
        self.assertEqual(top.joinpath("blender").stat().st_mode & 0o7777, 0o755)
        self.assertEqual(top.joinpath("5.3/scripts/startup").stat().st_mode & 0o7777, 0o755)
        self.assertTrue(top.joinpath("lib/libfoo.so").is_symlink())

    def test_stage_refuses_an_install_that_is_not_portable(self):
        install = fake_install(self.scratch / "install")
        os.remove(install / "angelo")
        with self.assertRaisesRegex(linux_package.PackageError, "angelo is missing"):
            self.stage(install=install)

    def test_stage_refuses_a_folder_that_already_holds_the_package(self):
        self.stage()
        with self.assertRaisesRegex(linux_package.PackageError, "exists"):
            self.stage(install=self.scratch / "install")

    def test_pack_refuses_an_unsigned_tree_unless_asked(self):
        self.stage()
        with self.assertRaisesRegex(linux_package.PackageError, "unsigned"):
            self.pack()
        receipt = self.pack(unsigned=True)
        self.assertFalse(receipt["signed"])

    def test_pack_refuses_a_tree_changed_after_signing(self):
        receipt = self.stage()
        top = self.scratch / "stage" / receipt["package"]
        FakeSigner.sign(top)
        self.assertTrue(self.pack(dist="ok")["signed"])
        write(top / "angelo.svg", "<svg>changed</svg>")
        with self.assertRaisesRegex(linux_package.PackageError, "differ from the manifest"):
            self.pack(dist="changed")

    def test_debug_symbols_pack_beside_and_never_inside(self):
        receipt = self.stage()
        FakeSigner.sign(self.scratch / "stage" / receipt["package"])
        write(self.scratch / "symbols/blender.debug", b"dwarf")
        packed = self.pack(debug_symbols=self.scratch / "symbols")
        self.assertEqual(packed["debug_symbols"]["tarball"], "Angelo-9.9.9-linux-x64.debug.tar.xz")
        with tarfile.open(self.scratch / "dist" / packed["debug_symbols"]["tarball"]) as archive:
            self.assertIn("Angelo-9.9.9-linux-x64.debug/blender.debug", archive.getnames())
        with tarfile.open(self.scratch / "dist" / packed["tarball"]) as archive:
            self.assertFalse(any(name.endswith(".debug") for name in archive.getnames()))
        self.assertEqual(sorted(p.name for p in (self.scratch / "dist").iterdir()), sorted([
            packed["tarball"], packed["tarball"] + ".sha256", "Angelo-9.9.9-linux-x64.debug.tar.xz",
            "Angelo-9.9.9-linux-x64.debug.tar.xz.sha256", linux_package.PACK_RECEIPT]))

    def test_same_tree_makes_the_same_bytes(self):
        """Two installs with different timestamps, permission bits and creation order pack identically."""
        first = fake_install(self.scratch / "one")
        time.sleep(1.1)
        second = fake_install(self.scratch / "two")
        os.chmod(second / "angelo.svg", 0o664)
        os.chmod(second / "5.3/scripts/startup", 0o775)
        os.utime(second / "blender", (1, 1))
        digests, names = [], []
        for index, install in enumerate((first, second)):
            receipt = self.stage(install=install, out=self.scratch / ("stage%d" % index))
            FakeSigner.sign(self.scratch / ("stage%d" % index) / receipt["package"])
            packed = self.pack(stage="stage%d" % index, dist="dist%d" % index)
            digests.append(packed["sha256"])
            with tarfile.open(self.scratch / ("dist%d" % index) / packed["tarball"]) as archive:
                members = archive.getmembers()
            names.append([member.name for member in members])
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(names[0], sorted(names[0]))
        self.assertEqual({name.split("/")[0] for name in names[0]}, {"Angelo-9.9.9-linux-x64"})
        by_name = {member.name: member for member in members}
        top = "Angelo-9.9.9-linux-x64/"
        self.assertEqual(by_name[top + "blender"].mode, 0o755)
        self.assertEqual(by_name[top + "angelo.svg"].mode, 0o644)
        self.assertTrue(by_name[top + "lib/libfoo.so"].issym())
        self.assertTrue(all(member.mtime == 1763600000 and member.uid == 0 and member.uname == "" for member in members))
        sums = (self.scratch / "dist1" / (packed["tarball"] + ".sha256")).read_text()
        self.assertEqual(sums, "%s  %s\n" % (digests[1], packed["tarball"]))

    def test_check_unpacks_one_top_folder_only(self):
        receipt = self.stage()
        FakeSigner.sign(self.scratch / "stage" / receipt["package"])
        packed = self.pack()
        top = linux_package_checks.unpack(self.scratch / "dist" / packed["tarball"], self.scratch / "unpacked")
        self.assertEqual(linux_package_checks.layout_problems(top), [])
        self.assertEqual(linux_package_checks.elf_problems(top), [])
        self.assertEqual(linux_package_checks.link_problems(top), [])
        self.assertEqual(linux_package_checks.manifest_problems(top, verify=FakeSigner.verify), [])
        os.remove(top / linux_package.SIGNATURE_NAME)
        self.assertEqual(len(linux_package_checks.manifest_problems(top, verify=FakeSigner.verify)), 1)
        renamed = self.scratch / "dist" / "Angelo-0.0.0-linux-x64.tar.xz"
        os.rename(self.scratch / "dist" / packed["tarball"], renamed)
        with self.assertRaisesRegex(linux_package.PackageError, "holds the top entries"):
            linux_package_checks.unpack(renamed, self.scratch / "unpacked2")

    def test_layout_names_a_desktop_icon_the_package_lacks(self):
        receipt = self.stage()
        top = self.scratch / "stage" / receipt["package"]
        (top / "angelo.desktop").write_text("[Desktop Entry]\nExec=blender %f\nIcon=blender\n")
        self.assertEqual(len(linux_package_checks.layout_problems(top)), 1)


@POSIX_ONLY
class LaunchWritesNothing(unittest.TestCase):
    """A background launch must leave the install exactly as signed (the updater stops updating a modified one)."""

    def launch(self, body):
        with tempfile.TemporaryDirectory() as scratch:
            top = Path(scratch) / "top"
            write(top / "angelo", "#!/bin/sh\n" + body, 0o755)
            write(top / "5.3/scripts/mod.py", "x = 1\n")
            return linux_package_checks.launch_problems(top)

    def test_a_clean_launch_passes(self):
        self.assertEqual(self.launch('mkdir -p "$HOME/.config/angelo" && exit 0\n'), [])

    def test_bytecode_or_logs_written_inside_are_named(self):
        problems = self.launch('d=$(dirname "$0"); mkdir -p "$d/5.3/scripts/__pycache__" && '
                               'echo x > "$d/5.3/scripts/__pycache__/mod.cpython-313.pyc"; echo y >> "$d/5.3/scripts/mod.py"\n')
        self.assertIn("the launch wrote inside the install, added: 5.3/scripts/__pycache__", problems)
        self.assertIn("the launch wrote inside the install, changed: 5.3/scripts/mod.py", problems)

    def test_a_failed_launch_is_named(self):
        self.assertTrue(self.launch("exit 3\n")[0].startswith("angelo --background --factory-startup exited 3"))


class ShippedFiles(unittest.TestCase):
    """The launcher, the desktop file and the install rules the package relies on."""

    def test_package_name_follows_the_identity(self):
        identity = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text())
        self.assertEqual(linux_package.package_name(),
                         "%s-%s-linux-x64" % (identity["product"]["name"], identity["version"]["label"]))

    def test_launcher_is_a_posix_script_that_runs_the_folder_s_own_app(self):
        launcher = (ROOT / "release/bin/angelo").read_text()
        self.assertTrue(launcher.startswith("#!/bin/sh\n"))
        self.assertIn('readlink -f "$0"', launcher)   # works through a symbolic link in ~/.local/bin
        self.assertIn("blender-launcher", launcher)
        if os.name == "posix":
            self.assertTrue(os.access(ROOT / "release/bin/angelo", os.X_OK))

    def test_portable_install_ships_the_launcher(self):
        rules = (ROOT / "source/creator/CMakeLists.txt").read_text()
        portable = rules[rules.index("elseif(WITH_INSTALL_PORTABLE)"):rules.index("# main blender binary")]
        self.assertIn("release/bin/angelo", portable)
        self.assertIn("angelo_install_agent_runtime(linux", rules)

    def test_desktop_file_names_the_shipped_icon(self):
        desktop = (ROOT / "release/freedesktop/blender.desktop").read_text().splitlines()
        self.assertIn("Icon=angelo", desktop)
        self.assertIn("Exec=blender %f", desktop)   # freedesktop.py's register rewrites exactly this line


class GpuToolkits(unittest.TestCase):
    """The GPU SDK versions a release build uses are read from the SDKs and judged against Blender's pins."""

    def setUp(self):
        import linux_gpu_toolkits
        import linux_gpu_toolkits_fetch
        self.reader, self.fetch = linux_gpu_toolkits, linux_gpu_toolkits_fetch
        self.scratch = Path(tempfile.mkdtemp(prefix="angelo-gpu-test-"))
        self.addCleanup(shutil.rmtree, self.scratch)

    def toolkits(self, cuda=("12.9.1", "13.4.1"), optix=90000, hip="7.2.1-81", dlss="310.9.1"):
        root = self.scratch / "toolkits"
        for version in cuda:
            folder = root / ("cuda-" + ".".join(version.split(".")[:2]))
            write(folder / "bin/nvcc", "", 0o755)
            write(folder / "redistrib.json", json.dumps({"release_label": version}))
        write(root / "optix-sdk-9.0.0/include/optix.h", "#define OPTIX_VERSION %d\n" % optix)
        write(root / "rocm-7.2.1/.info/version", hip + "\n")
        write(root / ("dlss-sdk-%s/include/nvsdk_ngx.h" % dlss), "")
        return root

    def judge(self, root):
        import gpu_sdk_pins
        pins = gpu_sdk_pins.source_pins(ROOT, "linux")
        return gpu_sdk_pins.judge(pins, self.reader.versions(root), [])[0]

    def test_the_pinned_sdks_pass(self):
        pins = __import__("gpu_sdk_pins").source_pins(ROOT, "linux")["sdks"]
        root = self.toolkits(cuda=tuple(pins["cuda"]), hip=pins["hip"] + "-1",
                             optix=int("%d%02d%02d" % tuple(int(p) for p in pins["optix"].split("."))), dlss=pins["dlss"])
        self.assertEqual(self.judge(root), [])

    def test_another_version_is_named(self):
        self.assertTrue(any("9.1.0" in problem for problem in self.judge(self.toolkits(optix=90100))))
        shutil.rmtree(self.scratch / "toolkits")
        self.assertTrue(any("12.8.1" in problem for problem in self.judge(self.toolkits(cuda=("12.8.1", "13.4.1")))))

    def test_fetch_refuses_an_archive_that_is_not_nvidia_s(self):
        archive = io_tar({"cuda_nvcc-linux-x86_64-12.9.86-archive/bin/nvcc": b"nvcc"})
        manifest = {"release_label": "12.9.1", "cuda_nvcc": {"version": "12.9.86", "linux-x86_64": {
            "relative_path": "cuda_nvcc/x.tar.xz", "sha256": hashlib.sha256(archive).hexdigest()}}}
        served = {"redistrib_12.9.1.json": json.dumps(manifest).encode(), "cuda_nvcc/x.tar.xz": archive}
        get = lambda url: served[url[len(self.fetch.REDIST):]]   # noqa: E731
        folder, fetched = self.fetch.fetch_cuda("12.9.1", self.scratch / "tk", get=get)
        self.assertEqual(fetched, ["cuda_nvcc 12.9.86"])
        self.assertEqual(self.reader.cuda_version(folder), "12.9.1")
        served["cuda_nvcc/x.tar.xz"] = archive + b"tampered"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.fetch.fetch_cuda("12.9.1", self.scratch / "tk", get=get)


def io_tar(files):
    import io
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class BuildRecord(unittest.TestCase):
    """build_release_linux.sh's record: every release feature read back from the CMake cache, and the files whose
    debug symbols are split (only what the build compiled, never the pinned libraries)."""

    def setUp(self):
        import linux_build_record
        self.record = linux_build_record
        self.scratch = Path(tempfile.mkdtemp(prefix="angelo-record-test-"))
        self.addCleanup(shutil.rmtree, self.scratch)

    def cache(self, off=(), absent=()):
        names = list(self.record.FEATURES) + [n for names in self.record.GPU_FEATURES.values() for n in names]
        lines = ["%s:BOOL=%s" % (name, "OFF" if name in off else "ON") for name in names if name not in absent]
        return {line.split(":BOOL=")[0]: line.split("=")[1] for line in lines}

    def test_every_feature_on_passes(self):
        problems, features = self.record.feature_problems(self.cache(), [])
        self.assertEqual(problems, [])
        self.assertIn("WITH_CYCLES_DEVICE_OPTIX", features)

    def test_a_feature_off_or_unknown_is_named(self):
        problems, _ = self.record.feature_problems(self.cache(off=["WITH_USD"], absent=["WITH_GHOST_WAYLAND"]), [])
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("WITH_USD is OFF" in problem for problem in problems))
        self.assertTrue(any("WITH_GHOST_WAYLAND is not an option" in problem for problem in problems))

    def test_gpu_parts_left_out_on_purpose_are_not_required(self):
        off = [n for names in self.record.GPU_FEATURES.values() for n in names]
        self.assertEqual(len(self.record.feature_problems(self.cache(off=off), [])[0]), len(off))
        self.assertEqual(self.record.feature_problems(self.cache(off=off), list(self.record.GPU_FEATURES))[0], [])

    def test_symbols_split_only_from_compiled_files(self):
        install = self.scratch / "bin"
        for rel in ("blender", "blender-thumbnailer", "lib/libusd_ms.so", "5.3/python/lib/python3.13/x.so",
                    "5.3/scripts/addons_core/a/native.so", "agent-runtime/codex", "agent-runtime/codex-code-mode-host"):
            write(install / rel, GOOD_ELF, 0o755)
        write(install / "angelo", "#!/bin/sh\n", 0o755)
        found = sorted(p.relative_to(install).as_posix()
                       for p in self.record.compiled_elf_files(install, "agent-runtime"))
        self.assertEqual(found, ["5.3/scripts/addons_core/a/native.so", "blender", "blender-thumbnailer"])
        self.assertIn("agent-runtime/codex", [p.relative_to(install).as_posix()
                                              for p in self.record.compiled_elf_files(install)])
        script = (ROOT / "scripts/angelo/build_release_linux.sh").read_text()
        self.assertIn('strip "$BUILD" ${RUNTIME_FOLDER:+--runtime-folder "$RUNTIME_FOLDER"}', script)

    def test_strip_refuses_a_runtime_folder_the_install_lacks(self):
        """A runtime installed under another folder than the strip was told would be stripped silently."""
        build = self.scratch / "build"
        write(build / "install/angelo", "#!/bin/sh\n", 0o755)
        with self.assertRaisesRegex(RuntimeError, "agent-runtime"):
            self.record.strip(build, "agent-runtime")
        (build / "install/agent-runtime").mkdir()
        self.assertEqual(self.record.strip(build, "agent-runtime"), [])


@POSIX_ONLY
class DesktopRegistration(unittest.TestCase):
    """Registering Angelo (`angelo -r`, Preferences > System > Register) and unregistering it never replace or
    remove a stock Blender's registration in the same home folder."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "angelo_freedesktop_test", ROOT / "scripts/modules/_bpy_internal/platform/freedesktop.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.scratch = Path(tempfile.mkdtemp(prefix="angelo-register-test-"))
        self.addCleanup(shutil.rmtree, self.scratch)
        app = fake_install(self.scratch / "Angelo-9.9.9-linux-x64")
        write(app / "blender-thumbnailer", GOOD_ELF, 0o755)
        bin_dir, data = self.scratch / "home/.local/bin", self.scratch / "home/.local/share"
        stock = {bin_dir / "blender": "stock", bin_dir / "blender-thumbnailer": "stock",
                 data / "thumbnailers/blender.thumbnailer": "stock", data / "applications/blender.desktop": "stock"}
        for path, text in stock.items():
            write(path, text)
        self.stock, self.app, self.bin_dir, self.data = stock, app, bin_dir, data
        module = self.module
        module.VERBOSE = False
        module.HOMEDIR_LOCAL_BIN, module.XDG_DATA_HOME = str(bin_dir), str(data)
        module.BLENDER_BIN, module.BLENDER_DIR = str(app / "blender"), str(app)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bin_dir) + os.pathsep + old_path   # register links only into a folder on PATH
        self.addCleanup(os.environ.__setitem__, "PATH", old_path)

    def run_handlers(self, do_register):
        for handler in (self.module.handle_bin, self.module.handle_desktop_file, self.module.handle_thumbnailer):
            self.assertIsNone(handler(do_register, False))

    def test_register_and_unregister_leave_stock_blender_alone(self):
        self.run_handlers(True)
        self.assertEqual(os.readlink(self.bin_dir / "angelo"), str(self.app / "angelo"))   # the launcher
        self.assertTrue((self.bin_dir / "angelo-thumbnailer").is_file())
        entry = (self.data / "thumbnailers/angelo.thumbnailer").read_text()
        self.assertIn("angelo-thumbnailer", entry) if shutil.which("bwrap") else self.assertIn(str(self.app), entry)
        desktop = (self.data / "applications/angelo.desktop").read_text()
        self.assertIn("Exec=%s %%f" % (self.app / "blender"), desktop)
        self.run_handlers(False)
        self.assertFalse((self.bin_dir / "angelo").exists())
        for path, text in self.stock.items():
            self.assertEqual(path.read_text(), text, path)

    def test_mime_definition_has_its_own_file(self):
        self.assertEqual(self.module.REGISTERED_MIME_XML, "angelo-x-blender.xml")


if __name__ == "__main__":
    unittest.main()
