# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The application identity has one source and no file keeps a Blender identity of its own.

Dependency-free. ``scripts/modules/angelo_identity.json`` is read by the CMake loader and the Python
module; these tests pin that every consumer reads it, that nothing that could collide with a stock
Blender install (folders, GUIDs, ids, executable names, links) is hard-coded, and that the Windows
and Linux templates -- which cannot be built on the CI host -- render to well-formed files through
the real CMake loader. Windows installers themselves are not built here (see
doc/angelo/app_identity.rst, "Windows validation").
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/modules"))
import identity_fixtures  # noqa: E402
import angelo_identity  # noqa: E402
# Only angelo_identity is needed from Blender's modules folder. Left on the path, it would make Blender's own
# ``bpy`` package importable (and fail on ``_bpy``) for every later test that asks for bpy in the same run.
sys.path.remove(str(HERE.parents[2] / "scripts/modules"))

ROOT = identity_fixtures.ROOT
IDENTITY = identity_fixtures.IDENTITY

# Values stock Blender uses. An Angelo install must never share them.
BLENDER_WIN_UPGRADE_NAMESPACE = uuid.UUID("507F933F-5898-404A-9A05-18282FD491A6")
BLENDER_THUMBNAIL_CLSID = "D45F043D-F17F-4E8A-8435-70971D9FA46D"

