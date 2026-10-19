# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""visibility_set through real staged programs: an object's eye and a collection's visibility.

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is,
so each carries its derived ``visibility`` check. Covers staging without a
live edit, commit to the bound view layer, one-step Undo, the scene settings
revision going stale on a manual exclude, the live check through
angelo_scene_verify and the refusals. An object that ends a program only in
collections excluded from the view layer has no visibility to set: moved,
created, nested or already there, it is a correctable refusal before anything
live is written (earlier builds failed with INTERNAL_ERROR at commit), and a
collection the artist excludes while the job waits is a stale dependency. Run
only in a disposable instance with
the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-visibility-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent import scene_validation  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def scene_binding():
    settings = scene_settings.execute(window, {})
    return {"handle": settings["handle"], "revision": settings["revision"]}


def program(operations, writes=(), scene=None):
    write_set = [binding(obj) for obj in writes] + ([scene] if scene else [])
    return validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": write_set, "operations": operations})


def op(target, **parameters):
    return {"kind": "visibility_set", "target": target if isinstance(target, str) else handle(target),
            "parameters": parameters}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def failed_with(result, code):
    return result.get("status") == "failed" and code in (result.get("error") or "")


def layer(name):
    return next(item for item in window.view_layer.layer_collection.children if item.collection.name == name)


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def objects():
    props = bpy.data.collections.new("QA Props")
    window.scene.collection.children.link(props)
    props.objects.link(bpy.data.objects.new("QA Prop", bpy.data.meshes.new("QA Prop")))
    baseline("visibility QA objects")
    cube = window.scene.objects["Cube"]
    job, staged = run(program([op(cube, hidden=True)], writes=[cube]))
    check("object_hide_ready_without_live_edit", staged.get("ready_to_commit") and not cube.hide_get())
    check("object_hide_commits", commit(job) and passed(job) and window.scene.objects["Cube"].hide_get())
    check("object_hide_derives_visibility_check", any(row.get("kind") == "visibility" for row in job.result["checks"]))
    verify = scene_validation.execute(window, "angelo_scene_verify", {
        "read_set": [binding(window.scene.objects["Cube"])],
        "checks": [{"kind": "visibility", "target": handle(window.scene.objects["Cube"]), "hidden": True}]})
    check("live_visibility_check_passes", verify["passed"])
    undo()
    check("object_hide_one_undo", not window.scene.objects["Cube"].hide_get() and no_stages())
    created = validate_call("angelo_scene_program", {"mode": "APPLY", "operations": [
        {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "alias": "ghost", "name": "QA Ghost"}},
        {"kind": "visibility_set", "target": "$ghost", "parameters": {"hidden": True}}]})
    job, _ = run(created)
    check("new_object_hidden_on_commit", commit(job) and passed(job) and window.scene.objects["QA Ghost"].hide_get())
    undo()


def collections():
    baseline("visibility QA collections")
    scene = scene_binding()
    job, staged = run(program([op(scene["handle"], collection="QA Props", exclude=True),
                               op(scene["handle"], collection="Collection", hide_render=True, hidden=True)], scene=scene))
    check("collection_ready_without_live_edit", staged.get("ready_to_commit") and not layer("QA Props").exclude
          and not bpy.data.collections["Collection"].hide_render)
    check("collection_commits", commit(job) and passed(job))
    check("collection_live", layer("QA Props").exclude and layer("Collection").hide_viewport
          and bpy.data.collections["Collection"].hide_render and not bpy.data.collections["Collection"].hide_viewport)
    undo()
    check("collection_one_undo", not layer("QA Props").exclude and not layer("Collection").hide_viewport
          and not bpy.data.collections["Collection"].hide_render and no_stages())
    stale = scene_binding()
    arguments = program([op(stale["handle"], collection="QA Props", hidden=True)], scene=stale)
    layer("QA Props").exclude = True  # A manual edit after the plan was made.
    job, result = run(arguments)
    check("manual_exclude_makes_plan_stale", result.get("status") == "failed" and not layer("QA Props").hide_viewport)
    layer("QA Props").exclude = False
    queries.invalidate()
    scene = scene_binding()
    job, result = run(program([op(scene["handle"], collection="Missing", exclude=True)], scene=scene))
    check("missing_collection_refused", failed_with(result, "COLLECTION_NOT_FOUND"))
    layer("QA Props").exclude = True
    queries.invalidate()
    prop = window.scene.objects["QA Prop"]
    job, result = run(program([op(prop, hidden=True)], writes=[prop]))
    check("excluded_object_refused_before_commit", failed_with(result, "NOT_IN_VIEW_LAYER") and no_stages())
    layer("QA Props").exclude = False
    queries.invalidate()


