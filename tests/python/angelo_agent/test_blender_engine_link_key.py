# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine link's live export follows every scene edit its glTF exporter sees, against Blender's own RNA and
exporter (``engine_link.scene_key``, ``gltf_scene_reads``; doc/angelo/engine_link.rst, "Live scenes").

Run in Angelo, in background (``--factory-startup``), through ``run_native_suites.py``::

    python3 tests/python/angelo_agent/run_native_suites.py --binary <Angelo> --include test_blender_engine_link_key

Not a list of cases: for each engine's live options (Godot with its defaults and with the .import settings that make
it read the active collection and render visibility, Unity, Unreal, the web), every writable setting of the scene
and its render settings is changed in turn (and, where render visibility is read, every writable switch of an
object), the real depsgraph handler sees Blender's own update, and the engine's live .glb is written before and
after with Blender's exporter. The rule is the export's own: an edit that changes the file must schedule a live
export. Edits that schedule one with no difference in this scene are listed (``needless``) with whether the key or
a content update scheduled them. Named checks: a scene custom property (Godot's extras), the active collection,
a selection (nothing). Also holds ``gltf_scene_reads.DEFAULTS`` to the operator's own defaults and times the key per
depsgraph update, before (the shared ``scene_changes.scene_key``) and after. Writes ``results.json`` in
``ANGELO_QA_DIR``.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-engine-link-key-qa"))
OUT.mkdir(parents=True, exist_ok=True)

import addon_utils  # noqa: E402

# Enabled, as in the app: the exporter calls Angelo's glTF hooks only of enabled add-ons.
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import engine_link, engine_link_assets as assets, scene_changes, web_link_live  # noqa: E402

checks, observed = {}, {}
report = {"success": False, "checks": checks, "observed": observed}
scene = bpy.context.scene
layer = bpy.context.view_layer
FOLDER = Path(tempfile.mkdtemp(prefix="angelo-engine-key-"))
LINKS = (("godot", {}), ("godot", {"blender/nodes/active_collection_only": True, "blender/nodes/visible": 2}),
         ("unity", {}), ("unreal", {}), ("web", {}))
# Settings whose change would end the session or the check itself, never a live export's concern.
UNSAFE = {"use_lock_interface", "use_persistent_data", "threads_mode", "threads", "filepath", "use_stamp_note",
          "frame_current"}   # The frame is in the shared key since 1.0.6 (scene_changes).


def check(name, value, detail=None):
    checks[name] = bool(value)
    print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": %s" % (detail,)), flush=True)


def link(engine, params):
    engine_link._state.update(session={"asset": type("Asset", (), {"engine": engine})(), "params": params},
                              exporting=False, dirty_at=None)
    settle()
    return assets.adapter(engine)


def key():
    key_function = getattr(engine_link, "scene_key", None)   # 1.0.10 had none: the shared one.
    return key_function(scene, layer) if key_function else scene_changes.scene_key(scene, layer)


def settle():
    bpy.context.view_layer.update()
    engine_link._state.update(dirty_at=None, scene=key())


def scheduled():
    """Evaluate the last edit (Blender calls the link's handler); True when it scheduled a live export."""
    bpy.context.view_layer.update()
    return engine_link._state["dirty_at"] is not None


def exported(engine, params):
    """The engine's live .glb now: its JSON and its binary's digest."""
    adapter = assets.adapter(engine)
    path = FOLDER / "live.glb"
    engine_link._state["exporting"] = True   # The exporter's own updates are no edit (as in a publish).
    try:
        assets.export(None, str(path), adapter.live_options(params),
                      getattr(adapter, "LIVE_EXPORT_ID", assets.EXPORT_ID))
    finally:
        engine_link._state["exporting"] = False
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    return data[20:20 + length].decode("utf-8"), hashlib.sha256(data[20 + length:]).hexdigest()


def enum_choices(owner, name):
    """The values ``owner.name`` takes now (a dynamic enum lists only them; nothing is set)."""
    try:
        setattr(owner, name, "\x01")
    except TypeError as error:
        text = str(error)
        return list(ast.literal_eval(text[text.rindex(" in (") + 4:]))
    return []


def changed_value(prop, value, owner):
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


def build_scene():
    """The factory scene (cube, camera, light) with a keyframed cube, a scene custom property and a second
    collection holding a sphere."""
    cube = bpy.data.objects["Cube"]
    cube.keyframe_insert("location", frame=1)
    cube.location.x += 2.0
    cube.keyframe_insert("location", frame=40)
    cube.location.x -= 2.0
    scene.frame_set(1)
    scene["level"] = 1
    props = bpy.data.collections.new("Props")
    scene.collection.children.link(props)
    mesh = bpy.data.meshes.new("Ball")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    props.objects.link(bpy.data.objects.new("Ball", mesh))
    bpy.context.view_layer.update()


last = {}


def what_differs(before, after):
    """Where two exports' JSON differ (paths, at most a few), for a miss's report."""
    found = []

    def walk(one, two, path):
        if len(found) >= 6:
            return
        if isinstance(one, dict) and isinstance(two, dict):
            for name in sorted(set(one) | set(two)):
                walk(one.get(name), two.get(name), path + "/" + str(name))
        elif isinstance(one, list) and isinstance(two, list) and len(one) == len(two):
            for index, (a, b) in enumerate(zip(one, two)):
                walk(a, b, "%s[%d]" % (path, index))
        elif one != two:
            found.append((path, str(one)[:80], str(two)[:80]))
    walk(json.loads(before[0]), json.loads(after[0]), "")
    return found or ["binary only"]


