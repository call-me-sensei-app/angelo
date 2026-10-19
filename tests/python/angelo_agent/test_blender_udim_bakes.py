# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UDIM texture bakes as background jobs, on Blender's own Cycles bake job.

A mesh whose UVs lie in tiles 1001 and 1002 bakes its emission into a UDIM image
of three tiles saved in the person's own folder. Checked on the result: the two
tiles the faces lie in hold the emitted colour inside the faces' UV squares and
their old colour outside; tile 1003 is byte-identical; every tile is published
with its number, and only the baked ones carry the map. Checked on the
contract: the approval names the tiles; the person's tile files on disk are
never written; a cancel puts every tile back from its saved file and leaves the
image without unsaved changes; a bake Blender refuses is not reported done; an
image with unsaved changes says before the job that a cancel cannot undo it; a
packed image is put back from its packed tiles; a tile without pixels and UVs
outside every tile are refused with the fix; the
selection, mode and material are as they were; and a second bake through a
material that uses the baked UDIM image reproduces its tiles in another one.

A window is required: run through ``run_native_suites.py --gui --include
test_blender_udim_bakes``; check ``udim-bakes.json`` for ``success`` and
``phase == "complete"``.
"""

import sys
import time
from pathlib import Path

import addon_utils
import bpy

from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_jobs_fixtures as fx  # noqa: E402
import production_jobs_fixtures as pfx  # noqa: E402
from production_jobs_harness import Harness, ended, job_seconds  # noqa: E402
from angelo_agent import job_bakes, job_settings  # noqa: E402
from angelo_agent.action_world import image_id  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

harness = Harness("ANGELO_UDIM_BAKES", "udim-bakes.json", "/tmp/angelo-udim-bakes-qa")
ctx = harness.ctx
OWN, LOOK = harness.out / "the persons own tiles", harness.out / "tile copies"
# The tiles' own colour, and the plane's emission (0.8, 0.2, 0.1) as an sRGB image stores it.
OLD, EMITTED = [0.2, 0.4, 0.6, 1.0], [0.906, 0.486, 0.349, 1.0]


def unchanged(label):
    scene = bpy.context.scene
    now = (pfx.selection_state(harness.window()), pfx.bake_settings(scene), pfx.node_state(ctx["material"]))
    if now != ctx["before"]:
        harness.state.setdefault("differences", {})[label] = {"before": ctx["before"], "now": now}
    return now == ctx["before"] and scene.render.engine == 'BLENDER_EEVEE' and job_settings.TAG not in scene


def bake(image, **fields):
    arguments = {"kind": "BAKE_TEXTURE", "target": object_handle(ctx["plane"]), "bake_type": "EMIT",
                 "image": image_id(image), "samples": 4}
    arguments.update(fields)
    return harness.start_job(arguments)[0]


def baked_tile(tile):
    """The emitted colour inside the face's UV square, the old colour in the corner outside it."""
    return pfx.near(tile["centre"], EMITTED) and pfx.near(tile["corner"], OLD)