# Files that carry the product identity. They must not hard-code Blender's.
IDENTITY_FILES = (
    "build_files/cmake/packaging.cmake",
    "build_files/cmake/angelo_identity.cmake",
    "release/windows/installer_wix/WIX.template.in",
    "release/windows/installer_wix/WixUI_Angelo.wxs",
    "release/windows/msix/AppxManifest.xml.template.in",
    "release/windows/icons/winblender.rc",
    "release/darwin/Blender.app/Contents/Info.plist",
    "release/darwin/Blender.app/Contents/PlugIns/blender-thumbnailer.appex/Contents/Info.plist",
    "release/freedesktop/angelo.metainfo.xml.in",
    "source/blender/blendthumb/src/blendthumb_win32.rc",
    "source/blender/blendthumb/src/blendthumb_win32_dll.cc",
    "source/blender/blendthumb/src/thumbnail_provider.mm",
    "source/blender/blenlib/intern/winstuff.cc",
    "source/blender/blenlib/intern/system_win32.cc",
    "source/creator/blender_launcher_win32.c",
    "intern/ghost/intern/GHOST_SystemPathsCocoa.mm",
    "intern/ghost/intern/GHOST_SystemPathsWin32.cc",
    "intern/ghost/intern/GHOST_SystemPathsUnix.cc",
    "scripts/startup/bl_ui/space_topbar.py",
)
FORBIDDEN = (
    "Blender Foundation", "fund.blender.org", "redirect.blender.org", "org.blenderfoundation",
    "blender-launcher", "blender.exe", "foundation@blender.org", "D45F043D", "WixUI_Blender", "RegisterBlender",
    "Registering Blender", "Blender has stopped working", "Blender Thumbnail", "Blender Thumbnailer",
)


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class IdentityDataTests(unittest.TestCase):
    def test_macos_floor_is_shared_by_compiler_and_both_bundles(self):
        minimum = IDENTITY["bundle"]["macos_min_version"]
        self.assertEqual(minimum, "26.0")
        for path, thumbnailer in ((identity_fixtures.APP_PLIST, False),
                                  (identity_fixtures.THUMBNAILER_PLIST, True)):
            self.assertEqual(identity_fixtures.resolved_plist(path, thumbnailer)["LSMinimumSystemVersion"], minimum)
        platform = read("build_files/cmake/platform/platform_apple_xcode.cmake")
        self.assertIn('set(OSX_MIN_DEPLOYMENT_TARGET "${ANGELO_MACOS_MIN_VERSION}")', platform)
        self.assertIn('set(CMAKE_OSX_DEPLOYMENT_TARGET "${OSX_MIN_DEPLOYMENT_TARGET}"', platform)

    def test_the_python_module_is_the_json(self):
        self.assertEqual(angelo_identity.PRODUCT_NAME, IDENTITY["product"]["name"])
        self.assertEqual(angelo_identity.VENDOR_NAME, IDENTITY["vendor"]["name"])
        self.assertEqual(angelo_identity.BUNDLE_ID, IDENTITY["bundle"]["macos_id"])
        self.assertEqual(angelo_identity.URL_SCHEME, IDENTITY["windows"]["url_scheme"])
        self.assertEqual(angelo_identity.BASED_ON, "Blender", "the attribution is part of the identity")

    def test_the_current_values_are_the_decided_ones(self):
        """Decided values are as decided; pending ones keep their placeholder until the one line changes."""
        self.assertEqual(IDENTITY["version"]["label"], "1.0.2")
        self.assertEqual(angelo_identity.APP_VERSION_DISPLAY, "1.0.2")
        self.assertEqual(IDENTITY["vendor"]["name"], "Hyperbond Studio PTE. LTD.")
        self.assertEqual(IDENTITY["bundle"]["macos_id"], "studio.hyperbond.angelo")  # Final.
        self.assertEqual(IDENTITY["bundle"]["linux_desktop_id"], IDENTITY["bundle"]["macos_id"])
        self.assertIn("bundle.macos_id", IDENTITY["_decided"])
        self.assertNotIn("bundle.macos_id", IDENTITY["_pending"])
        self.assertNotIn("version.label_override", IDENTITY["_pending"])
        self.assertIn("distribution", IDENTITY["_decided"])
        for key in IDENTITY["_pending"]:
            self.assertTrue(key.split(".")[0] in IDENTITY, f"pending note for an unknown section: {key}")

    def test_guids_are_new_fixed_and_distinct_from_blenders(self):
        windows = IDENTITY["windows"]
        for key in ("msi_upgrade_code", "thumbnail_clsid"):
            self.assertEqual(str(uuid.UUID(windows[key])).upper(), windows[key], f"{key} is a canonical upper-case GUID")
        self.assertNotEqual(windows["msi_upgrade_code"], windows["thumbnail_clsid"])
        self.assertNotEqual(windows["thumbnail_clsid"], BLENDER_THUMBNAIL_CLSID)
        # Blender derives its upgrade code from its install folder; Angelo's must never equal it, for any 5.x.
        for minor in range(0, 10):
            derived = str(uuid.uuid5(BLENDER_WIN_UPGRADE_NAMESPACE, f"Blender Foundation/Blender 5.{minor}")).upper()
            self.assertNotEqual(windows["msi_upgrade_code"], derived)

    def test_no_identifier_collides_with_a_stock_blender_install(self):
        everything = " ".join(str(v) for section in ("paths", "bundle", "windows") for v in IDENTITY[section].values())
        scanned = everything.lower().replace(IDENTITY["bundle"]["macos_imported_document_uti"], "")  # Blender's .blend type.
        self.assertNotRegex(scanned, r"blender|toonlab|sensei", "ids and folders name Angelo, not Blender")
        # The one deliberate exception: the .blend file type is Blender's, imported rather than owned.
        self.assertEqual(IDENTITY["bundle"]["macos_imported_document_uti"], "org.blenderfoundation.blender.file")
        self.assertNotEqual(IDENTITY["bundle"]["macos_id"], "org.blenderfoundation.blender")
        self.assertNotEqual(IDENTITY["paths"]["user_dir_name"].lower(), "blender")
        self.assertNotEqual(IDENTITY["windows"]["app_user_model_id"].lower().split(".")[0], "blender")

    def test_every_link_is_https_and_angelo_links_are_marked_pending(self):
        table = angelo_identity.url_table()
        for key, value in table.items():
            self.assertTrue(value.startswith("https://"), f"{key}: {value}")
        for key in IDENTITY["pending_urls"]:
            self.assertTrue(table[key].startswith("https://angelo.studio"), f"pending {key} is an angelo.studio placeholder")
        for key in ("blender_manual", "blender_api", "blender_website", "license", "credits"):
            self.assertIn("blender.org", table[key], f"{key} is Blender attribution/documentation")
        self.assertEqual(angelo_identity.url("blender_manual", language="en", major=5, minor=2),
                         "https://docs.blender.org/manual/en/5.2/")
        with self.assertRaises(KeyError):
            angelo_identity.url("no_such_link")


