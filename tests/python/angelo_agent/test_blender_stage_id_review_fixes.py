# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression: edge cases of staged names, the World a result names and new objects' collections.

* R1 long names: a check or identity guard carried a staged copy's private name, 24 characters longer than the
  name it stands for, into a 64-character name field, so every program reusing, editing or checking a material
  named with more than 40 characters (36 for an isolated copy) failed as UNKNOWN with recovery_required (the
  failure of test_blender_stage_id_names), and an object_name check failed for an object named with more than 41.
* R2 the World a result names: an edited copy of a World with a fake user, another scene, or a user it gains or
  loses before commit was reported under the name it did not commit as.
* R3 an isolated copy (node_edit ISOLATE) and the copy a push-down of a shared Action plays were reported under the
  original's name while they commit as "<name>.001".
* R4 a new object made after its source changed collection (duplicate, linked duplicate, batch copy, conversion,
  separated part, a copy of a moved new object or of a moved copy) was linked where its source was before the
  program: a membership check failed live (CHECK_FAILED), a copy with its own collection edit failed as
  INTERNAL_ERROR, and without a check the copy silently landed in the wrong collection. A new
  object commits into exactly the collections it holds on the stage, in program order (program_memberships).
* R6 a library collection named like a local one: creating the local collection was
  refused as NAME_TAKEN, and moving into the local one linked into the library one (UNKNOWN, recovery_required).

Disposable headless instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call. Writes stage-id-review-fixes.json in ANGELO_QA_DIR.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-stage-id-review-fixes-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "results": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path}
window = bpy.context.window_manager.windows[0]
LONG = "Castle keep outer wall weathered stone A big"  # 44 characters; a private copy's name would be 68.


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def objects():
    return window.scene.objects


def handle(name):
    return queries.object_handle(objects()[name])


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def run(label, operations, writes=(), reads=(), checks=(), scene=False, before_commit=None):
    """One program through staging and the QA commit operator; the job's final result."""
    entry = lambda name: {"handle": handle(name), "revision": queries.object_revision(window, objects()[name])}
    write_set = [entry(name) for name in writes]
    if scene:
        settings = scene_settings.execute(window, {})
        write_set.append({"handle": settings["handle"], "revision": settings["revision"]})
    arguments = validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [entry(name) for name in reads],
                                                       "write_set": write_set, "operations": operations,
                                                       "expected_checks": list(checks)})
    job = native.begin(window, "angelo_scene_program", arguments)
    for _ in range(400):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if staged.get("ready_to_commit"):
        if before_commit is not None:
            before_commit()
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = dict(job.result or staged)
    report["results"][label] = {key: value for key, value in result.items() if key not in ("delta", "live_revisions")}
    queries.invalidate()
    return result


def committed(result):
    return result.get("status") == "committed" and not result.get("error")


def mesh_object(name, faces=1, collection=None):
    """A quad mesh object (``faces`` loose quads) in the scene's master collection, or only in ``collection``."""
    mesh = bpy.data.meshes.new(name + " Mesh")
    mesh.from_pydata([(x + 3 * face, y, 0) for face in range(faces) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))], [],
                     [tuple(range(4 * face, 4 * face + 4)) for face in range(faces)])
    obj = bpy.data.objects.new(name, mesh)
    (bpy.data.collections[collection] if collection else window.scene.collection).objects.link(obj)
    return obj


def material(name):
    block = bpy.data.materials.new(name)
    block.use_nodes = True
    return block


