# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Borrowed live materials after the animation rule (second independent review), in a disposable process.

Pins behaviour the second review verified on the fix head (8c6aa6f960d):

* Animated dependencies (parent, constraint target, Hook and Armature
  objects) are bound and staged, so ``_plan`` sees their Actions: nothing is
  borrowed, the move commits and the artist's unkeyed material value is kept.
* A material shared with an animated object the program does not bind is
  borrowed safely; bound together with that object, nothing is borrowed.
* The scene's and the World's own Actions are not evaluated by the stage.
* Muted NLA tracks, an Action with no F-Curves, and an Action whose assigned
  slot has no F-Curves (another slot keys the material) all prevent borrowing.
* Edits while a borrowing job waits, on native and file-backed stages, are
  correctable refusals (STALE_*), never INTERNAL_ERROR, and the stage never
  writes the live material.
* Drivers on the object or its mesh whose paths run into a material do write
  it in a live graph, Geometry Nodes object and material inputs, animated
  particle settings and instancers: each is refused by the scene reader, so
  no program binds them.
* MAX_STAGE_IDS: a borrowing stage whose clones fit privatizes for
  ``job_context`` and commits without copies; one that would not fit refuses
  at staging with LIMIT_EXCEEDED and leaves nothing behind.
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
from program_qa_helpers import FullStore, args_for, no_stages, spent_budget  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
KEYED, TWEAKED = .2, .9
MOVE = {"kind": "transform", "parameters": {"location": [1., 2., 3.]}}
STALE = {"STALE_DEPENDENCY", "STALE_REVISION"}


def check(name, condition):
    report["checks"][name] = bool(condition)


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Program did not settle")


def key(owner, path, action=None):
    action = action or bpy.data.actions.new("Keyed " + owner.name)
    owner.animation_data_create()
    owner.animation_data.action = action
    curve = action.fcurve_ensure_for_datablock(owner, path)
    for frame in (1, 20):
        curve.keyframe_points.insert(frame, KEYED)
    return action


def quad(name, materials=()):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    for material in materials:
        mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.window.scene.collection.objects.link(obj)
    return obj


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.window.scene.collection.objects.link(obj)
    return obj


def run(name, writes, read, *, reads=(), between=None, memory=True, tweak=None, store=None):
    """Tweak unkeyed, stage a move, optionally edit while the job waits, settle and commit."""
    window = bpy.context.window
    window.scene.frame_set(1)
    window.view_layer.update()
    if tweak is not None:
        tweak()
        window.view_layer.update()
    operations = [dict(MOVE, target=queries.object_handle(obj)) for obj in writes]
    kwargs = {"store": store} if store else {}
    job = native.begin(window, "angelo_scene_program",
                       args_for(window, operations, writes=list(writes), reads=list(reads), **kwargs), **kwargs)
    job.backend.memory_staging = memory
    with spent_budget():
        first = job.poll()
    rec = {"borrowed": len((getattr(job.stage, "extras", None) or {}).get(stage_borrowing.BORROWED, ())),
           "after_staging": read()}
    if between is not None:
        between()
    result = first if first.get("final") else settle(job)
    rec.update(after_hydration=read(), status=result.get("status"), error_code=result.get("error_code"),
               partial=bool(result.get("partial_changes_possible")))
    if result.get("ready_to_commit"):
        rec["status"] = job.commit().get("status")
    elif not result.get("final"):
        job.cancel()
    rec["no_stages"] = no_stages()
    native.cleanup_all()
    report["observed"][name] = rec
    return rec


def material_case(label, build, *, borrowed, reads_of=lambda obj, extra: ()):
    material = bpy.data.materials.new("Pin " + label)
    obj = quad("Pin " + label, [material])
    extra = build(obj, material)
    rec = run(label, [obj] + list(extra.get("writes", ())), lambda: round(material.roughness, 4),
              reads=reads_of(obj, extra), tweak=lambda: setattr(material, "roughness", TWEAKED))
    check(label + ":borrowing", (rec["borrowed"] > 0) == borrowed)
    check(label + ":commits_with_tweak_kept", rec["status"] == "committed" and rec["after_staging"] == TWEAKED
          and rec["after_hydration"] == TWEAKED and rec["no_stages"])
    return rec


def dependency(kind):
    def build(obj, material):
        target = bpy.data.objects.new("Rig " + kind, bpy.data.armatures.new("Rig " + kind)) if kind == "armature" \
            else empty("Target " + kind)
        if kind == "armature":
            bpy.context.window.scene.collection.objects.link(target)
            obj.modifiers.new("Armature", "ARMATURE").object = target
        elif kind == "parent":
            obj.parent = target
        elif kind == "constraint":
            obj.constraints.new("COPY_LOCATION").target = target
        else:
            obj.modifiers.new("Hook", "HOOK").object = target
        key(target, "location")
        return {"reads": [target]}
    return build


