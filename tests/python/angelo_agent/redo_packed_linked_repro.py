# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's own Redo on packed linked data, with no Angelo code (BREAKIT-C7A-13 M1; doc/angelo/agent_integration.rst).

The merge refuses a part whose packed linked data only a person's own linked asset uses, because Blender 5.2 crashed
(SIGSEGV in the depsgraph build) on Redo after an Undo that took such data back, a plain link included. This repro is
the plain case, with Angelo's add-on disabled first: a library whose collection's material uses a node group packed
from another library, linked as a person would (``wm.link``), instanced, then Undo, Redo and a depsgraph update.

    Angelo --background --factory-startup --python-exit-code 1 --python redo_packed_linked_repro.py -- OUT [unpacked]

It prints ``REDO_SURVIVED {...}`` and exits 0 when Blender lives through it; a crash ends the process with a signal.
``unpacked`` links the node group without packing it: the control, which never crashed.
"""

import json
import sys
from pathlib import Path

import addon_utils
import bpy

args = sys.argv[sys.argv.index("--") + 1:]
OUT, PACK = Path(args[0]), "unpacked" not in args[1:]
OUT.mkdir(parents=True, exist_ok=True)
addon_utils.disable("angelo_agent", default_set=False)   # Blender only, as the person's own link would be.


def quad(name, material):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.materials.append(material)
    return mesh


bpy.ops.wm.read_factory_settings(use_empty=True)
assets = OUT / "asset-library.blend"
bpy.data.libraries.write(str(assets), {bpy.data.node_groups.new("Kit Bevel", "ShaderNodeTree")}, fake_user=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(str(assets), link=True, pack=PACK) as (_source, target):
    target.node_groups = ["Kit Bevel"]
material = bpy.data.materials.new("Own Paint")
material.use_nodes = True
material.node_tree.nodes.new("ShaderNodeGroup").node_tree = target.node_groups[0]
collection = bpy.data.collections.new("Own Set")
collection.use_fake_user = True
collection.objects.link(bpy.data.objects.new("Own Kit", quad("Own Mesh", material)))
kit = OUT / "kit-own.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(kit), compress=False)

bpy.ops.wm.read_factory_settings()   # A window, and Undo on, as a person's session has (and the merge suite's lead).
bpy.context.preferences.edit.use_global_undo = True
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "scene.blend"), compress=False)
WINDOW = bpy.context.window_manager.windows[0]   # Undo's steps run in a window's screen, as a person's would.


def in_window(operator, **arguments):
    with bpy.context.temp_override(window=WINDOW, screen=WINDOW.screen):
        return operator(**arguments)


in_window(bpy.ops.ed.undo_push, message="start")
bpy.ops.wm.link(filepath=str(kit / "Collection" / "Own Set"), directory=str(kit / "Collection") + "/",
                filename="Own Set", instance_collections=True)
in_window(bpy.ops.ed.undo_push, message="linked")   # As the person's own link pushes its step.
group = bpy.data.node_groups.get("Kit Bevel")
facts = {"packed": bool(group and group.library and group.is_linked_packed), "pack_requested": PACK,
         "blender": bpy.app.version_string}
bpy.context.view_layer.update()
in_window(bpy.ops.ed.undo)
bpy.context.view_layer.update()
facts["undone"] = bpy.data.collections.get("Own Set") is None   # The link taken back.
in_window(bpy.ops.ed.redo)
bpy.context.view_layer.update()   # Where 5.2 crashed: the depsgraph build after Redo.
facts["instancers_after_redo"] = sorted(obj.name for obj in bpy.data.objects if obj.instance_collection)
print("REDO_SURVIVED " + json.dumps(facts))
