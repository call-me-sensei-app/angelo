# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live web preview re-exports after every scene edit its export can see, against Blender's own RNA
(``web_preview.scene_key``; doc/angelo/web_export.rst, "Every change re-exports").

Run in Angelo, in background (``--factory-startup``)::

    <Angelo> --background --factory-startup --python tests/python/angelo_agent/test_blender_web_preview_key.py

Not a list of cases: every writable setting of the scene, its render settings and its colour management is changed
in turn, the real depsgraph handler sees Blender's own update, and the web export is run before and after. The rule
is the export's own: an edit that changes what the export writes (the .glb or its report) must schedule a re-export.
Edits that schedule one with no difference in this scene are listed (``needless``) with the reason the export reads
them. Named checks cover the 1.0.9 report (View Transform, Exposure, Look, display device), the World's colour and
the film, and that a selection schedules nothing. Also times the key per depsgraph update, before (the shared
``scene_changes.scene_key``) and after. Writes ``results.json`` in ``ANGELO_QA_DIR``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import ast
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-web-preview-key-qa"))
OUT.mkdir(parents=True, exist_ok=True)

import addon_utils  # noqa: E402

# Enabled, as in the app: the glTF exporter calls the scene hook (the look in the extras) only of enabled add-ons.
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import scene_changes, web_export, web_preview  # noqa: E402

checks, observed = {}, {}
report = {"success": False, "checks": checks, "observed": observed}
scene = bpy.context.scene
layer = bpy.context.view_layer
FOLDER = Path(tempfile.mkdtemp(prefix="angelo-preview-key-"))
REQUEST = {"meshes": "AUTO", "textures": "AUTO", "merge": True, "selection_only": False}   # The preview's own.
# Settings whose change would end the session or the check itself, never a preview's concern.
UNSAFE = {"use_lock_interface", "use_persistent_data", "threads_mode", "threads", "filepath", "use_stamp_note",
          "frame_current"}   # The frame steps the animation and is in the key since 1.0.6 (scene_changes).


def check(name, value, detail=None):
    checks[name] = bool(value)
    print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": %s" % (detail,)), flush=True)


class StandInServer:
    def publish(self, *args, **kwargs):
        pass

    def close(self):
        pass


def key():
    key_function = getattr(web_preview, "scene_key", None)   # 1.0.9 had none: the shared one.
    return key_function(scene, layer) if key_function else scene_changes.scene_key(scene, layer)


def start():
    web_preview._state.update(server=StandInServer(), exporting=False, dirty_at=None, scene=key())
    web_preview._window = lambda: type("Window", (), {"scene": scene, "view_layer": layer})
    if web_preview._on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(web_preview._on_depsgraph)


def scheduled():
    """Evaluate the last edit (Blender calls the preview's handler); True when it scheduled a re-export."""
    bpy.context.view_layer.update()
    return web_preview._state["dirty_at"] is not None


def exported():
    """What the export writes now: the .glb's JSON, its binary's digest and the report without its timing."""
    web_preview._state["exporting"] = True   # Its own frame steps are no edit (as in ``refresh``).
    try:
        path = FOLDER / "scene.glb"
        if path.exists():
            path.unlink()
        result = web_export.export(None, scene, layer, str(path), dict(REQUEST), require_environment=False)
    finally:
        web_preview._state["exporting"] = False
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    result.pop("export_ms", None)
    return (data[20:20 + length].decode("utf-8"), hashlib.sha256(data[20 + length:]).hexdigest(),
            json.dumps(result, sort_keys=True, default=str))


def settle():
    """After an export: the preview's key is the scene's and nothing is pending."""
    bpy.context.view_layer.update()
    web_preview._state.update(dirty_at=None, scene=key())


def enum_choices(owner, name):
    """The values ``owner.name`` takes now: a dynamic enum (a look, a display) offers only the configuration's, which
    ``enum_items`` doesn't list, so they are read from the refusal of a value none has (nothing is set)."""
    try:
        setattr(owner, name, "\x01")
    except TypeError as error:
        text = str(error)
        return list(ast.literal_eval(text[text.rindex(" in (") + 4:]))
    return []


def changed_value(prop, value, owner):
    """Another value for ``prop`` (an RNA property of ``owner``) than ``value``, or None when there is none to try."""
    if prop.type == "BOOLEAN" and not prop.is_array:
        return not value
    if prop.type == "ENUM" and not prop.is_enum_flag:
        others = [item for item in enum_choices(owner, prop.identifier) if item != value]
        return others[0] if others else None
    if prop.type in {"INT", "FLOAT"} and not prop.is_array:
        step = 1 if prop.type == "INT" else 0.25
        bumped = value + step if value + step <= prop.soft_max else value - step
        return bumped if prop.soft_min <= bumped <= prop.soft_max else None
    if prop.type == "STRING" and prop.subtype == "NONE":
        return value + "2"
    return None


