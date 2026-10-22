# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows release builds with clang-cl and lld-link (Visual Studio's ClangCL toolset), as official Blender.

Dependency-free. The build runs only on Windows, so the PowerShell side (build_release_windows.ps1 and its helper
windows_release_toolchain.ps1, the release stage, the machine's bootstrap) is checked by contract here; the rule that
decides whether a build is clang-cl's (scripts/angelo/windows_toolset.py, ``release_checks.py windows-toolset``) runs
for real: on flags recorded from real builds (official Blender 5.2.2 and the clang-cl and MSVC builds of Angelo
c196a74455d on the same machine), on variations of them, and on PE images built here with and without link.exe's
Rich header.
"""

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "scripts/angelo"
sys.path.insert(0, str(TOOLS))
import windows_toolset  # noqa: E402  (scripts/angelo; standard library only)

BUILD = (TOOLS / "build_release_windows.ps1").read_text(encoding="utf-8")
HELPER = (TOOLS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
STAGE = (TOOLS / "windows_release_validate.ps1").read_text(encoding="utf-8")
BOOTSTRAP = (TOOLS / "windows_cloud_bootstrap.ps1").read_text(encoding="utf-8")
FINAL_RUNS = (TOOLS / "windows_final_runs.ps1").read_text(encoding="utf-8")
COMPONENTS = ["Microsoft.VisualStudio.Component.VC.Llvm.Clang", "Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset"]

# bpy.app.build_cflags / build_cxxflags as each build reported them (win-build4, 2026-10-20).
STOCK_522 = {
    "build_cflags": " /W3 -Werror=implicit-function-declaration -Werror=return-type -Wno-tautological-compare "
                    "-Wno-unknown-pragmas -Wno-char-subscripts -Wstrict-prototypes -Wmissing-prototypes -Wunused-parameter "
                    "-Wundef -Wundef-prefix -Werror=unguarded-availability-new /DWIN32 /D_WINDOWS /MP /nologo /J /Gy /Gd "
                    "/showFilenames -Wno-unused-command-line-argument -Wno-microsoft-enum-forward-reference "
                    "/clang:-funsigned-char /clang:-fno-strict-aliasing /clang:-ffp-contract=off -march=x86-64-v2 ",
    "build_cxxflags": " /W3 -Wno-tautological-compare -Wno-unknown-pragmas -Wno-char-subscripts -Wno-overloaded-virtual "
                      "-Wno-sign-compare -Wno-invalid-offsetof -Wno-suggest-override -Wundef -Wundef-prefix "
                      "-Wunused-parameter -Wmismatched-tags -Werror=unguarded-availability-new /DWIN32 /D_WINDOWS /EHsc  "
                      "/Gy /MP /nologo /J /Gd /showFilenames /EHsc -Wno-unused-command-line-argument "
                      "-Wno-microsoft-enum-forward-reference /clang:-funsigned-char /clang:-fno-strict-aliasing "
                      "/clang:-ffp-contract=off -march=x86-64-v2 ",
    "build_linkflags": "/MACHINE:X64  /SUBSYSTEM:CONSOLE /STACK:2097152 /ignore:4049 /ignore:4217 /ignore:4221",
}
ANGELO_CLANG_CL = dict(STOCK_522)   # byte-identical to the official build's
ANGELO_MSVC = {
    "build_cflags": "/W3 /w34062 /w34100 /w34115 /w34189 /w35038 /wd4018 /wd4146 /wd4065 /wd4127 /wd4181 /wd4200 "
                    "/wd4244 /wd4267 /wd4305 /wd4800 /wd4828 /wd4996 /wd4661 /we4013 /we4133 /we4431 /we4033 /DWIN32 "
                    "/D_WINDOWS /nologo /J /Gd /MP /bigobj /Zc:inline /Zc:preprocessor  ",
    "build_cxxflags": "/W3 /w34062 /w34100 /w34115 /w34189 /w35038 /wd4018 /wd4146 /wd4065 /wd4127 /wd4181 /wd4200 "
                      "/wd4244 /wd4267 /wd4305 /wd4800 /wd4828 /wd4996 /wd4661 /we4013 /we4133 /we4431 /we4033 /DWIN32 "
                      "/D_WINDOWS /EHsc /nologo /J /Gd /MP /EHsc /bigobj /permissive- /Zc:__cplusplus /Zc:inline "
                      "/Zc:preprocessor  ",
    "build_linkflags": STOCK_522["build_linkflags"],
}


def pe_image(rich):
    """A minimal PE32+ image: DOS header, optionally link.exe's Rich header after it, then the PE signature."""
    lfanew = 0x80
    data = bytearray(0x200)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, lfanew)
    if rich:
        key = b"\x12\x34\x56\x78"
        data[0x40:0x44] = bytes(a ^ b for a, b in zip(b"DanS", key))
        data[0x60:0x68] = b"Rich" + key
    data[lfanew:lfanew + 4] = b"PE\0\0"
    struct.pack_into("<HH", data, lfanew + 4, 0x8664, 1)
    return bytes(data)


