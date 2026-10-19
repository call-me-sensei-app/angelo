# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Builds a stock Blender user folder for the settings-import suite, inside a Blender (or Angelo) window process.

Run by ``test_blender_settings_import.py`` with ``BLENDER_USER_RESOURCES`` set to the sandbox's
``<home>/Library/Application Support/Blender/5.2`` (or the platform's equivalent), so everything Blender saves lands
there, exactly where a stock Blender keeps a person's settings. The window is needed for one thing: Quick Favorites
are not in Blender's Python API, so the entry is added the way the "Add to Quick Favorites" context menu adds it,
through the exported ``ED_screen_user_menu_*`` functions, in a 3D view.

``--variant full`` makes what a switching artist has: a non-default preference, a custom keymap item, a user add-on
with preferences and a config folder of its own, an extension standing in for Blender's MCP add-on (with its user
folder), an asset library, a Quick Favorites entry, an operator preset, an app template, a startup script, a MatCap,
bookmarks, a recent file, a saved startup scene and the folder an early Angelo build left in Blender's ``config``.
``--variant broken`` adds an add-on that is enabled in the preferences but fails to register. Writes
``profile.json`` (what was made) beside the folder; never touches anything outside ``BLENDER_USER_RESOURCES`` and
the fixture folder.
"""

import json
import os
import sys
import traceback
from pathlib import Path

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
VARIANT = ARGS[ARGS.index("--variant") + 1] if "--variant" in ARGS else "full"
FIXTURE = Path(ARGS[ARGS.index("--fixture") + 1])
ROOT = Path(os.environ["BLENDER_USER_RESOURCES"])

ADDON = '''bl_info = {"name": "Switcher Tool", "author": "Fixture", "version": (1, 0, 0), "blender": (4, 2, 0),
           "category": "Object"}
import bpy


class SWITCHER_OT_hello(bpy.types.Operator):
    bl_idname = "switcher.hello"
    bl_label = "Switcher Hello"

    def execute(self, context):
        return {'FINISHED'}


class SwitcherPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    greeting: bpy.props.StringProperty(name="Greeting", default="default")


def register():
    bpy.utils.register_class(SWITCHER_OT_hello)
    bpy.utils.register_class(SwitcherPreferences)


def unregister():
    bpy.utils.unregister_class(SwitcherPreferences)
    bpy.utils.unregister_class(SWITCHER_OT_hello)
'''

BROKEN = '''bl_info = {"name": "Broken Tool", "author": "Fixture", "version": (1, 0, 0), "blender": (4, 2, 0),
           "category": "Object"}


def register():
    raise RuntimeError("Broken Tool fails to register (fixture)")


def unregister():
    pass
'''

MCP_MANIFEST = '''schema_version = "1.0.0"
id = "mcp"
version = "1.0.0"
name = "Blender MCP (fixture)"
tagline = "Stands in for the Blender MCP extension"
maintainer = "Fixture"
type = "add-on"
blender_version_min = "4.2.0"
license = ["SPDX:GPL-3.0-or-later"]
'''

MCP = '''import bpy


class MCP_FIXTURE_OT_ping(bpy.types.Operator):
    bl_idname = "mcp_fixture.ping"
    bl_label = "MCP Ping"

    def execute(self, context):
        return {'FINISHED'}


def register():
    bpy.utils.register_class(MCP_FIXTURE_OT_ping)


def unregister():
    bpy.utils.unregister_class(MCP_FIXTURE_OT_ping)
'''

STARTUP_SCRIPT = '''import bpy


class SWITCHER_OT_startup_marker(bpy.types.Operator):
    bl_idname = "switcher.startup_marker"
    bl_label = "Registered by a startup script"

    def execute(self, context):
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SWITCHER_OT_startup_marker)


def unregister():
    bpy.utils.unregister_class(SWITCHER_OT_startup_marker)
'''

TEMPLATE = '''def register():
    pass


def unregister():
    pass
'''

PRESET = '''import bpy
op = bpy.context.active_operator
op.size = 3.5
'''

made = {"variant": VARIANT, "root": str(ROOT), "success": False}


def write(relative, text):
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def quick_favorite(window):
    """Adds "Switcher Monkey" to the 3D view's Quick Favorites, as the context menu does (no Python API exists)."""
    import ctypes
    program = ctypes.CDLL(None)
    ensure = program._ZN7blender26ED_screen_user_menu_ensureEPNS_8bContextE
    ensure.restype, ensure.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    add = program._ZN7blender37ED_screen_user_menu_item_add_operatorEPNS_9ListBaseTINS_13bUserMenuItemEEEPKcPKNS_14wmOperatorTypeEPKNS_10IDPropertyES5_NS_2wm13OpCallContextE
    add.restype = None
    add.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int8]
    find = program._ZN7blender20WM_operatortype_findEPKcb
    find.restype, find.argtypes = ctypes.c_void_p, [ctypes.c_char_p, ctypes.c_bool]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        menu = ensure(bpy.context.as_pointer())
        operator = find(b"MESH_OT_primitive_monkey_add", False)
        assert menu and operator, "Quick Favorites entry points not found"
        items = menu + 8 + 8 + 1 + 7 + 64   # bUserMenu: next, prev, space_type, _pad0[7], context[64], items
        add(items, b"Switcher Monkey", operator, None, b"", 1)   # wm::OpCallContext::InvokeRegionWin


