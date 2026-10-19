# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Approved artwork and packaged icon integrity; no image editing or GUI needed."""

import hashlib
import json
import struct
import sys
import unittest
import re
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity_fixtures  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
BRAND = ROOT / "release/branding/angelo"
EXPORTS = BRAND / "exports"


def lfs_content(path):
    """An LFS-tracked file as ``(sha256, bytes or None)``: the checkout holds either the file or its pointer."""
    data = path.read_bytes()
    pointer = re.match(rb"version https://git-lfs\.github\.com/spec/v1\noid sha256:([0-9a-f]{64})\n", data)
    if pointer:
        return pointer.group(1).decode(), None
    return hashlib.sha256(data).hexdigest(), data


def lfs_size(path):
    """The byte size of an LFS-tracked file, from the file or from its pointer."""
    data = path.read_bytes()
    pointer = re.match(rb"version https://git-lfs\.github\.com/spec/v1\noid sha256:[0-9a-f]{64}\nsize (\d+)\n", data)
    return int(pointer.group(1)) if pointer else len(data)


def tiff_tags(data):
    """The first directory of a little-endian TIFF as ``{tag: value}``, for its SHORT, LONG and RATIONAL tags."""
    offset, = struct.unpack_from("<I", data, 4)
    count, = struct.unpack_from("<H", data, offset)
    tags = {}
    for index in range(count):
        tag, kind, _length, value = struct.unpack_from("<HHII", data, offset + 2 + 12 * index)
        if kind == 3:
            value &= 0xFFFF
        elif kind == 5:
            numerator, denominator = struct.unpack_from("<II", data, value)
            value = numerator / denominator
        tags[tag] = value
    return tags