def block(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


class FlagRuleTests(unittest.TestCase):
    def test_the_official_build_and_angelos_clang_cl_build_pass(self):
        self.assertEqual(windows_toolset.flag_problems(STOCK_522), [])
        self.assertEqual(windows_toolset.flag_problems(ANGELO_CLANG_CL), [])

    def test_an_msvc_build_fails_on_both_rules_for_both_languages(self):
        problems = windows_toolset.flag_problems(ANGELO_MSVC)
        self.assertEqual(len(problems), 4, problems)
        for name in windows_toolset.COMPILE_FLAGS:
            self.assertTrue(any(name in p and "clang-cl's driver" in p for p in problems), name)
            self.assertTrue(any(name in p and "-march=x86-64-vN" in p for p in problems), name)

    def test_the_rule_is_the_toolsets_not_one_builds_text(self):
        def with_cxx(text):
            return dict(STOCK_522, build_cxxflags=text)
        # Any pass-through option and any baseline from x86-64-v2 up; order and other flags do not matter.
        for fine in ("-march=x86-64-v3 /clang:-fno-math-errno", "/O2 /clang:-funsigned-char -march=x86-64-v4 /EHsc",
                     "/clang:-ffp-contract=off /march=x86-64-v2"):
            self.assertEqual(windows_toolset.flag_problems(with_cxx(fine)), [], fine)
        for bad, why in (("/clang:-funsigned-char -march=x86-64-v1", "below Blender's baseline"),
                         ("/clang:-funsigned-char -march=x86-64", "no -march=x86-64-vN"),
                         ("-march=x86-64-v2 -funsigned-char -fno-strict-aliasing", "clang-cl's driver"),   # GCC/Clang
                         ("/clang: -march=x86-64-v2", "clang-cl's driver"),
                         ("/clang:-funsigned-char -march=x86-64-v2x", "no -march=x86-64-vN"),
                         ("", "not reported")):
            problems = windows_toolset.flag_problems(with_cxx(bad))
            self.assertEqual(len(problems), 1, (bad, problems))
            self.assertIn(why, problems[0], bad)
        self.assertIn("bpy.app.build_cflags is not reported", windows_toolset.flag_problems({"build_cxxflags": "x"})[0])

    def test_the_app_reports_its_flags_in_one_line(self):
        """READ_FLAGS is what the executable runs (--python-expr): one line, decoded, every flag string."""
        self.assertNotIn("\n", windows_toolset.READ_FLAGS)
        self.assertNotIn('"', windows_toolset.READ_FLAGS, "one argument of a Windows command line")
        fake = types.ModuleType("bpy")
        fake.app = types.SimpleNamespace(build_cflags=b"/clang:-x", build_cxxflags="-march=x86-64-v2", build_linkflags=b"/L")
        saved = sys.modules.get("bpy")
        sys.modules["bpy"] = fake
        try:
            out = StringIO()
            with redirect_stdout(out):
                # Locals apart from globals, as an embedding may run it: names it binds must not be needed in a scope.
                exec(windows_toolset.READ_FLAGS, {}, {})
        finally:
            if saved is None:
                del sys.modules["bpy"]
            else:
                sys.modules["bpy"] = saved
        line = out.getvalue().strip()
        self.assertTrue(line.startswith(windows_toolset.MARKER))
        self.assertEqual(json.loads(line[len(windows_toolset.MARKER):]),
                         {"build_cflags": "/clang:-x", "build_cxxflags": "-march=x86-64-v2", "build_linkflags": "/L"})


class ImageRuleTests(unittest.TestCase):
    def test_the_rich_header_marks_link_exe(self):
        self.assertIs(windows_toolset.rich_header(pe_image(rich=True)), True)
        self.assertIs(windows_toolset.rich_header(pe_image(rich=False)), False)
        self.assertIsNone(windows_toolset.rich_header(b"#!/bin/sh\n"))
        self.assertIsNone(windows_toolset.rich_header(b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x10)), "e_lfanew inside the DOS header")

    def test_each_image_is_judged(self):
        with tempfile.TemporaryDirectory() as folder:
            lld, msvc, text = (Path(folder, name) for name in ("lld.exe", "msvc.dll", "notes.txt"))
            lld.write_bytes(pe_image(rich=False))
            msvc.write_bytes(pe_image(rich=True))
            text.write_text("not an image")
            problems, facts = windows_toolset.image_problems([lld, msvc, text, Path(folder, "missing.exe")])
            self.assertEqual([f["rich_header"] for f in facts], [False, True, None])
            self.assertEqual(len(problems), 3, problems)
            self.assertIn("msvc.dll carries the Rich header", problems[0])
            self.assertIn("notes.txt is not a PE image", problems[1])
            self.assertIn("missing.exe cannot be read", problems[2])