class UiAndSourceTests(unittest.TestCase):
    def test_every_link_in_the_table_is_used_somewhere(self):
        """A link nothing draws is a dead entry: a decision about it would change nothing."""
        roots = ("scripts/startup", "scripts/modules", "release", "build_files", "source/creator",
                 "source/blender/blenlib", "README.md")
        files = []
        for root in roots:
            base = ROOT / root
            files += [base] if base.is_file() else [
                path for path in base.rglob("*") if path.is_file() and path.suffix in
                {".py", ".in", ".cmake", ".txt", ".html", ".cc", ".md", ".rc", ".template", ".xml", ".wxs"}
                and path.name != "angelo_identity.json" and "icons" not in path.parts]
        text = "\n".join(path.read_text(errors="ignore") for path in files)
        for key in angelo_identity.url_table():
            patterns = (f'url("{key}"', f"ANGELO_URL_{key.upper()}", f'["urls"]["{key}"]')
            self.assertTrue(any(pattern in text for pattern in patterns), f"link {key!r} is not used anywhere")

    def class_source(self, path, name):
        text = read(path)
        start = text.index(f"class {name}(")
        match = re.search(r"\n(?=class |def |\w+ = )", text[start + 1:])
        return text[start:start + 1 + (match.start() if match else len(text))]

    def test_help_menu_and_splash_take_every_link_from_the_url_table(self):
        sources = {
            "Help": self.class_source("scripts/startup/bl_ui/space_topbar.py", "TOPBAR_MT_help"),
            "Splash": self.class_source("scripts/startup/bl_operators/wm.py", "WM_MT_splash"),
            "About": self.class_source("scripts/startup/bl_operators/wm.py", "WM_MT_splash_about"),
            "Presets": self.class_source("scripts/startup/bl_operators/wm.py", "WM_OT_url_open_preset"),
        }
        for name, source in sources.items():
            self.assertNotRegex(source, r"https?://", f"{name} hard-codes a link")
            for key in re.findall(r'angelo_identity\.url\("([a-z_]+)"', source):
                self.assertIn(key, angelo_identity.url_table(), f"{name} uses an unknown link {key}")
        help_menu = sources["Help"]
        for entry in ("'MANUAL'", "'BLENDER_MANUAL'", "'SUPPORT'", "'RELEASE_NOTES'"):
            self.assertIn(entry, help_menu)
        # Donations are not a primary Help entry; About keeps Donate and Support Blender, below Angelo's links.
        self.assertNotIn("'FUND'", help_menu)
        about = sources["About"]
        self.assertIn("'FUND'", about)
        self.assertIn('"blender_fund"', about)
        self.assertLess(about.index("'CREDITS'"), about.index("'FUND'"))
        self.assertNotIn("build_branch", about, "About does not show the source branch")
        self.assertIn("APP_VERSION_DISPLAY", about, "About uses the title bar's version (1.0, not 1.0.0)")
        self.assertIn("Blender Manual", help_menu, "Blender features keep their own manual entry")
        self.assertNotIn("BUG'", help_menu.split("show_developer")[0])  # Report a Bug follows the developer block.
        self.assertIn("'BUG'", help_menu)

    def test_preset_urls_resolve_to_angelo_or_marked_blender_destinations(self):
        wm = read("scripts/startup/bl_operators/wm.py")
        presets = wm[wm.index("    preset_items = ["):wm.index("    @staticmethod\n    def lookup_url_from_type")]
        angelo_keys = {"release_notes", "manual", "support", "community", "site", "donate"}
        self.assertEqual(set(re.findall(r'angelo_identity\.url\("([a-z_]+)"\)', presets)) - angelo_keys,
                         {"blender_website", "credits", "blender_extensions"})
        self.assertIn("_url_from_blender_manual", presets)
        self.assertIn('("report_bug")', read("scripts/modules/_bpy_internal/system_info/url_prefill_runtime.py").replace("'", '"'))

    def test_files_carrying_the_identity_hard_code_no_blender_identity(self):
        for path in IDENTITY_FILES:
            text = read(path)
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, text, f"{path} still contains {forbidden!r}")

    def test_os_store_items_and_managed_policy_are_named_from_the_bundle_identifier(self):
        """The team sign-in's OS key-store items and the macOS managed-preferences domain are named from the
        bundle id (the add-on keeps its own copy: it also runs without the identity module). Changing one
        alone would orphan every saved sign-in and IT policy, so they must agree."""
        sys.path.insert(0, str(ROOT / "scripts/addons_core"))
        from angelo_agent import managed_policy, team_token_store
        bundle_id = IDENTITY["bundle"]["macos_id"]
        self.assertEqual(managed_policy.DOMAIN, bundle_id)
        for service in (team_token_store.ACCOUNT_SERVICE, team_token_store.GRANT_SERVICE, team_token_store.ACCESS_SERVICE):
            self.assertTrue(service.startswith(bundle_id + "."), service)

    def test_windows_helper_scripts_start_the_renamed_executable(self):
        stem = IDENTITY["windows"]["exe_stem"]
        scripts = sorted((ROOT / "release/windows/batch").glob("blender_*.cmd"))
        self.assertTrue(scripts)
        for script in scripts:
            text = script.read_text()
            self.assertIn('"%~dp0\\' + stem + '"', text, script.name)
            self.assertNotIn("projects.blender.org", text, script.name)
            self.assertNotIn("dp0\\blender", text, script.name)
        self.assertIn("\\bin\\" + stem + ".exe", read("build_files/windows/find_blender.cmd"))

    def test_the_windows_validation_script_reads_only_identity_members_that_exist(self):
        script = read("scripts/angelo/windows_validate.ps1")
        members = set(re.findall(r"\$identity\.([a-z_]+)\.([a-z_]+)", script))
        self.assertTrue(members)
        for section, member in members:
            self.assertIn(member, IDENTITY[section], f"windows_validate.ps1 reads identity.{section}.{member}")
        self.assertIn("make.bat $BuildTarget", script)
        self.assertIn("cpack -G WIX", script)
        self.assertIn('"{0}-{1}-windows-x64.msi"', script)
        self.assertNotRegex(script, r"Read-Host|SecureString|-Credential|Authorization|ghp_|https?://[^ ]*@",
                            "the script never handles credentials")

    def test_native_sources_use_the_identity_macros(self):
        cocoa, win, unix = (read(f"intern/ghost/intern/GHOST_SystemPaths{p}") for p in ("Cocoa.mm", "Win32.cc", "Unix.cc"))
        self.assertIn('"%s/" ANGELO_USER_DIR_NAME "/%s"', cocoa)
        self.assertEqual(win.count('"\\\\" ANGELO_USER_DIR_NAME "\\\\"'), 2)
        self.assertIn("ANGELO_USER_DIR_NAME_UNIX", unix)
        self.assertIn("ANGELO_CRASH_FILE_NAME", read("source/blender/blenkernel/intern/blender.cc"))
        self.assertIn("ANGELO_USER_DIR_NAME_UNIX", read("source/blender/imbuf/intern/thumbs.cc"))
        self.assertNotIn('"fail" SEP_STR "blender"', read("source/blender/imbuf/intern/thumbs.cc"))
        self.assertIn("ANGELO_TEMP_PREFIX", read("source/blender/blenkernel/intern/appdir.cc"))
        self.assertIn("ANGELO_USER_DIR_NAME", read("source/blender/blenkernel/intern/appdir.cc"))
        self.assertIn("ANGELO_WIN_LAUNCHER_NAME", read("source/blender/blenlib/intern/system_win32.cc"))
        self.assertIn("ANGELO_PRODUCT_NAME L\" has stopped working\"", read("source/blender/blenlib/intern/system_win32.cc"))
        self.assertIn("ANGELO_WIN_CLAIM_BLEND_DEFAULT", read("source/blender/blenlib/intern/winstuff.cc"))
        self.assertIn("ANGELO_WIN_EXE_NAME", read("source/creator/blender_launcher_win32.c"))

    def test_cmake_sets_everything_the_plists_ask_for_and_defines_what_the_loader_promises(self):
        """CMake passes only the *standard* MACOSX_BUNDLE_* target properties into an Info.plist (a custom
        MACOSX_BUNDLE_FOO resolved to an empty string in the real build); the other values must be plain
        identity variables the loader sets."""
        creator = read("source/creator/CMakeLists.txt")
        loader = read("build_files/cmake/angelo_identity.cmake")
        standard = {"MACOSX_BUNDLE_GUI_IDENTIFIER", "MACOSX_BUNDLE_BUNDLE_NAME", "MACOSX_BUNDLE_COPYRIGHT",
                    "MACOSX_BUNDLE_SHORT_VERSION_STRING", "MACOSX_BUNDLE_LONG_VERSION_STRING",
                    "MACOSX_BUNDLE_EXECUTABLE_NAME"}
        for plist in (identity_fixtures.APP_PLIST, identity_fixtures.THUMBNAILER_PLIST):
            for name in set(re.findall(r"\$\{((?:MACOSX_BUNDLE|ANGELO)_[A-Z_]+)\}", plist.read_text())):
                if name.startswith("MACOSX_BUNDLE_"):
                    self.assertIn(name, standard, f"{plist.name}: {name} is not passed through by CMake")
                    if name != "MACOSX_BUNDLE_EXECUTABLE_NAME":
                        self.assertRegex(creator, rf"(?m)^\s+{name} ", f"{name} is never set")
                else:
                    self.assertRegex(loader, rf"_angelo_get\({name}\b|set\({name}\b", f"{name} is never defined")
        self.assertIn('MACOSX_BUNDLE_GUI_IDENTIFIER "${ANGELO_MACOS_BUNDLE_ID}"', creator)
        self.assertEqual(creator.count('MACOSX_BUNDLE_LONG_VERSION_STRING "${ANGELO_APP_VERSION} (based on ${ANGELO_BASED_ON_NAME} '
                                       '${ANGELO_BLENDER_BASE_VERSION}) ${BLENDER_DATE}"'), 2)
        self.assertEqual(creator.count('MACOSX_BUNDLE_SHORT_VERSION_STRING "${ANGELO_APP_VERSION}"'), 2)
        self.assertIn('MACOSX_BUNDLE_GUI_IDENTIFIER "${ANGELO_MACOS_THUMBNAILER_ID}"', creator)
        self.assertNotIn("MACOSX_BUNDLE_ANGELO", creator + loader)
        used_in = IDENTITY_FILES + ("source/blender/blenkernel/intern/appdir.cc", "intern/ghost/intern/GHOST_WindowWin32.cc",
                                    "source/blender/blenkernel/intern/blender.cc", "intern/ghost/intern/GHOST_SystemWin32.cc",
                                    "source/blender/windowmanager/intern/wm_platform_support.cc")
        for name in re.findall(r"-D(ANGELO_[A-Z_]+|BLENDER_WIN_APPID[A-Z_]*)=", loader):
            self.assertTrue(any(name in read(path) for path in used_in)
                            or name in {"ANGELO_BASED_ON_NAME", "ANGELO_VENDOR_NAME", "ANGELO_COPYRIGHT"},
                            f"{name} is defined by the loader but used nowhere")

    def test_scripts_and_readmes_agree_with_the_identity(self):
        self.assertIn(f'{IDENTITY["bundle"]["macos_executable"]}.app', read("scripts/angelo/build_native.sh"))
        for path in ("README.md", ".github/README.md"):
            self.assertIn(IDENTITY["urls"]["site"], read(path))
            self.assertIn("based on [Blender]", read(path).replace("\n", " "))
        self.assertEqual(read("README.md"), read(".github/README.md"))
        self.assertFalse((ROOT / ".github/FUNDING.yml").exists(), "no sponsor button pointing at Blender's fund")


