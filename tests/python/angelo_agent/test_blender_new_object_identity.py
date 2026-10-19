# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression: a new object keeps its identity while its stage is suspended.

A suspended stage holds none of its IDs in Main, so the name a staged new object had is free while the approval
waits. When the artist takes it (a Shift+D of a sibling, or a new object), the staged object comes back under
another name. Separated parts were looked up by name, so a part then committed into the Scene Collection rather
than its target's collections, results named the artist's object, and a retained preview lost the object
("lost an exact staged target", INTERNAL_ERROR with recovery_required).

Now every new object keeps its staged collections, results name what commit produced, a requested name the artist
took is a correctable refusal, and every new object's live collections are checked after linking.

Disposable headless instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call. Writes new-object-identity.json in ANGELO_QA_DIR.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-new-object-identity-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_memberships, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def objects():
    return window.scene.objects


def handle(name):
    return queries.object_handle(objects()[name])


def entry(name):
    return {"handle": handle(name), "revision": queries.object_revision(window, objects()[name])}


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("the native program did not settle")


def stage(operations, writes=(), reads=(), mode="APPLY"):
    arguments = validate_call("angelo_scene_program", {
        "mode": mode, "read_set": [entry(name) for name in reads], "write_set": [entry(name) for name in writes],
        "operations": operations, "expected_checks": []})
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def commit(job, label, before_commit=None):
    """Commit a staged job through the QA operator, ``before_commit`` acting while the stage is suspended."""
    if before_commit is not None:
        before_commit()
    if job.ready_to_commit:
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = dict(job.result or {})
    report["cases"][label] = {key: value for key, value in result.items() if key not in ("delta", "live_revisions")}
    queries.invalidate()
    return result


def accept(preview, label, before_accept=None):
    if before_accept is not None:
        before_accept()
    arguments = {"preview_id": preview["preview_id"]}
    native.precondition(window, "angelo_accept_preview", arguments)
    job = native.begin(window, "angelo_accept_preview", arguments)
    settle(job)
    return commit(job, label)


def committed(result):
    return result.get("status") == "committed" and not result.get("error")


def collection(name):
    block = bpy.data.collections.new(name)
    window.scene.collection.children.link(block)
    return block


def wall(name, home, faces=2):
    mesh = bpy.data.meshes.new(name + " Mesh")
    mesh.from_pydata([(x + 3 * face, y, 0) for face in range(faces) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))], [],
                     [tuple(range(4 * face, 4 * face + 4)) for face in range(faces)])
    obj = bpy.data.objects.new(name, mesh)
    (home or window.scene.collection).objects.link(obj)
    return obj


def where(name):
    obj = bpy.data.objects.get(name)
    return sorted(block.name for block in obj.users_collection) if obj else None


def named(result):
    """Every object name the result gives: the added rows and the created operation rows."""
    names = [row["name"] for row in (result.get("changes") or {}).get("added", ())]
    for row in result.get("operation_results") or ():
        if row.get("created") is True:
            names.append(row.get("object"))
        elif isinstance(row.get("created"), list):
            names += [part["name"] for part in row["created"]]
    return sorted(set(names))


def artist_takes(name):
    """The artist adds an object with this exact name in the Scene Collection (as a Shift+D can)."""
    def act():
        obj = wall(name, None, faces=1)
        obj.location = (40, 40, 0)
        report.setdefault("artist", []).append(obj.name)
    return act


def part_lands_with_its_target(prefix, result, artist, parts):
    """The target and its ``parts`` new objects are in Props; the artist's objects are untouched."""
    made = [obj.name for obj in bpy.data.objects
            if obj.name.startswith(prefix + " Wall") and obj.name != prefix + " Wall" and obj.name not in artist]
    live = {name: where(name) for name in made + [prefix + " Wall"] + artist}
    report["cases"][prefix.lower() + "_live"] = live
    return (committed(result) and len(made) == parts and all(live[name] == [prefix + " Props"] for name in made)
            and live[prefix + " Wall"] == [prefix + " Props"] and all(live[name] == ["Scene Collection"] for name in artist)
            and all(bpy.data.objects[name].location.x == 40 for name in artist)
            and set(named(result)) == set(made) and not set(named(result)) & set(artist))


def separated_part_taken_while_suspended():
    props = collection("N1 Props")
    wall("N1 Wall", props)
    baseline("N1")
    job, staged = stage([op("mesh_separate", handle("N1 Wall"), mode="LOOSE")], writes=["N1 Wall"])
    result = commit(job, "n1", artist_takes("N1 Wall.001"))
    check("n1_a_part_whose_name_was_taken_keeps_its_collections_and_is_reported_as_committed",
          staged.get("ready_to_commit") and part_lands_with_its_target("N1", result, ["N1 Wall.001"], 1))