class CommandTests(unittest.TestCase):
    def run_check(self, flags, rich):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "flags.json").write_text(json.dumps(flags))
            Path(folder, "Angelo.exe").write_bytes(pe_image(rich=rich))
            done = subprocess.run([sys.executable, "-I", "-B", str(TOOLS / "release_checks.py"), "windows-toolset",
                                   "--flags-json", str(Path(folder, "flags.json")), "--pe", str(Path(folder, "Angelo.exe")),
                                   "--report", str(Path(folder, "report.json"))], capture_output=True, text=True)
            return done, json.loads(Path(folder, "report.json").read_text())

    def test_release_checks_runs_it_and_writes_the_report(self):
        done, report = self.run_check(ANGELO_CLANG_CL, rich=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((report["toolset"], report["problems"]), ("clang-cl", []))
        self.assertIn("clang-cl and lld-link (1 image(s) checked)", done.stdout)
        done, report = self.run_check(ANGELO_MSVC, rich=True)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(report["toolset"], "not clang-cl")
        self.assertEqual(len(report["problems"]), 5, report["problems"])
        self.assertIn("windows toolset: bpy.app.build_cflags passes nothing", done.stderr)

    def test_an_app_that_reports_nothing_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder, "report.json")
            with redirect_stdout(StringIO()):
                code = windows_toolset.main(["--binary", str(Path(folder, "absent.exe")), "--report", str(report)])
            self.assertEqual(code, 1)
            data = json.loads(report.read_text())
            self.assertEqual(data["toolset"], "unknown")
            self.assertIn("could not report its build flags", data["problems"][0])


