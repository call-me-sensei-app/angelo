# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A physical sky and Cycles settings through real staged programs, and the audit that admits the settings.

``world_configure``'s ``sky``: a Sky Texture feeding the background, set,
changed, replaced by a colour again, read back by the derived check, undone
and redone; a forced commit failure and a stale binding leave the World as it
was. Cycles: every Python-defined setting ``rna_rules`` admits is compared
with the definition the installed Cycles add-on registers (no get or set, an
update that is absent or exactly ``update_render_passes``, constant enum
items), the settings it refuses stay refused, and admitted settings are
written, verified, covered by the scene revision, undone and rolled back.
Last, one program carries a whole lighting request: three area lights, the
sky and the render settings. Run only in a disposable instance with the exact
Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
"""

import inspect
import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-render-setup-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, rna_rules, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.action_world_spec import world_graph  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion, compact_receipt, terse_receipt  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
FIVE_DEGREES = round(math.radians(5), 4)


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return "FINISHED" in result and job.result.get("status") == "committed"


def scene_binding():
    settings = scene_settings.execute(window, {})
    return {"handle": settings["handle"], "revision": settings["revision"]}


def program(operations, binding=None):
    """A scene-bound program: every ``@scene`` target is the scene handle, bound at ``binding`` (now by default)."""
    binding = binding or scene_binding()
    operations = [dict(operation, target=binding["handle"]) if operation["target"] == "@scene" else operation
                  for operation in operations]
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [binding],
                                                  "operations": operations})


def op(kind, target="@scene", **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def run(canonical):
    job = native.begin(window, "angelo_scene_program", canonical)
    return job, settle(job)


def committed(name, operations):
    job, staged = run(program(operations))
    report["last_error"] = staged.get("error")
    check(name + "_stages", staged.get("ready_to_commit"))
    done = commit(job)
    report["last_error"] = job.result.get("error")
    check(name + "_commits", done and bool(job.result.get("checks"))
          and all(row.get("passed") for row in job.result["checks"]))
    queries.invalidate()
    return job


def refused(name, operations, text):
    try:
        canonical = program(operations)
    except ValueError as error:  # Refused when the plan is validated.
        report["last_error"] = str(error)
        check(name, text in str(error))
        return str(error)
    job, result = run(canonical)
    failed = result.get("status") == "failed" or not commit(job)
    error = (job.result or result).get("error", "")
    report["last_error"] = error
    check(name, failed and text in error and no_stages())
    return error


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def sky():
    """The scene World's Sky Texture node, or None."""
    world = window.scene.world
    return None if world is None else world_graph(world)["sky"]


def near(left, right, tolerance=1e-5):
    return abs(left - right) <= tolerance


