# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression: Blender's hidden "previously excluded" flag in visibility programs.

A collection excluded before its parent keeps LAYER_COLLECTION_PREVIOUSLY_EXCLUDED, a flag RNA does not expose:
including the parent again leaves it excluded (w0). Staging assumed every descendant comes back, so a hide in such
a child failed at commit as INTERNAL_ERROR with recovery_required (w1), a staged check that the child is included
passed and then failed live (w2), and a rolled-back commit restored the descendants' flags through RNA, which
cleared the hidden flag: the artist's next include of the parent then brought the child back (w1-w3).

Now staging reads both flags natively and predicts with Blender's own rule, commit checks the live flags against
the prediction, and a rollback writes every layer collection's bits back exactly. Disposable headless instance with
the exact Angelo binary: ``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call. Writes previously-excluded.json in ANGELO_QA_DIR.

A second defect: the native read walked the layer tree without syncing the view layer first, as RNA does. A collection
just created was missing (x1b: a program creating a collection and excluding another failed COMMIT_FIDELITY) and one
just removed was read through a dangling layer collection (SIGSEGV under ASan). Both calls now sync first (x1-x1c).
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-previously-excluded-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import live_checks, native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def handle(name):
    return queries.object_handle(window.scene.objects[name])


def entry(name):
    return {"handle": handle(name), "revision": queries.object_revision(window, window.scene.objects[name])}


def scene_entry():
    settings = scene_settings.execute(window, {})
    return {"handle": settings["handle"], "revision": settings["revision"]}


def op(kind, target, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def run(label, operations, writes=(), scene=False, checks=(), fault=None):
    scene_row = scene_entry() if scene else None
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "write_set": [entry(name) for name in writes] + ([scene_row] if scene_row else []),
        "operations": [operation(scene_row["handle"]) if callable(operation) else operation
                       for operation in operations], "expected_checks": list(checks)})
    job = native.begin(window, "angelo_scene_program", arguments)
    for _ in range(400):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if staged.get("ready_to_commit"):
        if fault is None:
            bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        else:
            with patch.object(live_checks, "verify_live", fault):
                bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = dict(job.result or staged)
    report["cases"][label] = {key: result.get(key) for key in ("status", "error_code", "error",
                                                                 "partial_changes_possible")}
    queries.invalidate()
    return result


def layer(name, view_layer=None):
    pending = list((view_layer or window.view_layer).layer_collection.children)
    while pending:
        item = pending.pop(0)
        if item.collection.name == name:
            return item
        pending[:0] = list(item.children)
    raise KeyError(name)


def flags():
    """Each layer collection's exclude (1) and previously-excluded (2) bits, depth first, read natively."""
    from _bpy import _angelo_stage
    return [row[2] for row in _angelo_stage.layer_flags(window.scene, window.view_layer.name)]


def collection(name, parent=None):
    block = bpy.data.collections.new(name)
    (bpy.data.collections[parent] if parent else window.scene.collection).children.link(block)
    return name


def mesh(name, home):
    bpy.data.collections[home].objects.link(bpy.data.objects.new(name, bpy.data.meshes.new(name)))


def artist_includes(name, *children):
    """Blender's own answer when the artist includes ``name`` later; the flags are put back after."""
    before = flags()
    layer(name).exclude = False
    seen = {child: layer(child).exclude for child in children}
    from _bpy import _angelo_stage
    _angelo_stage.set_layer_flags(window.scene, window.view_layer.name, before)
    return seen


def late_fault(*args, **kwargs):
    raise ProgramError("injected fault after the visibility edits", code="CHECK_FAILED")


def history(prefix):
    """G > P > {C1 (excluded before G), C2, C3 > D (excluded before G)}; S under P and under Q; then G excluded."""
    for name, parent in (("G", None), ("P", "G"), ("C1", "P"), ("C2", "P"), ("C3", "P"), ("D", "C3"), ("Q", None),
                         ("Home", None)):
        collection(prefix + " " + name, prefix + " " + parent if parent else None)
    bpy.data.collections[prefix + " Q"].children.link(bpy.data.collections.new(prefix + " S"))
    bpy.data.collections[prefix + " P"].children.link(bpy.data.collections[prefix + " S"])
    mesh(prefix + " Crate", prefix + " Home")
    layer(prefix + " C1").exclude = True
    layer(prefix + " D").exclude = True
    layer(prefix + " G").exclude = True