def build(window):
    prefs = bpy.context.preferences
    home = FIXTURE / "home-files"
    home.mkdir(parents=True, exist_ok=True)
    import addon_utils
    if VARIANT == "broken":
        write("scripts/addons/switcher_tool/__init__.py", ADDON)
        write("scripts/addons/broken_tool/__init__.py", BROKEN)
        bpy.utils.refresh_script_paths()
        addon_utils.enable("switcher_tool", default_set=True)
        prefs.addons.new().module = "broken_tool"   # Enabled in Blender's preferences; fails wherever it loads.
        made["addons"] = ["switcher_tool", "broken_tool"]
        return
    prefs.edit.undo_steps = 77
    prefs.view.show_tooltips_python = True
    keymap = bpy.context.window_manager.keyconfigs.user.keymaps["Window"]
    keymap.keymap_items.new("wm.search_menu", 'F7', 'PRESS')
    write("scripts/addons/switcher_tool/__init__.py", ADDON)
    write("config/switcher_tool/settings.json", json.dumps({"saved_by": "Blender"}))   # An add-on's own folder.
    write("extensions/user_default/mcp/blender_manifest.toml", MCP_MANIFEST)
    write("extensions/user_default/mcp/__init__.py", MCP)
    write("extensions/.user/user_default/mcp/server.json", json.dumps({"port": 9876}))   # extension_path_user
    write("scripts/startup/switcher_startup.py", STARTUP_SCRIPT)
    write("scripts/startup/bl_app_templates_user/switcher_template/__init__.py", TEMPLATE)
    write("scripts/presets/operator/mesh.primitive_cube_add/Switcher_Cube.py", PRESET)
    # An early Angelo build kept its agent state here; the import must leave it out.
    write("config/angelo-agent/sessions/early.json", json.dumps({"title": "early"}))
    bpy.utils.refresh_script_paths()
    addon_utils.enable("switcher_tool", default_set=True)
    prefs.addons["switcher_tool"].preferences.greeting = "from Blender"
    addon_utils.extensions_refresh()
    addon_utils.enable("bl_ext.user_default.mcp", default_set=True)
    library = home / "SwitcherAssets"
    library.mkdir(exist_ok=True)
    prefs.filepaths.asset_libraries.new(name="Switcher Assets", directory=str(library))
    matcap = ROOT / "datafiles/studiolights/matcap/switcher_matcap.png"
    matcap.parent.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("switcher_matcap", 64, 64)
    image.pixels.foreach_set([0.8, 0.3, 0.2, 1.0] * 64 * 64)
    image.filepath_raw, image.file_format = str(matcap), 'PNG'
    image.save()
    bpy.data.images.remove(image)
    quick_favorite(window)
    projects = home / "SwitcherProjects"
    projects.mkdir(exist_ok=True)
    scene_path = projects / "switcher_scene.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_path), copy=True)
    write("config/bookmarks.txt", "[Bookmarks]\n%s/\n[Recent]\n" % projects)
    write("config/recent-files.txt", str(scene_path) + "\n")
    bpy.ops.mesh.primitive_monkey_add()
    bpy.context.active_object.name = "SwitcherMonkey"
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.save_homefile()
    made.update(addons=["switcher_tool", "bl_ext.user_default.mcp"], scene=str(scene_path), library=str(library),
                bookmarks=str(projects))


def tick():
    window = bpy.context.window_manager.windows[0]
    try:
        build(window)
        bpy.ops.wm.save_userpref()
        made["success"] = (ROOT / "config/userpref.blend").is_file()
    except Exception:
        made["error"] = traceback.format_exc()
    (FIXTURE / "profile.json").write_text(json.dumps(made, indent=2))
    print("ANGELO_BLENDER_PROFILE", json.dumps({"success": made["success"]}), flush=True)
    bpy.ops.wm.quit_blender()



bpy.app.timers.register(tick, first_interval=1.0)
