# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Redo brings back a linked asset whose data uses packed linked data, and Blender lives through it.

Evidence (BREAKIT-C7A-13 M1; plain Blender 5.2.3 and 5.3.0, no Angelo code): a library file whose own data uses
data packed from another library (an asset's material using an Essentials node group, which Blender packs by
default) is linked as a person links it, the link is undone and redone, and Blender dies with SIGSEGV in the
dependency graph's build (``DepsgraphNodeBuilder::build_nodetree``, from the material of the linked object). The
same file linked without packing never crashed. Angelo refused worker parts in that shape as a stopgap.

Each case, in a new session: an asset library with one datablock; a kit file whose own collection uses that
datablock, linked and packed (or only linked: the control); the kit's collection linked into the scene the way
``File > Link`` does and instanced; an Undo step; then Undo (the link is taken back), Redo (it returns), and again.
After every Redo the dependency graph is built and evaluated, and the linked user must point at the packed
datablock Blender lists (by identity), and no datablock may be listed twice under one name and library. The cases
differ in the kind of packed data (a node group in a material, a mesh under an object, an image in a material's
texture node), since the fault is that Undo freed packed data the linked data it kept still used, whatever its type.
More shapes of that (relinking after a purge, nested and cross-library packed chains, ID properties and drivers, an
archive library stored without the packed datablock) are in test_blender_linked_packed_undo_chains.py.

The brush cases are the other way in: a brush is data Undo never frees, and a brush linked (and packed) after a step
stays through its Undo while its packed library was freed (5.3.0 then died writing the file). Each round there saves
a copy of the file after the Undo, which reads every library the brush and its texture point at.

The add-on is disabled first: this is Blender's own Undo. Run with ``--background --factory-startup
--python-exit-code 1``; a crash ends the process with a signal, so the control runs first and the report on disk
says which case was running.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
from collections import Counter
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-linked-packed-redo-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "linked-packed-redo.json"
report = {"success": False, "blender": bpy.app.version_string, "cases": {}, "running": None}
ROUNDS = 2   # Undo and Redo this many times: the second Redo reads over what the first one left.


def save_report():
    REPORT.write_text(json.dumps(report, indent=2))


def quad(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    return mesh


def make_asset(kind):
    """The asset library's one datablock of this kind, written to its file by the caller."""
    if kind == "node_group":
        return bpy.data.node_groups.new("Kit Asset", "ShaderNodeTree")
    if kind == "mesh":
        return quad("Kit Asset")
    image = bpy.data.images.new("Kit Asset", 4, 4)
    image.pack()
    return image


def use_asset(kind, asset):
    """The kit's own object, using the asset through data of the kit's own."""
    material = bpy.data.materials.new("Own Paint")
    material.use_nodes = True
    if kind == "node_group":
        material.node_tree.nodes.new("ShaderNodeGroup").node_tree = asset
    elif kind == "image":
        material.node_tree.nodes.new("ShaderNodeTexImage").image = asset
    mesh = asset if kind == "mesh" else quad("Own Mesh")
    kit = bpy.data.objects.new("Own Kit", mesh)
    if kind != "mesh":
        mesh.materials.append(material)
    return kit


def asset_in_use(kind):
    """The datablock the linked kit object reaches now, as (the datablock, where it was found)."""
    kit = bpy.data.objects.get("Own Kit")
    if kit is None or kit.library is None:
        return None, "no linked kit object"
    if kind == "mesh":
        return kit.data, "the object's data"
    slots = kit.data.materials
    if not slots or slots[0] is None or not slots[0].node_tree:
        return None, "no material tree"
    wanted = "ShaderNodeGroup" if kind == "node_group" else "ShaderNodeTexImage"
    node = next((node for node in slots[0].node_tree.nodes if node.bl_idname == wanted), None)
    if node is None:
        return None, "no node"
    return (node.node_tree if kind == "node_group" else node.image), "the material's node"


def in_window(operator, **arguments):
    window = bpy.context.window_manager.windows[0]   # Undo's steps run in a window's screen, as a person's do.
    with bpy.context.temp_override(window=window, screen=window.screen):
        return operator(**arguments)


def state(kind):
    """What a person would see of the link now."""
    found, where = asset_in_use(kind)
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    return {"collection_linked": bpy.data.collections.get("Own Set") is not None,
            "instancers": sorted(obj.name for obj in bpy.data.objects if obj.instance_collection),
            "instances_evaluated": sum(1 for instance in graph.object_instances if instance.is_instance),
            "asset": getattr(found, "name", None), "asset_found_in": where,
            "asset_is_the_listed_one": found is not None and any(item == found for item in asset_collection(kind)),
            "listed_twice": sorted(name for name, count in Counter(
                (item.name, getattr(item.library, "name", None)) for item in asset_collection(kind)).items()
                if count > 1),
            "asset_packed": bool(found and found.library and found.is_linked_packed),
            "libraries": sorted(os.path.basename(library.filepath) for library in bpy.data.libraries)}


def asset_collection(kind):
    return {"node_group": bpy.data.node_groups, "mesh": bpy.data.meshes, "image": bpy.data.images}[kind]


def case(kind, pack):
    name = "%s, %s" % (kind, "packed" if pack else "linked only")
    report["running"] = name
    save_report()
    folder = OUT / name.replace(", ", "-").replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    assets = folder / "asset-library.blend"
    bpy.data.libraries.write(str(assets), {make_asset(kind)}, fake_user=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(assets), link=True, pack=pack) as (_source, target):
        setattr(target, {"node_group": "node_groups", "mesh": "meshes", "image": "images"}[kind], ["Kit Asset"])
    asset = getattr(target, {"node_group": "node_groups", "mesh": "meshes", "image": "images"}[kind])[0]
    collection = bpy.data.collections.new("Own Set")
    collection.use_fake_user = True
    collection.objects.link(use_asset(kind, asset))
    kit = folder / "kit-own.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(kit), compress=False)

    bpy.ops.wm.read_factory_settings()   # A window, and Undo on, as a person's session has.
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "scene.blend"), compress=False)
    in_window(bpy.ops.ed.undo_push, message="start")
    bpy.ops.wm.link(filepath=str(kit / "Collection" / "Own Set"), directory=str(kit / "Collection") + "/",
                    filename="Own Set", instance_collections=True)
    in_window(bpy.ops.ed.undo_push, message="linked")   # As the person's own link pushes its step.

    facts = report["cases"][name] = {"linked": state(kind), "rounds": []}
    save_report()
    assert facts["linked"]["asset_packed"] == pack, "the kit's asset is %s" % (
        "not packed in the scene" if pack else "packed although only linked")
    for _round in range(ROUNDS):
        in_window(bpy.ops.ed.undo)
        undone = state(kind)
        in_window(bpy.ops.ed.redo)
        redone = state(kind)
        facts["rounds"].append({"undone": undone, "redone": redone})
        save_report()
        # The link's own step is the instancer. Blender keeps the linked data itself across this Undo: the
        # window's scene, which Undo never frees, used it before the step was loaded.
        assert not undone["instancers"] and not undone["instances_evaluated"], "Undo did not take the link back"
        assert redone == facts["linked"], "after Redo the link is not what it was: %s" % json.dumps(redone)
    facts["passed"] = True