def move(target, collection, **extra):
    return {"kind": "collection_link", "target": target if isinstance(target, str) else handle(target),
            "parameters": {"collection": collection, "mode": "MOVE", **extra}}


def unchanged_refusal(result, word):
    return (result.get("status") == "failed" and result.get("error_code") == "VALIDATION"
            and word in (result.get("error") or "") and result.get("partial_changes_possible") is False and no_stages())


def excluded():
    """Objects that end a program only in collections excluded from the bound view layer."""
    for name, parent in (("QA Off", None), ("QA Off Parent", None), ("QA Off Child", "QA Off Parent"), ("QA Home", None),
                         ("QA Shelf", None)):
        block = bpy.data.collections.new(name)
        (bpy.data.collections[parent] if parent else window.scene.collection).children.link(block)
    for name, collection in (("QA Mover", "QA Home"), ("QA Inside", "QA Off"), ("QA Waiting", "QA Home")):
        bpy.data.collections[collection].objects.link(bpy.data.objects.new(name, bpy.data.meshes.new(name)))
    layer("QA Off").exclude = layer("QA Off Parent").exclude = True
    baseline("visibility QA excluded")
    names = lambda obj: sorted(collection.name for collection in obj.users_collection)
    mover, inside, home = window.scene.objects["QA Mover"], window.scene.objects["QA Inside"], scene_binding()
    refused = {
        "moved_into": program([move(mover, "QA Off"), op(mover, hidden=True)], writes=[mover]),
        "moved_into_nested": program([move(mover, "QA Off Child"), op(mover, hidden=True)], writes=[mover]),
        "hidden_then_moved_into": program([op(mover, hidden=True), move(mover, "QA Off")], writes=[mover]),
        "already_in": program([op(inside, hidden=True)], writes=[inside]),
        "hidden_then_collection_excluded": program([op(mover, hidden=True), op(home["handle"], collection="QA Home",
                                                                               exclude=True)], writes=[mover], scene=home),
        "created_into": validate_call("angelo_scene_program", {"mode": "APPLY", "operations": [
            {"kind": "create_primitive", "target": None,
             "parameters": {"primitive": "CUBE", "alias": "box", "name": "QA Excluded Box"}},
            move("$box", "QA Off"), {"kind": "visibility_set", "target": "$box", "parameters": {"hidden": True}}]}),
    }
    for case, arguments in refused.items():
        job, result = run(arguments)
        report.setdefault("excluded", {})[case] = {key: result.get(key) for key in ("status", "error_code", "error")}
        check("excluded_%s_refused_before_commit" % case, unchanged_refusal(result, "NOT_IN_VIEW_LAYER")
              and names(mover) == ["QA Home"] and not mover.hide_get() and "QA Excluded Box" not in bpy.data.objects)
    # Moved out of an excluded collection first, and the collection included again in the same program: both commit.
    job, result = run(program([move(inside, "QA Home"), op(inside, hidden=True)], writes=[inside]))
    check("excluded_moved_out_then_hidden_commits", commit(job) and names(inside) == ["QA Home"] and inside.hide_get())
    undo()
    inside, scene = window.scene.objects["QA Inside"], scene_binding()
    job, result = run(program([op(scene["handle"], collection="QA Off", exclude=False), op(inside, hidden=True)],
                              writes=[inside], scene=scene))
    check("excluded_included_then_hidden_commits", commit(job) and not layer("QA Off").exclude
          and window.scene.objects["QA Inside"].hide_get())
    undo()
    # Excluded by the artist after staging, before commit: stale, nothing changed, the edit can be planned again.
    # The object's own collection changes its revision (STALE_REVISION before commit); the collection the program
    # moves it into does not, so commit finds it (was: INTERNAL_ERROR).
    for case, collection, operations, code in (
            ("own_collection", "QA Home", lambda obj: [op(obj, hidden=True)], "STALE_REVISION"),
            ("destination", "QA Shelf", lambda obj: [move(obj, "QA Shelf"), op(obj, hidden=True)], "STALE_DEPENDENCY")):
        waiting = window.scene.objects["QA Waiting"]
        job, staged = run(program(operations(waiting), writes=[waiting]))
        layer(collection).exclude = True
        commit(job)
        report["excluded"]["artist_excluded_" + case] = {key: job.result.get(key) for key in ("status", "error_code", "error")}
        check("excluded_by_the_artist_while_waiting_%s_is_stale" % case, staged.get("ready_to_commit")
              and job.result.get("error_code") == code and job.result.get("partial_changes_possible") is False
              and no_stages())
        layer(collection).exclude = False
        waiting = window.scene.objects["QA Waiting"]
        check("excluded_waiting_object_unchanged_" + case, not waiting.hide_get() and names(waiting) == ["QA Home"])
        queries.invalidate()


