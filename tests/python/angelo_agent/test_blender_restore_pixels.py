# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Restore and Undo restore tell the truth about unsaved image pixels, in the compiled app.

Run in an isolated ``--background --factory-startup`` process with the Angelo
add-on enabled (no provider, account or network). A generated image gets
pixel edits that are never saved, with the factory Save preference (modified
images are not saved with the file):

* the dialog's review names the image as at stake now; the Render Result never;
* Restore of a request snapshot records the image on its notice, says in the
  chat that its unsaved edits were discarded, and the restored image indeed
  lacks them;
* Undo restore says its safety snapshot never held those edits, and indeed
  the image still lacks them;
* with Save Modified Images set to Always Save, a file-backed image's unsaved
  pixels survive every private capture unsaved: neither the request snapshot
  nor Restore's safety snapshot writes the image file, the preference is left
  as it was, and Restore says the unsaved pixels were discarded;
* with Automatically Pack Resources on, a capture packs nothing into the live
  document, and Restore brings an external image back by its own file,
  unpacked, as the live document referenced it.

Run through ``run_native_suites.py --include test_blender_restore_pixels``;
check ``restore-pixels.json`` for ``success``.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-restore-pixels-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"checks": {}, "success": False}
PAINTED = (1.0, 0.0, 0.0, 1.0)
RED, BLUE = (1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)


def check(name, value):
    REPORT["checks"][name] = bool(value)


def first_pixel(name):
    image = bpy.data.images.get(name)
    return tuple(round(value, 3) for value in image.pixels[:4]) if image is not None else None


def paint():
    image = bpy.data.images.get("Paint") or bpy.data.images.new("Paint", 8, 8)
    pixels = list(image.pixels)
    pixels[:4] = PAINTED
    image.pixels[:] = pixels
    return image


def restore(controller, identity):
    before = len(controller.state["messages"])
    outcome = bpy.ops.angelo.restore_checkpoint(checkpoint_id=identity)
    return outcome, controller.state["messages"][before:]


def on_disk(path):
    probe = bpy.data.images.load(str(path), check_existing=False)
    try:
        return tuple(round(value, 3) for value in probe.pixels[:4])
    finally:
        bpy.data.images.remove(probe)


def always_save(root):
    """A private capture never writes the person's image files, whatever Save Modified Images says."""
    from angelo_agent import checkpoint_images, ui
    bpy.ops.wm.read_factory_settings(use_empty=False)
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    filepaths = bpy.context.preferences.filepaths
    factory = filepaths.save_modified_images
    try:
        filepaths.save_modified_images = "ALWAYS_SAVE"
        texture = root / "texture.png"
        seed = bpy.data.images.new("Seed", 4, 4)
        seed.pixels[:] = list(RED) * 16
        seed.filepath_raw, seed.file_format = str(texture), "PNG"
        seed.save()
        bpy.data.images.remove(seed)
        image = bpy.data.images.load(str(texture))
        image.name = "Texture"
        material = bpy.data.materials.new("Textured")
        material.use_nodes = True
        material.node_tree.nodes.new("ShaderNodeTexImage").image = image
        bpy.data.objects["Cube"].data.materials.append(material)
        bpy.ops.wm.save_as_mainfile(filepath=str(root / "always-save.blend"))
        controller = ui.get_controller()
        image.pixels[:] = list(BLUE) * 16  # The person's unsaved painting before the request.
        point = ui.capture_checkpoint(controller)
        check("always_save_request_capture_leaves_the_file", on_disk(texture) == RED)
        check("always_save_request_capture_leaves_the_pixels_unsaved",
              bpy.data.images["Texture"].is_dirty and first_pixel("Texture") == BLUE)
        check("always_save_preference_is_left_as_it_was", filepaths.save_modified_images == "ALWAYS_SAVE")
        controller.state["messages"].append({"role": "user", "text": "Make it green", "checkpoint": point["id"],
                                             "checkpoint_scope": "before_request"})
        bpy.data.images["Texture"].pixels[:] = list((0.0, 1.0, 0.0, 1.0)) * 16  # The agent's work, unsaved.
        outcome, said = restore(controller, point["id"])
        notice = next((item for item in said if item.get("restore_source_checkpoint") == point["id"]), {})
        discarded = next((item for item in said if "unsaved_images_discarded" in item), {})
        REPORT["always_save"] = {"outcome": sorted(outcome), "said": said}
        check("always_save_restore_finished", outcome == {'FINISHED'} and notice.get("restore_state") == "complete")
        check("always_save_safety_capture_leaves_the_file", on_disk(texture) == RED)
        check("always_save_restore_says_the_pixels_were_discarded",
              notice.get("unsaved_images") == ["Texture"] and discarded.get("unsaved_images_discarded") == ["Texture"])
        check("always_save_restored_image_is_the_file", first_pixel("Texture") == RED)
        check("always_save_preference_survives_restore", filepaths.save_modified_images == "ALWAYS_SAVE")
    finally:
        filepaths.save_modified_images = factory
        addon_utils.disable("angelo_agent", default_set=False)


def external_facts(pixels=False):
    """Packed images first: with Automatically Pack on, Blender itself packs an image when it loads its pixels."""
    image = bpy.data.images.get("External")
    facts = {"packed": [item.name for item in bpy.data.images if item.packed_file is not None],
             "filepath": image.filepath if image else None, "autopack": bpy.data.use_autopack,
             "dirty": bpy.data.is_dirty}
    return dict(facts, pixel=first_pixel("External")) if pixels else facts