def r1_long_names():
    keep, gate = mesh_object("R1 Keep"), mesh_object("R1 Gate")
    stone = material(LONG)
    keep.data.materials.append(stone)
    mesh_object("R1 Tower").data.materials.append(stone)
    long_object = mesh_object("R1 " + LONG)
    baseline("long names")
    reuse = run("r1_reuse", [op("material_assign", handle("R1 Gate"), material=LONG, source=handle("R1 Keep"))],
                writes=["R1 Gate"], reads=["R1 Keep"])
    check("r1_a_long_named_material_is_reused", committed(reuse) and gate.material_slots[0].material == stone)
    edit = run("r1_all_users", [op("node_edit", handle("R1 Keep"), material=LONG, shared="ALL_USERS",
                                   edits=[{"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": 0.35}}])],
               writes=["R1 Keep"], reads=["R1 Tower"])
    check("r1_a_long_named_material_is_edited_for_all_users", committed(edit))
    checked = run("r1_check", [op("transform", handle("R1 Keep"), location=[0, 0, 1])], writes=["R1 Keep"],
                  checks=[{"kind": "material_assignment", "target": handle("R1 Keep"), "material": LONG,
                           "all_faces": True}])
    check("r1_a_long_named_material_check_passes", committed(checked))
    named = run("r1_object_name", [op("transform", handle(long_object.name), location=[0, 0, 1])],
                writes=[long_object.name],
                checks=[{"kind": "object_name", "target": handle(long_object.name), "expected": long_object.name}])
    check("r1_a_long_named_object_check_passes", committed(named))
    isolated = run("r1_isolate", [op("node_edit", handle("R1 Keep"), material=LONG, shared="ISOLATE",
                                     edits=[{"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": 0.2}}])],
                   writes=["R1 Keep"], reads=["R1 Tower"])
    kept = objects()["R1 Keep"].material_slots[0].material
    check("r1_a_long_named_material_is_isolated_under_its_whole_name", committed(isolated)
          and kept != stone and kept.name.startswith(LONG) and "__angelo_" not in kept.name)
    tree = next(row["tree"]["name"] for row in isolated["operation_results"] if row["kind"] == "node_edit")
    report["r1_isolated"] = [kept.name, tree]
    check("r1_the_isolated_copy_is_reported_as_it_committed", tree == kept.name)


def r2_world_names():
    scene = window.scene

    def configure(label, before_commit=None):
        result = run(label, [op("world_configure", scene_settings.execute(window, {})["handle"], strength=0.5)],
                     scene=True, before_commit=before_commit)
        reported = next(row["world"]["name"] for row in result["operation_results"] if row["kind"] == "world_configure")
        report.setdefault("r2_worlds", {})[label] = [reported, scene.world.name]
        return committed(result) and reported == scene.world.name

    scene.world = bpy.data.worlds.new("R2 Faked")
    scene.world.use_fake_user = True
    baseline("fake user")
    check("r2_a_world_with_a_fake_user_is_named_as_committed", configure("r2_fake_user"))
    scene.world = bpy.data.worlds.new("R2 Shared")
    bpy.data.scenes.new("R2 Other").world = scene.world
    baseline("second scene")
    check("r2_a_world_two_scenes_use_is_named_as_committed", configure("r2_second_scene"))
    scene.world = bpy.data.worlds.new("R2 Gains")
    baseline("gains")
    check("r2_a_world_that_gains_a_user_before_commit_is_named_as_committed",
          configure("r2_gains", lambda: setattr(bpy.data.scenes.new("R2 Late"), "world", bpy.data.worlds["R2 Gains"])))
    scene.world = bpy.data.worlds.new("R2 Loses")
    bpy.data.scenes.new("R2 Early").world = scene.world
    baseline("loses")
    check("r2_a_world_that_loses_a_user_before_commit_is_named_as_committed",
          configure("r2_loses", lambda: setattr(bpy.data.scenes["R2 Early"], "world", None)))


def r3_copies_named_as_committed():
    first = mesh_object("R3 First")
    for frame, value in ((1, 0.0), (20, 3.0)):
        first.location.x = value
        first.keyframe_insert("location", frame=frame)
    second = mesh_object("R3 Second")
    second.animation_data_create().action = first.animation_data.action
    second.animation_data.action_slot = first.animation_data.action_slot
    baseline("shared action")
    pushed = run("r3_push_down", [op("nla_push_down", handle("R3 First"), track=None)], writes=["R3 First"])
    tracks = [(track.name, [(strip.name, strip.action.name) for strip in track.strips])
              for track in objects()["R3 First"].animation_data.nla_tracks]
    report["r3_tracks"] = tracks
    check("r3_a_shared_action_push_down_names_track_and_strip_as_committed", committed(pushed) and tracks
          and all(track == strip == action for track, strips in tracks for strip, action in strips))


def where(name):
    obj = objects().get(name)
    return sorted(collection.name for collection in obj.users_collection) if obj else None


def r4_collections(prefix, *names):
    for name in names:
        window.scene.collection.children.link(bpy.data.collections.new(prefix + " " + name))
    return [prefix + " " + name for name in names]


