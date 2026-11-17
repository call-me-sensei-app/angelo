# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A release is built with the GPU SDK versions Blender pins for its own release, or with an approved exception.

Blender names the SDKs of its release builds in ``build_files/buildbot/config/blender_version.cmake`` (OptiX, the
CUDA toolkits, HIP, ocloc, DLSS). An SDK's version decides what a person's machine needs: a newer OptiX SDK raises
the oldest NVIDIA driver OptiX runs on, so a build made with whichever SDK a build machine happens to hold can lose
a device that the same Blender release offers (found on 2026-11-14: OptiX SDK 9.1 where Blender pins 9.0.0, so no
OptiX on a 582 driver). ``scripts/angelo/gpu_sdk_pins.py`` is the one reader of that file and the one judge:

* the pins are read per platform, and a pinned name the check does not know is itself a problem;
* a version used must equal its pin exactly: newer is refused like older, a missing toolkit of a pinned pair too,
  and a version read with less precision than the pin is not a match;
* an exception (``gpu_sdk_exceptions.json``) names the SDK, the pin, the version used, the reason, who approved it
  and when; it applies only to exactly that pin and version, and one that nothing needs is a problem;
* the Windows release script judges before it configures and the release stage judges the build's own record.

The PowerShell side runs only on the Windows build machine, so its part is read from its source here.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts/angelo"
sys.path.insert(0, str(SCRIPTS))

PIN_FILE = """\
# A comment with set(IGNORED_VERSION 1.0) inside it.
set(DLSS_VERSION   310.9.1)
set(OPTIX_VERSION  9.0.0)

set(CUDA12_VERSION 12.9.1)
set(CUDA13_VERSION 13.4.1)

# CUDA_TOOLKIT_ROOT_DIR will be initialized to the CUDA toolkit with this major version.
set(DEFAULT_CUDA_MAJOR_VERSION 13)

if(WIN32)
  set(HIP_VERSION 7.1.51803)
  set(OCLOC_VERSION 101.8424)
elseif(UNIX AND NOT (APPLE OR HAIKU))
  set(HIP_VERSION 7.2.1)
endif()
"""
PINNED = {"dlss": "310.9.1", "optix": "9.0.0", "cuda": ["12.9.1", "13.4.1"], "hip": "7.1.51803", "ocloc": "101.8424"}


def module():
    import gpu_sdk_pins
    return gpu_sdk_pins


def exception(**changes):
    entry = {"sdk": "hip", "pin": "7.1.51803", "used": "6.4.0", "reason": "No machine can install the pinned SDK yet",
             "approved_by": "the maintainer", "approved_on": "2026-11-14"}
    entry.update(changes)
    return entry


