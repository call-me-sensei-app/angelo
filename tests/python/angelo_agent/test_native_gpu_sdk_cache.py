# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A release build compiles with the GPU SDKs it was given, not with ones a reused build folder remembers.

``gpu_sdk_pins.py`` judges the versions read from the folders the release script gives CMake. A build folder keeps
what CMake found under a toolkit's folder and never looks again when another folder is given: on 2026-11-15 the
first configure on the pinned toolkits passed the pin check and compiled with OptiX 9.1.0's headers and ROCm 6.4's
``hipcc`` (the Windows cache lines below are that folder's). ``scripts/angelo/gpu_sdk_cache.py`` is asked for three
things: a folder last configured with other toolkits, or with no record of them, starts its cache anew; a configure
whose cache holds a toolkit path outside the folder given, or in another version of it, is refused by the entry's
name; and so is one whose resolved headers and tools do not report the version that was judged. No Blender needed.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts/angelo"
sys.path.insert(0, str(SCRIPTS))

PINNED = {"dlss": "310.9.1", "optix": "9.0.0", "cuda": ["12.9.1", "13.4.1"], "hip": "7.1.51803", "ocloc": "101.8424"}
CUDA_FOLDER = "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA"
OPTIONS = [   # What the release script gave CMake on the build machine.
    "-DCUDA12_TOOLKIT_ROOT_DIR=%s/v12.9" % CUDA_FOLDER, "-DCUDA12_NVCC_EXECUTABLE=%s/v12.9/bin/nvcc.exe" % CUDA_FOLDER,
    "-DCUDA13_TOOLKIT_ROOT_DIR=%s/v13.4" % CUDA_FOLDER, "-DCUDA13_NVCC_EXECUTABLE=%s/v13.4/bin/nvcc.exe" % CUDA_FOLDER,
    "-DCUDA_TOOLKIT_ROOT_DIR=%s/v13.4" % CUDA_FOLDER, "-DCUDA_NVCC_EXECUTABLE=%s/v13.4/bin/nvcc.exe" % CUDA_FOLDER,
    "-DOPTIX_ROOT_DIR=C:/ProgramData/NVIDIA Corporation/OptiX SDK 9.0.0",
    "-DHIP_ROOT_DIR=C:/Program Files/AMD/ROCm/7.1",
    "-DOCLOC_INSTALL_DIR=C:/Program Files (x86)/Intel/oneAPI/ocloc/2025.3/bin",
    "-DDLSS_SDK_ROOT=C:/angelo/toolkits/dlss-sdk-310.9.1",
    "-DWITH_CYCLES_DEVICE_OPTIX=ON",
]
# A cache as CMake writes one, with what that configure kept from the folder's earlier ones (OptiX 9.1.0's headers,
# ROCm 6.4's compiler, and CMake's own note of it) among entries that are right or belong to no toolkit.
CACHE = r"""# This is the CMakeCache file.
//Path to a program.
CUDA_NVCC_EXECUTABLE:FILEPATH=C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.4/bin/nvcc.exe
CUDA_HOST_COMPILER:FILEPATH=$(VCInstallDir)Tools/MSVC/$(VCToolsVersion)/bin/Host$(Platform)/$(PlatformTarget)
CUDA_nvToolsExt_LIBRARY:FILEPATH=C:/Program Files/NVIDIA Corporation/NvToolsExt/lib/x64/nvToolsExt64_1.lib
CUDA_TOOLKIT_ROOT_DIR:UNINITIALIZED=C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.4
OPTIX_ROOT_DIR:UNINITIALIZED=C:/ProgramData/NVIDIA Corporation/OptiX SDK 9.0.0
OPTIX_INCLUDE_DIR:PATH=C:/ProgramData/NVIDIA Corporation/OptiX SDK 9.1.0/include
HIP_ROOT_DIR:UNINITIALIZED=C:/Program Files/AMD/ROCm/7.1
HIP_HIPCC_EXECUTABLE:FILEPATH=C:/Program Files/AMD/ROCm/6.4/bin/hipcc.exe
HIP_VERSION:STRING=7.1.51803
HIPRT_INCLUDE_DIR:PATH=C:/angelo/src/lib/windows_x64/hiprt/include
OCLOC_INSTALL_DIR:UNINITIALIZED=C:/Program Files (x86)/Intel/oneAPI/ocloc/2025.3/bin
DLSS_SDK_ROOT:UNINITIALIZED=C:\angelo\toolkits\dlss-sdk-310.9.1
DLSS_INCLUDE_DIR:PATH=C:/angelo/toolkits/dlss-sdk-310.9.1/include
WITH_CYCLES_DEVICE_OPTIX:BOOL=ON
FIND_PACKAGE_MESSAGE_DETAILS_HIP:INTERNAL=[C:/Program Files/AMD/ROCm/6.4/bin/hipcc.exe][v7.1.51803(6.0.0)]
"""
STALE = ["OPTIX_INCLUDE_DIR", "HIP_HIPCC_EXECUTABLE", "FIND_PACKAGE_MESSAGE_DETAILS_HIP"]


def module():
    import gpu_sdk_cache
    return gpu_sdk_cache


def names(lines):
    return [line.split(" = ")[0] for line in lines]


class LocationTests(unittest.TestCase):
    def strays(self, cache=CACHE, options=OPTIONS):
        return module().cache_strays(cache, options)

    def test_what_an_earlier_configure_found_elsewhere_is_named(self):
        found = self.strays()
        self.assertEqual(names(found), STALE, found)
        self.assertIn("OptiX SDK 9.1.0/include", found[0])
        self.assertIn("OptiX SDK 9.0.0", found[0], "the folder that was given is named beside it")

    def test_a_cache_that_holds_only_the_given_folders_is_clean(self):
        clean = CACHE.replace("OptiX SDK 9.1.0", "OptiX SDK 9.0.0").replace("ROCm/6.4", "ROCm/7.1")
        self.assertEqual(self.strays(clean), [])

    def test_another_cuda_toolkit_is_found_under_any_name(self):
        """CUDA's entries are judged by where they lie, not by name: a path in a toolkit beside the given ones is
        another version, while the helper library FindCUDA finds outside every toolkit is none of them."""
        for line in ("CUDA_TOOLKIT_INCLUDE:PATH=%s/v12.8/include" % CUDA_FOLDER,
                     "CUDA_cublas_LIBRARY:FILEPATH=%s/v13.0/lib/x64/cublas.lib" % CUDA_FOLDER,
                     "SOME_OTHER_NAME:STRING=-I%s/v12.9/include;%s/v12.8/bin/nvcc.exe" % (CUDA_FOLDER, CUDA_FOLDER)):
            with self.subTest(line=line):
                found = self.strays(line + "\n")
                self.assertEqual(len(found), 1, found)
                self.assertIn("another version", found[0])
        for line in ("CUDA_TOOLKIT_INCLUDE:PATH=%s/v12.9/include" % CUDA_FOLDER,
                     "CUDA_cublas_LIBRARY:FILEPATH=%s/v13.4/lib/x64/cublas.lib" % CUDA_FOLDER,
                     "CUDA_nvToolsExt_LIBRARY:FILEPATH=C:/Program Files/NVIDIA Corporation/NvToolsExt/lib/x64/a.lib",
                     "PYTHON_EXECUTABLE:FILEPATH=C:/angelo/src/lib/windows_x64/python/313/bin/python.exe"):
            with self.subTest(line=line):
                self.assertEqual(self.strays(line + "\n"), [])

    def test_paths_compare_as_windows_compares_them(self):
        cache = "HIP_HIPCC_EXECUTABLE:FILEPATH=c:\\program files\\AMD\\ROCm\\7.1\\bin\\hipcc.exe\n"
        self.assertEqual(self.strays(cache, ["-DHIP_ROOT_DIR=C:/Program Files/AMD/ROCm/7.1/"]), [])

    def test_a_folder_whose_name_only_starts_alike_is_another_folder(self):
        cache = "HIP_HIPCC_EXECUTABLE:FILEPATH=C:/Program Files/AMD/ROCm/7.10/bin/hipcc.exe\n"
        self.assertEqual(len(self.strays(cache, ["-DHIP_ROOT_DIR=C:/Program Files/AMD/ROCm/7.1"])), 1)

    def test_the_graphics_compiler_ocloc_loads_is_judged_with_ocloc(self):
        cache = "IGC_INSTALL_DIR:PATH=C:/Program Files (x86)/Intel/oneAPI/ocloc/2026.1/bin\n"
        self.assertEqual(len(self.strays(cache)), 1)

    def test_a_folder_without_a_version_in_its_path_has_no_other_version(self):
        options = ["-DOCLOC_INSTALL_DIR=C:/angelo/toolkits/ocloc"]
        self.assertEqual(self.strays("PYTHON_EXECUTABLE:FILEPATH=C:/angelo/src/lib/python.exe\n", options), [])
        self.assertEqual(len(self.strays("OCLOC_BINARY:FILEPATH=C:/elsewhere/ocloc.exe\n", options)), 1)

    def test_a_list_and_a_posix_path_are_read(self):
        options = ["-DOPTIX_ROOT_DIR=/opt/optix-9.0.0"]
        self.assertEqual(self.strays("OPTIX_INCLUDE_DIR:PATH=/opt/optix-9.0.0/include\n", options), [])
        both = "OPTIX_INCLUDE_DIR:PATH=/opt/optix-9.0.0/include;/opt/optix-9.1.0/include\n"
        self.assertEqual(len(self.strays(both, options)), 1)


class Machine:
    """A build machine's toolkits as files, and the options and cache a configure of them gives."""

    def __init__(self, case):
        self.root = Path(tempfile.mkdtemp())
        case.addCleanup(shutil.rmtree, self.root, True)
        self.optix = self.write("optix/OptiX SDK 9.0.0/include/optix.h", "#define OPTIX_VERSION 90000\n").parent
        self.write("rocm/7.1/include/hip/hip_version.h",
                   "#define HIP_VERSION_MAJOR 7\n#define HIP_VERSION_MINOR 1\n#define HIP_VERSION_PATCH 51803\n")
        self.hipcc = self.write("rocm/7.1/bin/hipcc.exe", "")
        self.nvcc = {major: self.cuda("v%s" % folder, version) for major, folder, version in
                     (("12", "12.9", "12.9.1"), ("13", "13.4", "13.4.1"))}
        self.ocloc = self.write("ocloc/2025.3/bin/ocloc.exe", "no version resource").parent
        (self.ocloc / "igc64.dll").write_bytes(self.version_resource("32.0.101.8424"))
        self.dlss = self.write("dlss-sdk-310.9.1/include/nvsdk_ngx.h", "").parent
        self.build = self.root / "build"
        self.build.mkdir()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def cuda(self, folder, version, cicc=True):
        self.write("cuda/%s/version.json" % folder, json.dumps({"cuda": {"name": "CUDA SDK", "version": version}}))
        if cicc:
            self.write("cuda/%s/nvvm/bin/cicc.exe" % folder, "")
        return self.write("cuda/%s/bin/nvcc.exe" % folder, "")

    @staticmethod
    def version_resource(text, then=b"\x34\x00\x0c\x00\x01\x00"):
        """A binary with a version resource's ``FileVersion`` entry as Windows lays it out: the entry's length, its
        value's length in characters with the closing NUL (0 for no value), its type, the key, padding to four
        bytes, the value. ``then`` is the next entry's header; its default length word reads as the character 4."""
        import struct
        key = "FileVersion".encode("utf-16-le") + b"\0\0"
        value = (text.encode("utf-16-le") + b"\0\0") if text else b""
        head = struct.pack("<HHH", 6 + len(key) + 2 + len(value), len(value) // 2, 1)
        return (b"MZ" + b"\0" * 62 + head + key + b"\0\0" + value + then + "ProductVersion".encode("utf-16-le")
                + b"\0\0")

    def options(self):
        found = ["-DCUDA%s_NVCC_EXECUTABLE=%s" % (major, nvcc.as_posix()) for major, nvcc in self.nvcc.items()]
        found += ["-DCUDA%s_TOOLKIT_ROOT_DIR=%s" % (major, nvcc.parent.parent.as_posix())
                  for major, nvcc in self.nvcc.items()]
        return found + ["-DCUDA_NVCC_EXECUTABLE=%s" % self.nvcc["13"].as_posix(),
                        "-DOPTIX_ROOT_DIR=%s" % self.optix.parent.as_posix(),
                        "-DHIP_ROOT_DIR=%s" % self.hipcc.parent.parent.as_posix(),
                        "-DOCLOC_INSTALL_DIR=%s" % self.ocloc.as_posix(),
                        "-DDLSS_SDK_ROOT=%s" % self.dlss.parent.as_posix(), "-DWITH_DLSS=ON"]

    def cache(self):
        lines = ["%s:UNINITIALIZED=%s" % tuple(option[2:].split("=", 1)) for option in self.options()]
        lines += ["OPTIX_INCLUDE_DIR:PATH=%s" % self.optix.as_posix(),
                  "HIP_HIPCC_EXECUTABLE:FILEPATH=%s" % self.hipcc.as_posix(),
                  "DLSS_INCLUDE_DIR:PATH=%s" % self.dlss.as_posix(), "HIP_VERSION:STRING=7.1.51803"]
        return "\n".join(lines) + "\n"

    def run(self, stage, cache="as configured", versions=PINNED, record=None):
        if cache is not None:
            text = self.cache() if cache == "as configured" else cache
            (self.build / "CMakeCache.txt").write_text(text, encoding="utf-8")
        used = {"options": self.options(), "versions": versions}
        if record is not None:
            (self.build / "angelo-toolkits.json").write_text(module().toolkit_record(record(used)), encoding="utf-8")
        file = self.root / "used.json"
        file.write_text(json.dumps(used), encoding="utf-8-sig")   # PowerShell may write a byte-order mark.
        lines = []
        code = module().main(["--build", str(self.build), "--used", str(file), "--stage", stage], out=lines.append)
        return code, "\n".join(lines)

    def recorded(self):
        return (self.build / "angelo-toolkits.json").is_file()


class BeforeTheConfigure(unittest.TestCase):
    def test_a_folder_configured_with_other_toolkits_starts_its_cache_anew(self):
        changes = {"no record": None,
                   "another version in the same folder": lambda used: dict(used, versions=dict(PINNED, cuda=["12.9.0"])),
                   "another folder": lambda used: dict(used, options=used["options"] + ["-DOPTIX_ROOT_DIR=/elsewhere"])}
        for what, record in changes.items():
            with self.subTest(what):
                machine = Machine(self)
                code, text = machine.run("before", record=record)
                self.assertEqual(code, 0, text)
                self.assertFalse((machine.build / "CMakeCache.txt").exists(), "the cache moves aside")
                self.assertEqual((machine.build / "CMakeCache.txt.toolkits-changed").read_text(), machine.cache())
                self.assertIn("CMakeCache.txt.toolkits-changed", text)

    def test_a_folder_configured_with_these_toolkits_keeps_its_cache(self):
        machine = Machine(self)
        code, text = machine.run("before", record=lambda used: used)
        self.assertEqual(code, 0, text)
        self.assertTrue((machine.build / "CMakeCache.txt").exists())

    def test_a_new_folder_has_nothing_to_move(self):
        machine = Machine(self)
        self.assertEqual(machine.run("before", cache=None)[0], 0)
        self.assertEqual(list(machine.build.iterdir()), [])

    def test_a_cuda_toolkit_without_its_compiler_stops_the_build_at_once(self):
        """CUDA 13 ships nvvm (cicc) as a component of its own; without it every kernel of that toolkit failed an
        hour into the build ("The system cannot find the path specified")."""
        machine = Machine(self)
        (machine.nvcc["13"].parent.parent / "nvvm/bin/cicc.exe").unlink()
        code, text = machine.run("before")
        self.assertEqual(code, 1, text)
        self.assertIn("cicc", text)
        self.assertIn("v13.4", text)
        self.assertTrue((machine.build / "CMakeCache.txt").exists(), "nothing moves for a build that cannot start")


class AfterTheConfigure(unittest.TestCase):
    def test_a_configure_of_the_given_toolkits_is_recorded_and_then_trusted(self):
        machine = Machine(self)
        code, text = machine.run("after")
        self.assertEqual(code, 0, text)
        self.assertTrue(machine.recorded())
        self.assertEqual(machine.run("before", cache=None)[0], 0)
        self.assertTrue((machine.build / "CMakeCache.txt").exists(), "the next build of these toolkits keeps its cache")

    def refused(self, machine, cache, *words, versions=PINNED):
        code, text = machine.run("after", cache=cache, versions=versions)
        self.assertEqual(code, 1, text)
        for word in words:
            self.assertIn(word, text)
        self.assertFalse(machine.recorded(), "a refused configure is not the folder's record")
        return text

    def test_the_build_machine_s_cache_is_refused_by_each_stale_entry_s_name(self):
        machine = Machine(self)
        used = {"options": OPTIONS, "versions": PINNED}
        (machine.build / "CMakeCache.txt").write_text(CACHE, encoding="utf-8")
        lines = []
        self.assertEqual(module().cache_stage(machine.build, used, "after", lines.append), 1)
        for name in STALE:
            self.assertIn(name, "\n".join(lines))
        self.assertFalse(machine.recorded())

    def test_headers_of_another_version_inside_the_given_folder_are_refused(self):
        """The folder is the one given and the version in it is not the one judged (an install over the old one
        between the check and the configure, or a reader that read another file)."""
        machine = Machine(self)
        machine.write("optix/OptiX SDK 9.0.0/include/optix.h", "#define OPTIX_VERSION 90100\n")
        self.refused(machine, "as configured", "OPTIX_INCLUDE_DIR", "9.1.0", "9.0.0")

    def test_each_sdk_s_resolved_version_is_read(self):
        cases = {
            "hip": ("rocm/7.1/include/hip/hip_version.h",
                    "#define HIP_VERSION_MAJOR 6\n#define HIP_VERSION_MINOR 4\n#define HIP_VERSION_PATCH 50101\n",
                    ("HIP_HIPCC_EXECUTABLE", "6.4.50101")),
            "cuda": ("cuda/v13.4/version.json", json.dumps({"cuda": {"version": "13.0.0"}}),
                     ("CUDA13_NVCC_EXECUTABLE", "13.0.0", "13.4.1")),
            "cuda without a version file": ("cuda/v12.9/version.json", "not json",
                                            ("CUDA12_NVCC_EXECUTABLE", "no version")),
        }
        for what, (file, text, words) in cases.items():
            with self.subTest(what):
                machine = Machine(self)
                machine.write(file, text)
                self.refused(machine, "as configured", *words)

    def test_ocloc_is_read_from_the_compiler_beside_it_and_dlss_from_its_folder(self):
        machine = Machine(self)
        (machine.ocloc / "igc64.dll").write_bytes(Machine.version_resource("32.0.101.9028"))
        self.refused(machine, "as configured", "OCLOC_INSTALL_DIR", "101.9028", "101.8424")
        machine = Machine(self)
        self.refused(machine, "as configured", "DLSS_INCLUDE_DIR", "310.9.1", versions=dict(PINNED, dlss="310.9.2"))

    def test_a_binary_s_version_is_read_as_windows_shows_it(self):
        machine = Machine(self)
        read = module().file_version
        self.assertEqual(read(machine.ocloc / "igc64.dll"), "101.8424")
        self.assertIsNone(read(machine.ocloc / "ocloc.exe"), "the pinned ocloc.exe carries no version")
        zeros = machine.write("zeros.dll", "")
        zeros.write_bytes(Machine.version_resource("0.0.0.0"))
        self.assertIsNone(read(zeros), "nothing but zeros is no version")
        empty = machine.write("empty.dll", "")
        empty.write_bytes(Machine.version_resource(""))
        self.assertIsNone(read(empty), "an entry without a value is no version: what follows it is another entry")
        fixed = machine.write("fixed.dll", "")
        fixed.write_bytes(b"x" * 9 + b"\xbd\x04\xef\xfe\x00\x00\x01\x00" + bytes([0, 0, 32, 0, 0x2c, 0x23, 101, 0]))
        self.assertIsNone(read(fixed), "the release script reads the text alone; the two readers must agree")
        short = machine.write("short.dll", "")
        short.write_bytes(Machine.version_resource("2026.1"))
        self.assertEqual(read(short), "2026.1", "a version of another shape is taken whole")
        self.assertIsNone(read(machine.root / "missing.dll"))

    def test_an_sdk_that_was_judged_and_not_resolved_is_refused(self):
        machine = Machine(self)
        kept = [line for line in machine.cache().splitlines() if not line.startswith("OPTIX_INCLUDE_DIR")]
        self.refused(machine, "\n".join(kept) + "\n", "OPTIX_INCLUDE_DIR", "9.0.0")

    def test_each_pinned_cuda_toolkit_is_some_nvcc_s(self):
        machine = Machine(self)
        cache = machine.cache().replace("/cuda/v12.9/", "/cuda/v13.4/")   # Both majors resolved to one toolkit.
        self.refused(machine, cache, "12.9.1")

    def test_a_part_left_out_of_the_build_is_not_expected(self):
        """A release without DLSS records ``"dlss": "off"`` (ANGELO_RELEASE_DLSS=off), a part left out with
        -AllowMissing records nothing for it: neither was judged as a version, so the configure resolves none.
        The first run on the build machine, a release without DLSS, was refused for "DLSS SDK off"."""
        for what, versions in (("off", dict(PINNED, dlss="off")),
                               ("not recorded", {key: value for key, value in PINNED.items() if key != "dlss"})):
            with self.subTest(what):
                machine = Machine(self)
                kept = [line for line in machine.cache().splitlines() if not line.startswith("DLSS_")]
                code, text = machine.run("after", cache="\n".join(kept) + "\n", versions=versions)
                self.assertEqual(code, 0, text)
                self.assertTrue(machine.recorded())

    def test_no_cache_after_a_configure_is_an_error(self):
        self.assertEqual(Machine(self).run("after", cache=None)[0], 1)


class ReleaseScriptTests(unittest.TestCase):
    """The Windows scripts run only on the build machine: these read their source."""

    def test_the_release_build_checks_its_folder_before_and_after_the_configure(self):
        toolchain = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        body = toolchain[toolchain.index("function Invoke-ToolkitCacheCheck"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("gpu_sdk_cache.py", body)
        self.assertIn("throw", body)
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        before = build.index('Invoke-ToolkitCacheCheck "before"')
        after = build.index('Invoke-ToolkitCacheCheck "after"')
        configure = build.index('Invoke-Logged $configureLog')
        self.assertLess(build.index("Get-GpuToolkits $allowed"), before)
        self.assertLess(before, configure)
        self.assertLess(configure, after)
        self.assertLess(after, build.index("cmake --build"))

    def test_the_documentation_says_what_a_reused_folder_keeps(self):
        text = (ROOT / "doc/angelo/native_editor_build.rst").read_text(encoding="utf-8")
        self.assertIn("gpu_sdk_cache.py", text)
        self.assertIn("angelo-toolkits.json", text)


if __name__ == "__main__":
    unittest.main()