def nested(name):
    pending = list(window.view_layer.layer_collection.children)
    while pending:
        item = pending.pop(0)
        if item.collection.name == name and item.collection.library is None:
            return item
        pending[:0] = list(item.children)
    return None


def exclusion_model():
    """Blender's rule: exclude is a collection's own flag, not inherited from its parent, and an
    RNA edit sets it on the collection and every descendant; the program's edits apply in program order."""
    parent = bpy.data.collections.new("QB Parent")
    window.scene.collection.children.link(parent)
    parent.children.link(bpy.data.collections.new("QB Child"))
    window.scene.collection.children.link(bpy.data.collections.new("QB Home"))
    for name, home in (("QB Inside", "QB Child"), ("QB Crate", "QB Home")):
        bpy.data.collections[home].objects.link(bpy.data.objects.new(name, bpy.data.meshes.new(name)))
    nested("QB Parent").exclude = True  # Excludes the child too, as the Outliner does.
    nested("QB Child").exclude = False  # The artist includes the child again: its objects have Bases.
    baseline("visibility QA exclusion model")
    flags = lambda: [nested("QB Parent").exclude, nested("QB Child").exclude]
    inside, crate = window.scene.objects["QB Inside"], window.scene.objects["QB Crate"]
    job, result = run(program([op(inside, hidden=True)], writes=[inside]))
    check("model_hide_in_an_included_child_of_an_excluded_parent_commits", commit(job)
          and window.scene.objects["QB Inside"].hide_get())
    undo()
    crate = window.scene.objects["QB Crate"]
    job, result = run(program([move(crate, "QB Child"), op(crate, hidden=True)], writes=[crate]))
    check("model_move_into_that_child_and_hide_commits", commit(job) and window.scene.objects["QB Crate"].hide_get())
    undo()
    nested("QB Parent").exclude = True  # Now the child is excluded too.
    baseline("visibility QA exclusion model, both excluded")
    scene, inside = scene_binding(), window.scene.objects["QB Inside"]
    job, result = run(program([op(scene["handle"], collection="QB Parent", exclude=False), op(inside, hidden=True)],
                              writes=[inside], scene=scene))
    check("model_include_the_parent_then_hide_in_its_child_commits", commit(job) and flags() == [False, False]
          and window.scene.objects["QB Inside"].hide_get())
    undo()
    nested("QB Parent").exclude = False
    baseline("visibility QA exclusion model, both included")
    for case, steps, expected in (("exclude_parent_include_child", [("QB Parent", True), ("QB Child", False)],
                                   [True, False]),
                                  ("include_child_exclude_parent", [("QB Child", False), ("QB Parent", True)], None)):
        scene, crate = scene_binding(), window.scene.objects["QB Crate"]
        job, result = run(program([op(scene["handle"], collection=name, exclude=value) for name, value in steps]
                                  + [op(crate, hidden=True)], writes=[crate], scene=scene))
        report.setdefault("model", {})[case] = {key: result.get(key) for key in ("status", "error_code", "error")}
        if expected is None:  # The child ends excluded although the program included it: refused before commit.
            check("model_%s_refused_before_commit" % case, result.get("status") == "failed"
                  and result.get("error_code") == "CHECK_FAILED" and flags() == [False, False] and no_stages())
        else:
            check("model_%s_commits_in_program_order" % case, commit(job) and flags() == expected)
            undo()
    # Hidden, then deleted, then its collection excluded: the deleted object's hide is moot and must not fail commit
    # (exclusion edits come before hides).
    keep = bpy.data.objects.new("QB Keep", bpy.data.meshes.new("QB Keep"))
    bpy.data.collections["QB Home"].objects.link(keep)
    baseline("visibility QA exclusion model, deleted")
    scene, crate = scene_binding(), window.scene.objects["QB Crate"]
    job, result = run(program([op(crate, hidden=True), {"kind": "object_delete", "target": handle(crate), "parameters": {}},
                               op(scene["handle"], collection="QB Home", exclude=True)], writes=[crate], scene=scene))
    check("model_hidden_then_deleted_then_excluded_commits", commit(job) and "QB Crate" not in window.scene.objects
          and nested("QB Home").exclude)
    undo()
    # A rollback restores every layer collection's own flag (an RNA edit set the descendants too).
    nested("QB Child").exclude = True
    baseline("visibility QA exclusion model, rollback")
    scene, crate = scene_binding(), window.scene.objects["QB Crate"]
    job, staged = run(program([op(scene["handle"], collection="QB Parent", exclude=True), op(crate, hidden=True)],
                              writes=[crate], scene=scene))
    from angelo_agent import live_checks
    from angelo_agent.program_jobs import ProgramError

    def late(*args, **kwargs):
        raise ProgramError("injected fault after the visibility edits", code="STALE_DEPENDENCY")

    with patch.object(live_checks, "verify_live", late):
        commit(job)
    check("model_rollback_restores_each_layer_collection", job.result.get("status") == "failed"
          and job.result.get("partial_changes_possible") is False and flags() == [False, True]
          and not window.scene.objects["QB Crate"].hide_get() and no_stages())
    nested("QB Child").exclude = False
    queries.invalidate()