class AngeloBrandingTests(unittest.TestCase):
    def test_final_raven_master_is_selected_and_unchanged(self):
        selected = json.loads((BRAND / "selection.json").read_text())
        self.assertEqual(selected["status"], "selected_final")
        self.assertEqual(selected["concept"], "Curious Raven")
        self.assertEqual(selected["app_name"], "Angelo")
        self.assertEqual(selected["master"], "angelo-raven-teal-v2.png")
        self.assertEqual(selected["background"], "transparent, head only; no icon tile")
        master = (BRAND / selected["master"]).read_bytes()
        self.assertEqual(master, (EXPORTS / "angelo-logo.png").read_bytes())
        manifest = json.loads((EXPORTS / "manifest.json").read_text())
        self.assertEqual(hashlib.sha256(master).hexdigest(), manifest["master_sha256"])
        for name, checksum in manifest["files"].items():
            self.assertEqual(hashlib.sha256((EXPORTS / name).read_bytes()).hexdigest(), checksum, name)

    def test_splash_is_the_exporters_render_of_its_source(self):
        # release/datafiles is not fetched from LFS in CI; the pointer still names the content.
        record = json.loads((BRAND / "coverage.json").read_text())["splash"]
        self.assertEqual(record["source"], "angelo-splash.svg")
        self.assertEqual(record["pixels"], [2000, 1000], "Twice the 1000 x 500 units: scaled down, never up, to interface scale 4")
        checksum, data = lfs_content(ROOT / record["output"])
        self.assertEqual(checksum, record["sha256"], "Rerun build_files/utils/angelo_branding.mjs and commit its output")
        if data is not None:
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(list(struct.unpack(">II", data[16:24])), record["pixels"])
        exporter = (ROOT / "build_files/utils/angelo_branding.mjs").read_text()
        self.assertIn("splashSvg({artwork, wordmark: word(facetedA())})", exporter)
        self.assertIn("await render(splash, splashPng, ...SPLASH_PIXELS)", exporter)

    def test_installer_art_is_the_exporters_output_in_the_sizes_the_installers_expect(self):
        # CPack copies the DMG picture and WiX embeds the two bitmaps as they are. CI holds LFS pointers,
        # which still name the content and its size; a materialised file is checked for its format as well.
        record = json.loads((BRAND / "coverage.json").read_text())["installer_art"]
        self.assertEqual(record["source"], "build_files/utils/angelo_installer_scenes.mjs")
        expected = {
            "dmg_background": (["release/darwin/background.tif", "release/darwin/buildbot/background.tif"],
                               [1080, 700], "TIFF, LZW, 8-bit RGB"),
            "wix_dialog": (["release/windows/installer_wix/WIX_UI_DIALOG.png"], [493, 312], "PNG, 8-bit RGB"),
            "wix_banner": (["release/windows/installer_wix/WIX_UI_BANNER.bmp"], [493, 58], "BMP, 24-bit"),
        }
        self.assertEqual(set(record) - {"source"}, set(expected))
        for name, (outputs, pixels, image_format) in expected.items():
            piece = record[name]
            self.assertEqual((piece["outputs"], piece["pixels"], piece["format"]), (outputs, pixels, image_format), name)
            for output in outputs:
                with self.subTest(output=output):
                    checksum, data = lfs_content(ROOT / output)
                    self.assertEqual((checksum, lfs_size(ROOT / output)), (piece["sha256"], piece["bytes"]),
                                     "Rerun build_files/utils/angelo_installer_art.mjs and commit its output")
                    if data is None:
                        continue
                    if name == "dmg_background":
                        self.assertEqual(data[:4], b"II*\x00")
                        tags = tiff_tags(data)
                        # Width, height, LZW, resolution in pixels per inch.
                        self.assertEqual([tags[256], tags[257], tags[259], tags[282], tags[283], tags[296]],
                                         [1080, 700, 5, 144, 144, 2])
                    elif name == "wix_dialog":
                        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                        self.assertEqual((*struct.unpack(">II", data[16:24]), data[24], data[25]), (493, 312, 8, 2))
                    else:
                        self.assertEqual(data[:2], b"BM")
                        # Width, height (bottom-up), planes, bits per pixel, no compression.
                        self.assertEqual(struct.unpack_from("<iiHHI", data, 18), (493, 58, 1, 24, 0))
        # Finder draws the picture at its size in points: 1080 x 700 pixels at 144 dpi are 540 x 350.
        self.assertEqual(record["dmg_background"]["dpi"], 144)
        packaging = (ROOT / "build_files/cmake/packaging.cmake").read_text()
        self.assertIn('set(CPACK_DMG_BACKGROUND_IMAGE "${CMAKE_SOURCE_DIR}/release/darwin/background.tif")', packaging)
        self.assertIn("set(CPACK_WIX_UI_BANNER ${CMAKE_SOURCE_DIR}/release/windows/installer_wix/WIX_UI_BANNER.bmp)", packaging)
        self.assertIn("set(CPACK_WIX_UI_DIALOG ${CMAKE_SOURCE_DIR}/release/windows/installer_wix/WIX_UI_DIALOG.png)", packaging)

    def test_installer_art_keeps_the_text_drawn_over_it_legible(self):
        # The exporter measures WCAG contrast on the pixels it writes and refuses to export below these floors,
        # so the recorded figures belong to the files the checksums above name.
        record = json.loads((BRAND / "coverage.json").read_text())["installer_art"]
        exporter = (ROOT / "build_files/utils/angelo_installer_art.mjs").read_text()
        self.assertIn("const AA = 4.5, LEGIBLE = 3;", exporter)
        names = record["dmg_background"]["contrast"]
        self.assertEqual(set(names), {"name_black", "name_black_at_85", "name_white", "name_white_at_85", "arrow"})
        # Finder draws an icon's name black in a window with a background picture: AA for that.
        self.assertGreaterEqual(min(names["name_black"], names["name_black_at_85"]), 4.5)
        # A white name (not what Finder does) and the drag arrow (a graphic) stay at 3:1 or better.
        self.assertGreaterEqual(min(names["name_white"], names["name_white_at_85"], names["arrow"]), 3)
        for key in ("name_black", "name_black_at_85"):
            self.assertIn(f'{key}: requireContrast(', exporter)
            self.assertRegex(exporter, key + r": requireContrast\([^\n]*, AA\),")
        # Windows Installer draws its text in the default colour: the UI set gives its styles none.
        styles = ET.parse(ROOT / "release/windows/installer_wix/WixUI_Angelo.wxs").findall(".//{*}TextStyle")
        self.assertEqual(len(styles), 3)
        for style in styles:
            self.assertFalse({"Red", "Green", "Blue"} & set(style.attrib), "The bitmaps are paper white for black text")
        self.assertGreaterEqual(min(record["wix_dialog"]["contrast"].values()), 4.5)
        self.assertEqual(set(record["wix_dialog"]["contrast"]), {"text_black", "check_box_black"})
        self.assertGreaterEqual(record["wix_banner"]["contrast"]["text_black"], 4.5)
        # The document's table states the recorded figures.
        document = (ROOT / "doc/angelo/app_identity.rst").read_text()
        table = document.split("Text over the artwork", 1)[1].split("The disk image window", 1)[0]
        recorded = [*names.values(), *record["wix_dialog"]["contrast"].values(), record["wix_banner"]["contrast"]["text_black"]]
        self.assertEqual(re.findall(r" (\d+\.\d\d)$", table, re.M), [f"{value:.2f}" for value in recorded])
        # The text controls of WiX 3's WelcomeDlg, ExitDialog and InstallDirDlg, in installer units.
        scenes = (ROOT / "build_files/utils/angelo_installer_scenes.mjs").read_text()
        for control in ("box(135, 20, 220, 60)", "box(135, 70, 220, 70)", "box(135, 190, 220, 40)",
                        "box(15, 6, 200, 15)", "box(25, 23, 280, 15)"):
            self.assertIn(control, scenes)

    def test_installer_art_is_lit_by_the_splash_module_not_a_copy_of_it(self):
        scenes = (ROOT / "build_files/utils/angelo_installer_scenes.mjs").read_text()
        self.assertIn('import {LEAN, lean, facetedLight, grain, shadow} from "./angelo_splash.mjs";', scenes)
        self.assertIn('import {FACETS} from "./angelo_wordmark.mjs";', scenes)
        self.assertEqual(scenes.count("facetedLight({") + scenes.count("facetedLight(DMG_SCENE)"), 3, "One per piece")
        for copied in ("feTurbulence", "Gradient id=", "#082229", "#07151a"):
            self.assertNotIn(copied, scenes.replace('<linearGradient id="paper"', ""), copied)
        self.assertNotRegex(scenes, r'"#(?:a1f4d6|27bdbf|1aa3ad|0b8a99|087787)"', "Facet colours come from FACETS")
        splash = (ROOT / "build_files/utils/angelo_splash.mjs").read_text()
        self.assertIn("export function facetedLight(scene) {", splash)
        self.assertIn("const {defs, layers} = facetedLight(SPLASH_SCENE);", splash)

    def test_dmg_window_script_applies_the_layout_the_picture_is_drawn_for(self):
        picture = json.loads((BRAND / "coverage.json").read_text())["installer_art"]["dmg_background"]
        window = picture["window"]
        script = (ROOT / "release/darwin/dmg_window.applescript.in").read_text()
        for line in (
                "set the bounds to {%d, %d, %d, %d}" % tuple(window["bounds"]),
                'set position of item "@ANGELO_MACOS_EXECUTABLE@.app" to {%d, %d}' % tuple(window["app"]),
                'set position of item "Applications" to {%d, %d}' % tuple(window["applications"]),
                "set icon size of viewOptions to %d" % window["icon_size"],
                "set text size of viewOptions to %d" % window["text_size"],
                "set belowX to %d" % window["below"][0],
                "set position of item (contents of otherName) of container window to {belowX, %d}" % window["below"][1],
                'set background picture of viewOptions to file ".background:background.tif"'):
            self.assertIn("\t" + line + "\n", script)
        left, top, right, bottom = window["bounds"]
        width, height = (side * 72 // picture["dpi"] for side in picture["pixels"])
        self.assertEqual(right - left, width, "The window is as wide as the picture")
        # The window is never taller than the picture, whatever the title bar takes of its 372 points.
        self.assertLessEqual(bottom - top - 22, height)
        self.assertEqual(window["visible_height"], bottom - top - 32, "macOS 26 has a 32-point title bar")
        self.assertGreater(window["below"][1] - window["icon_size"] // 2, height, "Other files stay under the picture")
        packaging = (ROOT / "build_files/cmake/packaging.cmake").read_text()
        self.assertIn("${CMAKE_SOURCE_DIR}/release/darwin/dmg_window.applescript.in", packaging)
        self.assertIn('set(CPACK_DMG_DS_STORE_SETUP_SCRIPT "${CMAKE_BINARY_DIR}/release/darwin/dmg_window.applescript")', packaging)

    def test_png_sizes_and_alpha(self):
        manifest = json.loads((EXPORTS / "manifest.json").read_text())
        for size in manifest["png_sizes"]:
            data = (EXPORTS / f"angelo-{size}.png").read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))
            self.assertEqual(data[25], 6, "Keep transparent RGBA, not opaque RGB")

    def test_complete_mac_icon_container_and_source_binding(self):
        data = (EXPORTS / "Angelo.icns").read_bytes()
        self.assertEqual(struct.unpack(">4sI", data[:8]), (b"icns", len(data)))
        offset, types = 8, set()
        while offset < len(data):
            kind, length = struct.unpack(">4sI", data[offset:offset + 8])
            self.assertGreaterEqual(length, 8)
            types.add(kind)
            offset += length
            self.assertLessEqual(offset, len(data))
        self.assertEqual(offset, len(data))
        self.assertTrue({b"ic08", b"ic09", b"ic10"} <= types, "256/512/1024 frames required")
        info = identity_fixtures.resolved_plist()
        self.assertEqual(info["CFBundleIconFile"], "Angelo.icns")
        self.assertEqual(info["CFBundleName"], "Angelo")
        self.assertEqual(info["CFBundleDisplayName"], "Angelo")
        self.assertEqual(info["CFBundleIdentifier"], identity_fixtures.IDENTITY["bundle"]["macos_id"])
        self.assertEqual(info["CFBundleExecutable"], "${MACOSX_BUNDLE_EXECUTABLE_NAME}")
        self.assertNotIn("CFBundleIconName", info, "Old Liquid Glass branding must not override this icon")
        self.assertIn("release/branding/angelo/exports/Angelo.icns", (ROOT / "source/creator/CMakeLists.txt").read_text())
        creator = (ROOT / "source/creator/CMakeLists.txt").read_text()
        self.assertEqual(identity_fixtures.IDENTITY["bundle"]["macos_executable"], "Angelo")
        self.assertIn("OUTPUT_NAME ${ANGELO_MACOS_EXECUTABLE}", creator)
        self.assertIn('set(TARGETDIR_VER "${ANGELO_MACOS_EXECUTABLE}.app/Contents/Resources/${BLENDER_VERSION}")', creator)
        self.assertIn('DESTINATION "${ANGELO_MACOS_EXECUTABLE}.app/Contents"', creator)
        self.assertNotIn("Sensei.app", creator)

    def test_windows_container_has_valid_multiple_resolution_png_frames(self):
        data = (EXPORTS / "angelo.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 7))
        dimensions = []
        for index in range(count):
            width, height, colors, reserved, planes, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
            width, height = width or 256, height or 256
            self.assertEqual((colors, reserved, planes, bits), (0, 0, 1, 32))
            self.assertLessEqual(offset + length, len(data))
            image = data[offset:offset + length]
            self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", image[16:24]), (width, height))
            dimensions.append(width)
        self.assertEqual(dimensions, [16, 24, 32, 48, 64, 128, 256])

    def test_native_ui_mark_is_transparent_vector_not_app_tile(self):
        master = (BRAND / "angelo-mark-white.svg").read_bytes()
        self.assertEqual(master, (ROOT / "release/datafiles/icons_svg/blender.svg").read_bytes())
        tree = ET.fromstring(master)
        self.assertEqual(len(tree.findall(".//{*}path")), 4)
        self.assertEqual(tree.find(".//{*}path").get("fill-rule"), "evenodd", "Visor is negative space in the monochrome mark")
        for unsupported in ("image", "text", "rect"):
            self.assertEqual(tree.findall(".//{*}" + unsupported), [], unsupported)
        self.assertEqual(tree.find("{*}g").get("fill"), "#ffffff")
        self.assertEqual((EXPORTS / "angelo-white-32.png").read_bytes()[25], 6)

    def test_wordmark_file_glyph_and_wayland_are_native_paths(self):
        for name in ("blender_logo_large.svg", "file_blend.svg"):
            tree = ET.parse(ROOT / "release/datafiles/icons_svg" / name)
            self.assertTrue(tree.findall(".//{*}path"))
            self.assertFalse(tree.findall(".//{*}text"), "Font must be outlined for NanoSVG")
            self.assertFalse(tree.findall(".//{*}image"))
        wayland = ET.parse(BRAND / "angelo-mark-color.svg")
        self.assertEqual(len(wayland.findall(".//{*}path")), 7)
        self.assertTrue(wayland.findall(".//{*}linearGradient"))
        self.assertFalse(wayland.findall(".//{*}image"))
        self.assertFalse(wayland.findall(".//{*}text"))
        self.assertIn("#a1f4d6", (BRAND / "angelo-mark-color.svg").read_text())
        self.assertIn("release/branding/angelo/angelo-mark-color.svg", (ROOT / "source/blender/windowmanager/CMakeLists.txt").read_text())

    def test_windows_and_mac_document_icons_are_wired_without_stale_override(self):
        rc = (ROOT / "release/windows/icons/winblender.rc").read_text()
        self.assertIn('APPICON ICON DISCARDABLE "angelo.ico"', rc)
        self.assertIn('BLENDERFILE ICON DISCARDABLE "angelo-file.ico"', rc)
        for name in ("angelo.ico", "angelo-file.ico"):
            self.assertEqual((ROOT / "release/windows/icons" / name).read_bytes(), (EXPORTS / name).read_bytes())
        packaging = (ROOT / "build_files/cmake/packaging.cmake").read_text()
        self.assertNotIn("release/windows/icons/winblender.ico", packaging)
        info = identity_fixtures.resolved_plist()
        self.assertEqual(info["CFBundleDocumentTypes"][0]["CFBundleTypeIconFile"], "AngeloDocument.icns")
        self.assertFalse(info["CFBundleDocumentTypes"][0]["CFBundleTypeIconSystemGenerated"])
        self.assertNotIn("UTTypeIcons", info["UTImportedTypeDeclarations"][0])
        self.assertNotIn("UTExportedTypeDeclarations", info, "Blender owns the .blend type; Angelo only imports it")
        self.assertNotEqual(info["CFBundleIdentifier"], "org.blenderfoundation.blender")
        self.assertTrue((EXPORTS / "AngeloDocument.icns").is_file())

    def test_all_msix_assets_are_real_png_and_variants_are_present(self):
        assets = list((ROOT / "release/windows/msix/Assets").rglob("*.png"))
        self.assertEqual(len(assets), json.loads((BRAND / "coverage.json").read_text())["msix_assets"])
        self.assertEqual(len(assets), 135)
        for file in assets:
            data = file.read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", str(file))
            self.assertGreater(len(data), 100)
        self.assertTrue(any("contrast-black" in str(p) for p in assets))
        self.assertTrue(any("contrast-white" in str(p) for p in assets))

    def test_linux_namespace_does_not_overwrite_upstream_blender_icon(self):
        desktop = (ROOT / "release/freedesktop/blender.desktop").read_text()
        self.assertIn("Name=Angelo\n", desktop)
        self.assertIn("Icon=angelo\n", desktop)
        self.assertIn("StartupWMClass=Angelo\n", desktop)
        self.assertTrue((ROOT / "release/freedesktop/icons/scalable/apps/angelo.svg").is_file())
        self.assertTrue((ROOT / "release/freedesktop/icons/symbolic/apps/angelo-symbolic.svg").is_file())
        cmake = (ROOT / "source/creator/CMakeLists.txt").read_text()
        self.assertIn("RENAME angelo.desktop", cmake)
        self.assertIn("apps/angelo.svg", cmake)
        # The Wayland app ID and X11 class must match the desktop file and StartupWMClass.
        self.assertIn('    "angelo"\n', (ROOT / "intern/ghost/intern/GHOST_SystemWayland.cc").read_text())
        self.assertIn('GHOST_X11_RES_CLASS "Angelo"', (ROOT / "intern/ghost/intern/GHOST_SystemX11.hh").read_text())
        self.assertIn('"angelo.desktop"', (ROOT / "scripts/modules/_bpy_internal/platform/freedesktop.py").read_text())
        x11 = (ROOT / "intern/ghost/intern/GHOST_IconX11.hh").read_text()
        self.assertIn("Generated from approved Angelo", x11)
        values = [int(v, 0) for v in re.findall(r"0x[0-9a-f]+|\d+", x11.split("BLENDER_ICONS_WM_X11[] = {")[1].split("};")[0])]
        offset = 0
        for size in (16,32,48,64):
            self.assertEqual(values[offset:offset+2], [size,size])
            offset += 2 + size * size
        self.assertEqual(offset, len(values))

    def test_provider_preferences_do_not_offer_exclusive_use_selected(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/ui.py").read_text()
        draw = source.split("def draw_ai_providers(")[1].split("class ANGELO_Preferences")[0]
        self.assertNotIn('row.operator("angelo.connection_use"', draw)
        self.assertNotIn('text="Selected"', draw)
        self.assertIn('text="Available in chat"', draw)


if __name__ == "__main__":
    unittest.main()
