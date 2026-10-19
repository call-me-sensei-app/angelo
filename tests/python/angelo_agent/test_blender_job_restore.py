# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a background job changes for itself is put back exactly: when it ends and from a file saved meanwhile.

``job_settings`` on real Blender data, without running a job. Checked: settings
that constrain each other come back whatever the order (an image format takes
its colour depth and view format with it; a video output takes its media
type), a setting found by name (a compositor node) is restored by its RNA
path, a setting that can no longer be reached is reported and never silently
skipped, and a file saved while a job's changes were applied (its settings,
the material node a bake made active, an image the bake created, the render
path and every File Output node's directory and file name, a simulation's
bake target and directory) opens as the person left it. Run only in a disposable instance with the exact Angelo
binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider, account, network request or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-job-restore-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import job_bake_caches, job_bake_targets, job_render_scene, job_settings  # noqa: E402
import long_jobs_fixtures as fx  # noqa: E402
import production_jobs_fixtures as pfx  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
FORMAT = "render.image_settings."
FORMAT_PATHS = tuple(FORMAT + name for name in ("views_format", "color_depth", "color_mode", "media_type", "file_format"))


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def image_format(scene):
    return {path: job_settings.get(scene, path) for path in FORMAT_PATHS}


def a_job_format(scene, identity="j1"):
    """A render job's own image format, applied as ``RenderRunner`` applies it."""
    overrides = job_settings.Overrides(scene, identity)
    overrides.apply({FORMAT + "media_type": "IMAGE", FORMAT + "file_format": "PNG"},
                    also_record=(FORMAT + "views_format", FORMAT + "color_depth", FORMAT + "color_mode"))
    return overrides


def constrained_settings():
    scene, settings = bpy.context.scene, bpy.context.scene.render.image_settings
    scene.render.use_multiview = True
    settings.media_type, settings.file_format = "IMAGE", "OPEN_EXR"
    settings.color_depth, settings.color_mode, settings.views_format = "32", "RGBA", "MULTIVIEW"
    before = image_format(scene)
    overrides = a_job_format(scene)
    check("a_png_job_changes_the_depth_and_view_format_too",
          (settings.file_format, settings.color_depth, settings.views_format) == ("PNG", "16", "INDIVIDUAL"))
    check("request_admitted_meanwhile_reads_the_persons_format",
          job_settings.scene_value(scene, FORMAT + "color_depth") == "32")
    check("constrained_settings_all_come_back", overrides.restore() == [] and image_format(scene) == before
          and job_settings.TAG not in scene)
    settings.media_type = "VIDEO"  # Blender then offers only its video format: a still job sets the media type first.
    before = image_format(scene)
    overrides = a_job_format(scene)
    check("a_video_output_renders_stills_for_the_job", (settings.media_type, settings.file_format) == ("IMAGE", "PNG"))
    check("a_video_output_comes_back", overrides.restore() == [] and image_format(scene) == before)
    settings.media_type = "IMAGE"
    scene.render.use_multiview = False


def named_settings():
    scene = bpy.context.scene
    tree = bpy.data.node_groups.new("Compositing", "CompositorNodeTree")
    scene.compositing_node_group = tree
    node = tree.nodes.new("CompositorNodeOutputFile")
    node.name, node.directory, node.file_name = 'Passes "A".001', "//renders/passes", "{blend_name}_"
    path = 'compositing_node_group.nodes["%s"].' % bpy.utils.escape_identifier(node.name)
    overrides = job_settings.Overrides(scene, "j2")
    overrides.apply({path + "directory": str(OUT / "staging"), path + "file_name": ""})
    check("a_named_setting_is_applied_by_its_rna_path", node.directory == str(OUT / "staging") and node.file_name == "")
    check("a_named_setting_comes_back", overrides.restore() == []
          and (node.directory, node.file_name) == ("//renders/passes", "{blend_name}_"))
    overrides = job_settings.Overrides(scene, "j3")
    overrides.apply({path + "directory": str(OUT / "staging"), "render.resolution_x": 320})
    node.name = "Renamed by the person"
    unrestored = overrides.restore()
    check("an_unreachable_setting_is_reported_and_the_rest_restored",
          unrestored == [path + "directory"] and scene.render.resolution_x != 320 and job_settings.TAG not in scene)
    node.directory = "//renders/passes"