def dependencies_and_sharing():
    for kind in ("parent", "constraint", "hook", "armature"):
        material_case("dependency_" + kind, dependency(kind), borrowed=False,
                      reads_of=lambda obj, extra: extra["reads"])

    def unbound_user(obj, material):
        key(quad("Unbound animated user", [material]), "material_slots[0].material.roughness")
        return {}

    def bound_user(obj, material):
        other = quad("Bound animated user", [material])
        key(other, "material_slots[0].material.roughness")
        return {"writes": [other]}

    material_case("shared_unbound_animated_user", unbound_user, borrowed=True)
    material_case("shared_bound_animated_user", bound_user, borrowed=False)


def scene_and_world():
    scene = bpy.context.window.scene

    def scene_action(obj, material):
        key(scene, 'objects["%s"].active_material.roughness' % obj.name)
        return {}

    material_case("scene_action_through_objects", scene_action, borrowed=True)
    scene.animation_data_clear()
    scene.world = scene.world or bpy.data.worlds.new("World")
    world = scene.world
    key(world, "mist_settings.intensity")
    obj = quad("World pin", [bpy.data.materials.new("World pin")])
    rec = run("world_action", [obj], lambda: round(world.mist_settings.intensity, 4),
              tweak=lambda: setattr(world.mist_settings, "intensity", TWEAKED))
    check("world_action:untouched_and_commits", rec["status"] == "committed" and rec["after_staging"] == TWEAKED
          and rec["after_hydration"] == TWEAKED)
    world.animation_data_clear()


def conservative_shapes():
    def muted_nla(obj, material):
        action = key(obj, "material_slots[0].material.roughness")
        track = obj.animation_data.nla_tracks.new()
        track.strips.new("Strip", 1, action)
        track.mute = True
        obj.animation_data.action = None
        return {}

    def empty_action(obj, material):
        obj.animation_data_create()
        obj.animation_data.action = bpy.data.actions.new("No curves")
        return {}

    def other_slot(obj, material):
        action = bpy.data.actions.new("Two slots")
        other = quad("Other slot user", [material])
        key(other, "material_slots[0].material.roughness", action=action)
        bpy.context.window.scene.collection.objects.unlink(other)
        obj.animation_data_create()
        obj.animation_data.action = action
        obj.animation_data.action_slot = action.slots.new(id_type="OBJECT", name="Unused")
        return {}

    material_case("muted_nla_track", muted_nla, borrowed=False)
    material_case("action_without_curves", empty_action, borrowed=False)
    material_case("assigned_slot_without_curves", other_slot, borrowed=False)


def races():
    def with_group(material):
        tree = bpy.data.node_groups.new("Race group", "ShaderNodeTree")
        tree.interface.new_socket("Value", in_out="OUTPUT", socket_type="NodeSocketFloat")
        tree.nodes.new("NodeGroupOutput")
        tree.nodes.new("ShaderNodeMath")
        material.node_tree.nodes.new("ShaderNodeGroup").node_tree = tree
        return tree

    def texcoord(material, obj, group):
        material.node_tree.nodes.new("ShaderNodeTexCoord").object = empty("Race texco target")

    edits = (("material_action", lambda m, o, g: key(m, "roughness"), STALE),
             ("tree_action", lambda m, o, g: key(m.node_tree, 'nodes["Principled BSDF"].inputs["Roughness"].default_value'), STALE),
             ("material_driver", lambda m, o, g: m.driver_add("metallic"), STALE),
             ("group_animated", lambda m, o, g: key(g, 'nodes["Math"].inputs[0].default_value'), STALE),
             ("texcoord_object", texcoord, STALE),
             ("slot_swapped", lambda m, o, g: o.data.materials.__setitem__(0, bpy.data.materials.new("Swap")), STALE),
             ("object_action_through_slot", lambda m, o, g: key(o, "material_slots[0].material.roughness"), STALE),
             ("mesh_action_through_slot", lambda m, o, g: key(o.data, "materials[0].roughness"), STALE),
             ("value_edited", lambda m, o, g: setattr(m, "metallic", .5), STALE),
             ("unbound_user_animated", lambda m, o, g: key(quad("Race unbound", [m]),
                                                           "material_slots[0].material.roughness"), {None}))
    for memory in (True, False):
        for label, edit, codes in edits:
            name = "race_%s_%s" % (label, "native" if memory else "bundle")
            material = bpy.data.materials.new("Race " + name)
            group = with_group(material)
            obj = quad("Race " + name, [material])
            rec = run(name, [obj], lambda: round(material.roughness, 4), memory=memory,
                      between=lambda: edit(material, obj, group), tweak=lambda: setattr(material, "roughness", TWEAKED))
            check(name + ":borrowed_then_refused_or_committed", rec["borrowed"] > 0 and rec["error_code"] in codes
                  and not rec["partial"] and rec["status"] in ({"failed"} if None not in codes else {"committed"}))
            check(name + ":stage_never_wrote_live", rec["after_staging"] == TWEAKED and rec["after_hydration"] == TWEAKED
                  and rec["no_stages"])


