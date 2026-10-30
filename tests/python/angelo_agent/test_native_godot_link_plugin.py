# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Godot plugin Angelo ships agrees with Angelo's side, read from its source (doc/angelo/godot_link.rst).

Covered: the protocol name and limits match ``engine_link_protocol``; the plugin looks for the endpoint record where
``storage.data_directory`` puts it on each platform, and honours the same override variable; it names its engine when
it authenticates; the live preview is added as an internal, unowned child (never saved into the scene); a dropped
connection ends the preview; the plugin fetches nothing from the network; every file carries the MIT licence and the
README states the Godot floor and the trademark; the plugin's own files are all the shipped kinds; every declaration
is statically typed (a project may raise GDScript's typing warnings to errors for addons); and plugin.gd names nothing
newer than Godot 4.2, so an older editor is told the plugin needs Godot 4.4 instead of failing to parse it. The
behaviour, including strict warning settings, is checked in a real Godot editor by ``test_blender_godot_link_e2e.py``.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_link_protocol as protocol, godot_link_assets as godot, storage  # noqa: E402

PLUGIN = godot.PLUGIN_SOURCE
SCRIPTS = ("plugin.gd", "link_client.gd", "live_preview.gd", "context_menu.gd")
# Godot 4.4 names plugin.gd must reach only through load()/call(), so Godot 4.2 and 4.3 can parse it.
NEWER_THAN_4_2 = ("EditorContextMenuPlugin", "EditorToaster", "add_context_menu_plugin(", "remove_context_menu_plugin(",
                  "get_editor_toaster(")


def source(name):
    return (PLUGIN / name).read_text(encoding="utf-8")


def code(name):
    """The script without comments and string contents, line by line."""
    lines = []
    for line in source(name).splitlines():
        line = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
        lines.append(line.split("#", 1)[0].rstrip())
    return lines


class PluginContractTests(unittest.TestCase):
    def test_protocol_constants_match(self):
        client = source("link_client.gd")
        self.assertIn('const PROTOCOL: String = "%s"' % protocol.PROTOCOL, client)
        self.assertIn("const MAX_HEADER: int = 64 * 1024", client)
        self.assertEqual(protocol.MAX_HEADER, 64 * 1024)
        self.assertIn("const MAX_PAYLOAD: int = 512 * 1024 * 1024", client)
        self.assertEqual(protocol.MAX_PAYLOAD, 512 * 1024 * 1024)
        self.assertIn('PROTOCOL + "/client"', client)
        self.assertIn('PROTOCOL + "/server"', client)

    def test_the_record_is_looked_for_where_angelo_writes_it(self):
        client = source("link_client.gd")
        self.assertIn('OS.get_environment("ANGELO_%s")' % protocol.RECORD_VARIABLE, client)
        expected = {
            "darwin": storage.data_directory({}, "darwin", "/H") / protocol.RECORD_FILE,
            "win32": storage.data_directory({"LOCALAPPDATA": "/L"}, "win32", "/H") / protocol.RECORD_FILE,
            "linux": storage.data_directory({}, "linux", "/H") / protocol.RECORD_FILE,
        }
        darwin = expected["darwin"].as_posix()
        self.assertTrue(darwin.startswith("/H/"))
        self.assertIn('"%s"' % darwin[len("/H/"):], client)
        self.assertEqual(expected["win32"].parent.as_posix(), "/L/Angelo")
        self.assertIn('OS.get_environment("LOCALAPPDATA")', client)
        self.assertIn('"Angelo/%s"' % protocol.RECORD_FILE, client)
        self.assertEqual(expected["linux"].as_posix(), "/H/.local/state/Angelo/" + protocol.RECORD_FILE)
        self.assertIn('"XDG_STATE_HOME"', client)
        self.assertIn('".local/state"', client)

    def test_the_plugin_names_its_engine(self):
        self.assertIn('LinkClient.new({"engine": "godot"', source("plugin.gd"))

    def test_the_preview_is_never_part_of_the_scene(self):
        preview = source("live_preview.gd")
        self.assertIn("add_child(built, false, Node.INTERNAL_MODE_BACK)", preview)
        self.assertNotRegex(preview, r"\.owner\s*=", "an owned node would be saved with the scene")
        self.assertIn("RenderingServer.instance_set_visible", preview, "originals hide on the server, not as a property")
        # Only the preview's own new mesh nodes (drawn) take a visibility; the originals never get a property set.
        self.assertNotRegex(preview, r"(?<!drawn)\.visible\s*=")
        self.assertIn("ImporterMeshInstance3D", preview, "the editor's GLTFDocument nodes are swapped for drawn meshes")

    def test_a_lost_connection_ends_the_preview(self):
        plugin = source("plugin.gd")
        self.assertIn("_connect(client.state_changed, _on_state)", plugin)
        handler = plugin.split("func _on_state(", 1)[1].split("\nfunc ", 1)[0]
        self.assertIn('state == "off"', handler)
        self.assertIn("preview.clear()", handler)
        self.assertIn('linked = ""', handler)

    def test_nothing_comes_from_the_network_and_every_file_is_licensed(self):
        for path in sorted(PLUGIN.iterdir()):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"https?://(?!www\.w3\.org/2000/svg)", text), "no network URL")
                self.assertIn("SPDX-License-Identifier: MIT", text)
                self.assertIn(path.suffix, {".gd", ".cfg", ".svg", ".md"})
        self.assertIn('script="plugin.gd"', source("plugin.cfg"))
        readme = source("README.md")
        self.assertIn("Godot 4.4 or newer", readme)
        self.assertIn("trademarks of Hyperbond Studio Pte. Ltd.", readme)

    def test_only_loopback_is_reached(self):
        client = source("link_client.gd")
        self.assertEqual(re.findall(r'connect_to_host\("([^"]+)"', client), ["127.0.0.1"])

    def test_every_declaration_is_statically_typed(self):
        for name in SCRIPTS:
            for number, line in enumerate(code(name), 1):
                where = "%s:%d: %s" % (name, number, line.strip())
                with self.subTest(line=where):
                    declaration = re.match(r"\s*(?:static\s+)?(var|const)\s+(\w+)\s*(.*)", line)
                    if declaration:
                        rest = declaration.group(3)
                        typed = rest.startswith(":") and not rest.startswith(":=")
                        preloaded = rest.startswith(":= preload(")   # Godot types a preload constant itself.
                        self.assertTrue(typed or preloaded, "untyped or inferred declaration")
                    loop = re.match(r"\s*for\s+(\w+)\s*(:?)", line)
                    if loop:
                        self.assertEqual(loop.group(2), ":", "untyped loop variable")
                    function = re.match(r"\s*(?:static\s+)?func\s+\w+\((.*)\)\s*(->)?", line)
                    if function and line.rstrip().endswith(":"):
                        self.assertEqual(function.group(2), "->", "no return type")
                        for parameter in filter(None, (p.strip() for p in function.group(1).split(","))):
                            self.assertIn(":", parameter, "untyped parameter")
                    self.assertNotRegex(line, r"\bfunc\(\w+\)", "untyped lambda parameter")

    def test_godot_older_than_4_4_is_told_instead_of_failing_to_parse(self):
        plugin = "\n".join(code("plugin.gd"))
        for name in NEWER_THAN_4_2:
            with self.subTest(name=name):
                self.assertNotIn(name, plugin)
        self.assertIn("const MINIMUM: Array[int] = [4, 4]", source("plugin.gd"))
        enter = source("plugin.gd").split("func _enter_tree()", 1)[1].split("\nfunc ", 1)[0]
        self.assertLess(enter.index("if not supported():"), enter.index("LinkClient.new("))
        self.assertIn('ClassDB.class_exists("EditorContextMenuPlugin")', source("plugin.gd"))
        self.assertIn('call("add_context_menu_plugin"', source("plugin.gd"))
        self.assertNotIn('preload("context_menu.gd")', source("plugin.gd"))
        self.assertIn("extends EditorContextMenuPlugin", source("context_menu.gd"))


if __name__ == "__main__":
    unittest.main()
