# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``batch`` through real staged programs: a unit repeated, mirrored and varied as one operation.

Rows and grids, explicit positions, a path at a spacing, a seeded scatter, a
unit of several parts with a parent inside it, mirroring and seeded variation,
on objects created in the program and on declared ones. Every result is read
back from the live scene after commit: names, transforms, shared data and
materials, parents and collections. Then the second edits (a Bevel on one
member, the shared material recoloured for every member), one Undo step and
Redo per program, a forced commit failure, a stale binding and the bounds.
Programs are normalized by ``tool_specs.validate_call`` as a model's plan is.
Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
No provider or paid call.
"""

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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-batch-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import batch_spec, native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion, terse_receipt  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from angelo_agent.refusal_codes import CORRECTABLE_CODES  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
IRON, RED = [0.02, 0.02, 0.025, 1], [0.8, 0.05, 0.05, 1]


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


def objects():
    return window.scene.objects


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def program(operations, writes=(), reads=()):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [binding(obj) for obj in reads],
                                                  "write_set": [binding(obj) for obj in writes], "operations": operations})


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def run(canonical):
    job = native.begin(window, "angelo_scene_program", canonical)
    return job, settle(job)


def committed(name, operations, writes=(), reads=()):
    job, staged = run(program(operations, writes, reads))
    report["last_error"] = staged.get("error")
    check(name + "_stages", staged.get("ready_to_commit"))
    done = commit(job)
    report["last_error"] = job.result.get("error")
    check(name + "_commits", done and bool(job.result.get("checks"))
          and all(row.get("passed") for row in job.result["checks"]))
    return job


def refused(name, operations, text, writes=(), reads=()):
    try:
        canonical = program(operations, writes, reads)
    except ValueError as error:  # Refused when the plan is validated.
        report["last_error"] = str(error)
        check(name, text in str(error))
        return str(error)
    job, result = run(canonical)
    failed = result.get("status") == "failed" or not commit(job)
    outcome = job.result or result
    error = outcome.get("error", "")
    report["last_error"] = error
    check(name, failed and text in error and no_stages())
    # A refusal of the program is correctable, never UNKNOWN with the restore-snapshot recovery (object_duplicate's).
    report.setdefault("refusal_codes", {})[name] = outcome.get("error_code")
    check(name + "_is_a_correctable_refusal", outcome.get("error_code") in CORRECTABLE_CODES
          and not outcome.get("partial_changes_possible") and "recovery" not in outcome)
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


def named(prefix):
    return sorted(obj.name for obj in objects() if obj.name.startswith(prefix))


def near(left, right, tolerance=1e-5):
    return len(left) == len(right) and all(abs(a - b) <= tolerance for a, b in zip(left, right))


def cube(name, alias, location, scale=(1, 1, 1), size=1.0):
    return op("create_primitive", primitive="CUBE", name=name, alias=alias, size=size, location=list(location),
              scale=list(scale))


def batch(sources, **parameters):
    return op("batch", sources=sources, **parameters)


def kinds(job):
    return [row["checks"][0]["kind"] for row in job.result["checks"]]


def rows_and_mirror():
    """The railing: a post every 1.5 m on both sides, with one material, as three operations."""
    baseline("batch QA rows")
    job = committed("posts", [
        cube("Railing Post 01", "post", (-3, -0.7, 0.8), (0.1, 0.1, 1.0)),
        op("material_principled", "$post", name="QA Iron", inputs={"Base Color": IRON, "Metallic": 0.9}),
        batch(["$post"], array=[{"step": [1.5, 0, 0], "count": 5}], mirror="Y")])
    posts = [objects()[name] for name in named("Railing Post")]
    check("ten_posts_named_in_order", [post.name for post in posts] == ["Railing Post %02d" % index for index in range(1, 11)])
    check("one_mesh_and_one_material", len({post.data for post in posts}) == 1 and posts[0].data.users == 10
          and len({post.material_slots[0].material for post in posts}) == 1
          and posts[0].material_slots[0].material.name == "QA Iron")
    wanted = [(-3 + 1.5 * index, -0.7, 0.8) for index in range(5)] + [(-3 + 1.5 * index, 0.7, 0.8) for index in range(5)]
    check("posts_stand_in_two_mirrored_rows", all(near(post.location, place) for post, place in zip(posts, wanted))
          and all(near(post.scale, (0.1, 0.1, 1.0)) and near(post.rotation_euler, (0, 0, 0)) for post in posts))
    check("no_negative_scale_from_mirroring", all(min(post.scale) > 0 for post in posts))
    result = job.result["operation_results"][2]
    check("the_batch_reports_what_it_made", (result["instances"], result["created"], result["linked"]) == (10, 9, True)
          and result["parts"][0] == {"source": "Railing Post 01", "copies": 9, "first": "Railing Post 02",
                                     "last": "Railing Post 10", "first_alias": "$post_2"}
          and result["bounds"] == [[-3.0, -0.7, 0.8], [3.0, 0.7, 0.8]])
    copies = next(row["checks"][0] for row in job.result["checks"] if row["checks"][0]["kind"] == "copies")
    check("the_copies_check_reads_the_scene", copies["observed"] == {"copies": 9, "linked": True, "first": "Railing Post 02",
                                                                  "last": "Railing Post 10"})
    check("all_ten_are_in_the_scene_collection", all(post.users_collection[0] == window.scene.collection for post in posts))
    report["evidence"] = evidence(job)
    undo()
    check("rows_one_undo", not named("Railing Post") and bpy.data.materials.get("QA Iron") is None and no_stages())
    redo()
    check("rows_redo", len(named("Railing Post")) == 10 and objects()["Railing Post 07"].data == objects()["Railing Post 01"].data)


def evidence(job):
    """What each runtime's model is handed for the committed batch (claude_outcomes)."""
    call = {"tool": "angelo_scene_program", "arguments": job.program}
    summary = certified_completion([call], [job.result], read_only=lambda name: False)
    terse = terse_receipt("angelo_scene_program", job.result)
    check("the_summary_states_the_copies", "copies on 'Railing Post 01': copies 9; linked true; first 'Railing Post 02'; "
          "last 'Railing Post 10'" in summary)
    # The terse receipt qualifies the copies check with its source when other checks have
    # different targets (including nested material-identity guards). Keep the exact count
    # and endpoints, rather than requiring the older unqualified "copies 9" spelling.
    check("the_handoff_states_the_copies", "copies 'Railing Post 01' 9, first 'Railing Post 02', "
          "last 'Railing Post 10'" in terse["verified"] and len(terse["live"]) == 10)
    live = [{"handle": handle(obj), "name": obj.name,
             "object_revision": queries.object_revision(window, obj)}
            for obj in (objects()["Railing Post %02d" % index] for index in range(1, 11))]
    check("the_handoff_keeps_every_exact_live_binding", terse["live"] == live
          and not terse.get("live_truncated"))
    return {"summary": summary, "claude": terse}