class PinTests(unittest.TestCase):
    def test_pins_are_read_for_the_platform(self):
        pins = module().read_pins(PIN_FILE, "windows")
        self.assertEqual(pins, {"sdks": PINNED, "cuda_default_major": 13, "unknown": []})
        linux = module().read_pins(PIN_FILE, "linux")
        self.assertEqual(linux["sdks"]["hip"], "7.2.1")
        self.assertNotIn("ocloc", linux["sdks"], "Blender pins no ocloc for Linux: it builds its own")
        self.assertNotIn("hip", module().read_pins(PIN_FILE, "macos")["sdks"])

    def test_a_pinned_name_the_check_does_not_know_is_a_problem(self):
        pins = module().read_pins(PIN_FILE + "set(NEWSDK_VERSION 2.5.0)\n", "windows")
        self.assertEqual(pins["unknown"], ["NEWSDK_VERSION"])
        problems = module().problems(pins, dict(PINNED), [])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("NEWSDK_VERSION", problems[0])

    def test_this_source_s_own_pin_file_is_read_whole(self):
        pins = module().source_pins(ROOT, "windows")
        self.assertEqual(pins["unknown"], [])
        self.assertEqual(sorted(pins["sdks"]), ["cuda", "dlss", "hip", "ocloc", "optix"])
        for name, value in pins["sdks"].items():
            for version in value if isinstance(value, list) else [value]:
                self.assertRegex(version, r"^\d+(\.\d+)+$", name)
        self.assertIn(pins["cuda_default_major"], [int(v.split(".")[0]) for v in pins["sdks"]["cuda"]])


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.pins = module().read_pins(PIN_FILE, "windows")

    def problems(self, used, exceptions=(), **more):
        return module().problems(self.pins, used, list(exceptions), **more)

    def test_the_pinned_versions_are_no_problem(self):
        self.assertEqual(self.problems(dict(PINNED)), [])

    def test_a_newer_sdk_is_refused_like_an_older_one(self):
        for name, version in (("optix", "9.1.0"), ("optix", "8.0.0"), ("hip", "6.4.0"), ("hip", "7.2.1"),
                              ("ocloc", "2026.1"), ("dlss", "310.9.2")):
            with self.subTest(sdk=name, used=version):
                problems = self.problems(dict(PINNED, **{name: version}))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(version, problems[0])
                self.assertIn(PINNED[name], problems[0])

    def test_each_pinned_cuda_toolkit_must_be_there_and_no_other(self):
        alone = self.problems(dict(PINNED, cuda=["12.9.1"]))
        self.assertEqual(len(alone), 1, alone)
        self.assertIn("13.4.1", alone[0])
        other = self.problems(dict(PINNED, cuda=["12.9.1", "13.4.1", "11.4.1"]))
        self.assertEqual(len(other), 1, other)
        self.assertIn("11.4.1", other[0])
        self.assertEqual(self.problems(dict(PINNED, cuda=["13.4.1", "12.9.1"])), [], "order is not a difference")

    def test_a_version_read_with_less_precision_than_its_pin_is_not_a_match(self):
        for name, version in (("cuda", ["12.9", "13.4.1"]), ("optix", "9.0"), ("hip", "7.1")):
            with self.subTest(sdk=name):
                problems = self.problems(dict(PINNED, **{name: version}))
                self.assertEqual(len(problems), 1, problems)

    def test_a_pinned_sdk_the_build_does_not_name_is_a_problem(self):
        used = dict(PINNED)
        del used["hip"]
        problems = self.problems(used)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("hip", problems[0].lower())

    def test_a_part_the_build_left_out_on_purpose_is_not_judged(self):
        used = dict(PINNED, hip="6.4.0", optix="9.1.0")
        self.assertEqual(self.problems(used, left_out=("hip", "optix")), [])
        self.assertEqual(len(self.problems(used, left_out=("hip",))), 1)

    def test_the_build_that_exposed_this_is_refused_on_every_sdk_that_differs(self):
        """2026-11-14: OptiX 9.1, CUDA 12.9 alone, HIP 6.4, an ocloc of another numbering; DLSS as pinned."""
        problems = self.problems({"optix": "9.1.0", "cuda": ["12.9"], "hip": "6.4", "ocloc": "2026.1", "dlss": "310.9.1"})
        text = "\n".join(problems)
        for name in ("OptiX", "CUDA", "HIP", "ocloc"):
            self.assertIn(name, text)
        self.assertNotIn("DLSS", text)