def sibling_duplicated_while_suspended():
    props, other = collection("N2 Props"), collection("N2 Other")
    wall("N2 Wall", props)
    for name in ("N2 Wall.002", "N2 Wall.003"):
        wall(name, other, faces=1)
    baseline("N2")
    job, _ = stage([op("mesh_separate", handle("N2 Wall"), mode="LOOSE")], writes=["N2 Wall"])

    def shift_d():  # Blender names a copy of "N2 Wall.003" with the lowest free number of "N2 Wall".
        source = bpy.data.objects["N2 Wall.003"]
        copy = source.copy()
        copy.location = (40, 40, 0)
        bpy.data.collections["N2 Other"].objects.link(copy)
        report["n2_artist"] = copy.name

    result = commit(job, "n2", shift_d)
    artist = report["n2_artist"]
    made = [obj.name for obj in bpy.data.objects
            if obj.name.startswith("N2 Wall.") and obj.name not in ("N2 Wall.002", "N2 Wall.003", artist)]
    report["cases"]["n2_live"] = {name: where(name) for name in made + [artist]}
    check("n2_a_part_whose_name_a_sibling_copy_took_keeps_its_collections", committed(result) and artist == "N2 Wall.001"
          and len(made) == 1 and where(made[0]) == ["N2 Props"] and where(artist) == ["N2 Other"]
          and named(result) == made)


def unnamed_duplicate_taken_while_suspended():
    props = collection("N3 Props")
    wall("N3 Wall", props, faces=1)
    baseline("N3")
    job, staged = stage([op("object_duplicate", source=handle("N3 Wall"), alias="dup", location=[3, 0, 0])],
                        reads=["N3 Wall"])
    staged_name = next(row["object"] for row in job.operation_results if row["kind"] == "object_duplicate")
    result = commit(job, "n3", artist_takes(staged_name))
    check("n3_an_unnamed_duplicate_whose_name_was_taken_is_reported_as_committed", staged_name == "N3 Wall.001"
          and part_lands_with_its_target("N3", result, [staged_name], 1))


def named_duplicate_taken_in_a_preview():
    props = collection("N4 Props")
    wall("N4 Wall", props, faces=1)
    baseline("N4")
    before = sorted(obj.name for obj in bpy.data.objects)
    job, preview = stage([op("object_duplicate", source=handle("N4 Wall"), alias="dup", name="N4 Copy")],
                         reads=["N4 Wall"], mode="PREVIEW")
    result = accept(preview, "n4", artist_takes("N4 Copy"))
    check("n4_a_requested_name_taken_before_a_preview_is_accepted_is_a_correctable_refusal",
          preview.get("status") == "preview" and result.get("status") == "failed"
          and result.get("error_code") not in ("UNKNOWN", "INTERNAL_ERROR") and not result.get("recovery_required")
          and sorted(obj.name for obj in bpy.data.objects) == sorted(before + ["N4 Copy"])
          and where("N4 Copy") == ["Scene Collection"] and no_stages())


def separated_part_taken_in_a_preview():
    props = collection("N5 Props")
    wall("N5 Wall", props)
    baseline("N5")
    job, preview = stage([op("mesh_separate", handle("N5 Wall"), mode="LOOSE")], writes=["N5 Wall"], mode="PREVIEW")
    result = accept(preview, "n5", artist_takes("N5 Wall.001"))
    check("n5_a_previewed_part_whose_name_was_taken_keeps_its_collections",
          preview.get("status") == "preview" and part_lands_with_its_target("N5", result, ["N5 Wall.001"], 1))


def two_parts_shifted_along():
    """The artist takes the first part's name; both parts come back under other names, each kept and reported."""
    props = collection("N6 Props")
    wall("N6 Wall", props, faces=3)
    baseline("N6")
    job, _ = stage([op("mesh_separate", handle("N6 Wall"), mode="LOOSE")], writes=["N6 Wall"])
    result = commit(job, "n6", artist_takes("N6 Wall.001"))
    check("n6_parts_renamed_in_a_chain_keep_their_collections", part_lands_with_its_target("N6", result,
                                                                                          ["N6 Wall.001"], 2))


def long_name_with_a_rename():
    """A target named at the 255-byte limit, in a program that also renames (program-order naming)."""
    props = collection("N7 Props")
    target = wall("N7 " + "x" * 300, props)
    other = wall("N7 Other", None, faces=1)
    baseline("N7")
    job, _ = stage([op("object_rename", handle(other.name), name="N7 Other Renamed"),
                    op("mesh_separate", handle(target.name), mode="LOOSE")], writes=[target.name, other.name])
    result = commit(job, "n7")
    parts = [obj for obj in bpy.data.objects if obj.name.startswith("N7 x") and obj != target]
    report["n7_parts"] = [[len(obj.name.encode()), where(obj.name)] for obj in parts]
    check("n7_a_part_named_at_the_byte_limit_keeps_its_collections", committed(result) and len(parts) == 1
          and where(parts[0].name) == ["N7 Props"] and named(result) == [parts[0].name])