def bake_material():
    """A material whose active node is its shader and whose shown texture is another image's node."""
    scene = bpy.context.scene
    low = pfx.low_plane(scene)
    material = low.data.materials[0]
    nodes = material.node_tree.nodes
    albedo = nodes.new("ShaderNodeTexImage")
    albedo.name, albedo.image = "Albedo", pfx.data_image("Albedo", 8)
    nodes.active = albedo  # The shown texture.
    shader = next(node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
    nodes.active = shader
    for node in nodes:
        node.select = node == shader
    return low, material


def saved_while_a_bake_ran():
    scene = bpy.context.scene
    scene.render.engine, scene.cycles.samples = "BLENDER_EEVEE", 32
    low, material = bake_material()
    before, images = pfx.node_state(material), sorted(image.name for image in bpy.data.images)
    material_name = material.name
    check("fixture_shows_another_texture_than_the_active_node",
          before["active"] == "Principled BSDF" and before["textured"] == ["Albedo"])
    overrides = job_settings.Overrides(scene, "j4")
    overrides.apply({"render.engine": "CYCLES", "cycles.samples": 4})
    name = job_bake_targets.new_image_name(low, "NORMAL")
    overrides.note(job_bake_targets.CREATED_KEY, name)
    created = job_bake_targets.create_image(name, (8, 8), "NORMAL")
    nodes = job_bake_targets.TargetNodes(low, created)
    overrides.note(job_bake_targets.RESTORE_KEY, nodes.apply())
    during = pfx.node_state(material)
    check("the_bake_image_is_the_active_selected_shown_texture", during["active"] == job_bake_targets.TEMPORARY_NODE
          and job_bake_targets.TEMPORARY_NODE in during["selected"]
          and during["textured"] == [job_bake_targets.TEMPORARY_NODE])
    saved = OUT / "saved-while-baking.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), copy=True)
    nodes.restore()
    job_bake_targets.remove_created(name)
    check("the_job_puts_the_material_and_images_back", overrides.restore() == [] and pfx.node_state(material) == before
          and sorted(image.name for image in bpy.data.images) == images and scene.render.engine == "BLENDER_EEVEE")
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    scene, material = bpy.context.scene, bpy.data.materials[material_name]  # Every earlier reference is gone.
    check("the_saved_file_holds_the_jobs_changes", job_settings.TAG in scene and scene.render.engine == "CYCLES"
          and pfx.node_state(material)["active"] == job_bake_targets.TEMPORARY_NODE and name in bpy.data.images)
    check("opening_it_restores_what_the_job_left", job_settings.restore_tags() == [scene.name]
          and pfx.node_state(material) == before and sorted(image.name for image in bpy.data.images) == images
          and (scene.render.engine, scene.cycles.samples) == ("BLENDER_EEVEE", 32) and job_settings.TAG not in scene)
    scene[job_settings.TAG] = json.dumps({"job": "j9", "values": ["not", "a", "map"], "extras": {"unknown": 1}})
    check("an_unreadable_record_is_dropped_without_an_error", job_settings.restore_tags() == [scene.name]
          and job_settings.TAG not in scene)


def saved_while_a_render_ran():
    """A stereo scene with File Output nodes, one in a group: a job's plan redirects them all, and a file saved
    meanwhile opens with the person's own paths and formats."""
    scene = bpy.context.scene
    own = OUT / "the persons own folder"
    pfx.stereo_scene(scene)
    pfx.compositor(scene, own)
    scene.render.filepath = str(own / "{blend_name}_####")
    settings = scene.render.image_settings
    settings.media_type, settings.file_format = "IMAGE", "OPEN_EXR"
    settings.color_depth, settings.views_format = "32", "MULTIVIEW"
    before = pfx.output_settings(scene)
    staging = OUT / "work {space}" / ".angelo-job-j5"
    plan = job_render_scene.Plan(scene, "PNG")
    overrides = job_settings.Overrides(scene, "j5")
    values = {job_render_scene.FORMAT + "media_type": "IMAGE", job_render_scene.FORMAT + "file_format": "PNG"}
    values.update(plan.settings(staging))
    overrides.apply(values, also_record=job_render_scene.FORMAT_DEPENDENTS)
    nodes = pfx.file_output_nodes(scene)
    check("every_file_output_node_is_sent_into_the_jobs_folder", all(
        node.directory.replace("{{", "{").replace("}}", "}").startswith(str(staging)) and "{blend_name}" not in node.file_name
        for name, node in nodes.items() if name != "Muted") and nodes["Muted"].directory == str(own)
        and scene.render.filepath == job_render_scene.escape(staging / "frame_"))
    check("blender_names_the_views_as_planned", plan.check_paths(scene, 7) is None)
    saved = OUT / "saved-while-rendering.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), copy=True)
    check("the_job_puts_every_path_and_format_back", overrides.restore() == [] and pfx.output_settings(scene) == before)
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    scene = bpy.context.scene
    check("the_saved_file_holds_the_jobs_paths", str(staging.name) in scene.render.filepath and job_settings.TAG in scene)
    check("opening_it_restores_every_path_and_format", job_settings.restore_tags() == [scene.name]
          and pfx.output_settings(scene) == before and job_settings.TAG not in scene)


def saved_while_a_simulation_baked():
    """A simulation's bake settings point into a job's staging while it bakes to disk: a file saved then opens
    with the person's own target and directory."""
    scene = bpy.context.scene
    host = fx.simulation(scene, grid=4, frames=3)
    modifier = host.modifiers["Simulation"]
    modifier.bake_directory, modifier.bakes[0].use_custom_path = "//own cache", True
    modifier.bakes[0].directory = "//own cache/zone"
    before = pfx.bake_settings_of(host)
    ranges = lambda bake: (1, 3)  # noqa: E731
    plan = job_bake_caches.plan(host, [(modifier, modifier.bakes[0])], ranges)
    overrides = job_settings.Overrides(scene, "j6")
    overrides.apply(job_bake_caches.settings(host, plan, OUT / "caches" / ".angelo-job-j6"))
    during = pfx.bake_settings_of(host)["Simulation"]
    check("the_bake_is_sent_into_the_jobs_folder", during["target"] == "DISK" and ".angelo-job-j6" in during["directory"]
          and during["bakes"][0][:2] == ("INHERIT", False))
    saved = OUT / "saved-while-simulating.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), copy=True)
    check("the_job_puts_the_bake_settings_back", overrides.restore() == [] and pfx.bake_settings_of(host) == before)
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    scene, host = bpy.context.scene, bpy.data.objects["SimHost"]
    check("opening_the_saved_file_restores_the_bake_settings", ".angelo-job-j6" in pfx.bake_settings_of(host)[
        "Simulation"]["directory"] and job_settings.restore_tags() == [scene.name]
        and pfx.bake_settings_of(host) == before and job_settings.TAG not in scene)


def run_checks():
    constrained_settings()
    named_settings()
    saved_while_a_bake_ran()
    saved_while_a_render_ran()
    saved_while_a_simulation_baked()
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "job-restore.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_JOB_RESTORE", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