class ExceptionTests(unittest.TestCase):
    def setUp(self):
        self.pins = module().read_pins(PIN_FILE, "windows")
        self.used = dict(PINNED, hip="6.4.0")

    def problems(self, exceptions):
        return module().problems(self.pins, self.used, exceptions)

    def test_an_exception_covers_exactly_its_pin_and_version(self):
        self.assertEqual(self.problems([exception()]), [])
        self.assertEqual(len(self.problems([])), 1)

    def test_an_exception_for_another_version_or_an_older_pin_covers_nothing(self):
        for changes in ({"used": "6.3.0"}, {"pin": "7.0.0"}, {"sdk": "optix"}):
            with self.subTest(**changes):
                problems = self.problems([exception(**changes)])
                self.assertTrue(any("6.4.0" in problem for problem in problems), problems)
                self.assertTrue(any("exception" in problem for problem in problems), problems)

    def test_an_exception_must_say_why_who_and_when(self):
        for field in ("reason", "approved_by", "approved_on"):
            with self.subTest(missing=field):
                problems = self.problems([exception(**{field: "  "})])
                self.assertTrue(any(field in problem for problem in problems), problems)

    def test_an_exception_nothing_needs_is_a_problem(self):
        problems = module().problems(self.pins, dict(PINNED), [exception()])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("exception", problems[0])

    def test_one_cuda_toolkit_can_be_excepted_without_the_other(self):
        used = dict(PINNED, cuda=["12.9.1"])
        entry = exception(sdk="cuda", pin="13.4.1", used="none", reason="Every kernel is built with toolkit 12")
        self.assertEqual(module().problems(self.pins, used, [entry]), [])

    def test_this_source_s_exceptions_file_is_complete(self):
        entries = module().source_exceptions(ROOT)
        for entry in entries:
            self.assertEqual(module().exception_problems(entry), [], entry)


class CommandTests(unittest.TestCase):
    def run_check(self, record, exceptions=()):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "source")
            (source / "build_files/buildbot/config").mkdir(parents=True)
            (source / "build_files/buildbot/config/blender_version.cmake").write_text(PIN_FILE, encoding="utf-8")
            (source / "scripts/angelo").mkdir(parents=True)
            (source / "scripts/angelo/gpu_sdk_exceptions.json").write_text(
                json.dumps({"exceptions": list(exceptions)}), encoding="utf-8")
            path = Path(folder, "angelo-release-build.json")
            path.write_text(json.dumps(record), encoding="utf-8-sig")   # PowerShell writes a byte-order mark.
            lines = []
            code = module().main(["check", "--source", str(source), "--record", str(path)], out=lines.append)
            return code, "\n".join(lines)

    def test_a_record_with_the_pinned_versions_passes(self):
        code, text = self.run_check({"toolkit_versions": PINNED, "allowed_missing": []})
        self.assertEqual(code, 0, text)

    def test_a_record_with_another_version_fails_and_says_which(self):
        code, text = self.run_check({"toolkit_versions": dict(PINNED, optix="9.1.0"), "allowed_missing": []})
        self.assertEqual(code, 1, text)
        self.assertIn("9.1.0", text)
        self.assertIn("9.0.0", text)

    def test_a_record_that_names_no_exact_versions_fails(self):
        code, text = self.run_check({"toolkits": {"optix": "9.1 at C:\\somewhere"}, "allowed_missing": []})
        self.assertEqual(code, 1, text)
        self.assertIn("toolkit_versions", text)

    def test_a_record_s_approved_exception_passes(self):
        code, text = self.run_check({"toolkit_versions": dict(PINNED, hip="6.4.0"), "allowed_missing": []}, [exception()])
        self.assertEqual(code, 0, text)
        self.assertIn("exception", text, "a pass under an exception says so")

    def test_the_pins_command_prints_what_the_release_script_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            (source / "build_files/buildbot/config").mkdir(parents=True)
            (source / "build_files/buildbot/config/blender_version.cmake").write_text(PIN_FILE, encoding="utf-8")
            lines = []
            self.assertEqual(module().main(["pins", "--source", str(source)], out=lines.append), 0)
            self.assertEqual(json.loads("\n".join(lines)), {"sdks": PINNED, "cuda_default_major": 13, "unknown": []})