def owners():
    yield "scene", scene
    yield "render", scene.render
    yield "view_settings", scene.view_settings
    yield "display_settings", scene.display_settings


def build_scene():
    """The factory scene (cube, camera, light, World colour) with an animation, so the time base reaches the file."""
    cube = bpy.data.objects["Cube"]
    cube.keyframe_insert("location", frame=1)
    cube.location.x += 2.0
    cube.keyframe_insert("location", frame=40)
    cube.location.x -= 2.0
    scene.frame_set(1)
    check("world_is_a_colour", web_export.web_facts._world(scene)["kind"] == "COLOR",
          web_export.web_facts._world(scene)["kind"])


def sweep():
    """Every writable setting: what the export writes, and whether the preview scheduled a re-export."""
    baseline = exported()
    check("export_is_deterministic", exported() == baseline)
    check("export_carries_the_look", '"angelo_look"' in baseline[0])
    settle()
    missed, needless, tried = [], [], []
    for label, owner in owners():
        for prop in owner.bl_rna.properties:
            name = prop.identifier
            if prop.is_readonly or name in UNSAFE or name == "rna_type":
                continue
            before = getattr(owner, name)
            after = changed_value(prop, before, owner)
            if after is None:
                continue
            try:
                setattr(owner, name, after)
            except (TypeError, ValueError, AttributeError):
                continue
            path = label + "." + name
            try:
                keyed = key() != web_preview._state["scene"]
                moved = scheduled()
                differs = exported() != baseline
            finally:
                setattr(owner, name, before)
                settle()
            tried.append(path)
            if differs and not moved:
                missed.append(path)
            elif moved and not differs:   # By the key (a conditional read) or by a content update of the edit.
                needless.append(path + (" (key)" if keyed else " (update)"))
    observed["swept"] = len(tried)
    observed["needless"] = needless
    check("sweep_ran", len(tried) > 100, len(tried))
    check("every_edit_the_export_sees_schedules_an_export", not missed, missed)


def other(owner, name):
    """Set ``owner.name`` to another of its values (whatever this configuration offers)."""
    setattr(owner, name, changed_value(owner.bl_rna.properties[name], getattr(owner, name), owner))


def named():
    """The 1.0.9 report's settings, the World and the film, and a selection."""
    background = next(node for node in scene.world.node_tree.nodes if node.type == "BACKGROUND")
    edits = (("look", lambda: other(scene.view_settings, "look")),
             ("exposure", lambda: setattr(scene.view_settings, "exposure", 1.5)),
             ("view_transform", lambda: setattr(scene.view_settings, "view_transform", "Standard")),
             ("display_device", lambda: other(scene.display_settings, "display_device")),
             ("film_transparent", lambda: setattr(scene.render, "film_transparent", True)),
             ("world_background_colour", lambda: setattr(background.inputs["Color"], "default_value",
                                                         (0.8, 0.3, 0.1, 1.0))),
             ("world_strength", lambda: setattr(background.inputs["Strength"], "default_value", 3.0)))
    for name, edit in edits:
        settle()
        edit()
        check(name + "_schedules_an_export", scheduled())
        settle()
        check(name + "_then_nothing_more", not scheduled())
    settle()
    for obj in bpy.data.objects:
        obj.select_set(obj.name == "Light")
    check("selection_alone_schedules_nothing", not scheduled())
    settle()
    scene.render.resolution_percentage = 37   # Read by no export.
    check("unread_setting_schedules_nothing", not scheduled())


def cost():
    """The key per depsgraph update (the handler computes it whenever no content flag is set), in µs."""
    def per_call(function, runs):
        started = time.perf_counter()
        for _ in range(runs):
            function(scene, layer)
        return round((time.perf_counter() - started) * 1e6 / runs, 2)
    new = getattr(web_preview, "scene_key", scene_changes.scene_key)
    sizes = {}
    for count in (3, 1000):
        while len(scene.objects) < count:
            scene.collection.objects.link(bpy.data.objects.new("Extra.%04d" % len(scene.objects), None))
        runs = 20000 if count < 10 else 2000
        sizes[str(len(scene.objects))] = {"before_us": per_call(scene_changes.scene_key, runs),
                                          "after_us": per_call(new, runs)}
    observed["key_cost"] = sizes


try:
    build_scene()
    start()
    named()
    sweep()
    cost()
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    web_preview.stop()   # The handler and the stand-in server go with the check.
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("WEB_PREVIEW_KEY_BACKGROUND failed checks:", json.dumps(failed), json.dumps(observed)[:3000])
assert report["success"], failed