@unittest.skipUnless(shutil.which("cmake"), "CMake is required to render the templates")
class RenderedTemplateTests(unittest.TestCase):
    """The templates are filled in by the real CMake loader, in script mode."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="angelo-identity-")
        cls.out = Path(cls.temp.name)
        script = cls.out / "render.cmake"
        script.write_text(f"""
set(CMAKE_SOURCE_DIR "{ROOT.as_posix()}")
set(BLENDER_VERSION 5.2)
set(BLENDER_VERSION_MAJOR 5)
set(BLENDER_VERSION_MINOR 2)
set(BLENDER_VERSION_PATCH 2)
macro(add_definitions)
  foreach(_d ${{ARGN}})
    file(APPEND "{cls.out.as_posix()}/definitions.txt" "${{_d}}\\n")
  endforeach()
endmacro()
macro(set_property)
endmacro()
set(WIN32 ON)
include("{ROOT.as_posix()}/build_files/cmake/angelo_identity.cmake")
# Info.plists are processed by `configure_file` with variables (the standard MACOSX_BUNDLE_* ones come from
# target properties in the real build; the ANGELO_* ones are plain loader variables).
set(MACOSX_BUNDLE_EXECUTABLE_NAME "Angelo")
set(MACOSX_BUNDLE_SHORT_VERSION_STRING "${{ANGELO_APP_VERSION}}")
set(MACOSX_BUNDLE_LONG_VERSION_STRING "${{ANGELO_APP_VERSION}} (based on ${{ANGELO_BASED_ON_NAME}} ${{ANGELO_BLENDER_BASE_VERSION}}) 2026-01-01")
set(MACOSX_BUNDLE_COPYRIGHT "${{ANGELO_COPYRIGHT}}")
set(MACOSX_BUNDLE_GUI_IDENTIFIER "${{ANGELO_MACOS_BUNDLE_ID}}")
set(MACOSX_BUNDLE_BUNDLE_NAME "${{ANGELO_PRODUCT_NAME}}")
configure_file("{ROOT.as_posix()}/release/darwin/Blender.app/Contents/Info.plist" "{cls.out.as_posix()}/app.plist")
set(MACOSX_BUNDLE_GUI_IDENTIFIER "${{ANGELO_MACOS_THUMBNAILER_ID}}")
set(MACOSX_BUNDLE_BUNDLE_NAME "${{ANGELO_PRODUCT_NAME}} Thumbnailer")
configure_file("{ROOT.as_posix()}/release/darwin/Blender.app/Contents/PlugIns/blender-thumbnailer.appex/Contents/Info.plist"
               "{cls.out.as_posix()}/thumbnailer.plist")