def physical_sky():
    """world_configure sky: set, edited again, removed, undone and redone, with its derived check."""
    baseline("render setup QA sky")
    job = committed("sky", [op("world_configure", sky={"sun_elevation": FIVE_DEGREES, "sun_rotation": 0.6}, strength=0.8)])
    node = sky()
    check("the_background_is_fed_by_a_sky_texture", node is not None and node.type == "TEX_SKY"
          and near(node.sun_elevation, FIVE_DEGREES) and near(node.sun_rotation, 0.6)
          and node.sky_type == "MULTIPLE_SCATTERING" and node.sun_disc)
    background = world_graph(window.scene.world)["background"]
    check("strength_is_set_beside_the_sky", near(background.inputs["Strength"].default_value, 0.8))
    observed = job.result["checks"][0]["checks"][0]["observed"]["values"]
    check("the_derived_check_read_the_sky", job.result["checks"][0]["checks"][0]["kind"] == "world_settings"
          and near(observed["sky"]["sun_elevation"], FIVE_DEGREES))
    check("the_result_reports_the_sky", near(job.result["operation_results"][0]["world"]["sky"]["sun_elevation"],
                                             FIVE_DEGREES))
    check("the_settings_read_reports_the_sky", near(scene_settings.execute(window, {})["world"]["sky"]["sun_rotation"], 0.6))
    evidence(job)
    undo()
    check("sky_one_undo", sky() is None and no_stages())
    redo()
    check("sky_redo", sky() is not None and near(sky().sun_elevation, FIVE_DEGREES))
    # The second edit: raise the sun and switch the model. Members left out keep their values.
    committed("sky_again", [op("world_configure", sky={"sun_elevation": 0.5, "type": "SINGLE_SCATTERING",
                                                       "sun_disc": False, "ozone_density": 2.0})])
    node = sky()
    check("a_second_edit_keeps_what_it_does_not_state", near(node.sun_elevation, 0.5) and near(node.sun_rotation, 0.6)
          and node.sky_type == "SINGLE_SCATTERING" and not node.sun_disc and near(node.ozone_density, 2.0)
          and len(window.scene.world.node_tree.nodes) == 3)
    # A colour beside the sky is kept under its link and reported unused (it used to be refused, COLOR_HIDDEN).
    job = committed("a_colour_beside_a_sky", [op("world_configure", color=[0.2, 0.3, 0.8])])
    report["colour_beside_sky"] = {"rows": job.result.get("operation_results"), "sky": sky() is not None,
                                   "colour": list(world_graph(window.scene.world)["background"].inputs["Color"].default_value)}
    check("a_colour_beside_a_sky_is_kept_unused", sky() is not None
          and any("sky drives the rendered background" in row.get("color_unused", "") for row in job.result["operation_results"])
          and [round(value, 4) for value in world_graph(window.scene.world)["background"].inputs["Color"].default_value[:3]]
          == [0.2, 0.3, 0.8])
    undo()
    refused("a_sky_and_an_environment_are_refused", [op("world_configure", sky={}, environment="dat:%s:IMAGE:1" % ("a" * 32))],
            "ONE_BACKGROUND")
    refused("an_elevation_outside_its_range_is_refused", [op("world_configure", sky={"sun_elevation": 2.0})], "closed schema")
    # Recovery: a forced commit failure and a stale binding leave the World as it was.
    before = scene_binding()["revision"]
    original = native.BlenderProgramBackend._apply_collection_intents

    def fail_after(self, stage, journal):
        original(self, stage, journal)
        raise RuntimeError("injected failure after the World was swapped")

    job, _ = run(program([op("world_configure", sky={"sun_elevation": 1.2, "air_density": 3.0})]))
    with patch.object(native.BlenderProgramBackend, "_apply_collection_intents", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    queries.invalidate()
    check("sky_rollback_restores_the_world", job.result.get("status") == "failed"
          and not job.result.get("partial_changes_possible") and near(sky().sun_elevation, 0.5)
          and near(sky().air_density, 1.0) and scene_binding()["revision"] == before and no_stages())
    stale = program([op("world_configure", sky={"sun_elevation": 0.3})])
    sky().sun_rotation = 1.0  # The World changed after the plan was written.
    queries.invalidate(window)
    job, result = run(stale)
    report["last_error"] = result.get("error")
    check("a_stale_sky_is_refused", result.get("status") == "failed" and "scene settings changed" in result.get("error", "")
          and near(sky().sun_elevation, 0.5) and no_stages())
    sky().sun_rotation = 0.6
    queries.invalidate(window)
    committed("sky_removed", [op("world_configure", sky="NONE", color=[0.2, 0.3, 0.8])])
    background = world_graph(window.scene.world)["background"]
    check("none_removes_the_sky_and_a_colour_shows_again", sky() is None and not background.inputs["Color"].is_linked
          and near(background.inputs["Color"].default_value[2], 0.8))
    undo()
    undo()
    check("two_more_undos", sky() is not None and near(sky().sun_elevation, FIVE_DEGREES) and sky().sun_disc)
    undo()
    check("back_to_the_factory_world", sky() is None and no_stages())


def evidence(job):
    """What each runtime is shown: the host's summary, a Claude handoff entry and a Codex receipt."""
    call = {"tool": "angelo_scene_program", "arguments": job.binding["program"]}
    summary = certified_completion([call], [job.result], read_only=lambda name: False)
    check("the_host_summary_reports_the_verified_world", "Host checks passed on the live result" in summary
          and "world_settings on 'Scene'" in summary)
    revision = scene_binding()["revision"]
    terse = terse_receipt("angelo_scene_program", job.result)  # A Claude context's handoff entry.
    check("the_claude_handoff_has_the_check_and_the_new_revision", "world_settings" in terse["verified"]
          and terse["live"][0]["object_revision"] == revision)
    receipt = compact_receipt("angelo_scene_program", job.result)  # What a Codex thread's next turn reads.
    check("the_codex_receipt_has_the_change_and_the_new_revision", receipt["status"] == "committed"
          and receipt["changes"]["modified"] == [{"name": "Scene", "fields": ["world"]}]
          and receipt["live_revisions"]["objects"][0]["object_revision"] == revision)


def cycles_definitions():
    """The audit: what rna_rules admits is what the installed Cycles add-on defines without callbacks."""
    import cycles.properties as properties
    # The add-on defers its annotations: evaluated, each is the deferred property with its keywords.
    definitions = inspect.get_annotations(properties.CyclesRenderSettings, eval_str=True)
    admitted = {path.split(".", 1)[1] for path in rna_rules.PROVEN_RUNTIME}
    check("every_admitted_setting_is_a_cycles_render_setting", admitted <= set(definitions)
          and all(path.startswith("cycles.") and path.count(".") == 1 for path in rna_rules.PROVEN_RUNTIME))
    problems = []
    for name in sorted(admitted):
        keywords = definitions[name].keywords
        update = keywords.get("update")
        if keywords.get("get") or keywords.get("set") or keywords.get("poll") or callable(keywords.get("items")):
            problems.append(name + ": get, set, poll or computed items")
        elif name in rna_rules.CYCLES_PASS_UPDATE and update is not properties.update_render_passes:
            problems.append(name + ": update is not update_render_passes")
        elif name in rna_rules.CYCLES_PLAIN and update is not None:
            problems.append(name + ": has an update callback")
    report["audit_problems"] = problems
    check("admitted_settings_run_no_unaudited_python", not problems)
    body = [line.strip() for line in inspect.getsource(properties.update_render_passes).splitlines()[1:] if line.strip()]
    check("update_render_passes_is_the_audited_function", body == ["view_layer = context.view_layer",
                                                                   "view_layer.update_render_passes()"])
    # Everything else the add-on defines has a reason to stay out; a new setting is refused until someone audits it.
    refused_names = sorted(set(definitions) - admitted)
    report["refused_cycles_settings"] = refused_names
    expected = {"preview_pause", "shading_system", "sampling_pattern", "denoiser", "preview_denoiser", "dicing_camera",
                "bake_type"}
    check("the_refused_settings_are_the_stated_ones", {name for name in refused_names if not name.startswith("debug_")}
          == expected and len(admitted) == 88)
    with_callbacks = {name for name, definition in definitions.items() if definition.keywords.get("update")
                      or callable(definition.keywords.get("items")) or definition.keywords.get("poll")}
    check("every_callback_was_looked_at", with_callbacks <= set(rna_rules.CYCLES_PASS_UPDATE) | expected)


CYCLES_VALUES = {"render.engine": "CYCLES", "cycles.samples": 128, "cycles.use_denoising": True, "cycles.device": "CPU",
                 "cycles.volume_bounces": 3, "cycles.dicing_rate": 2.0, "cycles.time_limit": 30.0,
                 "cycles.denoising_prefilter": "FAST", "cycles.use_guiding": True, "cycles.tile_size": 1024}


def live_values(paths=CYCLES_VALUES):
    scene, values = window.scene, {}
    for path in paths:
        holder = scene
        *parents, name = path.split(".")
        for parent in parents:
            holder = getattr(holder, parent)
        value = getattr(holder, name)
        values[path] = round(value, 5) if isinstance(value, float) else value
    return values


def cycles_settings():
    """Admitted settings are typed, verified, in the revision, undone and rolled back; the others stay refused."""
    baseline("render setup QA cycles")
    before = live_values()
    job = committed("cycles", [op("property_set", values=CYCLES_VALUES)])
    check("cycles_values_are_live", live_values() == CYCLES_VALUES)
    observed = next(row["checks"][0]["observed"] for row in job.result["checks"] if row["checks"][0]["kind"] == "property")
    check("the_derived_check_read_every_value", all(path in json.dumps(observed) for path in CYCLES_VALUES))
    undo()
    check("cycles_one_undo", live_values() == before and no_stages())
    redo()
    check("cycles_redo", live_values() == CYCLES_VALUES)
    # The second edit, and a setting the scene report does not list is still part of the revision.
    committed("cycles_again", [op("property_set", values={"cycles.samples": 256, "cycles.volume_bounces": 0})])
    check("a_second_edit_keeps_the_rest", window.scene.cycles.samples == 256 and window.scene.cycles.volume_bounces == 0
          and window.scene.cycles.use_denoising and window.scene.cycles.time_limit == 30.0)
    check("the_report_stays_small", "cycles.volume_bounces" not in scene_settings.execute(window, {})["values"])
    stale = program([op("property_set", values={"cycles.volume_bounces": 5})])
    window.scene.cycles.diffuse_bounces += 1  # An admitted setting changed after the plan was written.
    queries.invalidate(window)
    job, result = run(stale)
    report["last_error"] = result.get("error")
    check("a_stale_cycles_setting_is_refused", result.get("status") == "failed"
          and "scene settings changed" in result.get("error", "") and window.scene.cycles.volume_bounces == 0 and no_stages())
    window.scene.cycles.diffuse_bounces -= 1
    queries.invalidate(window)
    before_failure = live_values()
    original = native.BlenderProgramBackend._apply_collection_intents

    def fail_after(self, stage, journal):
        original(self, stage, journal)
        raise RuntimeError("injected failure after the settings were written")

    job, _ = run(program([op("property_set", values={"cycles.samples": 9, "cycles.device": "GPU",
                                                    "cycles.use_denoising": False, "cycles.dicing_rate": 8.0})]))
    with patch.object(native.BlenderProgramBackend, "_apply_collection_intents", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    queries.invalidate()
    check("cycles_rollback_restores_every_setting", job.result.get("status") == "failed"
          and not job.result.get("partial_changes_possible") and live_values() == before_failure and no_stages())
    for path, value in (("cycles.shading_system", True), ("cycles.preview_pause", True), ("cycles.denoiser", "OPTIX"),
                        ("cycles.sampling_pattern", "AUTOMATIC"), ("cycles.bake_type", "COMBINED"),
                        ("cycles.debug_use_spatial_splits", True), ("cycles.preview_denoiser", "AUTO")):
        refused("refused_" + path.split(".")[1], [op("property_set", values={path: value})], "RUNTIME_PROPERTY")
    # A refused setting is never written, not even with its own value while staging: its callback does not run.
    import cycles.properties as properties
    calls = []
    properties.CyclesRenderSettings.qa_unaudited = bpy.props.BoolProperty(update=lambda self, context: calls.append(1))
    try:
        refused("an_unaudited_setting_is_refused", [op("property_set", values={"cycles.qa_unaudited": True})],
                "RUNTIME_PROPERTY")
        check("a_refused_settings_callback_never_runs", not calls and not window.scene.cycles.qa_unaudited)
    finally:
        del properties.CyclesRenderSettings.qa_unaudited
    refused("a_value_outside_the_rna_range_is_refused", [op("property_set", values={"cycles.tile_size": 4})], "RNA_RANGE")
    refused("an_unknown_enum_item_is_refused", [op("property_set", values={"cycles.device": "TPU"})], "ENUM_ITEM")
    undo()
    undo()
    check("cycles_back_to_the_start", live_values() == before and no_stages())


def lighting_in_one_program():
    """A key, a fill and a rim area light, a physical sky five degrees up and Cycles at 128 samples with denoising."""
    baseline("render setup QA lighting")

    def area(name, location, energy, size):
        return op("light_create", None, name=name, location=location, look_at=[0, 0, 0],
                  settings={"light_type": "AREA", "energy": energy, "size": size})

    job = committed("lighting", [
        area("Key Light", [4, -4, 5], 1000, 2), area("Fill Light", [-2, -6, 2.5], 250, 3),
        area("Rim Light", [-5, 5, 3.5], 600, 1.5),
        op("world_configure", sky={"sun_elevation": FIVE_DEGREES}),
        op("property_set", values={"render.engine": "CYCLES", "cycles.samples": 128, "cycles.use_denoising": True})])
    scene = window.scene
    lights = [obj for obj in scene.objects if obj.type == "LIGHT" and obj.data.type == "AREA"]
    check("three_area_lights_a_sky_and_cycles", len(lights) == 3 and near(sky().sun_elevation, FIVE_DEGREES)
          and scene.render.engine == "CYCLES" and scene.cycles.samples == 128 and scene.cycles.use_denoising)
    check("one_program_five_operations", len(job.binding["program"]["operations"]) == 5)
    undo()
    check("lighting_one_undo", sky() is None and not [obj for obj in window.scene.objects if obj.name.endswith(" Light")]
          and window.scene.render.engine != "CYCLES" and no_stages())
    redo()
    check("lighting_redo", sky() is not None and window.scene.cycles.samples == 128)
    undo()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    physical_sky()
    report["cycles"] = bool(bpy.app.build_options.cycles and hasattr(window.scene, "cycles"))
    if report["cycles"]:  # An optional build feature: a build without it refuses the paths (test_blender_property_set).
        cycles_definitions()
        cycles_settings()
        lighting_in_one_program()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "render-setup.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_RENDER_SETUP", json.dumps({key: report.get(key) for key in ("success", "error", "last_error")})[:3000])
if not report.get("success"):
    sys.exit(1)
