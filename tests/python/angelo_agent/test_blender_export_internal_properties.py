# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No exporter writes Angelo's own saved custom properties into the file it makes, in a real background Blender.

Angelo saves records of its own as custom properties on the person's data: where a library or team item came from,
which character part an object is, a scene's merged parts, a render region (the ``angelo_*`` and ``_angelo_*`` keys of
``AGENTS.md``, "Actual Code Layout"). They are the app's bookkeeping, not the person's data. Four exporters write
custom properties: glTF (``export_extras``, on in Godot's and Unity's Blender profiles), FBX (``use_custom_props``),
USD and Alembic (``export_custom_properties``, on by default). Each wrote every key it found, so a file handed to
another tool or another company carried Angelo's records.

The rule is the key's prefix, never a list of today's keys: besides keys the add-on saves now, each data-block here
carries a key no code writes (``HELD_OUT``) and a key a file saved before the product's rename may still hold
(``LEGACY``: exact names, so a person's own ``sensei_*`` key is theirs). The person's own properties (``OWN``) must
still be written by every exporter, which also proves the exporter wrote custom properties at all. Keys and values are
plain strings, which all four exporters write; glTF also gets a nested value, and a property an add-on registers on
objects (``Object.angelo_*``, which Blender stores beside the custom ones and glTF writes too), with the person's
own registered one.

Run through ``run_native_suites.py --include test_blender_export_internal_properties``; check
``export-internal-properties.json`` for ``success``. No add-on of Angelo's, account, provider or network.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import struct
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-export-internal-properties-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "export-internal-properties.json"
FILES = OUT / "exports"   # Its path may hold the product's name; only whole keys are searched for.
FILES.mkdir(exist_ok=True)

# Keys the add-on saves today (library_import, team_library_spec, character_compose, asset_stock_import,
# parallel_sessions, render_region) and one of each prefix that no code writes.
SAVED = ("angelo_library", "angelo_team_item", "angelo_character_part", "angelo_blender_asset",
         "_angelo_parts_merged", "angelo_render_region")
HELD_OUT = ("angelo_qa_held_out_key", "_angelo_qa_held_out_key")
LEGACY = ("_sensei_program_stage", "sensei_director")
REGISTERED = "angelo_qa_registered"          # A property an add-on registers on objects, as Angelo's does.
INTERNAL = SAVED + HELD_OUT + LEGACY + (REGISTERED,)
# The person's own custom properties: every exporter keeps writing them, a name near Angelo's included.
OWN = ("qa_person_note", "sensei_person_note", "my_angelo_note")
OWN_REGISTERED = "qa_person_registered"      # Their own add-on's registered property: glTF keeps writing it.


def build():
    """A mesh object with a material, a camera and a light in the open scene, each data-block and the scene carrying
    every internal key and the person's own."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    mesh = bpy.data.meshes.new("QA mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    material = bpy.data.materials.new("QA material")
    mesh.materials.append(material)
    blocks = [scene, mesh, material]
    for name, data in (("QA object", mesh), ("QA camera", bpy.data.cameras.new("QA camera data")),
                       ("QA light", bpy.data.lights.new("QA light data", 'POINT'))):
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        blocks += [obj] if data is mesh else [obj, data]
    for block in blocks:
        for key in SAVED + HELD_OUT + LEGACY:
            block[key] = "internal record"
        for key in OWN:
            block[key] = "kept"
    for block in blocks:
        if isinstance(block, bpy.types.Object):
            setattr(block, REGISTERED, "internal record")
            setattr(block, OWN_REGISTERED, "kept")
    return blocks


def found(data):
    """``(internal keys present, whether every one of the person's own keys is present)`` in a file's bytes."""
    return sorted(key for key in INTERNAL if key.encode() in data), all(key.encode() in data for key in OWN)


def gltf(path):
    addon_utils.enable("io_scene_gltf2", default_set=False, persistent=False)
    bpy.context.scene["angelo_qa_held_out_key"] = {"nested": {"deeper": "internal record"}}   # glTF writes these too.
    bpy.ops.export_scene.gltf(filepath=str(path), check_existing=False, export_format="GLB", export_extras=True,
                              export_cameras=True, export_lights=True, use_active_scene=True)
    data = path.read_bytes()
    chunk = data[20:20 + struct.unpack_from("<I", data, 12)[0]]   # The JSON chunk: every extras lives there.
    if OWN_REGISTERED.encode() not in chunk:
        raise AssertionError("glTF no longer writes a registered property: the registered case proves nothing")
    return chunk


def fbx(path):
    addon_utils.enable("io_scene_fbx", default_set=False, persistent=False)
    bpy.ops.export_scene.fbx(filepath=str(path), check_existing=False, use_custom_props=True)
    return path.read_bytes()


def usd(path):
    bpy.ops.wm.usd_export(filepath=str(path), check_existing=False)   # Custom properties are on by default.
    return path.read_bytes()


def alembic(path):
    bpy.ops.wm.alembic_export(filepath=str(path), check_existing=False, as_background_job=False)
    return path.read_bytes()


EXPORTERS = (("gltf", gltf, "model.glb", None), ("fbx", fbx, "model.fbx", None),
             ("usd", usd, "model.usda", "usd"), ("alembic", alembic, "model.abc", "alembic"))


def main():
    state = {"success": False, "checks": {}, "leaked": {}, "not_built": []}
    bpy.types.Object.angelo_qa_registered = bpy.props.StringProperty()
    bpy.types.Object.qa_person_registered = bpy.props.StringProperty()
    try:
        for name, export, file, option in EXPORTERS:
            if option is not None and not getattr(bpy.app.build_options, option, True):
                state["not_built"].append(name)   # A build without the exporter has nothing to leak through.
                continue
            build()
            leaked, own = found(export(FILES / file))
            state["leaked"][name] = leaked
            state["checks"][name + "_writes_no_internal_property"] = not leaked
            state["checks"][name + "_still_writes_the_persons_property"] = own
        state["success"] = bool(state["checks"]) and all(state["checks"].values()) and "gltf" in state["leaked"]
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2))
    print("ANGELO_EXPORT_INTERNAL_PROPERTIES", json.dumps(state), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