def big_and_linked():
    """Past 1,024 layer collections the check still runs; a library collection never stands for a local one."""
    home = bpy.data.collections.new("QC Home")
    window.scene.collection.children.link(home)
    many = bpy.data.collections.new("QC Many")
    window.scene.collection.children.link(many)
    for index in range(1030):
        many.children.link(bpy.data.collections.new("QC %04d" % index))
    window.scene.collection.children.link(bpy.data.collections.new("QC Off"))
    home.objects.link(bpy.data.objects.new("QC Crate", bpy.data.meshes.new("QC Crate")))
    nested("QC Off").exclude = True
    library = OUT / "visibility-library.blend"
    shelf = bpy.data.collections.new("QC Props")
    shelf.objects.link(bpy.data.objects.new("QC Lib Rock", bpy.data.meshes.new("QC Lib Rock")))
    bpy.data.libraries.write(str(library), {shelf}, fake_user=True)
    bpy.data.objects.remove(bpy.data.objects["QC Lib Rock"])
    bpy.data.collections.remove(shelf)
    with bpy.data.libraries.load(str(library), link=True) as (source, destination):
        destination.collections = ["QC Props"]
    window.scene.collection.children.link(destination.collections[0])  # Included, from the library.
    window.scene.collection.children.link(bpy.data.collections.new("QC Props"))  # The local one, excluded below.
    nested("QC Props").exclude = True
    baseline("visibility QA big and linked")
    for case, target in (("past_1024_layer_collections", "QC Off"), ("local_beside_a_library_collection", "QC Props")):
        crate = window.scene.objects["QC Crate"]
        job, result = run(program([move(crate, target), op(crate, hidden=True)], writes=[crate]))
        report.setdefault("big_and_linked", {})[case] = {key: result.get(key) for key in ("status", "error_code", "error")}
        check("excluded_%s_refused_before_commit" % case, unchanged_refusal(result, "NOT_IN_VIEW_LAYER")
              and not crate.hide_get())


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    objects()
    collections()
    excluded()
    exclusion_model()
    big_and_linked()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "visibility.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_VISIBILITY", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