def previously_excluded():
    history("PE")
    baseline("previously excluded")
    hidden = flags()
    report["flags_before"] = hidden
    check("w0_the_native_read_shows_the_hidden_flag", any(bits == 3 for bits in hidden)
          and artist_includes("PE G", "PE C1", "PE C2", "PE D") == {"PE C1": True, "PE C2": False, "PE D": True})
    # w1: include G, move the crate into C1 (excluded before G) and hide it: no Base, refused before commit.
    result = run("w1", [lambda scene: op("visibility_set", scene, collection="PE G", exclude=False),
                        op("collection_link", handle("PE Crate"), collection="PE C1", mode="MOVE"),
                        op("visibility_set", handle("PE Crate"), hidden=True)], writes=["PE Crate"], scene=True)
    check("w1_a_hide_in_a_previously_excluded_child_is_refused_before_commit", result.get("error_code") == "VALIDATION"
          and "NOT_IN_VIEW_LAYER" in (result.get("error") or "") and flags() == hidden and no_stages())
    # The same in C2 (excluded only through G): it comes back, so the hide commits.
    result = run("v2b", [lambda scene: op("visibility_set", scene, collection="PE G", exclude=False),
                         op("collection_link", handle("PE Crate"), collection="PE C2", mode="MOVE"),
                         op("visibility_set", handle("PE Crate"), hidden=True)], writes=["PE Crate"], scene=True)
    check("v2b_a_hide_in_a_child_that_comes_back_commits", result.get("status") == "committed"
          and window.scene.objects["PE Crate"].hide_get() and not layer("PE C2").exclude and layer("PE C1").exclude)
    bpy.ops.ed.undo()
    queries.invalidate()
    check("v2b_undo_restores_every_flag", flags() == hidden)
    # w2: a check that C1 is included after including G fails before anything live is written.
    result = run("w2", [lambda scene: op("visibility_set", scene, collection="PE G", exclude=False)], scene=True,
                 checks=[{"kind": "visibility", "target": scene_entry()["handle"], "collection": "PE C1",
                          "exclude": False}])
    check("w2_a_check_on_a_previously_excluded_child_fails_on_the_stage", result.get("error_code") == "CHECK_FAILED"
          and "COMMIT_FIDELITY" not in (result.get("error") or "") and flags() == hidden and no_stages())
    # w3: several edits (include G, exclude Q, exclude C2, include D) rolled back after a late fault: every layer
    # collection's bits come back, the hidden flag included, at depth 4, with siblings and S under two parents.
    result = run("w3", [lambda scene: op("visibility_set", scene, collection="PE G", exclude=False),
                        lambda scene: op("visibility_set", scene, collection="PE Q", exclude=True),
                        lambda scene: op("visibility_set", scene, collection="PE C2", exclude=True),
                        lambda scene: op("visibility_set", scene, collection="PE D", exclude=False)],
                 scene=True, fault=late_fault)
    check("w3_a_rollback_restores_every_layer_collections_bits", result.get("status") == "failed"
          and result.get("partial_changes_possible") is False and flags() == hidden
          and artist_includes("PE G", "PE C1", "PE C2", "PE D") == {"PE C1": True, "PE C2": False, "PE D": True}
          and no_stages())
    # The same edits committed: Blender's own result, which staging predicted (no fidelity refusal).
    result = run("w3_committed", [lambda scene: op("visibility_set", scene, collection="PE G", exclude=False),
                                  lambda scene: op("visibility_set", scene, collection="PE Q", exclude=True),
                                  lambda scene: op("visibility_set", scene, collection="PE C2", exclude=True),
                                  lambda scene: op("visibility_set", scene, collection="PE D", exclude=False)],
                 scene=True)
    report["flags_committed"] = flags()
    check("w3_the_edits_commit_as_blender_gives_them", result.get("status") == "committed"
          and [layer("PE " + name).exclude for name in ("G", "C1", "C2", "C3", "D", "Q")]
          == [False, True, True, False, False, True])
    bpy.ops.ed.undo()
    queries.invalidate()
    check("w3_undo_restores_every_flag", flags() == hidden)


