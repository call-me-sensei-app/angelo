# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every object data type in the dependency closure, and borrowing on non-mesh objects, in a disposable process.

The second review found that the closure walk
(``program_dependencies._walk_dependencies``) never visited Grease Pencil or
hair-curves data: ``stage_borrowing._plan`` could not see their Actions, so a
plain move failed as INTERNAL_ERROR, and an undeclared Grease Pencil layer
parent or hair ``surface`` object failed as INTERNAL_ERROR instead of the
VALIDATION "declare it" refusal. Fixing it showed a second false block: the
link walk entered the IDs a collection holds (a curve's ``materials``) as if
they were embedded structs, so a staged curve or text object reached its
borrowed live material's node tree and stage validation refused every move of
one with a material (INTERNAL_ERROR, "still aliases original data"). This
suite pins:

* the closure holds every object data type staging copies (and shape keys);
* per-element views the link walks skip have leaf item types;
* an ID held in a collection is a link, never walked into;
* curve and text moves borrow their materials and commit;
* undeclared GP layer parents and hair surfaces are VALIDATION refusals,
  declared ones commit and stay live;
* animated GP data is not borrowed (the closure alone sees it); and when a
  stage owns an animated copy anyway, it unborrows before its first
  evaluation and commits, where the owned-ID check alone would block.
"""

import json
import os
from pathlib import Path
import sys
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, stage_borrowing  # noqa: E402
from angelo_agent import program_dependencies as dependencies  # noqa: E402
from program_qa_helpers import args_for, no_stages, spent_budget  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
KEYED, TWEAKED = .2, .9
MOVE = {"kind": "transform", "parameters": {"location": [1., 2., 3.]}}
D = bpy.data


def check(name, condition):
    report["checks"][name] = bool(condition)


def link(obj):
    bpy.context.window.scene.collection.objects.link(obj)
    return obj


def key(owner, path):
    action = D.actions.new("Keyed " + owner.name)
    owner.animation_data_create()
    owner.animation_data.action = action
    curve = action.fcurve_ensure_for_datablock(owner, path)
    for frame in (1, 20):
        curve.keyframe_points.insert(frame, KEYED)


def move(name, obj, *, reads=(), tweak=None, read=None):
    """Stage a move of ``obj``, settle and commit; what the stage borrowed and how it ended."""
    window = bpy.context.window
    window.scene.frame_set(1)
    window.view_layer.update()
    if tweak is not None:
        tweak()
        window.view_layer.update()
    operations = [dict(MOVE, target=queries.object_handle(obj))]
    job = native.begin(window, "angelo_scene_program", args_for(window, operations, writes=[obj], reads=list(reads)))
    with spent_budget():
        result = job.poll()
    rec = {"borrowed": len((getattr(job.stage, "extras", None) or {}).get(stage_borrowing.BORROWED, ()))}
    if read is not None:
        rec["after_staging"] = read()
    for _ in range(400):
        if result.get("final") or result.get("ready_to_commit"):
            break
        result = job.poll()
    rec.update(status=result.get("status"), error_code=result.get("error_code"),
               error=(result.get("error") or "")[:160], partial=bool(result.get("partial_changes_possible")))
    if result.get("ready_to_commit"):
        rec["status"] = job.commit().get("status")
    elif not result.get("final"):
        job.cancel()
    if read is not None:
        rec["final"] = read()
    rec["moved"] = list(obj.location) == [1., 2., 3.]
    rec["no_stages"] = no_stages()
    native.cleanup_all()
    report["observed"][name] = rec
    return rec


def grease_pencil(name):
    material = D.materials.new(name)
    D.materials.create_gpencil_data(material)
    data = D.grease_pencils.new(name)
    layer = data.layers.new("Layer")
    layer.frames.new(1).drawing.add_strokes([4])
    data.materials.append(material)
    return link(D.objects.new(name, data)), data, material


def hair(name):
    material = D.materials.new(name)
    data = D.hair_curves.new(name)
    data.add_curves([3, 3])
    data.materials.append(material)
    return link(D.objects.new(name, data)), data, material


def closure_covers_every_object_data_type():
    def curve():
        data = D.curves.new("c", "CURVE")
        data.splines.new("POLY").points.add(1)
        return data
    makers = {"MESH": lambda: D.meshes.new("m"), "CURVE": curve,
              "SURFACE": lambda: D.curves.new("s", "SURFACE"), "FONT": lambda: D.curves.new("f", "FONT"),
              "META": lambda: D.metaballs.new("mb"), "GREASEPENCIL": lambda: D.grease_pencils.new("gp"),
              "CURVES": lambda: D.hair_curves.new("h"), "POINTCLOUD": lambda: D.pointclouds.new("p"),
              "VOLUME": lambda: D.volumes.new("v"), "LATTICE": lambda: D.lattices.new("l"),
              "ARMATURE": lambda: D.armatures.new("a"), "LIGHT": lambda: D.lights.new("li", "POINT"),
              "CAMERA": lambda: D.cameras.new("ca"), "LIGHT_PROBE": lambda: D.lightprobes.new("lp", "SPHERE"),
              "SPEAKER": lambda: D.speakers.new("sp")}
    from angelo_agent.scene_spec import OBJECT_TYPES
    check("audit_lists_every_reader_type_but_empty", set(makers) == set(OBJECT_TYPES) - {"EMPTY"})
    missing = []
    for kind, make in makers.items():
        obj = D.objects.new("Closure " + kind, make())
        if kind in {"MESH", "CURVE", "LATTICE"}:
            obj.shape_key_add(name="Basis")
        closure = dependencies._mutable_dependencies(obj)
        wanted = [obj.data] + ([obj.data.shape_keys] if kind in {"MESH", "CURVE", "LATTICE"} else [])
        if obj.type != kind or not all(any(block == item for block in closure) for item in wanted):
            missing.append(kind)
    report["observed"]["closure_missing"] = missing
    check("closure_holds_every_object_data_type_and_shape_keys", not missing)


def element_views_are_leaves():
    """Item types of the views link walks skip declare no pointer or collection, and no type derives from them."""
    def links_of(rna):
        return [prop.identifier for prop in rna.properties
                if prop.type in {"POINTER", "COLLECTION"} and prop.identifier != "rna_type"]
    items, attributes = {}, []
    for name in dir(bpy.types):
        rna = getattr(getattr(bpy.types, name), "bl_rna", None)
        base = rna.base if rna is not None else None
        while base is not None and base.identifier != "Attribute":
            base = base.base
        if base is not None:
            attributes.append(name)
            items[name + ".data"] = rna.properties["data"].fixed_type
    for owner, views in dependencies.ELEMENT_VIEWS.items():
        for view in views:
            items[owner + "." + view] = getattr(bpy.types, owner).bl_rna.properties[view].fixed_type
    item_types = {item.identifier for item in items.values()}
    nested = {name: links_of(item) for name, item in items.items() if links_of(item)}
    derived = []
    for name in dir(bpy.types):
        base = getattr(getattr(getattr(bpy.types, name), "bl_rna", None), "base", None)
        while base is not None:
            if base.identifier in item_types:
                derived.append(name)
            base = base.base
    report["observed"]["element_views"] = {"attributes": len(attributes), "nested": nested, "derived": derived}
    check("every_attribute_type_is_covered", len(attributes) >= 14)
    # CurveSlice.points is the one nested view: CurvePoint, itself a leaf skipped on Curves.
    check("element_view_items_are_leaves", nested == {"Curves.curves": ["points"]}
          and not links_of(bpy.types.CurvePoint.bl_rna) and not derived)
    # Custom-link admission skips them too: their items cannot hold ID properties either.
    hair_data = D.hair_curves.new("Views")
    hair_data.add_curves([3])
    drawing = D.grease_pencils.new("Views").layers.new("Layer").frames.new(1).drawing
    drawing.add_strokes([3])
    views = [getattr(hair_data, name) for name in dependencies.ELEMENT_VIEWS["Curves"]]
    views += [drawing.curve_offsets, hair_data.attributes["position"].data, drawing.attributes["position"].data]
    holding = []
    for view in views:
        try:
            view[0].keys()
            holding.append(view[0].bl_rna.identifier)
        except TypeError:  # "this type doesn't support IDProperties"
            pass
    report["observed"]["element_views"]["holding_id_properties"] = holding
    check("element_view_items_hold_no_id_properties", not holding)


def collection_ids_are_links():
    material = D.materials.new("Collection link")
    curve = D.curves.new("Collection link", "CURVE")
    curve.materials.append(material)
    found = list(dependencies._embedded_links(curve))
    check("a_collection_id_is_a_link_not_an_embedded_struct",
          [(holder == curve, prop.identifier, dependency == material) for holder, prop, dependency in found]
          == [(True, "materials", True)])


def curve_and_text_moves_borrow_and_commit():
    for kind in ("CURVE", "FONT"):
        material = D.materials.new("Borrowed " + kind)
        data = D.curves.new("Borrowed " + kind, kind)
        if kind == "CURVE":
            data.splines.new("POLY").points.add(2)
        data.materials.append(material)
        obj = link(D.objects.new("Borrowed " + kind, data))
        rec = move(kind.lower(), obj)
        check(kind.lower() + "_borrows_its_material", rec["borrowed"] == 1)
        check(kind.lower() + "_move_commits", rec["status"] == "committed" and rec["moved"] and rec["no_stages"]
              and obj.data.materials[0] == material)


def undeclared_layer_parent_and_surface_are_validation():
    for name, make in (("gp_layer_parent", grease_pencil), ("hair_surface", hair)):
        for declared in (False, True):
            obj, data, _material = make("%s %d" % (name, declared))
            target = link(D.objects.new("Target %s %d" % (name, declared), None if name.startswith("gp") else
                                        D.meshes.new("Surface %d" % declared)))
            if name.startswith("gp"):
                data.layers["Layer"].parent = target
                live = lambda: data.layers["Layer"].parent == target
            else:
                data.surface = target
                live = lambda: data.surface == target
            label = name + ("_declared" if declared else "_undeclared")
            rec = move(label, obj, reads=[target] if declared else [])
            if declared:
                check(label + "_commits_and_stays_live", rec["status"] == "committed" and rec["moved"] and live())
            else:
                check(label + "_is_a_validation_refusal", rec["error_code"] == "VALIDATION" and not rec["partial"]
                      and "Declare every existing" in rec["error"] and not rec["moved"])
            check(label + "_leaves_no_stage", rec["no_stages"])


def animated_data(name):
    obj, data, material = grease_pencil(name)
    key(data, "materials[0].grease_pencil.mix_factor")
    tweak = lambda: setattr(material.grease_pencil, "mix_factor", TWEAKED)
    read = lambda: round(material.grease_pencil.mix_factor, 4)
    return obj, tweak, read


def animated_data_is_never_borrowed():
    original = stage_borrowing._copied
    try:  # The closure alone, without the data and shape keys _copied names again, sees the Action.
        stage_borrowing._copied = lambda obj: dependencies._mutable_dependencies(obj)
        obj, tweak, read = animated_data("Closure sees data animation")
        rec = move("closure_sees_data_animation", obj, tweak=tweak, read=read)
    finally:
        stage_borrowing._copied = original
    check("animated_gp_data_is_not_borrowed", rec["borrowed"] == 0 and rec["status"] == "committed")
    check("animated_gp_data_keeps_the_live_value", rec["after_staging"] == TWEAKED and rec["final"] == TWEAKED)


def owned_animation_unborrows_before_the_first_evaluation():
    original, owns = stage_borrowing._copied, stage_borrowing.owns_animated
    backend, unborrow, calls = native.BlenderProgramBackend, native.BlenderProgramBackend.unborrow, []
    try:  # _plan sees nothing animated, so the stage borrows while owning the GP data's copied Action.
        stage_borrowing._copied = lambda obj: [obj]
        backend.unborrow = lambda self, stage: calls.append(stage_borrowing.borrows_any(stage)) or unborrow(self, stage)
        obj, tweak, read = animated_data("Unborrowed")
        rec = move("unborrowed_before_first_evaluation", obj, tweak=tweak, read=read)
        backend.unborrow = unborrow
        stage_borrowing.owns_animated = lambda stage: False  # Control: without unborrowing, the guard blocks.
        obj, tweak, read = animated_data("Owned check alone")
        control = move("owned_check_alone", obj, tweak=tweak, read=read)
    finally:
        stage_borrowing._copied, stage_borrowing.owns_animated, backend.unborrow = original, owns, unborrow
    report["observed"]["unborrow_calls"] = calls
    check("the_borrowing_stage_unborrowed_once", calls == [True])
    check("unborrowed_stage_commits", rec["status"] == "committed" and rec["moved"] and rec["error_code"] is None)
    check("unborrowed_stage_holds_private_clones", rec["borrowed"] == 0)
    check("unborrowed_stage_keeps_the_live_value", rec["after_staging"] == TWEAKED and rec["final"] == TWEAKED)
    check("owned_check_alone_blocks_without_a_live_write", control["error_code"] == "INTERNAL_ERROR"
          and control["final"] == TWEAKED and control["no_stages"])


def layer_parents_are_never_remapped_as_objects():
    """Commit's repoint takes only an Object as an Object (the final review's repro: a GP layer was, UNKNOWN).

    Of 5.2's structs, Object, GreasePencilLayer and ParticleSystem have a writable ``parent`` object pointer and
    only the first two a ``matrix_parent_inverse``; a new one must be reviewed here. Blender recomputes a layer's
    inverse whenever its parent is set, so an edit that swaps a parented layer's drawings is refused before any
    write (UNSUPPORTED, nothing changed), while a move of the same object commits and keeps the inverse.
    """
    holders, inverses = set(), set()
    for name in dir(bpy.types):
        rna = getattr(getattr(bpy.types, name), "bl_rna", None)
        parent = rna.properties.get("parent") if rna is not None else None
        if parent is not None and parent.type == "POINTER" and not parent.is_readonly \
                and getattr(parent.fixed_type, "identifier", None) == "Object":
            holders.add(name)
            if "matrix_parent_inverse" in rna.properties:
                inverses.add(name)
    report["observed"]["parent_holders"] = sorted(holders)
    check("only_objects_layers_and_particle_systems_hold_an_object_parent",
          holders == {"Object", "GreasePencilLayer", "ParticleSystem"} and inverses == {"Object", "GreasePencilLayer"})
    window = bpy.context.window
    for label, operation in (("swap", {"kind": "operator_run", "parameters": {
            "operator": "grease_pencil.stroke_subdivide", "properties": {"number_cuts": 1}}}), ("move", MOVE)):
        obj, data, _material = grease_pencil("Moved layer parent " + label)
        parent = link(D.objects.new("Moved layer parent target " + label, None))
        parent.location = (1., 0., 0.)
        window.view_layer.update()
        data.layers["Layer"].parent = parent
        inverse = [tuple(row) for row in data.layers["Layer"].matrix_parent_inverse]
        parent.location = (3., 0., 0.)  # The layer follows its parent; its inverse stays the one set at parenting.
        window.view_layer.update()
        entry = lambda item: {"handle": queries.object_handle(item), "revision": queries.object_revision(window, item)}
        job = native.begin(window, "angelo_scene_program", {
            "mode": "APPLY", "read_set": [entry(parent)], "write_set": [entry(obj)], "expected_checks": [],
            "operations": [dict(operation, target=queries.object_handle(obj))]})
        result = job.poll()
        while not (result.get("final") or result.get("ready_to_commit")):
            result = job.poll()
        outcome = job.commit() if result.get("ready_to_commit") else result
        live = obj.data.layers["Layer"]
        kept = live.parent == parent and [tuple(row) for row in live.matrix_parent_inverse] == inverse
        report["observed"]["moved_layer_parent_" + label] = {key: outcome.get(key) for key in (
            "status", "error_code", "error", "partial_changes_possible")}
        if label == "swap":
            check("a_drawing_swap_of_a_parented_layer_is_refused_before_writing",
                  outcome.get("error_code") == "UNSUPPORTED" and not outcome.get("partial_changes_possible")
                  and "parent inverse" in (outcome.get("error") or "") and obj.data == data and kept)
        else:
            check("a_move_of_the_same_object_commits", outcome.get("status") == "committed" and kept
                  and list(obj.location) == [1., 2., 3.])
        check("moved_layer_parent_%s_leaves_no_stage" % label, no_stages())
        native.cleanup_all()


try:
    for test in (closure_covers_every_object_data_type, element_views_are_leaves, collection_ids_are_links,
                 curve_and_text_moves_borrow_and_commit, undeclared_layer_parent_and_surface_are_validation,
                 animated_data_is_never_borrowed, owned_animation_unborrows_before_the_first_evaluation,
                 layer_parents_are_never_remapped_as_objects):
        try:
            test()
        finally:
            native.cleanup_all()
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("BORROWED_CLOSURE failed checks:", json.dumps(failed))
assert report["success"], failed