foreach(_pair
    "release/windows/installer_wix/WIX.template.in|wix.xml"
    "release/windows/msix/AppxManifest.xml.template.in|msix.xml"
    "release/freedesktop/angelo.metainfo.xml.in|metainfo.xml"
    "release/text/readme.html|readme.html"
    "release/text/copyright.txt|copyright.txt"
    "release/darwin/dmg_window.applescript.in|dmg_window.applescript")
  string(REPLACE "|" ";" _pair "${{_pair}}")
  list(GET _pair 0 _in)
  list(GET _pair 1 _out)
  configure_file("{ROOT.as_posix()}/${{_in}}" "{cls.out.as_posix()}/${{_out}}" @ONLY)
endforeach()
message("WIX_FILE_ID=${{ANGELO_WIN_EXE_WIX_FILE_ID}}")
""")
        done = subprocess.run(["cmake", "-P", str(script)], capture_output=True, text=True)
        cls.result = done
        cls.definitions = (cls.out / "definitions.txt").read_text() if (cls.out / "definitions.txt").exists() else ""

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def rendered(self, name):
        return (self.out / name).read_text(encoding="utf-8")

    def test_cmake_loader_succeeds_and_defines_the_identity(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        for expected in ('-DANGELO_PRODUCT_NAME="Angelo"', '-DANGELO_USER_DIR_NAME="Angelo"',
                         '-DANGELO_CRASH_FILE_NAME="angelo.crash.txt"', '-DBLENDER_WIN_APPID="Angelo.Desktop"',
                         '-DANGELO_WIN_EXE_NAME="Angelo.exe"', '-DANGELO_WIN_LAUNCHER_NAME="Angelo-launcher.exe"',
                         '-DANGELO_WIN_CLAIM_BLEND_DEFAULT=0',
                         '-DANGELO_WIN_THUMBNAIL_CLSID_STR="{' + IDENTITY["windows"]["thumbnail_clsid"] + '}"'):
            self.assertIn(expected, self.definitions)
        others = [line for line in self.definitions.splitlines()
                  if not line.startswith(("-DANGELO_BASED_ON_NAME", "-DANGELO_COPYRIGHT", "-DANGELO_RC_COMMENTS"))]
        self.assertNotIn("Blender", "\n".join(others), "only the attribution definitions name Blender")

    def run_loader_with(self, label):
        """The loader against a copy of the identity with ``version.label`` set to ``label``."""
        import json
        tree = Path(tempfile.mkdtemp(prefix="override-", dir=self.out))
        (tree / "scripts/modules").mkdir(parents=True)
        (tree / "build_files/cmake").mkdir(parents=True)
        shutil.copy(ROOT / "build_files/cmake/angelo_identity.cmake", tree / "build_files/cmake")
        data = json.loads(json.dumps(IDENTITY))
        data["version"]["label"] = label
        (tree / "scripts/modules/angelo_identity.json").write_text(json.dumps(data))
        script = tree / "run.cmake"
        script.write_text(f"""
