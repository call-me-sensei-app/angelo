# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""High-to-low and cage texture bakes as background jobs, on Blender's own Cycles bake job.

A flat low-poly plane takes the normals of a high-poly mesh with a planted
pyramid. Checked on the baked pixels: the pyramid appears where it is and
nowhere else; a ray that starts below it (no extrusion) misses it and a cage
above it finds it. Checked on the contract: the approval names the sources,
the cage, the image and the output; the person's selection, active object,
mode, bake panel and the material's node tree are exactly as they were after a
finished, a cancelled and a failed bake; a cancel puts the pixels back within
the stop bound; a bake Blender refuses inside its job is never reported done;
the copy is published inside the workspace with its image's resource ID; the
next message's jobs block carries that identity; a second bake through a
material that uses the baked map shows the same pyramid; and a bake asked with
a resolution makes its own image, which a cancel removes again.

A window is required: run through ``run_native_suites.py --gui --include
test_blender_production_bakes``; check ``production-bakes.json`` for
``success`` and ``phase == "complete"``.
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
from angelo_agent import job_settings  # noqa: E402
from angelo_agent.action_world import image_id  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

harness = Harness("ANGELO_PRODUCTION_BAKES", "production-bakes.json", "/tmp/angelo-production-bakes-qa")
ctx = harness.ctx


def unchanged(label):
    """The person's selection, active object, mode, bake panel and the low material's node tree, as at the start."""
    scene = bpy.context.scene
    now = (pfx.selection_state(harness.window()), pfx.bake_settings(scene), pfx.node_state(ctx["material"]))
    same = now == ctx["before"]
    if not same:
        harness.state.setdefault("differences", {})[label] = {"before": ctx["before"], "now": now}
    return same and scene.render.engine == 'BLENDER_EEVEE' and scene.cycles.samples == 16 and job_settings.TAG not in scene


def bake(image, **fields):
    arguments = {"kind": "BAKE_TEXTURE", "target": object_handle(ctx["low"]), "bake_type": "NORMAL",
                 "image": image_id(image), "samples": 16}
    arguments.update(fields)
    return harness.start_job(arguments)[0]