def second_edits():
    """Bevel one member of the batch, then recolour the shared material once for every member."""
    baseline("batch QA second edits")
    member = objects()["Railing Post 04"]
    job = committed("bevel_one_member", [op("modifier_add", member, type="BEVEL", name="Soft Edges",
                                            settings={"width": 0.01, "segments": 2})], writes=[member])
    posts = [objects()[name] for name in named("Railing Post")]
    check("only_that_member_is_bevelled", [len(post.modifiers) for post in posts] == [0, 0, 0, 1, 0, 0, 0, 0, 0, 0]
          and len({post.data for post in posts}) == 1 and "modifier" in kinds(job))
    last = objects()["Railing Post 10"]
    job = committed("recolour_the_shared_material", [op("material_principled", last, name="QA Iron",
                                                        inputs={"Base Color": RED}, shared="ALL_USERS")], writes=[last])
    posts = [objects()[name] for name in named("Railing Post")]
    colors = {tuple(round(v, 3) for v in next(node for node in post.material_slots[0].material.node_tree.nodes
                                             if node.type == "BSDF_PRINCIPLED").inputs["Base Color"].default_value)
              for post in posts}
    check("every_member_changed_and_still_shares", colors == {(0.8, 0.05, 0.05, 1.0)} and len({post.data for post in posts}) == 1
          and job.result["shared_edits"][0]["count"] == 10 and "isolated_shared_data" not in job.result)
    undo()
    undo()
    check("second_edits_two_undos", not objects()["Railing Post 04"].modifiers and no_stages())
    redo()
    redo()
    check("second_edits_redo", len(objects()["Railing Post 04"].modifiers) == 1)


