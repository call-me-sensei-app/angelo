# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

"""No builder path and no credential in what ships (scripts/angelo/builder_paths.py, doc/angelo/releasing.rst,
"No builder paths"): the scan, its allowlist and receipts, and the build steps that keep paths out."""

import importlib.util
import json
import marshal
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import builder_paths  # noqa: E402
import release_checks  # noqa: E402

spec = importlib.util.spec_from_file_location("angelo_python_bytecode",
                                              ROOT / "build_files/utils/angelo_python_bytecode.py")
bytecode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bytecode)

IDENTITY = ["builder-host-7", "pat.example"]


def wide(text):
    return text.encode("utf-16-le")


class Bundle(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name, "Angelo.app")
        self.put("Contents/MacOS/Angelo", b"\xcf\xfa\xed\xfe clean")

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self, allow=()):
        return builder_paths.scan(self.root, IDENTITY, list(allow), folders=[])


class IsolatedInterpreterTests(Bundle):
    """The Windows build runs the scan as ``python.exe -I -B release_checks.py builder-paths``: isolated mode leaves
    the script's own folder off sys.path, and 1.0.1's first build failed there with "No module named builder_paths"."""

    def test_release_checks_finds_builder_paths_under_python_isolated_mode(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/angelo/release_checks.py"),
                                 "builder-paths", str(self.root)], cwd=tempfile.gettempdir(),
                                capture_output=True, text=True, timeout=120)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ScanTests(Bundle):
    def test_a_clean_bundle_passes_and_every_file_is_read(self):
        self.put("Contents/Resources/text.txt", b"no paths here: angelo/source/x.cc build/y.o ~/.cache")
        report = self.scan()
        self.assertEqual(report["hits"], {})
        self.assertEqual(report["files"], 2)

    def test_personal_and_machine_paths_in_text_binaries_and_utf16(self):
        cases = {
            "Contents/MacOS/Angelo": b"\x00\x01/Users/somebody/src/creator.cc\x00",
            "Contents/Resources/a.oso": b'%filename{"/home/dev/shaders/node.osl"}',
            "Contents/Resources/b.bin": b"tmp at /private/var/folders/ab12_cd/T/x and /var/folders/zz/T",
            "Contents/Resources/c.dll": b"\x00" + wide(r"C:\Users\Builder\AppData\x.pdb") + b"\x00",
            "Contents/Resources/d.txt": b"file:///Users/someone/notes and D:/Users/Other/x",
            ".DS_Store": b"\x00Z/:private:var:folders:m5:ab12:T:angelo-dmg-x:writable.dmg\x00Macintosh HD:Users:pat:",
        }
        for name, data in cases.items():
            self.put(name, data)
        report = self.scan()
        self.assertEqual(set(report["hits"]), set(cases) - set(), report["hits"])
        self.assertIn("home folder (Windows)", report["hits"]["Contents/Resources/c.dll"])
        self.assertEqual(report["hits"]["Contents/Resources/b.bin"]["temporary folder (macOS)"], 2)

    def test_windows_short_names_in_text_and_utf16(self):
        self.put("Contents/Helpers/codex.exe", b"panicked at C:\\ANGELO~1\\cargo\\registry\\src\\x.rs:12")
        self.put("Contents/Resources/w.dll", b"\x00" + wide(r"D:\BUILD~12\obj\a.obj") + b"\x00")
        self.put("Contents/Resources/fine.txt", b"version~1 and x86_64~2 and ~/.config")
        report = self.scan()
        self.assertEqual(set(report["hits"]), {"Contents/Helpers/codex.exe", "Contents/Resources/w.dll"})
        self.assertIn("Windows short (8.3) path", report["hits"]["Contents/Resources/w.dll"])

    def test_the_builders_own_folders_in_long_and_short_form_are_never_allowed(self):
        folders = [r"C:\angelo\src", r"C:\ANGELO~2\BUILD-~1"]
        self.put("Contents/Resources/a.exe", b"c:/Angelo/SRC/source/creator.cc")
        self.put("Contents/Resources/b.dll", wide(r"C:\ANGELO~2\BUILD-~1\x.pdb"))
        allow = [(["*"], re.compile(".*"), "everything")]
        report = builder_paths.scan(self.root, IDENTITY, allow, folders)
        self.assertEqual(set(report["hits"]), {"Contents/Resources/a.exe", "Contents/Resources/b.dll"})
        self.assertIn("builder folder", report["hits"]["Contents/Resources/a.exe"])

    def test_a_ci_accounts_home_is_no_builder_folder(self):
        old = os.environ.get("HOME")
        os.environ["HOME"] = "/Users/runner"
        try:
            self.assertNotIn("/Users/runner", builder_paths.builder_folders())
        finally:
            os.environ["HOME"] = old

    def test_paths_inside_other_paths_and_urls_are_not_personal(self):
        self.put("Contents/Resources/vr.py", b'"/user/hand/left/input/home/click" https://example.edu/home/paper.pdf')
        self.assertEqual(self.scan()["hits"], {})

    def test_the_building_machines_own_names_are_found_in_any_case_and_encoding(self):
        self.put("Contents/Resources/x.json", b'{"host": "BUILDER-HOST-7.local"}')
        self.put("Contents/Resources/y.dll", wide("signed by pat.example"))
        report = self.scan()
        self.assertEqual(set(report["hits"]), {"Contents/Resources/x.json", "Contents/Resources/y.dll"})
        self.assertTrue(all("builder identity" in counts for counts in report["hits"].values()))

    def test_generic_and_short_names_are_no_identity(self):
        os.environ["ANGELO_TEST_UNUSED"] = "1"
        names = builder_paths.builder_identity(["runner", "abc", "Builder-Host-7"])
        self.assertIn("Builder-Host-7", names)
        self.assertNotIn("runner", names)
        self.assertNotIn("abc", names)

    def test_credentials_are_found_and_never_shown_in_full(self):
        key = "sk-ant-api03-" + "A1b2C3d4" * 12
        self.put("Contents/Resources/k.py", ("KEY = %r" % key).encode())
        self.put("Contents/Resources/pem", b"-----BEGIN PRIVATE KEY-----\n" + b"MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" * 3)
        report = self.scan()
        self.assertEqual(set(report["hits"]), {"Contents/Resources/k.py", "Contents/Resources/pem"})
        self.assertNotIn(key, json.dumps(report))

    def test_a_table_of_key_prefixes_and_documented_examples_are_not_keys(self):
        self.put("Contents/Helpers/codex", b"OPENAI_BASE_URLsk-proj-sk-svcacct-sk-admin-sk-ant-sk-or-OPENAI_API_KEYT3BlbkFJmissing")
        self.put("Contents/Resources/aws.py", b'example = "AKIAIOSFODNN7EXAMPLE"')
        self.assertEqual(self.scan()["hits"], {})

    def test_an_empty_folder_is_refused(self):
        shutil.rmtree(self.root)
        self.root.mkdir()
        with self.assertRaises(builder_paths.ScanError):
            self.scan()