class BuildScriptTests(unittest.TestCase):
    def test_the_build_script_stays_within_the_limit(self):
        self.assertLessEqual(len(BUILD.splitlines()), 500)
        self.assertLessEqual(len(HELPER.splitlines()), 500)
        self.assertIn('. (Join-Path $PSScriptRoot "windows_release_toolchain.ps1")', BUILD)
        self.assertNotRegex(HELPER, r"(?m)^(?!\s|#|function |\$Angelo\w+ = |<#|\.|$|})", "the helper defines, never runs")

    def test_clang_cl_is_required_before_anything_compiles(self):
        order = [BUILD.index(marker) for marker in (
            "$toolset = Find-ClangClToolset $vs $vswhere", "# --- GPU toolkits", "# --- Configure",
            "Import-DeveloperEnvironment $toolset.vcvars", "$code = Invoke-Logged $configureLog",
            "Assert-ConfiguredToolset $BuildDir $toolset", "--target INSTALL")]
        self.assertEqual(order, sorted(order))
        refusal = block(HELPER, "function Find-ClangClToolset", "\n}\n")
        self.assertIn("-requires @AngeloClangComponents", refusal)
        self.assertIn("has no clang-cl toolset", refusal)
        self.assertIn("setup.exe modify --installPath", refusal, "the refusal says how to add them")
        for name in COMPONENTS:
            self.assertIn('"%s"' % name, HELPER)
        for tool in ("clang-cl.exe", "lld-link.exe", "vcvars64.bat"):
            self.assertIn(tool, refusal)

    def test_the_default_job_count_scales_with_the_machine(self):
        self.assertIn("[int]$Jobs = 0,", BUILD, "no fixed default: a smaller VM must not run out of memory")
        build_at = BUILD.index("--target INSTALL --parallel $Jobs")
        self.assertLess(BUILD.index("if ($Jobs -le 0) { $Jobs = Get-DefaultBuildJobs }"), build_at)
        rule = block(HELPER, "function Get-DefaultBuildJobs", "\n}\n")
        for fact in ("NumberOfLogicalProcessors", "TotalPhysicalMemory", "$cores / 4", "$memoryGb / 8",
                     "[math]::Max(1,", "[math]::Min(16,"):
            self.assertIn(fact, rule)
        # Every processor group's cores: ProcessorCount alone saw 44 of build4's 176 (11 jobs, not 16).
        self.assertLess(rule.index("NumberOfLogicalProcessors"), rule.index("[Environment]::ProcessorCount"),
                        "ProcessorCount is only the fallback")

    def test_configure_and_build_use_the_toolset_that_worked(self):
        configure = block(BUILD, "$arguments = @(", "Write-Host (\"cmake \"")
        self.assertIn('"-A", "x64", "-T", $toolset.generator_toolset', configure)
        self.assertIn('$AngeloReleaseToolset = "ClangCL"', HELPER)
        self.assertIn('msbuild = "/p:UseMultiToolTask=true /p:EnforceProcessCountAcrossBuilds=true"', HELPER)
        build = next(line for line in BUILD.splitlines() if "--target INSTALL --parallel $Jobs" in line)
        self.assertIn("/verbosity:minimal $($toolset.msbuild)", build)
        imported = block(HELPER, "function Import-DeveloperEnvironment", "\n}\n")
        self.assertIn("VCToolsRedistDir", imported, "platform_win32.cmake copies the MSVC runtime from it")
        self.assertIn("Get-Command cl.exe", imported, "CUDA's host compiler")
        self.assertLess(imported.index("Get-Command cmake"), imported.index("call `\"$Vcvars`\""),
                        "the CMake found before vcvars stays the one that runs")
        configured = block(HELPER, "function Assert-ConfiguredToolset", "\n}\n")
        for fact in ('"CMAKE_CXX_COMPILER_ID"] -eq "Clang"', '"CMAKE_CXX_COMPILER_FRONTEND_VARIANT"] -eq "MSVC"',
                     "lld-link\\.exe$", "CMAKE_GENERATOR_TOOLSET"):
            self.assertIn(fact, configured)

    def test_gpu_kernel_compilers_get_the_msvc_include_path(self):
        """ClangCL puts clang's headers first in IncludePath; nvcc's host cl.exe then stops at clang's vadefs.h (C1021)."""
        written = block(HELPER, "function Write-CustomCommandIncludePath", "\n}\n")
        self.assertIn("'`$(ConfigurationType)' == 'Utility' and '`$(PlatformToolset)' == '$AngeloReleaseToolset'", written)
        self.assertIn("<IncludePath>`$(VC_IncludePath);`$(WindowsSDK_IncludePath)</IncludePath>", written,
                      "the MSVC toolset's own default IncludePath")
        self.assertIn('Join-Path $BuildDir "Directory.Build.targets"', written, "MSBuild imports it for every project below")
        order = [BUILD.index(marker) for marker in (
            "Assert-ConfiguredToolset $BuildDir $toolset", "Write-CustomCommandIncludePath $BuildDir", "--target INSTALL")]
        self.assertEqual(order, sorted(order))

    def test_cpack_stages_in_a_short_folder_and_checks_wix_path_limit(self):
        """WiX 3 reads no path of 260+ characters: the first clang-cl MSI failed at 264 (LGHT0103) under the longer folder."""
        package = block(BUILD, "# --- Package", "if ($Sign) {")
        order = [package.index(marker) for marker in (
            "$packageDir = Get-PackageStagingDir $BuildDir", "Assert-StagedPathLengths $BuildDir $packageDir",
            '$cpack = "cpack -G WIX -C Release -B `"$packageDir`""')]
        self.assertEqual(order, sorted(order))
        self.assertIn('Get-ChildItem $packageDir -Filter "*.msi" -ErrorAction SilentlyContinue | Move-Item -Destination $BuildDir',
                      BUILD, "the MSI ends in the build folder, as before")
        check = block(HELPER, "function Assert-StagedPathLengths", "\n}\n")
        self.assertIn("_CPack_Packages\\Windows\\WIX\\$PackageName\\$component", check)
        self.assertIn("CPACK_COMPONENTS_ALL", check, "the longest component folder CPack will use")
        self.assertIn("-ge 260", check)
        staging = block(HELPER, "function Get-PackageStagingDir", "\n}\n")
        self.assertIn('"cpk-$hash"', staging)
        # The longest staged file of the clang-cl build (2026-10-20), below each folder, against the limit.
        relative = ("Unspecified\\5.2\\python\\lib\\site-packages\\pkg_resources\\tests\\data\\my-test-package_unpacked-egg\\"
                    "my_test_package-1.0-py3.7.egg\\EGG-INFO\\dependency_links.txt")
        def staged(folder):
            return len(folder + "\\_CPack_Packages\\Windows\\WIX\\Angelo-1.0.1-windows-x64\\" + relative)
        self.assertGreaterEqual(staged("C:\\angelo\\build_windows_Release_x64_vc17_clangcl_Release"), 260)
        self.assertLess(staged("C:\\angelo\\cpk-0123abcd"), 240)

    def test_the_manifest_records_the_toolset_and_the_built_app_is_checked(self):
        self.assertIn("; toolset = $toolset", BUILD)
        found = block(HELPER, "function Find-ClangClToolset", "\n}\n")
        for key in ("generator = ", "generator_toolset = ", "compiler = ", "linker = ", "msbuild = ", "cmake = "):
            self.assertIn(key, found)
        order = [BUILD.index(marker) for marker in (
            "--target INSTALL", "the build reports hash", "windows-toolset --binary $exe", "$toolset.reported =",
            'throw "the build is not clang-cl', "# --- Package")]
        self.assertEqual(order, sorted(order))
        self.assertIn("$runtimeFiles.folder", BUILD[BUILD.index("$images = "):BUILD.index("windows-toolset --binary")],
                      "OpenAI's runtime executables are not this build's images")

    def test_the_release_stage_reports_the_check_after_the_seal_check(self):
        order = [STAGE.index(marker) for marker in (
            'Test-Check "release: build_release_windows.ps1 -Package', 'Test-Check "release: a launch writes nothing',
            'Test-Check "release: the app is clang-cl', "windows-toolset --binary $exe",
            'Test-Check "release: BACKGROUND native suites')]
        self.assertEqual(order, sorted(order))
        self.assertIn('"BlendThumb.dll"', STAGE)