def unit_of_parts():
    """A lamp is a post and a lantern parented to it: the unit is copied whole, and mirrored."""
    baseline("batch QA unit")
    job = committed("lamps", [
        cube("Lamp Post 1", "lamp", (-9, -2.05, 2.7), (0.16, 0.16, 3.4)),
        op("create_primitive", primitive="UV_SPHERE", name="Lamp Lantern 1", alias="lantern", radius=0.2, segments=16,
           rings=8, location=[-9, -2.05, 4.6]),
        op("parent_set", "$lantern", parent="$lamp"),
        op("material_principled", "$lantern", name="QA Glow", inputs={
            "Base Color": [1, 0.55, 0.22, 1], "Emission Color": [1, 0.5, 0.2, 1], "Emission Strength": 4}),
        batch(["$lamp", "$lantern"], array=[{"step": [4.5, 0, 0], "count": 5}], mirror="Y")])
    posts, lanterns = [objects()["Lamp Post %d" % i] for i in range(1, 11)], [objects()["Lamp Lantern %d" % i] for i in range(1, 11)]
    window.view_layer.update()
    check("each_lantern_follows_its_own_post", all(lantern.parent == post for post, lantern in zip(posts, lanterns)))
    check("the_unit_keeps_its_shape", all(near(lantern.matrix_world.translation - post.matrix_world.translation, (0, 0, 1.9), 1e-4)
                                          for post, lantern in zip(posts, lanterns)))
    xs = sorted({round(post.matrix_world.translation.x, 4) for post in posts})
    ys = sorted({round(post.matrix_world.translation.y, 4) for post in posts})
    check("ten_lamps_in_two_rows", xs == [-9.0, -4.5, 0.0, 4.5, 9.0] and ys == [-2.05, 2.05])
    check("one_copies_check_per_part", kinds(job).count("copies") == 2
          and len({lantern.data for lantern in lanterns}) == 1 and len({post.data for post in posts}) == 1)
    undo()
    check("unit_one_undo", not named("Lamp") and no_stages())
    redo()
    # Parts that share a name's stem share its numbering: the mirrored piers are 4 to 6, never a part's own name.
    job = committed("piers", [cube("QA Pier 1", "low", (4, 20, 0.2), (0.5, 4.4, 0.4)),
                              cube("QA Pier 2", "mid", (5.5, 20, 0), (0.5, 4.4, 0.8)),
                              cube("QA Pier 3", "tall", (7, 20, -0.35), (0.5, 4.4, 1.5)),
                              batch(["$low", "$mid", "$tall"], mirror="X")])
    piers = [objects()["QA Pier %d" % index] for index in range(1, 7)]
    check("same_stem_parts_share_one_numbering", [round(pier.location.x, 4) for pier in piers] == [4, 5.5, 7, -4, -5.5, -7]
          and [pier.data for pier in piers[3:]] == [pier.data for pier in piers[:3]])
    parts = job.result["operation_results"][3]["parts"]
    check("the_result_states_each_parts_names_and_stride",
          [(part["first"], part["last"], part.get("stride")) for part in parts] == [
              ("QA Pier 4", "QA Pier 4", 3), ("QA Pier 5", "QA Pier 5", 3), ("QA Pier 6", "QA Pier 6", 3)])
    undo()
    check("piers_one_undo", not named("QA Pier") and no_stages())
    # A name without a number stays that name: Blender's copy suffix, as Shift+D gives, never 'QA Abutments 2'.
    job = committed("abutments", [cube("QA Abutments", "abut", (-10.5, 20, -0.3), (3, 4.4, 1.4)),
                                  batch(["$abut"], at=[[-10.5, 20, -0.3], [10.5, 20, -0.3]])])
    check("an_unnumbered_unit_keeps_its_name_on_the_copy", named("QA Abutments") == ["QA Abutments", "QA Abutments.001"]
          and objects()["QA Abutments.001"].data == objects()["QA Abutments"].data
          and near(objects()["QA Abutments.001"].location, (10.5, 20, -0.3)))
    part = job.result["operation_results"][1]["parts"][0]
    copies = next(row["checks"][0] for row in job.result["checks"] if row["checks"][0]["kind"] == "copies")
    check("the_result_and_check_name_the_suffixed_copy", (part["first"], part["last"]) == ("QA Abutments.001",) * 2
          and copies["passed"] and copies["observed"]["first"] == "QA Abutments.001")
    undo()
    check("abutments_one_undo", not named("QA Abutments") and no_stages())
    # Parts attached to a scaled, turned parent: steps are world metres, and mirror is across the parent's own plane.
    turn = 0.5
    job = committed("wheels", [
        op("create_primitive", primitive="CUBE", name="QA Car Body", alias="body", location=[10, 30, 1.75],
           rotation=[0, 0, turn], scale=[2, 0.9, 0.35]),
        op("create_primitive", primitive="CYLINDER", name="QA Wheel 1", alias="wheel", radius=0.35, depth=0.25,
           location=[10, 30, 1.35]),
        op("transform", "$wheel", location=[10 - 1.25 * math.cos(turn) + 0.8 * math.sin(turn),
                                           30 - 1.25 * math.sin(turn) - 0.8 * math.cos(turn), 1.35]),
        op("parent_set", "$wheel", parent="$body", keep_transform=True),
        batch(["$wheel"], array=[{"step": [2.5 * math.cos(turn), 2.5 * math.sin(turn), 0], "count": 2}], mirror="Y")])
    window.view_layer.update()
    body = objects()["QA Car Body"]
    local = sorted(tuple(round(value, 3) for value in (body.matrix_world.inverted() @ objects()["QA Wheel %d" % index]
                                                       .matrix_world.translation)) for index in range(1, 5))
    # In the body's own space (scaled 2 x 0.9 x 0.35): +/-1.25 m along it is +/-0.625, +/-0.8 m across is +/-0.889.
    check("steps_are_world_metres_under_a_scaled_parent",
          local == [(-0.625, -0.889, -1.143), (-0.625, 0.889, -1.143), (0.625, -0.889, -1.143), (0.625, 0.889, -1.143)])
    check("the_copies_keep_their_parent", all(objects()["QA Wheel %d" % index].parent == body for index in range(1, 5)))
    undo()
    check("wheels_one_undo", not named("QA Wheel") and "QA Car Body" not in objects() and no_stages())
    refused("a_constrained_parent_has_no_known_place", [
        cube("QA Arm 1", "arm", (0, 40, 0)), cube("QA Hand 1", "hand", (1, 40, 0)), cube("QA Aim", "aim", (5, 40, 0)),
        {"kind": "constraint_add", "target": "$arm", "parameters": {"type": "TRACK_TO", "target": "$aim"}},
        op("parent_set", "$hand", parent="$arm", keep_transform=True),
        batch(["$hand"], array=[{"step": [0, 1, 0], "count": 3}])], "PARENT_SPACE")