def r4_a_duplicate_lands_where_it_staged():
    props, home = r4_collections("R4", "Props", "Home")
    mesh_object("R4 Crate", collection=home)
    baseline("collections")
    result = run("r4", [op("collection_link", handle("R4 Crate"), collection=props, mode="MOVE"),
                        op("object_duplicate", source=handle("R4 Crate"), alias="dup", name="R4 Crate Copy")],
                 writes=["R4 Crate"],
                 checks=[{"kind": "collection_membership", "target": "$dup", "collections": [props]}])
    report["r4_error"] = result.get("error")
    report["r4_copy"] = where("R4 Crate Copy")
    check("r4_a_duplicate_after_a_move_lands_with_its_source", committed(result) and report["r4_copy"] == [props])
    check("r4_a_result_names_no_private_collection", "__angelo_" not in json.dumps(result, default=str))


def r4_text(name, collection):
    curve = bpy.data.curves.new(name + " Text", "FONT")
    curve.body = "A"
    bpy.data.collections[collection].objects.link(bpy.data.objects.new(name, curve))


def r4_case(tag, build):
    """``build(prefix)`` makes the scene and returns (operations, writes, reads, checks, {object: collections})."""
    prefix = "R4" + tag
    operations, writes, reads, checks, expected = build(prefix)
    baseline(prefix)
    result = run("r4_" + tag, operations(), writes=writes, reads=reads, checks=checks)
    live = {name: where(name) for name in expected}
    report.setdefault("r4_cases", {})[tag] = {"live": live, "error": result.get("error")}
    check("r4_%s_commits_where_it_staged" % tag, committed(result) and live == expected)