def native_names():
    from _bpy import _angelo_stage
    return [row[1] for row in _angelo_stage.layer_flags(window.scene, window.view_layer.name)]


def rna_names(parent=None):
    """The same walk through RNA, which syncs the view layer first: call it after the native read."""
    names = []
    for item in (parent or window.view_layer.layer_collection).children:
        names += [item.collection.name] + rna_names(item)
    return names


def unsynced_reads():
    """x1c: the native calls on a view layer left out of sync, with no RNA access in between."""
    from _bpy import _angelo_stage
    scene, master = window.scene, window.scene.collection
    made = bpy.data.collections.new("PE Fresh")
    master.children.link(made)
    seen = native_names()
    check("x1c_a_collection_just_linked_is_read", "PE Fresh" in seen and seen == rna_names())
    master.children.unlink(made)
    seen = native_names()
    check("x1c_a_collection_just_unlinked_is_gone", "PE Fresh" not in seen and seen == rna_names())
    master.children.link(made)
    native_names()
    master.children.unlink(made)
    bpy.data.collections.remove(made)
    seen = native_names()
    check("x1c_a_collection_unlinked_and_removed_reads_the_synced_tree", "PE Fresh" not in seen
          and seen == rna_names())
    doomed = bpy.data.collections.new("PE Doomed")
    doomed.children.link(bpy.data.collections.new("PE Doomed Child"))
    master.children.link(doomed)
    native_names()
    bpy.data.collections.remove(doomed)
    seen = native_names()
    check("x1c_a_collection_removed_while_linked_reads_the_synced_tree", "PE Doomed" not in seen
          and "PE Doomed Child" not in seen and seen == rna_names())
    bpy.data.collections.remove(bpy.data.collections["PE Doomed Child"])
    # A write after an unsynced link counts the synced tree (the new collection is last, depth first).
    before = flags()
    master.children.link(bpy.data.collections.new("PE Late"))
    _angelo_stage.set_layer_flags(scene, window.view_layer.name, before + [1])
    check("x1c_a_write_after_an_unsynced_link_writes_the_synced_tree", flags() == before + [1]
          and layer("PE Late").exclude)
    bpy.data.collections.remove(bpy.data.collections["PE Late"])
    check("x1c_the_tree_is_back", flags() == before)


def created_collection_programs():
    """x1b: create a collection and exclude another one; x1: the same rolled back after a late fault."""
    collection("PE Off")
    collection("PE Box")
    mesh("PE Plank", "PE Box")
    baseline("created collection")
    before = flags()

    def edits():
        return [op("collection_link", handle("PE Plank"), collection="PE New", mode="MOVE", create=True),
                lambda scene: op("visibility_set", scene, collection="PE Off", exclude=True)]

    result = run("x1", edits(), writes=["PE Plank"], scene=True, fault=late_fault)
    check("x1_a_rollback_after_a_created_collection_restores_every_flag", result.get("status") == "failed"
          and result.get("partial_changes_possible") is False and "PE New" not in bpy.data.collections
          and flags() == before and not layer("PE Off").exclude and no_stages())
    result = run("x1b", edits(), writes=["PE Plank"], scene=True)
    check("x1b_a_created_collection_and_an_exclusion_commit", result.get("status") == "committed"
          and [block.name for block in window.scene.objects["PE Plank"].users_collection] == ["PE New"]
          and layer("PE Off").exclude and not layer("PE New").exclude)
    bpy.ops.ed.undo()
    queries.invalidate()
    check("x1b_undo_restores_every_flag", flags() == before and "PE New" not in bpy.data.collections)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    baseline("previously excluded baseline")
    previously_excluded()
    unsynced_reads()
    created_collection_programs()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "previously-excluded.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_PREVIOUSLY_EXCLUDED", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}),
      flush=True)
