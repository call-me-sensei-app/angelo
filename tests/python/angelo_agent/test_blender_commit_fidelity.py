# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A commit reproduces the staged result its checks measured, or rolls back (``commit_fidelity``).

Regression: an extrude of a new linked duplicate, measured on the stage,
was lost at commit (the duplicate shared its source's unextruded live mesh) and
the program was certified "Host-verified". Here every linked-duplicate path
commits what was staged: an edited duplicate (or one whose source the program
edits) keeps its own data, reported as isolated, and an unedited one shares the
live data. A commit that would still differ from its stage (the old linker, an
edit commit does not swap) is caught by the guard and rolled back completely.
Objects outside the program whose shape follows reshaped geometry are named in
the result and the certificate. The guard's cost is measured for 1, 32 and
2,000 changed objects. Run in a disposable instance with the exact Angelo
binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
"""

import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-commit-fidelity-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import commit_fidelity, native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent import program_commit  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion  # noqa: E402
from angelo_agent.program_completion import capture_program, mint_completion  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
EXTRUDE = {"face_selector": {"normal": [0, 0, 1]}, "translation": [0, 0, 2.0]}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def handle(obj):
    return queries.object_handle(obj)


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def dims(target, expected):
    return {"kind": "dimensions", "target": target if isinstance(target, str) else handle(target),
            "expected": expected, "tolerance": 0.001}


def fresh():
    """A new scene with a 2 m cube 'Cube' and a separate cube 'Other'; returns them."""
    import bmesh
    scene = bpy.data.scenes.new("Fidelity")
    previous, window.scene = window.scene, scene
    if previous.name.startswith("Fidelity"):  # The previous case's objects and data go with it, so names repeat.
        doomed = set(previous.objects) | {obj.data for obj in previous.objects if obj.data is not None}
        bpy.data.scenes.remove(previous)
        bpy.data.batch_remove(ids=doomed | {block for block in bpy.data.materials if block.users == 0})
    made = []
    for name, x in (("Cube", 0), ("Other", 3)):
        mesh = bpy.data.meshes.new(name)
        shape = bmesh.new()
        bmesh.ops.create_cube(shape, size=2)
        shape.to_mesh(mesh)
        shape.free()
        obj = bpy.data.objects.new(name, mesh)
        obj.location.x = x
        scene.collection.objects.link(obj)
        made.append(obj)
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="fidelity baseline")
    queries.invalidate()
    return made


def facts(*names):
    found = {}
    for name in names:
        obj = window.scene.objects.get(name)
        if obj is not None:
            found[name] = {"mesh": obj.data.name, "users": obj.data.users, "vertices": len(obj.data.vertices),
                           "faces": len(obj.data.polygons), "materials": [m.name if m else None for m in obj.data.materials]}
    return found


def certify(job, arguments):
    host = SimpleNamespace(_epoch=1, _request_generation=1, _closing=False, cancelled=False)
    delivery = SimpleNamespace(id="fidelity", epoch=1, generation=1, name="angelo_scene_program",
                               arguments=arguments, program_arguments=None)
    completion = mint_completion(host, delivery, capture_program(job), job.result)
    return certified_completion([{"tool": "angelo_scene_program", "arguments": arguments}], [job.result],
                                read_only=lambda tool: False, completions=[completion])


def commit_program(operations, writes=(), reads=(), checks=()):
    binding = lambda obj: {"handle": handle(obj), "revision": queries.object_revision(window, obj)}
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [binding(obj) for obj in reads], "write_set": [binding(obj) for obj in writes],
        "operations": operations, "expected_checks": list(checks)})
    approved = native.precondition(window, "angelo_scene_program", arguments)
    job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
    for _ in range(4096):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if staged.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = job.result or staged
    summary = certify(job, arguments) if result.get("status") == "committed" else None
    return job, result, summary


def refused(operations, code, reads=(), writes=()):
    try:
        commit_program(operations, writes=writes, reads=reads)
    except ValueError as error:
        return code in str(error)
    return False


def linked_duplicates():
    """Every linked relation the program asks for holds live; an edit of linked data is refused before approval."""
    cases = report["cases"]
    cube, _other = fresh()
    check("a_linked_duplicates_edit_is_refused_before_approval", refused(
        [op("object_duplicate", source=handle(cube), alias="d", linked=True), op("mesh_extrude", "$d", **EXTRUDE)],
        "LINKED_EDIT", reads=[cube]) and no_stages() and "Cube.001" not in window.scene.objects)
    check("an_edit_of_its_source_after_it_is_refused", refused(
        [op("object_duplicate", source=handle(cube), alias="d", linked=True), op("mesh_extrude", cube, **EXTRUDE)],
        "LINKED_EDIT", writes=[cube]))
    # An edit before the duplicate is copied with it: both share the edited live mesh.
    job, result, summary = commit_program(
        [op("mesh_extrude", cube, **EXTRUDE), op("object_duplicate", source=handle(cube), alias="d", linked=True)],
        writes=[cube], checks=[dims(cube, [2, 2, 4])])
    cases["edit_then_linked_duplicate"] = {"after": facts("Cube", "Cube.001"), "summary": summary}
    check("an_edit_before_the_duplicate_is_shared", result.get("status") == "committed"
          and window.scene.objects["Cube.001"].data == window.scene.objects["Cube"].data
          and len(window.scene.objects["Cube"].data.vertices) == 12)
    bpy.ops.ed.undo()
    check("one_undo_removes_it", "Cube.001" not in window.scene.objects and window.scene.objects["Cube"].data.users == 1
          and len(window.scene.objects["Cube"].data.vertices) == 8 and no_stages())
    # A chain and a batch of a linked duplicate share the source's live data.
    cube, _other = fresh()
    job, result, summary = commit_program([op("object_duplicate", source=handle(cube), alias="d1", linked=True),
                                           op("object_duplicate", source="$d1", alias="d2", linked=True)], reads=[cube])
    shared = {obj.data for obj in window.scene.objects if obj.name.startswith("Cube")}
    check("a_chain_of_linked_duplicates_shares_one_mesh", result.get("status") == "committed" and len(shared) == 1
          and len([obj for obj in window.scene.objects if obj.name.startswith("Cube")]) == 3)
    cube, _other = fresh()
    job, result, summary = commit_program(
        [op("object_duplicate", source=handle(cube), alias="d", linked=True),
         op("batch", sources=["$d"], linked=True, array=[{"step": [3, 0, 0], "count": 3}])], reads=[cube],
        checks=[{"kind": "copies", "target": "$d", "count": 2, "linked": True}])
    shared = {obj.data for obj in window.scene.objects if obj.name.startswith("Cube")}
    cases["batch_of_linked_dup"] = {"status": result.get("status"), "error": result.get("error"),
                                    "meshes": len(shared), "verified": result.get("verified"), "summary": summary}
    check("a_batch_of_a_linked_duplicate_shares_the_source_mesh", result.get("status") == "committed"
          and shared == {window.scene.objects["Cube"].data} and summary is not None
          and result["verified"]["live_checks"] >= 1)
    # Unedited: it shares the live data, as Blender's linked duplicate does; a linked copy of a new object shares it.
    cube, _other = fresh()
    job, result, summary = commit_program([op("object_duplicate", source=handle(cube), alias="d", linked=True)],
                                          reads=[cube])
    check("an_unedited_linked_duplicate_shares_the_live_data", result.get("status") == "committed"
          and window.scene.objects["Cube.001"].data == window.scene.objects["Cube"].data and summary is not None)
    cube, _other = fresh()
    job, result, summary = commit_program([
        op("create_primitive", primitive="CUBE", name="Made", alias="m", size=1, location=[6, 0, 0]),
        op("object_duplicate", source="$m", alias="d", linked=True)])
    check("a_linked_duplicate_of_a_new_object_shares_its_mesh", result.get("status") == "committed"
          and window.scene.objects["Made.001"].data == window.scene.objects["Made"].data)
    # The same material on every member made here without slots: each copy shows it on every face (program_rules).
    cube, other = fresh()
    job, result, summary = commit_program([
        op("create_primitive", primitive="CUBE", name="Post", alias="post", size=1, location=[6, 0, 0]),
        op("batch", sources=["$post"], linked=True, array=[{"step": [2, 0, 0], "count": 3}]),
        op("material_principled", other, name="QA Stone", inputs={"Base Color": [.6, .5, .4, 1]},
           also=["$post", "$post_2"]),
        op("material_assign", "$post_3", material="QA Stone", source=handle(other))], writes=[other])
    posts = [window.scene.objects.get(name) for name in ("Post", "Post.001", "Post.002")]  # Blender's copy names
    cases["one_material_on_linked_copies"] = {"status": result.get("status"), "error": result.get("error"),
                                              "objects": sorted(obj.name for obj in window.scene.objects),
                                              "after": facts(*[obj.name for obj in window.scene.objects]),
                                              "faces": {post.name: sorted({p.material_index for p in post.data.polygons})
                                                        for post in posts if post is not None}}
    check("one_material_on_every_linked_copy_commits", result.get("status") == "committed" and all(posts)
          and len({post.data for post in posts}) == 1 and all(
              [slot.material.name if slot.material else None for slot in post.material_slots] == ["QA Stone"]
              and {polygon.material_index for polygon in post.data.polygons} == {0} for post in posts))
    # Adversarial case: the copy is painted, its root only gets an also slot: refused before approval.
    cube, other = fresh()
    check("a_copy_painted_while_its_root_gets_only_a_slot_is_refused", refused(
        [op("object_duplicate", source=handle(cube), alias="copy", linked=True),
         op("material_principled", "$copy", name="QA Paint", inputs={"Base Color": [.8, .1, .1, 1]},
            also=[handle(cube)])], "LINKED_EDIT", writes=[cube]) and no_stages())


def guard_rolls_back():
    """A commit that would differ from its stage fails and changes nothing (three injected faults)."""
    cube, _other = fresh()

    def unlinked(backend, stage, new_objects, memberships, scope):  # The old linker: its commit differs from the stage.
        stage.extras["linked_duplicates"] = {key: value for key, value in stage.extras.get("linked_duplicates", {}).items()
                                             if not value.startswith("$")}
        original(backend, stage, new_objects, memberships, scope)

    original = program_commit.link_new_objects
    with patch.object(program_commit, "link_new_objects", unlinked):
        job, result, summary = commit_program(
            [op("object_duplicate", source=handle(cube), alias="d", linked=True),
             op("batch", sources=["$d"], linked=True, array=[{"step": [3, 0, 0], "count": 3}])], reads=[cube])
    report["cases"]["unlinked_copies"] = {key: result.get(key) for key in ("status", "error", "rollback_verified")}
    check("unlinked_copies_are_caught_and_rolled_back", result.get("status") == "failed"
          and "COMMIT_FIDELITY" in result.get("error", "") and result.get("partial_changes_possible") is False
          and result.get("rollback_verified") is True and summary is None
          and not [obj for obj in window.scene.objects if obj.name.startswith("Cube.")] and no_stages())
    # The same fault past the structural guard: the live copies check catches it.
    cube, _other = fresh()
    with patch.object(program_commit, "link_new_objects", unlinked), \
            patch.object(commit_fidelity, "verify_links", lambda stage: 0):
        job, result, summary = commit_program(
            [op("object_duplicate", source=handle(cube), alias="d", linked=True),
             op("batch", sources=["$d"], linked=True, array=[{"step": [3, 0, 0], "count": 3}])], reads=[cube])
    report["cases"]["live_copies_check"] = {key: result.get(key) for key in ("status", "error", "rollback_verified")}
    check("the_live_checks_catch_what_the_guard_did_not", result.get("status") == "failed"
          and "passed on the staged copy but not on the committed scene" in result.get("error", "")
          and result.get("rollback_verified") is True and not [obj for obj in window.scene.objects
                                                              if obj.name.startswith("Cube.")])
    cube, _other = fresh()
    with patch.object(program_commit, "DATA_SWAP_RESOURCES", frozenset()):
        job, result, summary = commit_program([op("mesh_extrude", cube, **EXTRUDE)], writes=[cube],
                                              checks=[dims(cube, [2, 2, 4])])
    report["cases"]["unswapped_edit"] = {key: result.get(key) for key in ("status", "error", "rollback_verified")}
    check("an_edit_commit_would_not_swap_is_caught", result.get("status") == "failed"
          and "COMMIT_FIDELITY: the committed geometry of 'Cube'" in result.get("error", "")
          and result.get("rollback_verified") is True and len(window.scene.objects["Cube"].data.vertices) == 8)


def no_false_rollbacks():
    """Curves, lattices and text moved while hidden, disabled, excluded or after an Undo commit."""
    def make(kind, name):
        if kind == "lattice":
            data = bpy.data.lattices.new(name)
        elif kind == "text":
            data = bpy.data.curves.new(name, "FONT")
            data.body = "Hi"
        else:
            data = bpy.data.curves.new(name, "CURVE")
            data.dimensions, data.bevel_depth = "3D", 0.1 if kind == "bevel" else 0
            spline = data.splines.new("BEZIER" if kind == "bezier" else "POLY")
            points = spline.bezier_points if kind == "bezier" else spline.points
            points.add(2)
            for index, point in enumerate(points):
                point.co = (index, 0, 0.5 * index) if kind == "bezier" else (index, 0, 0.5 * index, 1)
        obj = bpy.data.objects.new(name, data)
        window.scene.collection.objects.link(obj)
        return obj

    def exclude(obj):
        collection = bpy.data.collections.new("Excluded")
        window.scene.collection.children.link(collection)
        window.scene.collection.objects.unlink(obj)
        collection.objects.link(obj)
        window.view_layer.layer_collection.children[collection.name].exclude = True

    outcomes = {}
    for kind in ("poly", "bezier", "bevel", "lattice", "text"):
        for state in ("visible", "disabled", "hidden", "excluded", "after_undo"):
            fresh()
            obj = make(kind, "T")
            if state == "disabled":
                obj.hide_viewport = True
            elif state == "hidden":
                obj.hide_set(True)
            elif state == "excluded":
                exclude(obj)
            bpy.context.evaluated_depsgraph_get()
            bpy.context.view_layer.update()
            bpy.ops.ed.undo_push(message="fidelity curve baseline")
            if state == "after_undo":
                obj.location.x += 1
                bpy.ops.ed.undo_push(message="plain move")
                bpy.ops.ed.undo()
                obj = window.scene.objects["T"]
                bpy.context.evaluated_depsgraph_get()
            queries.invalidate()
            job, result, summary = commit_program([op("transform", obj, location=[2, 0, 0])], writes=[obj])
            outcomes[kind + "_" + state] = result.get("status") == "committed" and abs(obj.location.x - 2) < 1e-6
            if not outcomes[kind + "_" + state]:
                report["cases"].setdefault("false_rollbacks", {})[kind + "_" + state] = result.get("error")
    report["cases"]["moves"] = outcomes
    check("no_false_rollbacks_for_curves_lattices_and_text", all(outcomes.values()))


def dependents():
    """Objects outside the program that use a changed object, through any reference, are named."""
    cube, other = fresh()
    lattice = bpy.data.objects.new("Lat", bpy.data.lattices.new("Fidelity Lattice"))
    window.scene.collection.objects.link(lattice)
    other.modifiers.new("Lattice", "LATTICE").object = lattice
    info = bpy.data.objects.new("Info User", bpy.data.meshes.new("Info Mesh"))
    window.scene.collection.objects.link(info)
    group = bpy.data.node_groups.new("Fidelity Info", "GeometryNodeTree")
    node = group.nodes.new("GeometryNodeObjectInfo")
    node.inputs["Object"].default_value = lattice
    info.modifiers.new("GN", "NODES").node_group = group
    collection = bpy.data.collections.new("Fidelity Instanced")
    collection.objects.link(lattice)
    instancer = bpy.data.objects.new("Instancer", None)
    instancer.instance_type, instancer.instance_collection = "COLLECTION", collection
    window.scene.collection.objects.link(instancer)
    bpy.context.view_layer.update()
    queries.invalidate()
    job, result, summary = commit_program([op("transform", lattice, location=[0, 0, 1])], writes=[lattice])
    names = sorted(row.get("name") for row in result.get("unchecked_dependents", []))
    report["cases"]["dependents"] = {"dependents": result.get("unchecked_dependents"), "summary": summary}
    check("every_user_of_a_moved_object_is_named", result.get("status") == "committed"
          and names == ["Info User", "Instancer", "Other"] and summary is not None
          and "- Not checked: the evaluated shape or placement of 'Info User' (uses 'Lat')" in summary)
    # A large scene: the reference walk stays within a few milliseconds.
    for index in range(2000):
        holder = bpy.data.objects.new("Many %d" % index, cube.data)
        window.scene.collection.objects.link(holder)
    bpy.context.view_layer.update()
    queries.invalidate()
    spent = []
    original = commit_fidelity.dependents

    def timed_walk(*args):
        started = time.perf_counter()
        try:
            return original(*args)
        finally:
            spent.append((time.perf_counter() - started) * 1000)

    with patch.object(commit_fidelity, "dependents", timed_walk):
        commit_program([op("transform", lattice, location=[0, 0, 2])], writes=[lattice])
    report["dependents_ms_2000_objects"] = round(spent[0], 3)
    from qa_waits import timeout_scale
    check("the_reference_walk_is_cheap", spent[0] < 50 * timeout_scale())  # macOS: about 1 ms.


def timed(objects, operations, writes=(), reads=()):
    """Capture and verify time of one commit, in milliseconds."""
    spent, named = [], []
    original_capture, original_verify = commit_fidelity.capture, commit_fidelity.verify
    original_dependents = commit_fidelity.dependents

    def dependents(*args):
        started = time.perf_counter()
        try:
            return original_dependents(*args)
        finally:
            named.append(time.perf_counter() - started)

    def capture(*args):
        started = time.perf_counter()
        try:
            return original_capture(*args)
        finally:
            spent.append(time.perf_counter() - started)

    def verify(*args):
        started = time.perf_counter()
        try:
            return original_verify(*args)
        finally:
            spent.append(time.perf_counter() - started)

    from angelo_agent import live_checks
    original_live, live = live_checks.verify_live, []

    def verify_live(*args):
        started = time.perf_counter()
        try:
            return original_live(*args)
        finally:
            live.append(time.perf_counter() - started)

    with patch.object(commit_fidelity, "capture", capture), patch.object(commit_fidelity, "verify", verify), \
            patch.object(commit_fidelity, "dependents", dependents), patch.object(live_checks, "verify_live", verify_live):
        job, result, _summary = commit_program(operations, writes, reads)
    report["last_timed"] = {key: result.get(key) for key in ("status", "error", "verified")}
    check("timed_%d_committed" % objects, result.get("status") == "committed"
          and result["verified"]["matches_stage"] == objects)
    return sum(spent) * 1000, sum(named) * 1000, result["native_exec_ms"], sum(live) * 1000, len(job.checks)


def direct_rows(count):
    """The guard alone on ``count`` written objects whose data commit did not swap (its most expensive path)."""
    scene, previous = bpy.data.scenes.new("Fidelity Direct"), window.scene
    window.scene = scene
    try:
        live = []
        for index in range(count):
            obj = bpy.data.objects.new("Direct %d" % index, bpy.data.meshes["Cube"].copy())
            scene.collection.objects.link(obj)
            live.append(obj)
        staged = []
        for obj in live:
            copy = obj.copy()
            copy.data = obj.data.copy()
            scene.collection.objects.link(copy)
            staged.append(copy)
        bpy.context.view_layer.update()
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            rows = [(obj.name, obj, copy.data, copy, commit_fidelity._matrix(copy)) for obj, copy in zip(live, staged)]
            matched = commit_fidelity.verify((rows, {}))
            samples.append((time.perf_counter() - started) * 1000)
        check("direct_%d_matches" % count, matched == count)
        # Load-independent: per object it reads two counts and one matrix, never a coordinate.
        calls = {"_counts": 0, "_matrix": 0}

        def counting(name):
            original = getattr(commit_fidelity, name)

            def wrapper(*args):
                calls[name] += 1
                return original(*args)
            return wrapper

        rows = [(obj.name, obj, copy.data, copy, commit_fidelity._matrix(copy)) for obj, copy in zip(live, staged)]
        with patch.object(commit_fidelity, "_counts", counting("_counts")), \
                patch.object(commit_fidelity, "_matrix", counting("_matrix")):
            commit_fidelity.verify((rows, {}))
        check("direct_%d_work_per_object" % count, calls == {"_counts": 2 * count, "_matrix": count})
        return {"median_ms": round(statistics.median(samples), 3), "path": "counts and matrix of each object",
                "calls": calls}
    finally:
        window.scene = previous
        removed = set(scene.objects) | {obj.data for obj in scene.objects}
        bpy.data.scenes.remove(scene)
        bpy.data.batch_remove(ids=removed)


def latency():
    rows = {}
    # Copies with their own data stay within the stage's 2,048 owned IDs (an object and a mesh each); linked copies
    # reach a program's 1,023-copy maximum.
    grids = {"959": [{"step": [0, 3, 0], "count": 30}, {"step": [3, 0, 0], "count": 32}],
             "1023_linked": [{"step": [0, 3, 0], "count": 32}, {"step": [3, 0, 0], "count": 32}]}
    for label, count in (("1", 1), ("32", 32), ("959", 959), ("1023_linked", 1023)):
        samples = []
        for _ in range(3):
            cube, _other = fresh()
            if label in ("1", "32"):  # Moved written objects: no data swap, so counts, bounds and the matrix compare.
                moved = [cube] + [bpy.data.objects.new("Moved %d" % index, cube.data.copy()) for index in range(count - 1)]
                for obj in moved[1:]:
                    window.scene.collection.objects.link(obj)
                bpy.context.view_layer.update()
                queries.invalidate()
                samples.append(timed(count, [op("transform", obj, location=[index, 5, 0])
                                             for index, obj in enumerate(moved)], writes=moved))
            else:  # New objects keep their staged data (a pointer each); linked copies share one compared pair.
                samples.append(timed(count, [op("batch", sources=[handle(cube)], linked=label.endswith("linked"),
                                                array=grids[label])], reads=[cube]))
        rows[label] = {"median_ms": round(statistics.median(row[0] for row in samples), 3),
                       "dependents_median_ms": round(statistics.median(row[1] for row in samples), 3),
                       "live_checks_median_ms": round(statistics.median(row[3] for row in samples), 3),
                       "checks": samples[0][4], "commit_median_ms": round(statistics.median(row[2] for row in samples), 3)}
    fresh()
    rows["2000_direct"] = direct_rows(2000)
    report["guard_latency"] = rows
    # Wall-clock bounds are coarse (macOS medians: about 80 to 130 ms, 1 ms and 4 ms, so 3x to 10x headroom) and
    # scale with the runner's timeout scale; the per-object work above is the load-independent bound.
    from qa_waits import timeout_scale
    scale = timeout_scale()
    report["guard_bounds"] = {"scale": scale, "2000_direct_ms": 400 * scale, "32_ms": 40 * scale, "959_ms": 100 * scale}
    check("the_guard_is_cheap", rows["2000_direct"]["median_ms"] < 400 * scale and rows["32"]["median_ms"] < 40 * scale
          and rows["959"]["median_ms"] < 100 * scale)


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes))  # Factory names would be taken.
    bpy.utils.register_class(commit_operator(report))
    native.register()
    linked_duplicates()
    guard_rolls_back()
    no_false_rollbacks()
    dependents()
    latency()
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "commit-fidelity.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_COMMIT_FIDELITY", json.dumps({key: report.get(key) for key in ("success", "guard_latency")}), flush=True)
assert report.get("success"), report.get("error")