def brush_state():
    """The linked brush, which Undo keeps, and the data and library it points at."""
    brush = bpy.data.brushes.get("Kit Brush")
    texture = brush.texture if brush else None
    return {"brush": brush is not None, "brush_packed": bool(brush and brush.is_linked_packed),
            "brush_library": brush.library.name if brush and brush.library else None,
            "brush_library_listed": bool(brush and brush.library and brush.library in list(bpy.data.libraries)),
            "image": texture.image.name if texture and texture.image else None,
            "libraries": sorted(os.path.basename(library.filepath) for library in bpy.data.libraries)}


def brush_case(pack):
    name = "brush, %s" % ("packed" if pack else "linked only")
    report["running"] = name
    save_report()
    folder = OUT / name.replace(", ", "-").replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    image = bpy.data.images.new("Kit Image", 4, 4)
    image.pack()
    texture = bpy.data.textures.new("Kit Texture", "IMAGE")
    texture.image = image
    brush = bpy.data.brushes.new("Kit Brush", mode='SCULPT')
    brush.texture = texture
    brushes = folder / "brush-library.blend"
    bpy.data.libraries.write(str(brushes), {brush}, fake_user=True)

    bpy.ops.wm.read_factory_settings()
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "scene.blend"), compress=False)
    in_window(bpy.ops.ed.undo_push, message="start")
    with bpy.data.libraries.load(str(brushes), link=True, pack=pack) as (_source, target):
        target.brushes = ["Kit Brush"]
    in_window(bpy.ops.ed.undo_push, message="linked")

    facts = report["cases"][name] = {"linked": brush_state(), "rounds": []}
    save_report()
    assert facts["linked"]["brush_packed"] == pack, "the brush is %s" % ("not packed" if pack else "packed")
    for turn in range(ROUNDS):
        in_window(bpy.ops.ed.undo)
        undone = brush_state()
        bpy.ops.wm.save_as_mainfile(filepath=str(folder / ("undone-%d.blend" % turn)), copy=True, compress=False)
        in_window(bpy.ops.ed.redo)
        redone = brush_state()
        facts["rounds"].append({"undone": undone, "redone": redone})
        save_report()
        assert undone["brush"] and undone["brush_library_listed"], "after Undo the brush's library is gone: %s" % (
            json.dumps(undone))
        assert redone == facts["linked"], "after Redo the brush is not what it was: %s" % json.dumps(redone)
    facts["passed"] = True


def run():
    addon_utils.disable("angelo_agent", default_set=False)   # Blender only, as the person's own link would be.
    for pack in (False, True):   # The control first: a crash ends the process.
        for kind in ("node_group", "mesh", "image"):
            case(kind, pack)
        brush_case(pack)
    report["running"] = None
    report["success"] = all(facts.get("passed") for facts in report["cases"].values()) and len(report["cases"]) == 8


try:
    run()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    save_report()
    print("ANGELO_LINKED_PACKED_REDO", json.dumps({"success": report["success"], "running": report["running"],
                                                   "error": (report.get("error") or "")[-600:]}), flush=True)
    if not report["success"]:
        sys.exit(1)