class BuildFolderTests(unittest.TestCase):
    def folder_name(self):
        pattern = re.search(r'\("(build_windows_Release_x64_vc17_\{0\}_Release)" -f \$AngeloReleaseToolset\.ToLowerInvariant\(\)\)',
                            HELPER).group(1)
        toolset = re.search(r'\$AngeloReleaseToolset = "(\w+)"', HELPER).group(1)
        return pattern.replace("{0}", toolset.lower())

    def test_the_folder_name_carries_the_toolset(self):
        self.assertEqual(self.folder_name(), "build_windows_Release_x64_vc17_clangcl_Release")
        self.assertNotEqual(self.folder_name(), "build_windows_Release_x64_vc17_Release", "never make.bat's MSVC folder")

    def test_every_script_finds_the_release_folder_by_the_one_name(self):
        self.assertIn("if (-not $BuildDir) { $BuildDir = Get-AngeloReleaseBuildDir $SourceDir }", BUILD)
        self.assertIn("if (-not $BuildDir) { $BuildDir = Get-AngeloReleaseBuildDir $SourceDir }", STAGE)
        self.assertIn('[string]$BuildDir = "",', STAGE)
        self.assertLess(STAGE.index("windows_release_toolchain.ps1"), STAGE.index("function Invoke-Logged"),
                        "the stage's own Invoke-Logged is defined after the helper's, so it is the one used")
        self.assertIn("$buildDir = Get-AngeloReleaseBuildDir $SourceDir", FINAL_RUNS)
        for script in sorted(TOOLS.glob("*.ps1")):
            self.assertNotIn("vc17_Release", script.read_text(encoding="utf-8"), script.name)

    def test_a_folder_configured_with_another_toolset_is_refused_before_it_is_touched(self):
        guard = block(HELPER, "function Assert-BuildDirToolset", "\n}\n")
        pattern = re.search(r'-Pattern "(.*?)"', guard).group(1)
        toolset = re.search(r'\$AngeloReleaseToolset = "(\w+)"', HELPER).group(1)
        # The cache lines CMake wrote for each toolset (the A/B builds' CMakeCache.txt); PowerShell's -ne ignores case.
        for line, refused in (("CMAKE_GENERATOR_TOOLSET:INTERNAL=", True), ("CMAKE_GENERATOR_TOOLSET:INTERNAL=v143", True),
                              ("CMAKE_GENERATOR_TOOLSET:INTERNAL=ClangCL", False), ("CMAKE_GENERATOR_TOOLSET:INTERNAL=ClangCl", False)):
            have = re.match(pattern, line).group(1).strip()
            self.assertEqual(have.lower() != toolset.lower(), refused, line)
        self.assertIn("if (-not (Test-Path $cache)) { return }", guard, "a new folder is fine")
        order = [BUILD.index(marker) for marker in ("Assert-BuildDirToolset $BuildDir", "New-Item -ItemType Directory -Force $BuildDir",
                                                    "& $signScript -Mode Preflight")]
        self.assertEqual(order, sorted(order))


class MachineTests(unittest.TestCase):
    def test_every_new_build_machine_gets_clang_cl(self):
        tools = block(BOOTSTRAP, "vs_BuildTools.exe\" -OutFile $vs", 'Stage-Mark "tools"')
        for name in COMPONENTS:
            self.assertIn("--add %s" % name, tools)
        self.assertIn("--add Microsoft.VisualStudio.Component.VC.Tools.x86.x64", tools)


if __name__ == "__main__":
    unittest.main()