class ReleaseScriptTests(unittest.TestCase):
    """The Windows scripts run only on the build machine: these read their source."""

    def test_the_release_build_judges_its_toolkits_before_it_configures(self):
        toolchain = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        body = toolchain[toolchain.index("function Get-GpuToolkits"):]
        self.assertIn("gpu_sdk_pins.py", body)
        for key in ("cuda", "optix", "hip", "ocloc", "dlss"):
            self.assertRegex(body, r"\$versions\.%s\s*=" % key, "the exact %s version is collected" % key)
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        self.assertLess(build.index("Get-GpuToolkits $allowed"), build.index("# --- Configure"))
        self.assertIn("toolkit_versions", build, "the build's record names the exact versions")

    def test_the_build_s_record_carries_the_machine_s_verified_installers(self):
        """``windows_toolkits.ps1`` records each pinned installer it installed (version, SHA-256, signer) in
        ``toolkits-installed.json``; the build's record carries it beside the versions the build judged."""
        toolchain = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        body = toolchain[toolchain.index("function Get-ToolkitInstallRecord"):]
        self.assertIn("toolkits-installed.json", body)
        self.assertIn("return $null", body[:body.index("\n}\n")], "a machine set up by hand has no record: nothing is made up")
        self.assertRegex(toolchain, r"return @\{ toolkits = \$toolkits;[^\n]*installers = ")
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("toolkit_installers = $gpu.installers", build)

    def test_both_cuda_toolkits_reach_cmake_as_blender_s_own_build_passes_them(self):
        toolchain = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        for option in ("-DCUDA${major}_TOOLKIT_ROOT_DIR=", "-DCUDA${major}_NVCC_EXECUTABLE=",
                       "-DCUDA_TOOLKIT_ROOT_DIR=", "-DCUDA_NVCC_EXECUTABLE="):
            self.assertIn(option, toolchain)
        self.assertIn("cuda_default_major", toolchain, "the default toolkit is the major version Blender names")

    def test_ocloc_s_version_is_read_where_its_build_carries_one(self):
        """2026-11-15, the pinned ocloc (oneAPI 2025.3): its ``ocloc.exe`` carries no version at all (every field
        0), while the Graphics Compiler beside it, ``igc64.dll``, carries the driver build Blender pins
        (32.0.101.8424). A reader of ``ocloc.exe`` alone refuses the pinned ocloc itself."""
        toolchain = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        body = toolchain[toolchain.index("function Get-OclocVersion"):]
        body = body[:body.index("\n}\n")]
        self.assertLess(body.index('"ocloc.exe"'), body.index('"igc64.dll"'), "ocloc.exe first, then the compiler beside it")
        self.assertRegex(body, r"-ne 0", "a version of nothing but zeros is no version")
        self.assertIn("Test-Path", body, "a folder without the compiler library still answers from ocloc.exe")

    def test_every_toolkit_path_reaches_cmake_with_forward_slashes(self):
        """2026-11-15, the first build with both CUDA toolkits passed to CMake: ``FindCUDA.cmake`` hands
        ``CUDA_TOOLKIT_ROOT_DIR`` to a macro, which parses it again, and ``C:\\Program Files`` stopped the configure
        with "Invalid character escape '\\P'". A path with forward slashes means the same to CMake and survives any
        number of readings, so every toolkit path goes through one function, not only the two that failed."""
        toolchain = (SCRIPTS / "windows_release_toolchain.ps1").read_text(encoding="utf-8")
        helper = toolchain[toolchain.index("function ConvertTo-CMakePath"):]
        self.assertIn(".Replace('\\', '/')", helper[:helper.index("\n}\n")])
        body = toolchain[toolchain.index("function Get-GpuToolkits"):]
        options = re.findall(r'\$cmakeGpu \+= "(-D[A-Za-z0-9_${}]+)=([^\n]*)"\s*$', body, re.M)
        self.assertGreaterEqual(len(options), 8, "the two toolkits of each CUDA major, the default, OptiX, HIP, ocloc, DLSS")
        for name, value in options:
            self.assertTrue(value.startswith("$(ConvertTo-CMakePath "), "%s is passed as %s" % (name, value))

    def test_no_path_reaches_cmake_or_cpack_with_a_backslash(self):
        """The class behind three separate failures (LIBDIR written into generated CMake code, the staging hook
        handed to CPack, the CUDA roots parsed twice by FindCUDA): a Windows path in a CMake value is one reading
        away from an invalid escape. Every ``-D`` value in the Windows scripts is either a literal without a
        backslash or goes through ``ConvertTo-CMakePath``, and so does every path argument of the release
        build's own cmake and cpack commands. A ``-D`` whose value is computed and is not converted is named here."""
        not_paths = {
            "{0}": "the switches of -AllowMissing: ON or OFF",
            # CMake's own name for the Visual Studio installation, given as vswhere reports it. CMake matches it
            # against the installed instances itself, no project code reads it, and its documentation says a build
            # tree's value must not change once set: every existing tree holds this spelling.
            "CMAKE_GENERATOR_INSTANCE": "as Visual Studio reports it",
        }
        seen = 0
        for script in sorted(SCRIPTS.glob("*.ps1")):
            for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for name, rest in re.findall(r'-D ?(?:`")?([A-Za-z0-9_${}]+)=([^\n]*)', line):
                    where = "%s:%d -D%s" % (script.name, number, name)
                    seen += 1
                    if rest.startswith("$(ConvertTo-CMakePath "):
                        continue
                    if rest[:1] in ("$", "{"):
                        self.assertIn(name, not_paths, where + " is computed: a path goes through ConvertTo-CMakePath")
                        continue
                    literal = re.match(r'[^\s"`\')]*', rest).group(0)
                    self.assertNotIn("\\", literal, where)
        self.assertGreaterEqual(seen, 20, "the reading still finds the scripts' -D options")

    def test_every_quoted_argument_of_the_release_build_s_commands_is_converted_or_named(self):
        """The release build's own cmake and cpack commands: each argument that is quoted for the command line (a
        path may hold a space) and holds a variable gets it through ``ConvertTo-CMakePath``, or is named here with
        why not. However a line is written, a new path argument that skips the function fails this."""
        as_given = {
            "$($toolset.generator)": "the generator's name, not a path",
            "-DCMAKE_GENERATOR_INSTANCE=$($vs.installationPath)": "as Visual Studio reports it (the test above)",
            "$_": "each toolkit option, converted where Get-GpuToolkits makes it",
        }
        build = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
        configure = build[build.index("$arguments = @("):build.index('Write-Host ("cmake "')]
        lines = [line for line in configure.splitlines() if not line.lstrip().startswith("#")]
        lines += [line for line in build.splitlines() if "cmake --build" in line or "$cpack =" in line or "$cpack +=" in line]
        quoted = [argument for line in lines for argument in re.findall(r'`"(.*?)`"', line)]
        self.assertGreaterEqual(len(quoted), 9, quoted)
        for argument in quoted:
            rest = re.sub(r"\$\(ConvertTo-CMakePath \$[\w.:]+\)", "", argument)
            if "$" in rest:
                self.assertIn(argument, as_given, "a variable reaches cmake or cpack without ConvertTo-CMakePath")
            self.assertNotIn("\\", rest, argument)
        text = "\n".join(lines)
        for passed in ('"-S", "`"', '"-B", "`"', '"-C", "`"', 'cmake --build `"', "cpack -G WIX -C Release -B `\"",
                       "CPACK_PRE_BUILD_SCRIPTS=$("):
            self.assertIn(passed, text, "the release build passes this path")
        self.assertNotIn(".Replace(", text, "one function writes CMake's paths, not each line its own")

    def test_the_release_stage_judges_the_build_s_own_record(self):
        validate = (SCRIPTS / "windows_release_validate.ps1").read_text(encoding="utf-8")
        self.assertIn("gpu-sdks", validate)
        self.assertIn("angelo-release-build.json", validate)
        checks = (SCRIPTS / "release_checks.py").read_text(encoding="utf-8")
        self.assertIn('"gpu-sdks"', checks)

    def test_the_documentation_says_what_is_pinned_and_how_an_exception_is_made(self):
        text = (ROOT / "doc/angelo/native_editor_build.rst").read_text(encoding="utf-8")
        self.assertIn("gpu_sdk_pins.py", text)
        self.assertIn("gpu_sdk_exceptions.json", text)


if __name__ == "__main__":
    unittest.main()
