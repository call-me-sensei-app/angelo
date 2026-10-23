# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dropped images become Image steps of a Generation Flow, with real Blender images (1.0.5 drop targets).

Background, no window: ``angelo.flow_drop_items`` gets what the node editor's drop hands it (``image-id:``,
``image-file:``, ``studio-item:``, ``model:`` lines) for an image loaded from disk, a packed image, an image generated
in Blender, an image file, an image already in Media Studio's library (written by another writer, so new to the flow's
library until the drop reloads it) and a 3D model. Each image must land in Media Studio's library (a Studio image is
used as it is, never imported twice) and get one Image step, in drop order, in a column from the drop point,
selected and ready (it has its image); the model is skipped; an image that isn't one is reported and the rest still
land; the person's own images keep their file paths. The drag itself is ``test_blender_node_drop_targets`` (GUI).
Run through ``run_native_suites.py --include test_blender_flow_drop_items``; check ``flow-drop-items.json`` for
``success``.
"""

import json
import os
import sys
import traceback
from pathlib import Path

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-flow-drop-items-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
import addon_utils  # noqa: E402
import bpy  # noqa: E402

from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False
from angelo_agent import flow_drop, flow_host, flow_nodes  # noqa: E402

REPORT = OUT / "flow-drop-items.json"


def png(path, colour):
    image = bpy.data.images.new(path.stem, 8, 8)
    image.pixels.foreach_set(list(colour) * 64)
    image.filepath_raw, image.file_format = str(path), 'PNG'
    image.save()
    bpy.data.images.remove(image)
    return path


def main():
    state = {"success": False, "checks": {}}
    checks = state["checks"]
    try:
        on_disk = bpy.data.images.load(str(png(OUT / "on-disk.png", (1, 0, 0, 1))))
        packed = bpy.data.images.load(str(png(OUT / "packed.png", (0, 1, 0, 1))))
        packed.pack()
        generated = bpy.data.images.new("Generated", 16, 16)
        loose = png(OUT / "loose.png", (0, 0, 1, 1))
        not_image = OUT / "notes.png"
        not_image.write_text("not an image")
        paths = {image.name: image.filepath for image in bpy.data.images}
        tree = bpy.data.node_groups.new("Drop", flow_nodes.TREE)
        before = tree.nodes.new(flow_nodes.ANGELO_FlowGenerateImageNode.bl_idname)
        before.select = True
        # Written by another writer (a worker, Media Studio's asset writer): the flow's library learns of it on drop.
        from angelo_agent.studio_state import StudioLibrary
        flow_host.library()
        other = StudioLibrary(flow_host.library().directory)
        studio = other.import_image(str(png(OUT / "studio.png", (1, 1, 0, 1))))["id"]
        checks["the_studio_image_is_new_to_the_flows_library"] = studio not in flow_host.library().assets
        library_before = set(flow_host.library().assets) | {studio}
        items = "\n".join(["image-id:%d" % on_disk.session_uid, "image-id:%d" % packed.session_uid,
                           "model:Chair", "image-id:%d" % generated.session_uid, "image-file:%s" % not_image,
                           "image-file:%s" % loose, "studio-item:images/%s.blend/Material/Studio" % studio])
        result = bpy.ops.angelo.flow_drop_items(tree=tree.name, items=items, location=(300.0, 120.0))
        placed = [node for node in tree.nodes if node.bl_idname == flow_nodes.ANGELO_FlowImageNode.bl_idname]
        added = set(flow_host.library().assets) - library_before
        states = {node.name: (flow_host.node_state(tree, node.name) or {}).get("state") for node in placed}
        state["observed"] = {"result": sorted(result), "states": states, "library_added": sorted(added),
                             "placed": [(n.name, n.asset_id, tuple(n.location)) for n in placed]}
        checks["the_drop_finished"] = result == {'FINISHED'}
        # A library image is named after its file; an image with no current file is written out as "image.png".
        names = [flow_host.library().assets[n.asset_id]["prompt"] for n in placed]
        state["observed"]["names"] = names
        checks["one_image_step_per_image_in_drop_order"] = (
            len(added) == 4 and names == ["on-disk.png", "image.png", "image.png", "loose.png", "studio.png"])
        everything = len(flow_host.library().assets) == len(library_before) + 4
        used = placed[-1].asset_id == studio and studio not in added
        checks["a_media_studio_image_is_used_as_it_is"] = used and everything
        checks["a_column_from_the_drop_point"] = [tuple(round(v) for v in n.location) for n in placed] == [
            (300 - 30, 120 + 10 - flow_drop.STEP_SPACING * i) for i in range(5)]
        checks["only_the_dropped_steps_are_selected"] = not before.select and all(n.select for n in placed)
        checks["every_step_has_its_image"] = all(
            flow_host.library().assets[n.asset_id]["kind"] == "IMAGE" for n in placed) and all(
            s not in {"MISSING", "PARTIAL", "BLOCKED", "FAILED"} for s in states.values())
        checks["the_model_and_the_non_image_add_nothing"] = len(tree.nodes) == 6
        checks["the_persons_images_keep_their_files"] = all(
            bpy.data.images[name].filepath == path for name, path in paths.items())
        state["success"] = all(checks.values())
    except Exception:
        state["error"] = traceback.format_exc()
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_FLOW_DROP_ITEMS", json.dumps({"success": state["success"]}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