def as_the_interface_sets(owner, prop, value):
    """Set ``prop`` of ``owner`` as a person does in the interface. Blender's own settings update the depsgraph either
    way; an add-on's (Python-defined: ``is_runtime``) updates it only through the interface's RNA update, which tags
    the owner's transform, data and parameters (rna_access.cc), so that tag is given here as the interface would."""
    setattr(owner, prop.identifier, value)
    if prop.is_runtime:
        tag(owner.id_data)


def tag(block):
    """The interface's tag for an edit of ``block``'s add-on or custom property: its transform and data (Python can
    name those for an object only; another ID is tagged whole)."""
    if isinstance(block, bpy.types.Object):
        block.update_tag(refresh={"OBJECT", "DATA"})
    else:
        block.update_tag()


def sweep(engine, params, label, owners):
    """Every writable setting of ``owners``: what the live export writes, and whether the link scheduled one. Each
    edit is compared with an export made just before it, so a setting a restore leaves stored (an add-on's default
    written out) never reads as the next edit's change."""
    baseline = exported(engine, params)
    check(label + ":export_is_deterministic", exported(engine, params) == baseline)
    settle()
    missed, needless, tried = [], [], 0
    for prefix, owner in owners:
        for prop in owner.bl_rna.properties:
            name = prop.identifier
            if prop.is_readonly or name in UNSAFE or name == "rna_type":
                continue
            before = getattr(owner, name)
            after = changed_value(prop, before, owner)
            if after is None:
                continue
            baseline = exported(engine, params)
            settle()
            try:
                as_the_interface_sets(owner, prop, after)
            except (TypeError, ValueError, AttributeError):
                continue
            path = prefix + "." + name
            try:
                keyed = key() != engine_link._state["scene"]
                moved = scheduled()
                last["export"] = exported(engine, params)
                differs = last["export"] != baseline
            finally:
                as_the_interface_sets(owner, prop, before)
                settle()
            tried += 1
            if differs and not moved:
                missed.append(path)
                if len(missed) <= 4:
                    observed.setdefault(label + ":miss_diff", {})[path] = what_differs(baseline, last["export"])
            elif moved and not differs:
                needless.append(path + (" (key)" if keyed else " (update)"))
    observed[label] = {"swept": tried, "needless": needless}
    check(label + ":sweep_ran", tried > 80, tried)
    check(label + ":every_edit_the_export_sees_schedules_an_export", not missed, missed)


def named(engine, params, label):
    """A scene custom property (as the Properties editor sets it: an RNA update of the scene), the active
    collection and a selection."""
    baseline = exported(engine, params)
    settle()
    scene["level"] = 2
    tag(scene)   # As the Properties editor's RNA update does (rna_access.cc).
    moved, differs = scheduled(), exported(engine, params) != baseline
    check(label + ":custom_property", moved or not differs, {"scheduled": moved, "export_differs": differs})
    observed.setdefault(label + ":reads", {})["custom_property"] = differs
    scene["level"] = 1
    tag(scene)
    settle()
    baseline = exported(engine, params)
    children = layer.layer_collection.children
    layer.active_layer_collection = children["Props"]
    moved, differs = scheduled(), exported(engine, params) != baseline
    check(label + ":active_collection", moved or not differs, {"scheduled": moved, "export_differs": differs})
    observed[label + ":reads"]["active_collection"] = differs
    layer.active_layer_collection = layer.layer_collection
    settle()
    for obj in bpy.data.objects:
        obj.select_set(obj.name == "Light")
    check(label + ":selection_schedules_nothing", not scheduled())
    settle()


def defaults_match_the_operator():
    try:
        from angelo_agent import gltf_scene_reads
    except ImportError:   # An add-on before 1.0.10's key (the "before" run): the sweep still runs.
        check("defaults_are_the_operators", False, "no gltf_scene_reads")
        return
    properties = bpy.ops.export_scene.gltf.get_rna_type().properties
    found = {name: properties[name].default for name in gltf_scene_reads.DEFAULTS}
    check("defaults_are_the_operators", found == gltf_scene_reads.DEFAULTS, found)


def cost():
    """The key per depsgraph update, in µs, for each link."""
    def per_call(function, runs):
        started = time.perf_counter()
        for _ in range(runs):
            function(scene, layer)
        return round((time.perf_counter() - started) * 1e6 / runs, 2)
    new = getattr(engine_link, "scene_key", scene_changes.scene_key)
    sizes = {}
    for count in (5, 1000):
        while len(scene.objects) < count:
            scene.collection.objects.link(bpy.data.objects.new("Extra.%04d" % len(scene.objects), None))
        runs = 20000 if count < 10 else 2000
        row = {"before_us": per_call(scene_changes.scene_key, runs)}
        for engine, params in LINKS:
            link(engine, params)
            row["%s%s_us" % (engine, "+params" if params else "")] = per_call(new, runs)
        sizes[str(len(scene.objects))] = row
    observed["key_cost"] = sizes


try:
    build_scene()
    defaults_match_the_operator()
    if engine_link._on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(engine_link._on_depsgraph)
    web_link_live.note = lambda depsgraph: None   # The web's delta bookkeeping is not what is checked here.
    for engine, params in LINKS:
        label = engine + ("+params" if params else "")
        link(engine, params)
        named(engine, params, label)
        owners = [("scene", scene), ("render", scene.render)]
        if params.get("blender/nodes/visible") == 2:   # Render visibility is read: an object's switches too.
            owners.append(("object", bpy.data.objects["Cube"]))
        sweep(engine, params, label, owners)
    cost()
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    if engine_link._on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(engine_link._on_depsgraph)
    engine_link._state.update(session=None, dirty_at=None, scene=None)
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("ENGINE_LINK_KEY_BACKGROUND failed checks:", json.dumps(failed), json.dumps(observed)[:4000])
assert report["success"], failed