def fails_closed():
    """No new object commits outside its staged collections: an unknown or a wrong one rolls back exactly."""
    from angelo_agent import program_commit
    props = collection("N8 Props")
    wall("N8 Wall", props)
    baseline("N8")
    original = program_memberships.link_new_objects

    def unknown(backend, stage, new_objects, memberships, scope):  # A part whose staged collections are lost.
        stage.extras[program_memberships.NAMED] = {}
        return original(backend, stage, new_objects, memberships, scope)

    def wrong(backend, stage, new_objects, memberships, scope):  # A linker that also links into the master.
        original(backend, stage, new_objects, memberships, scope)
        for obj in new_objects:
            backend.scene.collection.objects.link(obj)

    for label, fault in (("unknown", unknown), ("wrong", wrong)):
        before = sorted(obj.name for obj in bpy.data.objects)
        job, _ = stage([op("mesh_separate", handle("N8 Wall"), mode="LOOSE")], writes=["N8 Wall"])
        with patch.object(program_commit, "link_new_objects", fault):
            result = commit(job, "n8_" + label)
        check("n8_%s_staged_collections_roll_back_exactly" % label, result.get("status") == "failed"
              and result.get("partial_changes_possible") is False and sorted(obj.name for obj in bpy.data.objects) == before
              and where("N8 Wall") == ["N8 Props"] and len(objects()["N8 Wall"].data.polygons) == 2 and no_stages())


def other_ids_follow_too():
    """n2, only object fields follow an object's new name (a material named like it keeps its own);
    n3, a new material whose name the artist took is reported under the name it committed as."""
    props = collection("N9 Props")
    crate = wall("N9 Crate", props, faces=1)
    crate.data.materials.append(bpy.data.materials.new("N9 Wood"))
    wall("N9 Plank", props, faces=1)
    baseline("N9")
    job, _ = stage([op("object_duplicate", source=handle("N9 Crate"), alias="dup", location=[3, 0, 0]),
                    op("material_assign", handle("N9 Plank"), material="N9 Wood", source=handle("N9 Crate"),
                       copy_as="N9 Crate.001")], writes=["N9 Plank"], reads=["N9 Crate"])
    result = commit(job, "n9", artist_takes("N9 Crate.001"))
    plank = objects()["N9 Plank"].material_slots[0].material.name
    rows = {row["kind"]: row for row in result.get("operation_results") or ()}
    report["n9_rows"] = rows
    check("n9_a_material_named_like_a_renamed_object_keeps_its_name", committed(result) and plank == "N9 Crate.001"
          and rows["material_assign"].get("material") == plank and rows["object_duplicate"]["object"] == "N9 Crate.002"
          and where("N9 Crate.002") == ["N9 Props"])
    wall("N10 Plank", props, faces=1).data.materials.append(bpy.data.materials.new("N10 Wood"))
    baseline("N10")
    job, _ = stage([op("material_assign", handle("N10 Plank"), material="N10 Wood", source=None,
                       copy_as="N10 Red")], writes=["N10 Plank"])

    def artist_material():
        bpy.data.materials.new("N10 Red").use_fake_user = True

    result = commit(job, "n10", artist_material)
    rows = [row for row in result.get("operation_results") or () if row["kind"] == "material_assign"]
    plank = objects()["N10 Plank"].material_slots[rows[0]["slot"] if rows else 0].material.name
    moved = result.get("renamed_while_waiting") or []
    report["n10"] = {"plank": plank, "rows": rows, "moved": moved}
    check("n10_a_new_material_whose_name_was_taken_is_reported_as_committed", committed(result)
          and plank == "N10 Red.001" and rows and all(row.get("material") == plank for row in rows)
          and {"type": "MATERIAL", "staged": "N10 Red", "committed": "N10 Red.001"} in moved
          and bpy.data.materials["N10 Red"].users == 1)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    baseline("new object identity baseline")
    separated_part_taken_while_suspended()
    sibling_duplicated_while_suspended()
    unnamed_duplicate_taken_while_suspended()
    named_duplicate_taken_in_a_preview()
    separated_part_taken_in_a_preview()
    two_parts_shifted_along()
    long_name_with_a_rename()
    fails_closed()
    other_ids_follow_too()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "new-object-identity.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_NEW_OBJECT_IDENTITY", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}),
      flush=True)