set(CMAKE_SOURCE_DIR "{tree.as_posix()}")
set(BLENDER_VERSION 5.2)
set(BLENDER_VERSION_MAJOR 5)
set(BLENDER_VERSION_MINOR 2)
set(BLENDER_VERSION_PATCH 2)
macro(add_definitions)
endmacro()
macro(set_property)
endmacro()
include("{tree.as_posix()}/build_files/cmake/angelo_identity.cmake")
message("PARTS=${{ANGELO_APP_VERSION_MAJOR}}.${{ANGELO_APP_VERSION_MINOR}}.${{ANGELO_APP_VERSION_PATCH}} VER=${{ANGELO_APP_VERSION}} "
        "DISPLAY=${{ANGELO_APP_VERSION_DISPLAY}} BASE=${{ANGELO_BLENDER_BASE_VERSION}} DEFS=${{ANGELO_VERSION_DEFINITIONS}}")
""")
        return subprocess.run(["cmake", "-P", str(script)], capture_output=True, text=True)

    def test_the_product_version_is_angelos_own_and_blenders_is_only_the_base(self):
        done = self.run_loader_with("1.0.0")
        self.assertEqual(done.returncode, 0, done.stderr)
        output = done.stderr + done.stdout
        self.assertIn("PARTS=1.0.0 VER=1.0.0 DISPLAY=1.0 BASE=5.2.2", output)
        self.assertIn('-DANGELO_APP_VERSION_DISPLAY="1.0"', output)
        self.assertIn("DISPLAY=1.2.3 BASE=5.2.2", (self.run_loader_with("1.2.3")).stderr)
        self.assertIn("PARTS=2.0.1 VER=2.0.1 DISPLAY=2.0.1", self.run_loader_with("2.0.1").stderr)
        for bad in ("one", "1.0", "1.0.0.0", ""):
            result = self.run_loader_with(bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn("version.label must look like 1.0.0", result.stderr)
        for forbidden in ("a;b", 'a"b'):
            self.assertNotEqual(self.run_loader_with(forbidden).returncode, 0, forbidden)

    def test_global_definitions_hold_only_stable_names(self):
        """Every compile command carries the global definitions: a value that follows a pending decision
        (bundle id, links, vendor, version) must be scoped, or deciding it recompiles all of Blender."""
        loader = read("build_files/cmake/angelo_identity.cmake")
        first = re.search(r"add_definitions\(([^)]*)\)", loader).group(1)
        names = set(re.findall(r"-D(ANGELO_[A-Z_]+)=", first))
        self.assertEqual(names, {"ANGELO_PRODUCT_NAME", "ANGELO_BASED_ON_NAME", "ANGELO_USER_DIR_NAME",
                                 "ANGELO_USER_DIR_NAME_UNIX", "ANGELO_CRASH_FILE_NAME", "ANGELO_TEMP_PREFIX"})
        self.assertIn("add_definitions(${ANGELO_THUMBNAILER_DEFINITIONS})", read("source/blender/blendthumb/CMakeLists.txt"))
        for volatile in ("URL", "VENDOR", "THUMBNAILER", "VERSION", "BUNDLE"):
            self.assertFalse([n for n in names if volatile in n], volatile)

    def test_version_definitions_are_scoped_and_blenders_version_plumbing_is_untouched(self):
        loader = read("build_files/cmake/angelo_identity.cmake")
        for body in re.findall(r"add_definitions\(([^)]*)\)", loader):
            self.assertNotIn("APP_VERSION", body,
                             "a global definition of the version would recompile everything on every release")
        for path in ("source/blender/windowmanager/CMakeLists.txt", "source/blender/editors/space_info/CMakeLists.txt"):
            self.assertIn("add_definitions(${ANGELO_VERSION_DEFINITIONS})", read(path))
        creator = read("source/creator/CMakeLists.txt")
        for blender_only in ('set(TARGETDIR_VER "${ANGELO_MACOS_EXECUTABLE}.app/Contents/Resources/${BLENDER_VERSION}")',
                             'set(TARGETDIR_VER "${BLENDER_VERSION}")'):
            self.assertIn(blender_only, creator, "config/scripts subfolders keep Blender's 5.2")
        packaging = read("build_files/cmake/packaging.cmake")
        self.assertIn("${ANGELO_PRODUCT_NAME}-${ANGELO_APP_VERSION}-${ANGELO_PACKAGE_OS}-${PACKAGE_ARCH}", packaging)
        self.assertIn("set(MAJOR_VERSION ${ANGELO_APP_VERSION_MAJOR})", packaging)
        for path in ("source/blender/windowmanager/intern/wm_splash_screen.cc", "source/blender/windowmanager/intern/wm_window.cc",
                     "source/blender/editors/space_info/info_stats.cc"):
            self.assertIn("ANGELO_APP_VERSION_DISPLAY", read(path), path)
        self.assertNotIn("def app_version_label", read("scripts/modules/angelo_identity.py"))

    def test_templates_render_completely(self):
        for name in ("wix.xml", "msix.xml", "metainfo.xml", "readme.html", "copyright.txt", "dmg_window.applescript"):
            self.assertNotRegex(self.rendered(name), r"@ANGELO_[A-Z_]+@", f"{name} has an unfilled identity value")
        for name in ("wix.xml", "msix.xml", "metainfo.xml"):
            ET.fromstring(self.rendered(name).encode("utf-8"))
        bundle = IDENTITY["bundle"]["macos_executable"] + ".app"  # The item the DMG window script places.
        self.assertIn(f'set position of item "{bundle}" to ', self.rendered("dmg_window.applescript"))

    def test_info_plists_resolve_every_value_from_the_identity(self):
        import plistlib
        bundle = IDENTITY["bundle"]
        for name, thumbnailer in (("app.plist", False), ("thumbnailer.plist", True)):
            data = (self.out / name).read_bytes()
            self.assertNotIn(b"${", data, f"{name} has an unresolved variable")
            info = plistlib.loads(data)
            expected = identity_fixtures.resolved_plist(
                identity_fixtures.THUMBNAILER_PLIST if thumbnailer else identity_fixtures.APP_PLIST, thumbnailer)
            for key in ("CFBundleIdentifier", "CFBundleName", "CFBundleDisplayName", "CFBundleGetInfoString"):
                if key in info and key in expected:
                    self.assertEqual(info[key].replace("2026-01-01", "${DATE}"),
                                     expected[key].replace("${MACOSX_BUNDLE_LONG_VERSION_STRING}",
                                                          "1.0.2 (based on Blender 5.2.2) ${DATE}"), key)
        app = plistlib.loads((self.out / "app.plist").read_bytes())
        self.assertEqual(app["CFBundleIdentifier"], bundle["macos_id"])
        self.assertTrue(app["CFBundleGetInfoString"].endswith(", " + IDENTITY["vendor"]["name"]))
        self.assertEqual([t["UTTypeIdentifier"] for t in app["UTImportedTypeDeclarations"]], [bundle["macos_imported_document_uti"]])
        self.assertEqual(app["CFBundleDocumentTypes"][0]["LSItemContentTypes"], [bundle["macos_imported_document_uti"]])
        thumb = plistlib.loads((self.out / "thumbnailer.plist").read_bytes())
        self.assertEqual(thumb["CFBundleIdentifier"], bundle["macos_id"] + ".thumbnailer")
        self.assertEqual(thumb["NSExtension"]["NSExtensionAttributes"]["QLSupportedContentTypes"],
                         [bundle["macos_imported_document_uti"]])

    def test_wix_template_names_angelo_and_its_launcher_file_id(self):
        text = self.rendered("wix.xml")
        self.assertIn("CM_FP_Angelo.Angelo.exe", text)
        self.assertNotIn("Angelo_launcher", text, "the installer waits for the main executable, not the launcher")
        self.assertIn("Registering Angelo", text)
        self.assertIn("RegisterAngelo", text)
        self.assertIn("https://angelo.studio/welcome?utm_source=angelo-", text)
        self.assertNotRegex(text, r"(?i)blender")
        self.assertIn('Id="WixUI_Angelo"', read("release/windows/installer_wix/WixUI_Angelo.wxs"))

    def test_msix_manifest_has_angelo_identity_and_protocol(self):
        text = self.rendered("msix.xml")
        windows = IDENTITY["windows"]
        self.assertIn(f'Identity Name="{windows["msix_identity_name"]}[PACKAGETYPE]"', text)
        self.assertIn(f"<PublisherDisplayName>{IDENTITY['vendor']['name']}</PublisherDisplayName>", text)
        self.assertIn(f'Application Id="{windows["msix_application_id"]}"', text)
        self.assertIn('Executable="Angelo\\Angelo-launcher.exe"', text)
        self.assertIn(f'Protocol Name="{windows["url_scheme"]}"', text)
        self.assertIn('Alias="Angelo-launcher.exe"', text)
        self.assertNotRegex(text, r"(?i)blender\\\\|BLENDER\"|Name=\"blender\"")
        self.assertIn("[VERSION]", text, "the packaging step still owns the bracketed values")

    def test_metainfo_readme_and_copyright_name_angelo_and_credit_blender(self):
        meta = self.rendered("metainfo.xml")
        self.assertIn("<id>studio.hyperbond.angelo</id>", meta)
        self.assertIn("<name>Angelo</name>", meta)
        self.assertNotIn("blender.org", meta)
        readme = self.rendered("readme.html")
        self.assertIn("Angelo 1.0.2", readme)
        self.assertIn("based on\n      Blender 5.2.2", readme)
        self.assertIn("https://www.blender.org/about/license/", readme)
        copyright_text = self.rendered("copyright.txt")
        for needed in ("Angelo 1.0.2 is based on Blender 5.2.2", "Hyperbond Studio PTE. LTD.", "Copyright (C) 2023  Blender Foundation",
                       "GNU General Public License", "Stichting Blender Foundation"):
            self.assertIn(needed, copyright_text)


if __name__ == "__main__":
    unittest.main()