def positions_path_and_scatter():
    """Explicit positions with their own rotation and scale, a path at a spacing, and a seeded scatter."""
    baseline("batch QA placements")
    placements = [[0, 6, 0], [2, 6, 0], [4, 7, 0.5]]
    committed("at", [cube("QA Crate 1", "crate", (9, 9, 9)),
                     batch(["$crate"], at=placements, rotations=[[0, 0, 0], [0, 0, 0.5], [0.2, 0, 1.0]],
                           scales=[[1, 1, 1], [2, 2, 2], [1, 0.5, 0.25]])])
    crates = [objects()["QA Crate %d" % index] for index in (1, 2, 3)]
    check("the_unit_itself_is_the_first_instance", all(near(crate.location, place) for crate, place in zip(crates, placements)))
    check("each_instance_takes_its_rotation_and_scale", near(crates[1].rotation_euler, (0, 0, 0.5))
          and near(crates[1].scale, (2, 2, 2)) and near(crates[2].rotation_euler, (0.2, 0, 1.0), 1e-4)
          and near(crates[2].scale, (1, 0.5, 0.25)))
    committed("along", [cube("QA Bollard 1", "bollard", (0, 0, 0), (0.2, 0.2, 1)),
                        batch(["$bollard"], along={"points": [[0, 10, 0], [4, 10, 0], [4, 13, 0]], "spacing": 1.0,
                                                   "align": True})])
    bollards = [objects()["QA Bollard %d" % index] for index in range(1, 9)]
    path = [(0, 10), (1, 10), (2, 10), (3, 10), (4, 10), (4, 11), (4, 12), (4, 13)]
    check("a_copy_every_metre_along_the_path", all(near(tuple(bollard.location)[:2], place, 1e-4)
                                                   for bollard, place in zip(bollards, path)))
    check("copies_turn_with_the_path", near([bollards[1].rotation_euler.z, bollards[6].rotation_euler.z], [0, math.pi / 2], 1e-4))
    scatter = {"sources": ["$rock"], "scatter": {"count": 8, "min": [4.5, -18, -3], "max": [8, 18, -2.4]}, "mirror": "X",
               "vary": {"seed": 17, "scale": [[0.7, 0.6, 0.5], [1.6, 1.4, 1.1]], "rotation": [[0, 0, 0], [0.3, 0.3, 6.28]]}}
    job = committed("scatter", [
        op("create_primitive", primitive="ICO_SPHERE", name="Rock 01", alias="rock", radius=0.5, subdivisions=2),
        op("material_principled", "$rock", name="QA Stone", inputs={"Base Color": [0.23, 0.24, 0.23, 1], "Roughness": 0.9}),
        {"kind": "batch", "target": None, "parameters": scatter}])
    rocks = [objects()["Rock %02d" % index] for index in range(1, 17)]
    plan = batch_spec.instances(job.program["operations"][2]["parameters"])
    check("sixteen_rocks_eight_a_side", sum(rock.location.x > 0 for rock in rocks) == 8
          and all(4.5 <= abs(rock.location.x) <= 8 and -18 <= rock.location.y <= 18 and -3 <= rock.location.z <= -2.4
                  for rock in rocks))
    check("one_rock_per_slice_of_the_bank", [int((rock.location.y + 18) // 4.5) for rock in rocks[:8]] == list(range(8)))
    check("the_scene_is_the_seeded_plan", all(
        near(rock.location, [(-1 if entry["mirror"] else 1) * entry["position"][0], *entry["position"][1:]], 1e-4)
        and near(rock.scale, entry["scale"], 1e-4) for rock, entry in zip(rocks, plan)))
    check("mirrored_rocks_vary_on_their_own", not near(rocks[0].scale, rocks[8].scale)
          and near(rocks[0].location, (-rocks[8].location.x, rocks[8].location.y, rocks[8].location.z), 1e-4))
    check("rocks_share_one_mesh_and_material", len({rock.data for rock in rocks}) == 1
          and len({rock.material_slots[0].material for rock in rocks}) == 1)
    report["scatter_locations"] = [[round(v, 4) for v in rock.location] for rock in rocks]
    undo()
    job_again, _ = run(program([
        op("create_primitive", primitive="ICO_SPHERE", name="Rock 01", alias="rock", radius=0.5, subdivisions=2),
        {"kind": "batch", "target": None, "parameters": scatter}]))
    check("the_same_seed_gives_the_same_scene", commit(job_again) and report["scatter_locations"] == [
        [round(v, 4) for v in objects()["Rock %02d" % index].location] for index in range(1, 17)])
    undo()
    undo()
    undo()
    check("placements_undone", not named("QA Crate") and not named("QA Bollard") and not named("Rock") and no_stages())


def declared_sources():
    """Copies of an object that already exists: read for a row, written when the batch places it."""
    source = objects()["Cube"]
    source.name = "QA Plank 01"
    source.location, source.scale = (0, -6, 0), (0.07, 0.7, 0.04)
    window.view_layer.update()  # As after an artist's edit: the world matrix is part of the revision.
    baseline("batch QA declared")
    source = objects()["QA Plank 01"]
    job = committed("row_of_a_declared_object", [batch([handle(source)], array=[{"step": [0.15, 0, 0], "count": 6}])],
                    reads=[source])
    planks = [objects()["QA Plank %02d" % index] for index in range(1, 7)]
    check("linked_copies_share_the_live_data", all(plank.data == source.data for plank in planks) and source.data.users == 6
          and near(planks[5].location, (0.75, -6, 0)) and source.name == "QA Plank 01")
    check("copies_join_the_sources_collection", all([c.name for c in plank.users_collection] == ["Collection"] for plank in planks))
    check("the_source_is_only_read", [row["name"] for row in job.result["changes"]["added"]] == [p.name for p in planks[1:]]
          and not job.result["changes"]["modified"] and job.program["write_set"] == [])
    copies = next(row["checks"][0] for row in job.result["checks"])
    check("a_declared_sources_copies_are_checked", copies["kind"] == "copies" and copies["observed"]["copies"] == 5)
    refused("placing_a_declared_object_needs_the_write_set", [batch([handle(source)], at=[[0, 0, 0], [1, 0, 0]])],
            "must appear in the write set", reads=[source])
    full = objects()["QA Plank 06"]
    job = committed("full_copies_moved_from_a_declared_object", [
        batch([handle(full)], linked=False, at=[[0, -8, 0], [0, -9, 0]])], writes=[full])
    copy = objects()["QA Plank 07"]
    check("a_full_copy_owns_its_data", copy.data != full.data and near(full.location, (0, -8, 0))
          and near(copy.location, (0, -9, 0)) and near(copy.scale, (0.07, 0.7, 0.04))
          and copy.material_slots[0].material == full.material_slots[0].material)
    undo()
    undo()
    check("declared_two_undos", named("QA Plank") == ["QA Plank 01"] and objects()["QA Plank 01"].data.users == 1 and no_stages())


def recovery():
    """A forced commit failure, a stale binding and a name too long leave the scene as it was; a held number is
    passed over."""
    queries.invalidate()
    source = objects()["QA Plank 01"]
    before = {"objects": sorted(obj.name for obj in bpy.data.objects), "meshes": len(bpy.data.meshes),
              "fingerprint": native._fingerprint(source), "location": tuple(source.location)}
    original = native.BlenderProgramBackend._apply_collection_intents

    def fail_after(self, stage, journal):
        original(self, stage, journal)
        raise RuntimeError("injected failure after the copies were linked")

    job, _ = run(program([batch([handle(source)], at=[[5, 5, 5], [6, 5, 5], [7, 5, 5]], mirror="X")], writes=[source]))
    with patch.object(native.BlenderProgramBackend, "_apply_collection_intents", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    check("rollback_reports_failure", job.result.get("status") == "failed" and not job.result.get("partial_changes_possible"))
    check("rollback_restores_names_transforms_and_the_scene",
          sorted(obj.name for obj in window.scene.objects if obj.name.startswith("QA Plank")) == ["QA Plank 01"]
          and tuple(source.location) == before["location"] and native._fingerprint(source) == before["fingerprint"]
          and source.data.users == 1 and no_stages())
    queries.invalidate()
    stale = program([batch([handle(source)], array=[{"step": [1, 0, 0], "count": 3}])], reads=[source])
    source.location.z += 1
    queries.invalidate(window)
    job, result = run(stale)
    check("a_stale_binding_is_refused", result.get("status") == "failed" and "revision changed" in result.get("error", "")
          and named("QA Plank") == ["QA Plank 01"] and no_stages())
    queries.invalidate()
    # A number already held (here by an object outside the scene) is passed over, never refused: 'QA Plank 03'
    # makes the two copies 04 and 05 (batch_spec.unit_names).
    bpy.data.objects.new("QA Plank 03", None)
    source.location.z -= 1  # Back from the stale binding above; the world matrix is part of the revision.
    window.view_layer.update()
    baseline("batch QA held numbers")
    job = committed("held_numbers", [batch([handle(source)], array=[{"step": [1, 0, 0], "count": 3}])], reads=[source])
    part = job.result["operation_results"][0]["parts"][0]
    check("copies_pass_over_a_held_number", named("QA Plank") == ["QA Plank 01", "QA Plank 04", "QA Plank 05"]
          and (part["first"], part["last"]) == ("QA Plank 04", "QA Plank 05")
          and objects()["QA Plank 05"].data == source.data)
    undo()
    check("held_numbers_one_undo", named("QA Plank") == ["QA Plank 01"] and no_stages())
    bpy.data.objects.remove(bpy.data.objects["QA Plank 03"])
    queries.invalidate()
    longest = "QA " + "L" * 58 + " 9"  # 63 characters: its copy's number would make 64.
    refused("a_copy_name_too_long_is_refused", [cube(longest, "longest", (0, 40, 0)),
                                                batch(["$longest"], array=[{"step": [1, 0, 0], "count": 2}])],
            "NAME_TAKEN")
    queries.invalidate()


def bounds():
    """One operation however many copies it makes; the copies have their own bounds."""
    post = [cube("QA Unit 1", "unit", (0, 30, 0))]
    refused("a_batch_over_its_copy_limit_is_refused", post + [batch(["$unit"], array=[{"step": [1, 0, 0], "count": 40},
                                                                                     {"step": [0, 1, 0], "count": 40}])],
            "BATCH_TOO_LARGE")
    refused("a_program_over_its_copy_limit_is_refused", post + [
        batch(["$unit"], array=[{"step": [1, 0, 0], "count": 600}]),
        cube("QA Other 1", "other", (0, 40, 0)), batch(["$other"], array=[{"step": [1, 0, 0], "count": 600}])],
        "PROGRAM_TOO_LARGE")
    refused("data_shared_by_copies_is_not_edited_in_the_same_program", post + [
        batch(["$unit"], array=[{"step": [2, 0, 0], "count": 3}]),
        op("mesh_bevel", "$unit_2", edge_selector={}, offset=0.05)], "linked copies made earlier in this program")
    refused("an_animated_source_is_refused", post + [
        op("keyframe_insert", "$unit", property="location", value=[0, 30, 0], frame=1),
        batch(["$unit"], array=[{"step": [2, 0, 0], "count": 3}])], "ANIMATED_SOURCE")
    refused("parts_of_a_unit_share_one_parent", post + [
        cube("QA Stray 1", "stray", (0, 32, 0)), cube("QA Root 1", "root", (0, 34, 0)),
        op("parent_set", "$stray", parent="$root"), batch(["$unit", "$stray"], mirror="X")], "UNIT_PARENTS")
    # Like object_duplicate, batch copies mesh, curve, text, empty, camera and light objects: a Grease Pencil
    # source is a VALIDATION refusal before anything is copied (it was UNKNOWN, with partial changes possible).
    data = bpy.data.grease_pencils.new("QA Sketch")
    data.layers.new("Layer").frames.new(1).drawing.add_strokes([4])
    sketch = bpy.data.objects.new("QA Sketch", data)
    window.scene.collection.objects.link(sketch)
    window.view_layer.update()
    queries.invalidate()
    refused("a_grease_pencil_source_is_refused", [batch([handle(sketch)], array=[{"step": [1, 0, 0], "count": 3}])],
            "batch copies mesh, curve, text, empty, camera and light objects", reads=[sketch])
    check("a_grease_pencil_source_is_validation", report["refusal_codes"]["a_grease_pencil_source_is_refused"]
          == "VALIDATION" and named("QA Sketch") == ["QA Sketch"])
    bpy.data.objects.remove(sketch)
    bpy.data.grease_pencils.remove(data)
    queries.invalidate()
    baseline("batch QA thirty-two")
    operations = post + [batch(["$unit"], array=[{"step": [1, 0, 0], "count": 100}])] + [
        op("transform", "$unit_%d" % index, rotation=[0, 0, 0.1]) for index in range(2, 32)]
    job = committed("a_hundred_copies_are_one_of_thirty_two_operations", operations)
    check("thirty_two_operations_and_a_hundred_objects", len(job.program["operations"]) == 32
          and len(named("QA Unit")) == 100 and job.result["changes"]["truncated"] is False
          and len(job.result["changes"]["added"]) == 100 and job.result["verified"]["added_count"] == 100)
    check("members_stay_addressable_in_the_program", near(objects()["QA Unit 31"].rotation_euler, (0, 0, 0.1))
          and near(objects()["QA Unit 32"].rotation_euler, (0, 0, 0)))
    undo()
    check("hundred_one_undo", not named("QA Unit") and no_stages())


def names_the_program_asks_for():
    """Copies never take a name the same program asks for, and a name asked twice keeps Blender's ".NNN": both rolled
    whole worker programs back before (recorded 2026-10-25, phase 1 workers; program_names, derived_checks)."""
    baseline("batch QA requested names")
    job = committed("a_copy_skips_a_name_the_program_creates_later", [
        cube("QA Rung 0", "rung0", (0, 50, 0)), batch(["$rung0"], array=[{"step": [1, 0, 0], "count": 3}]),
        cube("QA Rung 1", "rung1", (0, 52, 0))])
    part = job.result["operation_results"][1]["parts"][0]
    check("the_copies_pass_over_the_requested_name", named("QA Rung") == ["QA Rung 0", "QA Rung 1", "QA Rung 2", "QA Rung 3"]
          and (part["first"], part["last"]) == ("QA Rung 2", "QA Rung 3")
          and near(objects()["QA Rung 1"].location, (0, 52, 0)))
    undo()
    check("requested_names_one_undo", not named("QA Rung") and no_stages())
    job = committed("a_name_asked_twice_keeps_blenders_suffix", [
        cube("QA Peg", "peg1", (0, 54, 0)), cube("QA Peg", "peg2", (1, 54, 0))])
    check("the_second_is_suffixed_and_stated", named("QA Peg") == ["QA Peg", "QA Peg.001"]
          and job.result["operation_results"][1]["object"] == "QA Peg.001" and near(objects()["QA Peg.001"].location, (1, 54, 0)))
    undo()
    check("asked_twice_one_undo", not named("QA Peg") and no_stages())
    held = bpy.data.objects.new("QA Held", None)  # Held before the program: its name check still fails.
    window.scene.collection.objects.link(held)
    window.view_layer.update()
    queries.invalidate()
    refused("a_name_held_before_the_program_still_fails", [cube("QA Held", "held", (0, 56, 0))], "object_name")
    # Asked twice, whichever creation asks first (break-it E2E-19B M-1): the repeat derives no name check, so the stage
    # refuses a name an object held before the program.
    unaliased = op("create_primitive", primitive="CUBE", name="QA Held", size=1, location=[0, 58, 0], scale=[1, 1, 1])
    rig = op("armature_create", name="QA Held", alias="rig", bones=[{"name": "Root", "head": [0, 0, 0], "tail": [0, 0, 1]}])
    for label, first in (("aliased", cube("QA Held", "first", (0, 58, 0))), ("unaliased", unaliased), ("armature", rig)):
        refused("a_name_held_before_and_asked_twice_fails_" + label, [first, cube("QA Held", "second", (2, 58, 0))],
                "NAME_TAKEN: an object from before this program is named 'QA Held'")
    check("asked_twice_held_leaves_only_the_holder", named("QA Held") == ["QA Held"])
    bpy.data.objects.remove(held)
    queries.invalidate()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    rows_and_mirror()
    second_edits()
    unit_of_parts()
    positions_path_and_scatter()
    declared_sources()
    recovery()
    bounds()
    names_the_program_asks_for()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "batch.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_BATCH", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