def autopack(root):
    """With Automatically Pack Resources on, a capture never packs into the live document; Restore brings the
    external image back by its file, as the live document referenced it."""
    from angelo_agent import checkpoint_images, ui
    bpy.ops.wm.read_factory_settings(use_empty=False)
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(root / "autopack.blend"))
        bpy.data.use_autopack = True  # After the Save: a Save itself would pack.
        (root / "textures").mkdir(exist_ok=True)
        texture, other = root / "textures" / "texture.png", root / "textures" / "other.png"
        for path, color in ((texture, RED), (other, BLUE)):
            seed = bpy.data.images.new("Seed", 4, 4)
            seed.pixels[:] = list(color) * 16
            seed.filepath_raw, seed.file_format = str(path), "PNG"
            seed.save()
            bpy.data.images.remove(seed)
        image = bpy.data.images.load(str(texture))
        image.name, image.filepath = "External", "//textures/texture.png"
        material = bpy.data.materials.new("Packed?")
        material.use_nodes = True
        material.node_tree.nodes.new("ShaderNodeTexImage").image = image
        bpy.data.objects["Cube"].data.materials.append(material)
        controller = ui.get_controller()
        before = external_facts()
        point = ui.capture_checkpoint(controller)
        REPORT["autopack"] = {"before": before, "after_capture": external_facts(),
                              "capture_packs": checkpoint_images.capture_packs()}
        check("autopack_capture_leaves_the_live_document_as_it_was", external_facts() == before
              and not before["packed"] and before["autopack"])
        controller.state["messages"].append({"role": "user", "text": "Swap the texture", "checkpoint": point["id"],
                                             "checkpoint_scope": "before_request"})
        image.filepath = "//textures/other.png"  # The agent's work: the image now reads another file.
        outcome, said = restore(controller, point["id"])
        restored = bpy.data.images.get("External")
        after = REPORT["autopack"]["after_restore"] = external_facts(pixels=True)
        check("autopack_restore_finished", outcome == {'FINISHED'})
        check("autopack_restored_image_is_its_own_file_unpacked", restored is not None and not after["packed"]
              and after["pixel"] == RED and os.path.samefile(bpy.path.abspath(restored.filepath), texture))
        # Builds whose capture never packs keep the setting in the snapshot; older builds held it off.
        check("autopack_setting_in_the_restored_document", after["autopack"] is not checkpoint_images.capture_packs())
    finally:
        addon_utils.disable("angelo_agent", default_set=False)


def run(root):
    from angelo_agent import checkpoint_images, ui
    ui._authentication.start = lambda *args, **kwargs: None
    ui.refresh_connected_models = lambda *args, **kwargs: None
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(root / "project.blend"))
        controller = ui.get_controller()
        check("factory_save_preference_keeps_modified_images_unsaved",
              bpy.context.preferences.filepaths.save_modified_images != "ALWAYS_SAVE")
        paint()
        check("painted_image_is_unsaved", bpy.data.images["Paint"].is_dirty)
        point = ui.capture_checkpoint(controller)
        controller.state["messages"].append({"role": "user", "text": "Pixel fixture", "checkpoint": point["id"],
                                             "checkpoint_scope": "before_request"})
        paint()
        review = checkpoint_images.restore_review(bpy, controller, point["id"])
        check("dialog_names_the_image_at_stake", review["unsaved_now"] == ["Paint"] and not review["unsaved_then"])
        outcome, said = restore(controller, point["id"])
        notice = next((item for item in said if item.get("restore_source_checkpoint") == point["id"]), {})
        discarded = next((item for item in said if "unsaved_images_discarded" in item), {})
        REPORT["restore"] = {"outcome": sorted(outcome), "said": said}
        check("restore_finished", outcome == {'FINISHED'} and notice.get("restore_state") == "complete")
        check("restore_notice_records_the_image", notice.get("unsaved_images") == ["Paint"])
        check("chat_says_the_edits_were_discarded", discarded.get("unsaved_images_discarded") == ["Paint"]
              and "Undo restore can't bring them back" in discarded.get("text", ""))
        check("restored_image_indeed_lacks_the_edits", first_pixel("Paint") != PAINTED)
        safety = notice.get("restore_checkpoint")
        review = checkpoint_images.restore_review(bpy, controller, safety)
        check("undo_dialog_names_what_its_snapshot_never_held", review["unsaved_then"] == ["Paint"])
        outcome, said = restore(controller, safety)
        never = next((item for item in said if "unsaved_images_not_restored" in item), {})
        REPORT["undo_restore"] = {"outcome": sorted(outcome), "said": said}
        check("undo_restore_finished", outcome == {'FINISHED'})
        check("undo_restore_says_its_snapshot_never_held_them", never.get("unsaved_images_not_restored") == ["Paint"])
        check("undo_restored_image_indeed_lacks_the_edits", first_pixel("Paint") != PAINTED)
    finally:
        addon_utils.disable("angelo_agent", default_set=False)


def main():
    try:
        with tempfile.TemporaryDirectory(prefix="angelo-restore-pixels-") as directory:
            root = Path(directory)
            os.environ["ANGELO_STATE_DIR"] = str(root / "state")
            os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
            run(root)
            always_save(root)
            autopack(root)
        REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
    except Exception:
        REPORT.update(success=False, error=traceback.format_exc())
    (OUT / "restore-pixels.json").write_text(json.dumps(REPORT, indent=2, default=str))
    failed = sorted(name for name, passed in REPORT["checks"].items() if not passed)
    print("ANGELO_RESTORE_PIXELS", json.dumps({"success": REPORT["success"], "failed": failed,
                                              "error": REPORT.get("error", "")[-600:]}), flush=True)
    assert REPORT["success"], failed or REPORT.get("error")


main()