def raw_bake_seconds(image):
    """Blender's own time for the first job's bake, started as the Bake button would with the same settings."""
    scene, low, high, done = bpy.context.scene, ctx["low"], ctx["high"], []
    nodes = ctx["material"].node_tree.nodes
    node = nodes.new("ShaderNodeTexImage")
    node.image, nodes.active = image, node
    handler = lambda *_: done.append(time.monotonic())  # noqa: E731
    bpy.app.handlers.object_bake_complete.append(handler)
    scene.render.engine = 'CYCLES'
    started = time.monotonic()
    with bpy.context.temp_override(window=harness.window(), active_object=low, object=low, selected_objects=[high, low],
                                   selected_editable_objects=[high, low]):
        bpy.ops.object.bake('INVOKE_DEFAULT', type='NORMAL', use_clear=False, target='IMAGE_TEXTURES',
                            save_mode='INTERNAL', use_selected_to_active=True, use_cage=False, cage_object="",
                            cage_extrusion=0.5, max_ray_distance=0.0)
    ctx["raw"] = {"started": started, "done": done, "handler": handler, "node": node.name}


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.cycles_scene(scene)
        scene.render.engine = 'BLENDER_EEVEE'  # The person's engine: a bake runs on Cycles and puts this back.
        low, high = pfx.low_plane(scene), pfx.high_pyramid(scene)
        ctx.update(low=low, high=high, cage=pfx.cage_above(scene, low), material=low.data.materials[0],
                   normal=pfx.data_image("NormalMap"))
        for obj in scene.objects:
            obj.select_set(obj.name == "Cube")
        harness.window().view_layer.objects.active = bpy.data.objects["Cube"]
        ctx["before"] = (pfx.selection_state(harness.window()), pfx.bake_settings(scene), pfx.node_state(ctx["material"]))
        target, source, image = object_handle(low), object_handle(high), image_id(ctx["normal"])
        base = {"kind": "BAKE_TEXTURE", "target": target, "bake_type": "NORMAL", "image": image}
        refusals = {
            "a_target_among_its_sources": (dict(base, sources=[target]), "sources"),
            "a_cage_without_sources": (dict(base, cage=object_handle(ctx["cage"])), "sources"),
            "a_cage_with_other_faces": (dict(base, sources=[source], cage=object_handle(bpy.data.objects["Cube"])),
                                        "same faces"),
            "an_image_id_from_another_document": (dict(base, image="dat:" + "0" * 32 + ":IMAGE:1"), "image"),
        }
        for name, (arguments, expected) in refusals.items():
            message = harness.refused(arguments)
            harness.state.setdefault("refusals", {})[name] = message
            check("refused_" + name, message is not None and expected in message)
        low.hide_render = True
        message = harness.refused(dict(base, sources=[source]))
        check("refused_a_target_disabled_in_renders", message is not None and "disabled in renders" in message)
        low.hide_render = False
        high.hide_render = True  # Blender bakes from a source hidden in renders: so does the job.
        ctx["a"] = bake(ctx["normal"], sources=[source], extrusion=0.5, output="bakes/normal")
        facts, approval = ctx["facts"][ctx["a"]["id"]], ctx["approvals"][ctx["a"]["id"]]
        harness.state["approval"] = approval
        check("approval_facts_name_sources_image_and_rays", facts.get("sources") == ["HighPyramid"]
              and facts["images"] == ["NormalMap"] and facts["extrusion"] == 0.5 and facts["ray_distance"] == 0.0
              and facts["restorable"] is True and "cage" not in facts)
        check("approval_card_lists_sources_image_and_output", "HighPyramid" in approval["body"]
              and "NormalMap" in approval["body"] and "bakes/normal" in approval["body"]
              and "LowPlane" in approval["title"] + approval["body"])
        harness.go("a")
    elif phase == "a" and ended(ctx["a"]):
        record = ctx["a"]
        harness.state["receipt_a"] = record["receipt"]
        check("high_to_low_bake_done", record["status"] == "done")
        check("planted_pyramid_is_in_the_normal_map", pfx.shows_pyramid(ctx["normal"]))
        harness.state["pixels_a"] = {key: pfx.pixel(ctx["normal"], uv) for key, uv in pfx.NORMAL_PROBES.items()}
        check("copy_published_inside_the_workspace", fx.is_png(harness.workspace / "bakes/normal/NormalMap.png", 64, 64)
              and harness.hidden_left() == [])
        check("output_identified_by_its_image_resource", record["receipt"].get("outputs") == [
            {"file": "NormalMap.png", "image": image_id(ctx["normal"]), "name": "NormalMap", "map": "NORMAL"}])
        check("selection_mode_and_material_unchanged_after_done", unchanged("done"))
        entry = next((item for item in harness.jobs_block() if item["job"] == record["id"]), {})
        harness.state["jobs_block_a"] = entry
        check("jobs_block_carries_the_output_identity", entry.get("outputs") == record["receipt"]["outputs"]
              and entry.get("output") == "bakes/normal" and "/Users" not in str(entry) and "/private" not in str(entry))
        ctx["missed"] = pfx.data_image("Missed")
        ctx["b"] = bake(ctx["missed"], sources=[object_handle(ctx["high"])], extrusion=0.0)
        harness.go("b")
    elif phase == "b" and ended(ctx["b"]):
        check("no_extrusion_misses_the_pyramid", ctx["b"]["status"] == "done" and pfx.is_flat_normal(ctx["missed"])
              and not pfx.shows_pyramid(ctx["missed"]))
        ctx["caged"] = pfx.data_image("Caged")
        ctx["c"] = bake(ctx["caged"], sources=[object_handle(ctx["high"])], cage=object_handle(ctx["cage"]), extrusion=0.0)
        check("approval_names_the_cage", ctx["facts"][ctx["c"]["id"]].get("cage") == "Cage"
              and "Cage" in ctx["approvals"][ctx["c"]["id"]]["body"])
        harness.go("c")
    elif phase == "c" and ended(ctx["c"]):
        check("a_cage_above_finds_the_pyramid", ctx["c"]["status"] == "done" and pfx.shows_pyramid(ctx["caged"]))
        check("selection_mode_and_material_unchanged_after_cage", unchanged("cage"))
        ctx["slow"] = bpy.data.images.new("Slow", 128, 128, is_data=True)
        ctx["slow_pixels"] = fx.pixels(ctx["slow"])
        ctx["d"] = bake(ctx["slow"], sources=[object_handle(ctx["high"])], extrusion=0.5, samples=4096, output="bakes/slow")
        harness.go("d")
    elif phase == "d" and harness.running_with_progress(ctx["d"]):
        check("selection_untouched_while_it_runs", pfx.selection_state(harness.window()) == ctx["before"][0])
        harness.cancel(ctx["d"])
        harness.go("d_end")
    elif phase == "d_end" and ended(ctx["d"]):
        harness.stopped("high_to_low_bake")
        record = ctx["d"]
        harness.progress_ok(record, "high_to_low_bake")
        check("cancel_puts_the_pixels_back", record["status"] == "cancelled" and fx.pixels(ctx["slow"]) == ctx["slow_pixels"]
              and "put back" in record["receipt"]["note"])
        check("cancel_publishes_nothing", not (harness.workspace / "bakes/slow").exists() and harness.hidden_left() == []
              and "outputs" not in record["receipt"])
        check("selection_mode_and_material_unchanged_after_cancel", unchanged("cancel"))
        # A bake Blender refuses only inside its job (a cage whose modifier changes its faces), and reports as complete.
        ctx["cage"].modifiers.new("Subdivide", 'SUBSURF')
        arguments = {"sources": [object_handle(ctx["high"])], "cage": object_handle(ctx["cage"]), "extrusion": 0.0}
        ctx["refused_image"] = pfx.data_image("Refused")
        ctx["e"] = bake(ctx["refused_image"], output="bakes/refused", **arguments)
        harness.go("e")
    elif phase == "e" and ended(ctx["e"]):
        record = ctx["e"]
        harness.state["failure"] = {"status": record["status"], "reason": record["reason"], "receipt": record["receipt"]}
        check("a_bake_blender_refuses_is_never_reported_done", record["status"] == "failed"
              and "without baking" in record["reason"] and not ctx["refused_image"].is_dirty
              and not (harness.workspace / "bakes/refused").exists() and harness.hidden_left() == []
              and "outputs" not in record["receipt"])
        check("selection_mode_and_material_unchanged_after_failure", unchanged("failure"))
        # The same refusal over an image with unsaved pixels: nothing shows it was written, so it is not done either.
        ctx["e2"] = bake(ctx["slow"], sources=[object_handle(ctx["high"])], cage=object_handle(ctx["cage"]), extrusion=0.0)
        harness.go("e2")
    elif phase == "e2" and ended(ctx["e2"]):
        record = ctx["e2"]
        check("a_refused_bake_over_unsaved_pixels_is_not_done", record["status"] == "failed"
              and "changed nothing" in record["reason"] and fx.pixels(ctx["slow"]) == ctx["slow_pixels"])
        ctx["cage"].modifiers.clear()
        # A second job on the result: the baked map drives the low material, whose own normals are baked next.
        pfx.wire_normal_map(ctx["material"], ctx["normal"])
        ctx["before"] = (ctx["before"][0], ctx["before"][1], pfx.node_state(ctx["material"]))
        ctx["second"] = pfx.data_image("SecondMap")
        ctx["f"] = bake(ctx["second"], output="bakes/second")
        check("a_plain_bake_states_no_sources", "sources" not in ctx["facts"][ctx["f"]["id"]])
        harness.go("f")
    elif phase == "f" and ended(ctx["f"]):
        harness.state["pixels_second"] = {key: pfx.pixel(ctx["second"], uv) for key, uv in pfx.NORMAL_PROBES.items()}
        check("second_bake_through_the_baked_map_shows_the_pyramid", ctx["f"]["status"] == "done"
              and pfx.shows_pyramid(ctx["second"], tolerance=0.06)
              and fx.is_png(harness.workspace / "bakes/second/SecondMap.png", 64, 64))
        check("selection_mode_and_material_unchanged_after_second", unchanged("second"))
        # Two sources: Blender bakes them one after the other and starts its progress again for the second.
        second = pfx.high_pyramid(scene, "HighPyramidTwo")
        second.location = (0, 0, -0.2)  # Below the first: the nearer surface wins, so the map still shows one pyramid.
        ctx["both"] = bpy.data.images.new("Both", 128, 128, is_data=True)
        ctx["g"] = bake(ctx["both"], sources=[object_handle(ctx["high"]), object_handle(second)], extrusion=0.5,
                        samples=512)
        check("approval_names_both_sources", ctx["facts"][ctx["g"]["id"]]["sources"] == ["HighPyramid", "HighPyramidTwo"])
        harness.go("g")
    elif phase == "g" and ended(ctx["g"]):
        harness.progress_ok(ctx["g"], "two_sources")
        check("two_sources_bake_done", ctx["g"]["status"] == "done" and pfx.shows_pyramid(ctx["both"]))
        # No image yet: the job makes one of the asked size, named after the target and the map.
        arguments = {"kind": "BAKE_TEXTURE", "target": object_handle(ctx["low"]), "bake_type": "NORMAL", "samples": 16,
                     "sources": [object_handle(ctx["high"])], "extrusion": 0.5, "resolution": [64, 64]}
        ctx["h"] = harness.start_job(dict(arguments, output="bakes/new"))[0]
        facts = ctx["facts"][ctx["h"]["id"]]
        check("approval_names_the_new_image", facts["images"] == ["LowPlane Normal"] and facts["new_image"] == [64, 64]
              and "A new 64×64 image" in ctx["approvals"][ctx["h"]["id"]]["body"])
        ctx["new_arguments"] = arguments
        harness.go("h")
    elif phase == "h" and ended(ctx["h"]):
        image = bpy.data.images.get("LowPlane Normal")
        check("new_image_holds_the_bake", ctx["h"]["status"] == "done" and image is not None and pfx.shows_pyramid(image)
              and tuple(image.size) == (64, 64) and image.colorspace_settings.name == "Non-Color" and image.use_fake_user)
        check("new_image_identified_in_the_receipt", image is not None and ctx["h"]["receipt"].get("outputs") == [
            {"image": image_id(image), "name": "LowPlane Normal", "map": "NORMAL", "file": "LowPlane_Normal.png"}]
              and fx.is_png(harness.workspace / "bakes/new/LowPlane_Normal.png", 64, 64))
        check("selection_mode_and_material_unchanged_after_new_image", unchanged("new_image"))
        ctx["images_before"] = sorted(image.name for image in bpy.data.images)
        ctx["i"] = harness.start_job(dict(ctx["new_arguments"], samples=4096, resolution=[128, 128]))[0]
        check("a_second_new_image_gets_a_free_name", ctx["facts"][ctx["i"]["id"]]["images"] == ["LowPlane Normal 2"])
        harness.go("i")
    elif phase == "i" and harness.running_with_progress(ctx["i"]):
        check("new_image_exists_while_it_bakes", bpy.data.images.get("LowPlane Normal 2") is not None)
        harness.cancel(ctx["i"])
        harness.go("i_end")
    elif phase == "i_end" and ended(ctx["i"]):
        check("cancel_removes_the_new_image", ctx["i"]["status"] == "cancelled"
              and sorted(image.name for image in bpy.data.images) == ctx["images_before"]
              and "removed again" in ctx["i"]["receipt"]["note"])
        check("selection_mode_and_material_unchanged_after_new_image_cancel", unchanged("new_image_cancel"))
        raw_bake_seconds(pfx.data_image("RawMap"))
        harness.go("raw")
    elif phase == "raw" and ctx["raw"]["done"]:
        raw = ctx["raw"]
        bpy.app.handlers.object_bake_complete.remove(raw["handler"])
        blender, job = round(raw["done"][0] - raw["started"], 3), job_seconds(ctx["a"])
        harness.state["overhead_s"]["high_to_low_bake"] = {"job": job, "blender_alone": blender,
                                                          "overhead": round(job - blender, 3)}
        check("no_hidden_staging_left", harness.hidden_left() == [])
        harness.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