def r4_new_objects_land_where_they_staged():
    """Every new-object path after (and before) a staged collection edit of its source, in program order."""
    move = lambda target, collection, **extra: op("collection_link", target, collection=collection, mode="MOVE", **extra)
    duplicate = lambda source, alias, name, **extra: op("object_duplicate", source=source, alias=alias, name=name, **extra)

    def unchecked(p):
        props, home = r4_collections(p, "Props", "Home")
        mesh_object(p + " Crate", collection=home)
        return (lambda: [move(handle(p + " Crate"), props), duplicate(handle(p + " Crate"), "dup", p + " Copy")],
                [p + " Crate"], [], [], {p + " Copy": [props]})

    def linked_duplicate(p):
        props, home = r4_collections(p, "Props", "Home")
        mesh_object(p + " Crate", collection=home)
        return (lambda: [op("collection_link", handle(p + " Crate"), collection=props, mode="LINK"),
                         duplicate(handle(p + " Crate"), "dup", p + " Copy", linked=True)],
                [p + " Crate"], [], [], {p + " Copy": sorted([home, props])})

    def batch(p):
        props, home = r4_collections(p, "Props", "Home")
        mesh_object(p + " Post", collection=home)
        return (lambda: [move(handle(p + " Post"), props),
                         op("batch", sources=[handle(p + " Post")], array=[{"step": [3, 0, 0], "count": 2}])],
                [p + " Post"], [], [], {p + " Post.001": [props]})

    def convert(p):
        props, home = r4_collections(p, "Props", "Home")
        r4_text(p + " Text", home)
        return (lambda: [move(handle(p + " Text"), props),
                         op("object_convert", None, source=handle(p + " Text"), alias="solid", name=p + " Solid",
                            keep_original=True)],
                [p + " Text"], [], [], {p + " Solid": [props]})

    def separate(p, after_move=True):
        props, home = r4_collections(p, "Props", "Home")
        mesh_object(p + " Parts", faces=2, collection=home)
        steps = lambda: [move(handle(p + " Parts"), props), op("mesh_separate", handle(p + " Parts"), mode="LOOSE")]
        return (lambda: steps() if after_move else steps()[::-1], [p + " Parts"], [], [],
                {p + " Parts": [props], p + " Parts.001": [props if after_move else home]})

    def created_collection(p):
        (home,) = r4_collections(p, "Home")
        mesh_object(p + " Crate", collection=home)
        return (lambda: [move(handle(p + " Crate"), p + " New", create=True), duplicate(handle(p + " Crate"), "dup", p + " Copy")],
                [p + " Crate"], [], [{"kind": "collection_membership", "target": "$dup", "collections": [p + " New"]}],
                {p + " Crate": [p + " New"], p + " Copy": [p + " New"]})

    def own_edit(p):  # Was INTERNAL_ERROR: the copy's own edit replayed from its source's pre-program collections.
        props, home, extra = r4_collections(p, "Props", "Home", "Extra")
        mesh_object(p + " Crate", collection=home)
        return (lambda: [move(handle(p + " Crate"), props), duplicate(handle(p + " Crate"), "dup", p + " Copy"),
                         op("collection_link", "$dup", collection=extra, mode="LINK")],
                [p + " Crate"], [], [], {p + " Copy": sorted([props, extra])})

    def new_sources(p):  # A copy of a moved new object, a copy of a moved copy, and a part of that copy.
        props, home = r4_collections(p, "Props", "Home")
        mesh_object(p + " Crate", faces=2, collection=home)
        return (lambda: [op("create_primitive", None, primitive="CUBE", name=p + " Box", alias="box"),
                         move("$box", props), duplicate("$box", "boxcopy", p + " Box Copy"),
                         duplicate(handle(p + " Crate"), "a", p + " A"), move("$a", props),
                         duplicate("$a", "b", p + " B"), op("mesh_separate", "$b", mode="LOOSE")],
                [], [p + " Crate"], [],
                {p + " Box": [props], p + " Box Copy": [props], p + " A": [props], p + " B": [props],
                 p + " B.001": [props], p + " Crate": [home]})

    def program_order(p):  # A copy made before its source moves stays where the source was.
        props, home = r4_collections(p, "Props", "Home")
        mesh_object(p + " Crate", collection=home)
        return (lambda: [duplicate(handle(p + " Crate"), "dup", p + " Copy"), move(handle(p + " Crate"), props)],
                [p + " Crate"], [], [], {p + " Copy": [home], p + " Crate": [props]})

    def unlinked_master(p):
        (props,) = r4_collections(p, "Props")
        bpy.data.collections[props].objects.link(mesh_object(p + " Crate"))
        return (lambda: [op("collection_link", handle(p + " Crate"), collection="Scene Collection", mode="UNLINK"),
                         duplicate(handle(p + " Crate"), "dup", p + " Copy")],
                [p + " Crate"], [], [], {p + " Copy": [props], p + " Crate": [props]})

    for tag, build in (("unchecked", unchecked), ("linked", linked_duplicate), ("batch", batch), ("convert", convert),
                       ("separate", separate), ("separate_first", lambda p: separate(p, after_move=False)),
                       ("created", created_collection), ("own_edit", own_edit), ("new_sources", new_sources),
                       ("order", program_order), ("master", unlinked_master)):
        r4_case(tag, build)
    # A collection a new object joins, renamed before commit: a stale dependency, and nothing changes.
    spare, home = r4_collections("R4stale", "Spare", "Home")
    mesh_object("R4stale Crate", collection=home)
    baseline("R4stale")
    stale = run("r4_stale", [duplicate(handle("R4stale Crate"), "dup", "R4stale Copy"), move("$dup", spare)],
                reads=["R4stale Crate"], before_commit=lambda: setattr(bpy.data.collections[spare], "name", "R4stale Moved"))
    report["r4_stale"] = {"error_code": stale.get("error_code"), "error": stale.get("error")}
    check("r4_a_collection_gone_before_commit_is_a_stale_dependency", stale.get("error_code") == "STALE_DEPENDENCY"
          and stale.get("partial_changes_possible") is False and "R4stale Copy" not in objects())
    # A collection the program creates, made by the artist in the scene before commit: never adopted (was: the
    # program's objects joined the artist's collection). The refusal comes after the kept copies took their public
    # names, so it also pins a rollback after publish, which this R4 defect triggered.
    (home,) = r4_collections("R4taken", "Home")
    mesh_object("R4taken Crate", collection=home).data.materials.append(material("R4taken Wood"))
    baseline("R4taken")
    names = lambda: sorted(block.name for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.objects)
                           for block in collection if "R4taken" in block.name or "__angelo_" in block.name)
    before = names()
    taken = run("r4_taken", [op("transform", handle("R4taken Crate"), location=[0, 0, 3]),
                             move(handle("R4taken Crate"), "R4taken New", create=True),
                             duplicate(handle("R4taken Crate"), "dup", "R4taken Copy")], writes=["R4taken Crate"],
                before_commit=lambda: window.scene.collection.children.link(bpy.data.collections.new("R4taken New")))
    report["r4_taken"] = {"error_code": taken.get("error_code"), "error": taken.get("error"), "names": [before, names()]}
    check("r4_a_created_collection_taken_before_commit_is_a_stale_dependency",
          taken.get("error_code") == "STALE_DEPENDENCY" and taken.get("partial_changes_possible") is False
          and not len(bpy.data.collections["R4taken New"].objects) and where("R4taken Crate") == [home]
          and "R4taken Copy" not in objects() and objects()["R4taken Crate"].location.z == 0)
    check("r4_a_rollback_after_publish_leaves_every_name_as_it_was", names() == before
          and "__angelo_" not in json.dumps(taken, default=str))