class ShippedSourceTests(unittest.TestCase):
    """Angelo's own shipped sources already pass the release scan's path and credential rules, so a comment or
    docstring example ("/Users/<name>/x.blend", written with a placeholder) fails here, not in the release build.
    The building machine's own names are the release scan's part: they come from the machine, not the source."""

    def test_angelos_shipped_sources_hold_no_personal_path_or_credential(self):
        sources = [ROOT / "scripts/addons_core/angelo_agent"] + sorted(
            path for folder in ("scripts/modules", "scripts/startup") for path in (ROOT / folder).glob("*angelo*"))
        for source in sources:
            folder = source if source.is_dir() else source.parent
            only = {path.relative_to(folder).as_posix() for path in ([source] if source.is_file() else
                    source.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}
            with self.subTest(source=source.relative_to(ROOT).as_posix()):
                report = builder_paths.scan(folder, (), [], folders=[], only=only)
                self.assertEqual({}, report["hits"], report["examples"])

class AllowTests(Bundle):
    def test_an_entry_allows_only_its_files_and_its_whole_match(self):
        allow = [(["Contents/Resources/lib/*"], re.compile("/Users/upstream"), "upstream library")]
        self.put("Contents/Resources/lib/libx.dylib", b"/Users/upstream/build/x.c")
        self.put("Contents/MacOS/Angelo", b"/Users/upstream/build/x.c /Users/upstreamer/y")
        report = self.scan(allow)
        self.assertEqual(set(report["hits"]), {"Contents/MacOS/Angelo"})
        self.assertEqual(report["hits"]["Contents/MacOS/Angelo"]["home folder (macOS)"], 2)
        self.assertEqual(report["allowed"], {"upstream library": 1})

    def test_credentials_and_identity_are_never_allowed(self):
        allow = [(["*"], re.compile(".*"), "everything")]
        self.put("Contents/Resources/a", b"host builder-host-7")
        self.put("Contents/Resources/b", b"AKIA" + b"Q7XW2B9KZPL4MNRT")
        self.assertEqual(set(self.scan(allow)["hits"]), {"Contents/Resources/a", "Contents/Resources/b"})

    def test_openais_runner_built_helpers_pass_in_long_and_short_form_and_nothing_else_does(self):
        """aws-lc's __FILE__ in OpenAI's unchanged Windows helpers: C:\\Users\\RUNNER~1\\CARGO~1\\registry\\..."""
        runner = rb"C:\Users\RUNNER~1\CARGO~1\registry\src\INDEXC~1.IO-\AWS-LC~1.0\aws-lc\crypto\x.c"
        self.put("agent-runtime/codex-code-mode-host.exe", runner + b" C:\\Users\\runneradmin\\.cargo")
        self.put("agent-runtime/codex.exe", runner)
        self.put("agent-runtime/codex-windows-sandbox-setup.exe", rb"C:\Users\BOB~1\src\a.rs")
        report = builder_paths.scan(self.root, IDENTITY, builder_paths.load_allow(), folders=[])
        self.assertEqual(set(report["hits"]), {"agent-runtime/codex.exe", "agent-runtime/codex-windows-sandbox-setup.exe"},
                         "only OpenAI's own helpers, and only its runner account")

    def test_the_shipped_allowlist_is_complete_and_names_no_builder_of_ours(self):
        data = json.loads(builder_paths.ALLOW_FILE.read_text())
        self.assertTrue(data["allow"])
        for entry in data["allow"]:
            with self.subTest(reason=entry.get("reason")):
                self.assertTrue(entry["files"] and entry["match"] and len(entry["reason"]) > 40)
                self.assertNotIn("*", entry["match"].replace("(?:", ""), "a match names exact accounts, no wildcard")
        builder_paths.load_allow()


class WindowsFirstRunTests(Bundle):
    """The first Windows release scan: what an install wrote, and the upstream files Angelo ships unchanged."""

    def test_only_the_files_an_install_wrote_are_scanned(self):
        self.put("Contents/Resources/installed.dll", b"clean")
        self.put("makesrna.pdb", wide(r"C:\angelo\src\source\blender\makesrna\x.cc"))
        manifest = self.root.parent / "install_manifest.txt"
        manifest.write_text("%s\n%s\n" % (self.root / "Contents/Resources/installed.dll", self.root / "Contents/MacOS/Angelo"))
        only = builder_paths.listed_files(self.root, manifest)
        self.assertEqual(only, {"Contents/Resources/installed.dll", "Contents/MacOS/Angelo"})
        report = builder_paths.scan(self.root, IDENTITY, [], folders=[r"C:\angelo\src"], only=only)
        self.assertEqual((report["files"], report["hits"]), (2, {}))
        self.assertIn("makesrna.pdb", builder_paths.scan(self.root, IDENTITY, [], folders=[r"C:\angelo\src"])["hits"])

    def test_upstream_windows_strings_pass_and_only_they_do(self):
        allow = builder_paths.load_allow()
        self.put("Angelo.exe", b"C:\\Users\\admin\\AppData\\Local\\Temp\\copybuffer.blend")
        self.put("5.2/python/Lib/site-packages/setuptools/_distutils/tests/test_util.py", b"'/home/distutils'")
        self.put("Angelo-helper.dll", b"C:\\Users\\admin\\x")
        report = builder_paths.scan(self.root, IDENTITY, allow, folders=[])
        self.assertEqual(set(report["hits"]), {"Angelo-helper.dll"})

    def test_bytecode_is_read_string_by_string(self):
        """In raw marshal bytes a path runs into the next object's type byte ("/home/distutilsN"); read through its
        code objects, each string ends where it ends, so exact allowlist entries hold in bytecode too."""
        source = self.root.parent / "test_util.py"
        source.write_text('"""Example: /home/distutils."""\nPATHS = ("/home/distutils", "/home/distutils", 1)\n')
        pyc = self.root / "5.2/python/Lib/site-packages/setuptools/_distutils/tests/__pycache__/test_util.cpython.pyc"
        pyc.parent.mkdir(parents=True)
        py_compile.compile(str(source), cfile=str(pyc), dfile="test_util.py", doraise=True)
        mine = self.root / "5.2/scripts/__pycache__/mine.cpython.pyc"
        mine.parent.mkdir(parents=True)
        (self.root.parent / "mine.py").write_text('WHERE = "/Users/somebody/build"\n')
        py_compile.compile(str(self.root.parent / "mine.py"), cfile=str(mine), dfile="mine.py", doraise=True)
        report = builder_paths.scan(self.root, IDENTITY, builder_paths.load_allow(), folders=[])
        self.assertEqual(set(report["hits"]), {"5.2/scripts/__pycache__/mine.cpython.pyc"})
        self.assertIn("/Users/somebody", report["examples"]["5.2/scripts/__pycache__/mine.cpython.pyc"])
        self.assertIsNone(builder_paths._pyc_strings(b"not bytecode"), "unreadable files are scanned raw")

    def test_build_artifacts_are_refused_by_type_and_never_allowed(self):
        """They are excluded from the install (source/creator/CMakeLists.txt), never allowlisted."""
        allow = [(["*"], re.compile(".*"), "everything")]
        for name in ("5.2/python/Lib/venv/scripts/nt/venvlauncher.pdb", "Angelo.exp", "x.ILK", "numpy/npymath.lib"):
            self.put(name, b"clean")
        report = builder_paths.scan(self.root, IDENTITY, allow, folders=[])
        self.assertEqual(set(report["hits"]), {"5.2/python/Lib/venv/scripts/nt/venvlauncher.pdb", "Angelo.exp", "x.ILK",
                                               "numpy/npymath.lib"})
        self.assertTrue(all("build artifact" in "".join(c) for c in report["hits"].values()))

    def test_windows_dlls_are_installed_without_their_import_libraries(self):
        text = (ROOT / "source/creator/CMakeLists.txt").read_text()
        for target in ("BlendThumb", "blender_cpu_check"):
            with self.subTest(target=target):
                self.assertIn("FILES $<TARGET_FILE:%s>" % target, text)
        windows = text[text.index("if(WIN32 AND NOT WITH_PYTHON_MODULE)"):]
        self.assertNotIn("TARGETS blender_cpu_check", windows[:windows.index("endif()\n  set_target_properties(")])

    def test_the_windows_python_install_leaves_build_artifacts_out(self):
        text = (ROOT / "source/creator/CMakeLists.txt").read_text()
        block = text[text.index("DIRECTORY ${LIBDIR}/python/${_PYTHON_VERSION_NO_DOTS}/lib"):]
        block = block[:block.index("\n      )")]
        for pattern in ('"*.pdb"', '"*.exp"', '"*.ilk"', '"*.lib"'):
            self.assertIn("PATTERN %s EXCLUDE" % pattern, block)


class ReceiptTests(Bundle):
    def test_a_clean_scan_receipt_matches_only_its_installer_bytes(self):
        installer = self.root.parent / "Angelo-1.0.0-windows-x64.msi"
        installer.write_bytes(b"msi bytes")
        receipt = installer.with_name(installer.name + ".builder-paths.json")
        self.assertIn("no builder-path receipt", builder_paths.receipt_problem(installer))
        self.assertEqual(builder_paths.main([str(self.root), "--installer", str(installer), "--receipt", str(receipt)]),
                         0)
        self.assertIsNone(builder_paths.receipt_problem(installer))
        installer.write_bytes(b"msi bytes, signed")
        self.assertIn("other bytes", builder_paths.receipt_problem(installer))

    def test_a_dirty_scan_fails_and_writes_no_receipt(self):
        installer = self.root.parent / "Angelo-1.0.0-windows-x64.msi"
        installer.write_bytes(b"msi")
        receipt = installer.with_name(installer.name + ".builder-paths.json")
        self.put("Contents/Resources/x", b"/Users/somebody/")
        self.assertEqual(builder_paths.main([str(self.root), "--installer", str(installer), "--receipt", str(receipt)]),
                         1)
        self.assertFalse(receipt.exists())

    def test_release_checks_runs_it(self):
        self.assertEqual(release_checks.main(["builder-paths", str(self.root)]), 0)
        self.put("Contents/Resources/x", b"/home/somebody/")
        self.assertEqual(release_checks.main(["builder-paths", str(self.root)]), 1)


class BytecodeTests(unittest.TestCase):
    def test_bytecode_records_a_relative_source_and_old_bytecode_is_compiled_again(self):
        with tempfile.TemporaryDirectory() as folder:
            scripts = Path(folder, "5.2", "scripts")
            scripts.mkdir(parents=True)
            source = scripts / "module.py"
            source.write_text("def f():\n    return 1\n")
            cfile = Path(bytecode.cache_file(str(source)))
            cfile.parent.mkdir()
            # A previous build's bytecode with its absolute path is not "current".
            py_compile.compile(str(source), cfile=str(cfile),
                               invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
            report = bytecode.precompile(str(scripts))
            self.assertEqual([Path(p).name for p in report["compiled"]], ["module.py"])
            code = marshal.loads(cfile.read_bytes()[16:])
            self.assertEqual(code.co_filename, "scripts/module.py")
            self.assertNotIn(folder.encode(), cfile.read_bytes())
            self.assertEqual(bytecode.precompile(str(scripts))["current"], 1)


class BuildConfigurationTests(unittest.TestCase):
    def test_every_target_maps_source_build_library_and_home(self):
        text = (ROOT / "build_files/cmake/angelo_build_paths.cmake").read_text()
        for needle in ('option(ANGELO_MAP_BUILD_PATHS', "$ENV{HOME}=~", "${CMAKE_SOURCE_DIR}=angelo",
                       "${CMAKE_BINARY_DIR}=build", "${LIBDIR}=lib", "-ffile-prefix-map=", "/d1trimfile:",
                       "/PDBALTPATH:%_PDB%", "-oso_prefix"):
            self.assertIn(needle, text)
        main = (ROOT / "CMakeLists.txt").read_text()
        self.assertLess(main.index("include(platform_apple)"), main.index("angelo_build_paths.cmake"),
                        "after the platform files, which set LIBDIR")
        self.assertLess(main.index("angelo_build_paths.cmake"), main.index("add_subdirectory(intern)"),
                        "before any target is defined")

    def test_osl_bytecode_is_rewritten_with_the_same_map(self):
        text = (ROOT / "intern/cycles/kernel/osl/shaders/CMakeLists.txt").read_text()
        self.assertIn("angelo_map_generated_paths(_OSO_MAP_PATHS ${_OSO_FILE})", text)
        self.assertIn("${_OSO_MAP_PATHS}", text)

    @unittest.skipUnless(shutil.which("cmake"), "cmake is not installed")
    def test_the_rewriter_maps_the_longest_prefix_first(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder, "x.oso")
            target.write_text('# options: -I/h/me/src/shaders -o /h/me/build/x.oso\n%filename{"/h/me/src/a.osl"}\n')
            subprocess.run(["cmake", "-DANGELO_FILE=%s" % target, "-DANGELO_MAP=/h/me=~::/h/me/src=angelo::/h/me/build=build",
                            "-P", str(ROOT / "build_files/cmake/angelo_map_paths.cmake")], check=True,
                           capture_output=True)
            self.assertEqual(target.read_text(),
                             '# options: -Iangelo/shaders -o build/x.oso\n%filename{"angelo/a.osl"}\n')

    def test_the_disk_image_is_styled_outside_the_per_user_temporary_folder(self):
        """Finder's background alias in the shipped .DS_Store records the writable image's path."""
        import dmg_macos
        self.assertEqual(dmg_macos.WORK_ROOT, "/tmp")
        self.assertIn('TemporaryDirectory(prefix="angelo-dmg-", dir=dmg_macos.WORK_ROOT)',
                      (ROOT / "scripts/angelo/sign_notarize_macos.py").read_text())

    def test_the_windows_installer_carries_no_pdb_unless_asked(self):
        text = (ROOT / "source/creator/CMakeLists.txt").read_text()
        self.assertIn('option(ANGELO_SHIP_WINDOWS_PDB "Install blender.pdb', text)
        self.assertLess(text.index("if(NOT ANGELO_SHIP_WINDOWS_PDB)"), text.index("RENAME blender.pdb"))

    def test_every_release_stage_runs_the_scan(self):
        mac = (ROOT / "scripts/angelo/build_release_macos.sh").read_text()
        self.assertIn('release_checks.py" builder-paths "$BUILD/bin/Angelo.app"', mac)
        self.assertIn('--identity-path "$SOURCE" --identity-path "$BUILD"', mac)
        windows = (ROOT / "scripts/angelo/build_release_windows.ps1").read_text()
        self.assertIn("builder-paths", windows)
        self.assertIn(".builder-paths.json", windows)
        self.assertEqual(windows.count("@builderFolders"), 2, "both Windows scans pass the source and build folders")
        self.assertIn('@("--files-from", $installManifest)', windows)
        publish = (ROOT / "scripts/angelo/publish_installers.py").read_text()
        self.assertIn("builder_paths.scan_disk_image", publish)
        self.assertIn("builder_paths.receipt_problem", publish)


if __name__ == "__main__":
    unittest.main()