def blender_alone(image):
    """Blender's own time for the first job's bake, started as the Bake button would."""
    scene, plane, done = bpy.context.scene, ctx["plane"], []
    nodes = ctx["material"].node_tree.nodes
    node = nodes.new("ShaderNodeTexImage")
    node.image, nodes.active = image, node
    handler = lambda *_: done.append(time.monotonic())  # noqa: E731
    bpy.app.handlers.object_bake_complete.append(handler)
    scene.render.engine, scene.cycles.samples = 'CYCLES', 4
    started = time.monotonic()
    with bpy.context.temp_override(window=harness.window(), active_object=plane, object=plane, selected_objects=[plane],
                                   selected_editable_objects=[plane]):
        bpy.ops.object.bake('INVOKE_DEFAULT', type='EMIT', use_clear=False, target='IMAGE_TEXTURES', save_mode='INTERNAL',
                            use_selected_to_active=False)
    ctx["raw"] = {"started": started, "done": done, "handler": handler}


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.cycles_scene(scene)
        scene.render.engine = 'BLENDER_EEVEE'
        scene.render.bake.margin = 0  # The person's own bake setting: nothing is painted outside the faces.
        plane = pfx.udim_object(scene)
        ctx.update(plane=plane, material=plane.data.materials[0], skin=pfx.udim_image("Skin", OWN / "skin"))
        ctx["before"] = (pfx.selection_state(harness.window()), pfx.bake_settings(scene), pfx.node_state(ctx["material"]))
        ctx["tiles_before"], ctx["files_before"] = pfx.tile_pixels(ctx["skin"], LOOK / "before"), pfx.file_hashes(OWN / "skin")
        empty = bpy.data.images.new("EmptyTile", 32, 32, tiled=True)
        empty.tiles.new(tile_number=1002)  # A tile that was added and never filled.
        message = harness.refused({"kind": "BAKE_TEXTURE", "target": object_handle(plane), "image": image_id(empty)})
        check("refused_a_tile_without_pixels", message is not None and "Tile 1002" in message and "fill or load" in message)
        far = pfx.udim_object(scene, "FarPlane")
        for loop in far.data.uv_layers.active.data:
            loop.uv[0] += 4.0  # Tiles 1005 and 1006, which the image does not have.
        message = harness.refused({"kind": "BAKE_TEXTURE", "target": object_handle(far), "image": image_id(ctx["skin"])})
        check("refused_uvs_outside_every_tile", message is not None and "None of FarPlane's faces" in message)
        bpy.data.objects.remove(far)
        ctx["a"] = bake(ctx["skin"], output="bakes/skin")
        facts, approval = ctx["facts"][ctx["a"]["id"]], ctx["approvals"][ctx["a"]["id"]]
        harness.state["approval"] = approval
        check("approval_names_the_tiles_it_bakes", facts.get("tiles") == {"Skin": {"baked": [1001, 1002], "count": 3}}
              and facts["restorable"] is True and "unsaved" not in facts
              and "Skin: tiles 1001, 1002 of 3" in approval["body"] and "Cancel puts them back" in approval["body"])
        harness.go("a")
    elif phase == "a" and ended(ctx["a"]):
        record = ctx["a"]
        tiles = pfx.tile_pixels(ctx["skin"], LOOK / "after")
        harness.state["tiles"] = {"before": ctx["tiles_before"], "after": tiles}
        harness.state["receipt_a"] = record["receipt"]
        check("udim_bake_done", record["status"] == "done")
        check("the_tiles_the_faces_lie_in_hold_the_bake", baked_tile(tiles[1001]) and baked_tile(tiles[1002]))
        check("the_tile_without_faces_is_byte_identical", tiles[1003]["hash"] == ctx["tiles_before"][1003]["hash"]
              and tiles[1001]["hash"] != ctx["tiles_before"][1001]["hash"])
        check("the_persons_tile_files_are_never_written", pfx.file_hashes(OWN / "skin") == ctx["files_before"])
        folder = harness.workspace / "bakes/skin"
        check("every_tile_is_published_inside_the_workspace", pfx.files_below(folder) == [
            "Skin.1001.png", "Skin.1002.png", "Skin.1003.png"] and fx.is_png(folder / "Skin.1002.png", 32, 32)
              and harness.hidden_left() == [])
        skin = image_id(ctx["skin"])
        check("outputs_identified_per_tile", record["receipt"].get("outputs") == [
            {"image": skin, "name": "Skin", "tile": 1001, "map": "EMIT", "file": "Skin.1001.png"},
            {"image": skin, "name": "Skin", "tile": 1002, "map": "EMIT", "file": "Skin.1002.png"},
            {"image": skin, "name": "Skin", "tile": 1003, "file": "Skin.1003.png"}])
        entry = next((item for item in harness.jobs_block() if item["job"] == record["id"]), {})
        check("jobs_block_carries_each_tile", entry.get("outputs") == record["receipt"].get("outputs")
              and str(harness.out) not in str(entry))
        from angelo_agent import job_ui
        check("a_tile_is_reached_by_the_name_the_job_gave_it", job_ui.output_file(record, "Skin.1002.png") == folder / "Skin.1002.png"
              and job_ui.output_file(record, "Skin.1004.png") is None)
        check("selection_mode_and_material_unchanged_after_done", unchanged("done"))
        # The baked image now has unsaved changes: nothing saved could put its tiles back, and the approval says so.
        ctx["b"] = bake(ctx["skin"])
        facts, approval = ctx["facts"][ctx["b"]["id"]], ctx["approvals"][ctx["b"]["id"]]
        check("an_unsaved_udim_image_says_cancel_cannot_undo", facts["restorable"] is False and facts["unsaved"] == ["Skin"]
              and "even after Cancel" in approval["body"] and "Save Skin first" in approval["body"])
        harness.go("b")
    elif phase == "b" and ended(ctx["b"]):
        record = ctx["b"]
        check("a_bake_over_unsaved_tiles_is_done_but_unchecked", record["status"] == "done"
              and record["receipt"].get("note") == job_bakes.UNCHECKED
              and [output.get("tile") for output in record["receipt"]["outputs"]] == [1001, 1002])
        # Cancel: a larger image so the bake runs long enough, saved, so every tile has its own file to go back to.
        ctx["slow"] = pfx.udim_image("Slow", OWN / "slow", tiles=(1001, 1002), size=256)
        ctx["slow_before"], ctx["slow_files"] = pfx.tile_pixels(ctx["slow"], LOOK / "slow-before"), pfx.file_hashes(OWN / "slow")
        ctx["c"] = bake(ctx["slow"], samples=4096, output="bakes/slow")
        harness.go("c")
    elif phase == "c" and harness.running_with_progress(ctx["c"]):
        harness.cancel(ctx["c"])
        harness.go("c_end")
    elif phase == "c_end" and ended(ctx["c"]):
        harness.stopped("udim_bake")
        harness.progress_ok(ctx["c"], "udim_bake")
        record, after = ctx["c"], pfx.tile_pixels(ctx["slow"], LOOK / "slow-after")
        check("cancel_puts_every_tile_back", record["status"] == "cancelled" and after == ctx["slow_before"]
              and not ctx["slow"].is_dirty and "put back" in record["receipt"]["note"])
        check("cancel_publishes_nothing_and_writes_no_own_file", not (harness.workspace / "bakes/slow").exists()
              and harness.hidden_left() == [] and pfx.file_hashes(OWN / "slow") == ctx["slow_files"])
        check("selection_mode_and_material_unchanged_after_cancel", unchanged("cancel"))
        # A bake Blender refuses only inside its job: a cage whose modifier changes its faces.
        high = pfx.udim_object(scene, "UdimHigh")
        cage = bpy.data.objects.new("UdimCage", ctx["plane"].data.copy())
        scene.collection.objects.link(cage)
        cage.modifiers.new("Subdivide", 'SUBSURF')
        ctx["d"] = bake(ctx["slow"], sources=[object_handle(high)], cage=object_handle(cage), extrusion=0.1)
        harness.go("d")
    elif phase == "d" and ended(ctx["d"]):
        record = ctx["d"]
        harness.state["failure"] = {"status": record["status"], "reason": record["reason"], "receipt": record["receipt"]}
        check("a_udim_bake_blender_refuses_is_never_reported_done", record["status"] == "failed"
              and "without baking" in record["reason"] and not ctx["slow"].is_dirty
              and pfx.tile_pixels(ctx["slow"], LOOK / "slow-refused") == ctx["slow_before"])
        check("selection_mode_and_material_unchanged_after_failure", unchanged("failure"))
        # A second job on the result: the baked UDIM image drives the emission, which is baked into another one.
        tree = ctx["material"].node_tree
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.name, texture.image, texture.select = "Skin Texture", ctx["skin"], False
        tree.links.new(texture.outputs["Color"], tree.nodes["Emit"].inputs["Color"])
        ctx["before"] = (ctx["before"][0], ctx["before"][1], pfx.node_state(ctx["material"]))
        ctx["second"] = pfx.udim_image("Second", OWN / "second", tiles=(1001, 1002), color=(0.0, 0.0, 0.0, 1.0))
        ctx["e"] = bake(ctx["second"], output="bakes/second")
        harness.go("e")
    elif phase == "e" and ended(ctx["e"]):
        tiles = pfx.tile_pixels(ctx["second"], LOOK / "second")
        harness.state["second_tiles"] = tiles
        check("second_bake_through_the_baked_udim_image_reproduces_its_tiles", ctx["e"]["status"] == "done"
              and all(pfx.near(tiles[number]["centre"], EMITTED, 0.05) for number in (1001, 1002))
              and pfx.files_below(harness.workspace / "bakes/second") == ["Second.1001.png", "Second.1002.png"])
        check("selection_mode_and_material_unchanged_after_second", unchanged("second"))
        # Without ``image`` the material's own active node decides, as in Blender: the same tiles are found.
        nodes = ctx["material"].node_tree.nodes
        nodes.active, nodes["Skin Texture"].select = nodes["Skin Texture"], True
        facts = __import__("json").loads(harness.executor().precondition("angelo_job_start", {
            "kind": "BAKE_TEXTURE", "target": object_handle(ctx["plane"]), "bake_type": "EMIT"})[2])
        check("the_materials_own_udim_image_is_found_with_its_tiles", facts["images"] == ["Skin"]
              and facts["tiles"] == {"Skin": {"baked": [1001, 1002], "count": 3}})
        # A packed UDIM image has no files: a cancel puts its tiles back from the data packed in the .blend.
        ctx["packed"] = pfx.udim_image("Packed", OWN / "packed", tiles=(1001, 1002), size=256)
        ctx["packed"].pack()
        for path in (OWN / "packed").iterdir():
            path.unlink()
        ctx["packed_before"] = pfx.tile_pixels(ctx["packed"], LOOK / "packed-before")
        ctx["f"] = bake(ctx["packed"], samples=4096)
        check("a_packed_udim_image_can_be_put_back", ctx["facts"][ctx["f"]["id"]]["restorable"] is True)
        harness.go("f")
    elif phase == "f" and harness.running_with_progress(ctx["f"]):
        harness.cancel(ctx["f"])
        harness.go("f_end")
    elif phase == "f_end" and ended(ctx["f"]):
        check("cancel_puts_packed_tiles_back", ctx["f"]["status"] == "cancelled" and not ctx["packed"].is_dirty
              and pfx.tile_pixels(ctx["packed"], LOOK / "packed-after") == ctx["packed_before"]
              and len(ctx["packed"].packed_files) == 2 and pfx.files_below(OWN / "packed") == [])
        blender_alone(pfx.udim_image("Alone", OWN / "alone"))
        harness.go("raw")
    elif phase == "raw" and ctx["raw"]["done"]:
        raw = ctx["raw"]
        bpy.app.handlers.object_bake_complete.remove(raw["handler"])
        blender, job = round(raw["done"][0] - raw["started"], 3), job_seconds(ctx["a"])
        harness.state["overhead_s"]["udim_bake"] = {"job": job, "blender_alone": blender, "overhead": round(job - blender, 3)}
        check("no_hidden_staging_left", harness.hidden_left() == [])
        harness.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