def r6_library_collections():
    """A library collection never stands for, nor blocks, a local collection of its name."""
    library = OUT / "r6-library.blend"
    for name in ("R6 New", "R6 Props", "R6 Shelf"):
        block = bpy.data.collections.new(name)
        block.objects.link(bpy.data.objects.new(name + " Rock", bpy.data.meshes.new(name + " Rock")))
    bpy.data.libraries.write(str(library), {bpy.data.collections[name] for name in ("R6 New", "R6 Props", "R6 Shelf")},
                             fake_user=True)
    for name in ("R6 New", "R6 Props", "R6 Shelf"):
        bpy.data.objects.remove(bpy.data.objects[name + " Rock"])
        bpy.data.collections.remove(bpy.data.collections[name])
    with bpy.data.libraries.load(str(library), link=True) as (source, destination):
        destination.collections = ["R6 New", "R6 Props", "R6 Shelf"]
    linked = {block.name: block for block in destination.collections}
    window.scene.collection.children.link(linked["R6 Props"])  # In the scene, beside a local "R6 Props".
    window.scene.collection.children.link(linked["R6 Shelf"])  # In the scene, with no local namesake.
    (home,) = r4_collections("R6", "Home")
    r4_collections("R6", "Props")
    mesh_object("R6 Crate", collection=home)
    baseline("R6")
    created = run("r6_create", [op("collection_link", handle("R6 Crate"), collection="R6 New", mode="MOVE", create=True)],
                  writes=["R6 Crate"])
    local = lambda name: sorted([block.name, block.library is not None] for block in objects()[name].users_collection)
    check("r6_a_library_namesake_does_not_block_creating_a_local_collection", committed(created)
          and local("R6 Crate") == [["R6 New", False]])
    undo_to("R6")
    moved = run("r6_move", [op("collection_link", handle("R6 Crate"), collection="R6 Props", mode="MOVE"),
                            duplicate_op("R6 Crate", "dup", "R6 Copy")], writes=["R6 Crate"],
                checks=[{"kind": "collection_membership", "target": "$dup", "collections": ["R6 Props"]}])
    check("r6_a_move_and_a_copy_land_in_the_local_collection_not_its_library_namesake", committed(moved)
          and local("R6 Crate") == local("R6 Copy") == [["R6 Props", False]])
    undo_to("R6")
    shelf = run("r6_shelf", [op("collection_link", handle("R6 Crate"), collection="R6 Shelf", mode="MOVE")],
                writes=["R6 Crate"])
    check("r6_a_library_only_collection_is_a_correctable_refusal", shelf.get("error_code") == "VALIDATION"
          and "LINKED" in (shelf.get("error") or "") and local("R6 Crate") == [["R6 Home", False]])


def duplicate_op(source, alias, name):
    return op("object_duplicate", source=handle(source), alias=alias, name=name)


def undo_to(label):
    bpy.ops.ed.undo()
    queries.invalidate()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    baseline("review fixes baseline")
    r1_long_names()
    r2_world_names()
    r3_copies_named_as_committed()
    r4_a_duplicate_lands_where_it_staged()
    r4_new_objects_land_where_they_staged()
    r6_library_collections()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "stage-id-review-fixes.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_STAGE_ID_REVIEW_FIXES", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}),
      flush=True)