def refused_by_reader(label, obj):
    try:
        queries.object_revision(bpy.context.window, obj)
        reason = None
    except Exception as error:
        reason = str(error)[:200]
    report["observed"]["reader_" + label] = reason
    check("reader_refuses:" + label, reason is not None and "unavailable" in reason)


def reader_refusals():
    for label, path, owner_of in (("object_driver_into_material", "material_slots[0].material.roughness", lambda o: o),
                                  ("mesh_driver_into_material", "materials[0].roughness", lambda o: o.data)):
        material = bpy.data.materials.new("Driver " + label)
        obj = quad("Driver " + label, [material])
        owner = owner_of(obj)
        owner.animation_data_create()
        driver = owner.animation_data.drivers.new(path).driver
        driver.type, driver.expression = "SCRIPTED", "0.2"
        bpy.context.window.view_layer.update()
        material.roughness = TWEAKED
        bpy.context.window.view_layer.update()
        check(label + ":a_driver_writes_outside_its_owner", round(material.roughness, 4) == KEYED)
        refused_by_reader(label, obj)
    for label, socket_type, value in (("gn_object_input", "NodeSocketObject", lambda: empty("GN input")),
                                      ("gn_material_input", "NodeSocketMaterial", lambda: bpy.data.materials.new("GN in"))):
        obj = quad("GN " + label, [bpy.data.materials.new("GN " + label)])
        tree = bpy.data.node_groups.new("GN " + label, "GeometryNodeTree")
        tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        socket = tree.interface.new_socket("In", in_out="INPUT", socket_type=socket_type)
        group_in, group_out = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
        tree.links.new(group_in.outputs[0], group_out.inputs[0])
        modifier = obj.modifiers.new("GN", "NODES")
        modifier.node_group = tree
        getattr(modifier.properties.inputs, socket.identifier).value = value()  # Blender 5.2 modifier inputs.
        refused_by_reader(label, obj)
    obj = quad("Particles", [bpy.data.materials.new("Particles")])
    key(obj.modifiers.new("Particles", "PARTICLE_SYSTEM").particle_system.settings, "normal_factor")
    refused_by_reader("animated_particle_settings", obj)
    collection = bpy.data.collections.new("Instanced")
    instancer = empty("Instancer")
    instancer.instance_type, instancer.instance_collection = "COLLECTION", collection
    refused_by_reader("collection_instancer", instancer)


def stage_id_budget():
    """Three 600-node materials (clone bound 1806) fit MAX_STAGE_IDS; four (2408) refuse at staging."""
    for count in (3, 4):
        group = bpy.data.node_groups.new("Wide %d" % count, "ShaderNodeTree")
        group.interface.new_socket("Value", in_out="OUTPUT", socket_type="NodeSocketFloat")
        group.nodes.new("NodeGroupOutput")
        materials = []
        for index in range(count):
            material = bpy.data.materials.new("Wide %d %d" % (count, index))
            for _ in range(600):
                material.node_tree.nodes.new("ShaderNodeGroup").node_tree = group
            materials.append(material)
        obj = quad("Wide %d" % count, materials)
        bpy.context.window.view_layer.update()
        users, names = [m.users for m in materials], sorted(m.name for m in bpy.data.materials)
        window, store = bpy.context.window, FullStore()
        job = native.begin(window, "angelo_scene_program",
                           args_for(window, [dict(MOVE, target=queries.object_handle(obj))], writes=[obj], store=store),
                           store=store)
        with spent_budget():
            first = job.poll()
        rec = {"first": first.get("error_code"), "partial": bool(first.get("partial_changes_possible")),
               "borrowed": len((getattr(job.stage, "extras", None) or {}).get(stage_borrowing.BORROWED, ()))}
        if count == 3:
            if settle(job).get("ready_to_commit"):
                with native.job_context(job.id) as inspected:
                    rec["inspected_owned"] = len(inspected.owned_ids)
                rec["status"] = job.commit().get("status")
            check("budget_fits:borrowed_privatized_and_committed", rec["borrowed"] == 3 and rec.get("status") == "committed"
                  and list(obj.location) == [1., 2., 3.])
        else:
            check("budget_exceeded:refused_at_staging", rec["first"] == "LIMIT_EXCEEDED" and not rec["partial"]
                  and list(obj.location) == [0., 0., 0.])
        native.cleanup_all()
        report["observed"]["stage_id_budget_%d" % count] = rec
        check("budget_%d:no_copies_or_users_left" % count, no_stages() and [m.users for m in materials] == users
              and sorted(m.name for m in bpy.data.materials) == names)


try:
    for test in (dependencies_and_sharing, scene_and_world, conservative_shapes, races, reader_refusals, stage_id_budget):
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
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("BORROWED_REVIEW2 failed checks:", json.dumps(failed))
assert report["success"], failed
