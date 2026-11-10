# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web link's live frames with Blender's real glTF exporter (``web_link_live``; doc/angelo/web_link.rst, "Live
frames"). Run inside Angelo without a window:

    Angelo --background --factory-startup --python tests/python/angelo_agent/test_blender_web_link_delta.py -- --out DIR

The scene: 120 boxes (some under parents), each with its own material, one textured; an armature with a skinned
"Hair" mesh; two instances of a collection. Checked: every node of a full frame has a unique key, and keys stay the same
across exports; changing one box's colour exports that box alone (its ancestors as parents, no mesh), and every other
box's digest is untouched; changing the hair's geometry (more vertices) sends the hair with its armature's bones as
parents and its skin bound to them; renaming and reparenting a box keeps its key; an image the page has goes as a
stub; deleting a box lists it as removed. It times full and delta exports (median of 15 after 2 untimed) and writes
``web-link-delta.json`` to DIR (``ANGELO_QA_DIR`` under run_native_suites.py), whose ``success`` is the verdict.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(ARGS[ARGS.index("--out") + 1]) if "--out" in ARGS else Path(
    os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-web-link-delta-qa"))
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "timings": {}}


def check(name, value, detail=None):
    report["checks"][name] = bool(value)
    if not value and detail is not None:
        report.setdefault("details", {})[name] = detail


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    image = bpy.data.images.new("Checker", 64, 64)
    image.generated_type = "COLOR_GRID"
    parents = []
    for index in range(4):
        bpy.ops.object.empty_add(location=(index * 30.0, 0, 0))
        bpy.context.object.name = "Shelf%d" % index
        parents.append(bpy.context.object)
    for index in range(120):
        bpy.ops.mesh.primitive_cube_add(size=0.5, location=(index % 30, index // 30, 0))
        box = bpy.context.object
        box.name = "Box%03d" % index
        material = bpy.data.materials.new("Paint%03d" % index)
        material.use_nodes = True
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.2, 0.4, 0.6, 1)
        if index == 7:
            texture = material.node_tree.nodes.new("ShaderNodeTexImage")
            texture.image = image
            material.node_tree.links.new(texture.outputs["Color"],
                                         material.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
        box.data.materials.append(material)
        if index % 10 == 0:
            box.parent = parents[index % 4]
    bpy.ops.object.armature_add(location=(0, -5, 0))
    rig = bpy.context.object
    rig.name = "Rig"
    bpy.ops.object.mode_set(mode="EDIT")
    head = rig.data.edit_bones.new("Head")
    head.head, head.tail = (0, 0, 1), (0, 0, 2)
    head.parent = rig.data.edit_bones[0]
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=6, location=(0, -5, 1.5))
    hair = bpy.context.object
    hair.name = "Hair"
    hair.data.materials.append(bpy.data.materials.new("HairPaint"))
    group = hair.vertex_groups.new(name="Head")
    group.add(list(range(len(hair.data.vertices))), 1.0, "REPLACE")
    modifier = hair.modifiers.new("Armature", "ARMATURE")
    modifier.object = rig
    hair.parent = rig
    kit = bpy.data.collections.new("Kit")
    bpy.ops.mesh.primitive_cone_add(location=(0, 0, 0))
    cone = bpy.context.object
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 1))
    child = bpy.context.object
    child.name, child.parent = "KitChild", cone
    bpy.ops.object.armature_add(location=(0, 0, 0))
    kit_rig = bpy.context.object
    kit_rig.name = "KitRig"
    for thing in (cone, child, kit_rig):
        for collection in thing.users_collection:
            collection.objects.unlink(thing)
        kit.objects.link(thing)
    for name, x in (("LightA", -4.0), ("LightB", 4.0)):
        bpy.ops.object.light_add(type="POINT", location=(x, 0, 3))
        bpy.context.object.name = name
    bpy.ops.mesh.primitive_cube_add(size=0.4, location=(8, -5, 0))
    face = bpy.context.object
    face.name = "Face"
    face.shape_key_add(name="Basis")
    bulge = face.shape_key_add(name="Bulge")
    for point in bulge.data:   # A rigid slide: positions change, normals don't.
        point.co.x += 0.5
    bulge.value = 1.0
    # Break-it round 3: linked duplicates under a mesh (the exporter's GPU instancing), an Angelo rigid body (its
    # physics adds nodes after Angelo's hooks), a particle system that renders an object (copies share its uid).
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(12, 0, 0))
    car = bpy.context.object
    car.name = "Car"
    bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=0.1, location=(12, 0, 0))
    wheel = bpy.context.object
    wheel.name, wheel.parent = "Wheel0", car
    for index in range(1, 4):
        copy = wheel.copy()   # Alt+D: the same mesh.
        copy.name, copy.location.x = "Wheel%d" % index, 12 + index * 0.3
        scene.collection.objects.link(copy)
        copy.parent = car
    bpy.ops.mesh.primitive_cube_add(size=0.6, location=(16, 0, 0))
    crate = bpy.context.object
    crate.name = "Crate"
    crate.angelo_physics.body, crate.angelo_physics.shape = "RIGID", "BOX"
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(16, 0, 0.5))
    lid = bpy.context.object
    lid.name, lid.parent = "CrateLid", crate
    lid.angelo_physics.shape = "SPHERE"
    bpy.ops.mesh.primitive_plane_add(size=2, location=(20, 0, 0))
    emitter = bpy.context.object
    emitter.name = "Emitter"
    bpy.ops.mesh.primitive_ico_sphere_add(radius=0.05, location=(20, 0, -3))
    dot = bpy.context.object
    dot.name = "Dot"
    emitter.modifiers.new("Particles", "PARTICLE_SYSTEM")
    settings = emitter.particle_systems[0].settings
    settings.count, settings.frame_start, settings.frame_end = 5, 1, 1
    settings.render_type, settings.instance_object = "OBJECT", dot
    bpy.ops.mesh.primitive_plane_add(size=1, location=(24, 0, 0))
    dupper = bpy.context.object
    dupper.name, dupper.instance_type = "Dupper", "VERTS"
    bpy.ops.mesh.primitive_cube_add(size=0.1, location=(24, 0, 0))
    dupli = bpy.context.object
    dupli.name, dupli.parent = "DupliCube", dupper
    for index in range(2):
        instancer = bpy.data.objects.new("KitCopy%d" % index, None)
        instancer.instance_type, instancer.instance_collection = "COLLECTION", kit
        instancer.location = (index * 3.0, 10, 0)
        scene.collection.objects.link(instancer)
    bpy.context.view_layer.update()
    return hair, rig


def main():
    import addon_utils
    if not addon_utils.check("angelo_agent")[1]:
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import engine_link_assets as assets, web_link_live as live
    hair, rig = build()
    server = SimpleNamespace(clients=lambda: {1: {}}, lagging=lambda client: False)
    link = SimpleNamespace(_state={"server": server},
                           _followed=lambda window: (bpy.context.scene, bpy.context.view_layer))
    live._clients = lambda link, found: [1]
    found = {"asset": "Shop", "number": 1, "params": {}}
    path = str(OUT / "live.glb")
    noted = lambda scene, depsgraph: live.note(depsgraph)  # noqa: E731 - as engine_link._on_depsgraph does.
    bpy.app.handlers.depsgraph_update_post.append(noted)
    version = [0]
    refused = []

    def frame():
        started = time.perf_counter()
        try:
            frames = live.live_frames(link, None, path, found, version[0])
        except RuntimeError as error:   # A refused frame: the page would stay as it was.
            refused.append(str(error))
            return None, 0.0
        took = (time.perf_counter() - started) * 1000.0
        if frames:
            version[0] += 1
        return (frames[0] if frames else None), took

    def nodes(frame_):
        document, _binary = live.read_glb(frame_[2])
        return document, {node["extras"][live.KEY]: node for node in document["nodes"]}

    first, _ = frame()
    document, keyed = nodes(first)
    check("first_frame_is_full", first[1]["kind"] == live.FULL)
    check("every_node_has_a_unique_key", len(keyed) == len(document["nodes"]))
    box = bpy.data.objects["Box042"]
    box_key = str(box.session_uid)
    check("box_keyed_by_session_uid", box_key in keyed)
    instanced = [key for key in keyed if ">" in key]
    check("collection_instances_keyed_apart", len(instanced) >= 2 and len(set(instanced)) == len(instanced),
          instanced)
    check("instanced_children_and_bones_keyed_apart",
          len([key for key in keyed if ">" in key and key.endswith("/Bone")]) == 2
          and len([key for key, node in keyed.items() if node.get("name") == "KitChild"]) == 2,
          sorted(key for key in keyed if ">" in key))
    bones = [key for key in keyed if "/" in key and ">" not in key]
    check("bones_keyed_by_armature_and_name", any(key.endswith("/Head") for key in bones), bones)
    check("nothing_changed_sends_nothing", frame()[0] is None)

    # One box's colour: that box alone.
    before = dict(live._session["digests"])
    box.data.materials[0].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (1, 0, 0, 1)
    bpy.context.view_layer.update()
    delta, took = frame()
    document, keyed = nodes(delta)
    content = [key for key, node in keyed.items() if node["extras"][live.ROLE] == live.CONTENT]
    check("colour_delta_holds_the_box_only", delta[1]["kind"] == live.DELTA and content == [box_key],
          {"kind": delta[1]["kind"], "content": content})
    after = live._session["digests"]
    check("only_its_digest_changed", {key for key in after if before.get(key) != after[key]} == {box_key}
          and set(after) == set(before))
    report["colour_delta"] = {"bytes": len(delta[2]), "nodes": len(keyed)}

    # Rename and reparent: the same key. Blender re-evaluates the whole scene for it, so the frame may be whole; the
    # page still changes only the nodes whose digest moved: the box and its new parent (its children changed).
    before = dict(live._session["digests"])
    box.name, box.parent = "Box042Renamed", bpy.data.objects["Shelf1"]
    bpy.context.view_layer.update()
    delta, _ = frame()
    document, keyed = nodes(delta)
    shelf_key = str(bpy.data.objects["Shelf1"].session_uid)
    check("rename_and_reparent_keep_the_key", box_key in keyed and keyed[box_key]["name"] == "Box042Renamed")
    after = live._session["digests"]
    moved = {key for key in after if before.get(key) != after[key]}
    emitted = str(bpy.data.objects["Emitter"].session_uid) + ">"   # Blender re-evaluates particles on such an edit.
    check("rename_moves_only_the_box", {key for key in moved if not key.startswith(emitted)} == {box_key},
          sorted(moved))   # A parent's digest holds no children.
    report["rename_frame"] = delta[1]["kind"]

    # The hair's geometry, more vertices: the hair, with its bones as parents and its skin bound to them.
    import bmesh
    mesh = bmesh.new()
    mesh.from_mesh(hair.data)
    bmesh.ops.subdivide_edges(mesh, edges=mesh.edges[:], cuts=1)
    mesh.to_mesh(hair.data)
    mesh.free()
    hair.vertex_groups["Head"].add(list(range(len(hair.data.vertices))), 1.0, "REPLACE")
    bpy.context.view_layer.update()
    delta, _ = frame()
    document, keyed = nodes(delta)
    hair_key = str(hair.session_uid)
    content = [key for key, node in keyed.items() if node["extras"][live.ROLE] == live.CONTENT]
    check("hair_delta_holds_the_hair", content == [hair_key], content)
    skin = document["skins"][keyed[hair_key]["skin"]] if "skin" in keyed.get(hair_key, {}) else None
    joint_keys = [document["nodes"][joint]["extras"][live.KEY] for joint in (skin or {}).get("joints", [])]
    check("hair_skin_binds_the_bones_as_parents", skin is not None and joint_keys and all(
        keyed[key]["extras"][live.ROLE] == live.PARENT for key in joint_keys), joint_keys)

    # An image the page already has goes as a stub.
    textured = bpy.data.objects["Box007"]
    textured.location.z += 1.0
    bpy.context.view_layer.update()
    delta, _ = frame()
    document, _ = nodes(delta)
    check("known_image_goes_as_a_stub", document.get("images") and all(
        (image.get("extras") or {}).get(live.STUB) for image in document["images"]), document.get("images"))

    # A deleted box is listed as removed.
    gone = bpy.data.objects["Box100"]
    gone_key = str(gone.session_uid)
    bpy.data.objects.remove(gone)
    bpy.context.view_layer.update()
    delta, _ = frame()   # Removing an object re-evaluates the scene: a delta lists it, a full frame lacks it.
    document, keyed = nodes(delta) if delta is not None else ({}, {})
    check("deleted_box_is_removed", delta is not None and (
        gone_key in delta[1].get("removed", []) if delta[1]["kind"] == live.DELTA else gone_key not in keyed),
          delta[1] if delta else None)
    report["delete_frame"] = delta[1]["kind"] if delta else None

    # A shape key slides further, twice (break-it N1: sparse values were never digested), then is renamed (N5).
    face = bpy.data.objects["Face"]
    face_key = str(face.session_uid)
    for step in range(3):
        before = dict(live._session["digests"])
        if step < 2:
            for point in face.data.shape_keys.key_blocks["Bulge"].data:
                point.co.x += 0.25
        else:
            face.data.shape_keys.key_blocks["Bulge"].name = "Swell"
        face.data.update()
        bpy.context.view_layer.update()
        produced, _ = frame()
        moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
        check("shape_key_step_%d_reaches_the_page" % step, produced is not None and face_key in moved,
              sorted(moved))
    # Two lights: changing one never moves the other's digest (N4).
    before = dict(live._session["digests"])
    bpy.data.objects["LightA"].data.energy *= 2
    bpy.context.view_layer.update()
    frame()
    moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
    check("one_light_moves_only_its_own_digest", moved == {str(bpy.data.objects["LightA"].session_uid)},
          sorted(moved))
    # Pixels set through the API reach no depsgraph update (N3): the next frame still carries them.
    image = bpy.data.images["Checker"]
    pixels = list(image.pixels)
    pixels[0:4] = [1.0, 0.0, 1.0, 1.0]
    image.pixels[:] = pixels
    image.update()
    before = dict(live._session["digests"])
    bpy.data.objects["Box003"].location.z += 0.1   # Any later change publishes.
    bpy.context.view_layer.update()
    frame()
    moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
    check("painted_image_reaches_the_page", str(bpy.data.objects["Box007"].session_uid) in moved, sorted(moved))

    # Round 3 scenes: frames are sent (never refused), and one wheel or the crate changes alone.
    for name in ("Wheel2", "Crate"):
        before = dict(live._session["digests"])
        bpy.data.objects[name].location.z += 0.2
        bpy.context.view_layer.update()
        produced, _ = frame()
        moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
        own = str(bpy.data.objects[name].session_uid)
        check("%s_change_is_sent_alone" % name.lower(), produced is not None and own in moved
              and all(key == own or key.startswith(own + "#") for key in moved), sorted(moved))
    # Round 4: an instancer's change carries its copies; a lowered count removes them; a child's collider moves with
    # it; a changed shape counts without any update; a second API edit of a dirty image counts.
    emitter = bpy.data.objects["Emitter"]
    copies = lambda: {key for key in live._session["digests"] if key.startswith(str(emitter.session_uid) + ">")}  # noqa: E731
    for step, change in (("seed", lambda: setattr(emitter.particle_systems[0], "seed", 7)),
                         ("count", lambda: setattr(emitter.particle_systems[0].settings, "count", 2))):
        before, had = dict(live._session["digests"]), copies()
        change()
        bpy.context.view_layer.update()
        produced, _ = frame()
        moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
        if step == "seed":
            check("particle_seed_moves_its_copies", had and moved & had, sorted(moved))
        else:
            gone = had - copies()
            check("particle_count_removes_copies", len(gone) == len(had) - 2 and produced is not None and (
                produced[1]["kind"] == live.FULL or gone <= set(produced[1].get("removed", []))),
                {"gone": sorted(gone), "frame": produced[1] if produced else None})
    lid_key = str(bpy.data.objects["CrateLid"].session_uid)
    for step, change in (("move", lambda: setattr(bpy.data.objects["CrateLid"].location, "z", 0.8)),
                         ("shape", lambda: setattr(bpy.data.objects["CrateLid"].angelo_physics, "shape", "BOX"))):
        before = dict(live._session["digests"])
        change()
        bpy.data.objects["Box001"].location.z += 0.01   # A frame follows any edit (physics runs no update).
        bpy.context.view_layer.update()
        frame()
        moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
        check("lid_%s_moves_its_collider" % step, any(key.startswith(lid_key + "#") for key in moved), sorted(moved))
    image = bpy.data.images["Checker"]
    pixels = list(image.pixels)
    pixels[4:8] = [0.0, 1.0, 0.0, 1.0]
    image.pixels[:] = pixels   # Still dirty from the first edit: no new edge.
    before = dict(live._session["digests"])
    bpy.data.objects["Box004"].location.z += 0.1
    bpy.context.view_layer.update()
    frame()
    moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
    check("second_image_edit_reaches_the_page", str(bpy.data.objects["Box007"].session_uid) in moved, sorted(moved))
    # A large image's every pixel write counts (break-it round 5, M3): Angelo's native pixel revision.
    big = bpy.data.images.new("Big", 2048, 2048)
    material = bpy.data.objects["Box009"].data.materials[0]
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = big
    material.node_tree.links.new(texture.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs[0])
    bpy.context.view_layer.update()
    frame()
    check("image_has_a_native_pixel_revision", hasattr(big, "angelo_pixel_revision"))
    for edit in range(2):
        revision = getattr(big, "angelo_pixel_revision", 0)
        import numpy
        pixels = numpy.full(2048 * 2048 * 4, 0.5, dtype=numpy.float32)
        pixels[edit * 4:edit * 4 + 4] = (1.0, 0.0, 0.0, 1.0)
        big.pixels.foreach_set(pixels)
        check("pixel_write_%d_bumps_the_revision" % edit, getattr(big, "angelo_pixel_revision", 0) != revision)
        before = dict(live._session["digests"])
        bpy.data.objects["Box005"].location.z += 0.1
        bpy.context.view_layer.update()
        frame()
        moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
        check("big_image_edit_%d_reaches_the_page" % edit, str(bpy.data.objects["Box009"].session_uid) in moved,
              sorted(moved))
    # Round 5: a vertex-instancing object's change carries its copies; a body's change moves its child's collider.
    dupper = bpy.data.objects["Dupper"]
    scope = str(dupper.session_uid) + ">"
    had = {key for key in live._session["digests"] if key.startswith(scope)}
    import bmesh
    mesh = bmesh.new()
    mesh.from_mesh(dupper.data)
    mesh.verts.new((0.3, 0.3, 0.0))
    mesh.to_mesh(dupper.data)
    mesh.free()
    bpy.context.view_layer.update()
    frame()
    now = {key for key in live._session["digests"] if key.startswith(scope)}
    check("vertex_instancing_sends_its_new_copy", len(now) == len(had) + 1, {"had": len(had), "now": len(now)})
    before = dict(live._session["digests"])
    bpy.data.objects["Crate"].angelo_physics.body = "TRIGGER"
    bpy.data.objects["Box002"].location.z += 0.01   # A frame follows any edit (physics runs no update).
    bpy.context.view_layer.update()
    frame()
    moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
    check("body_change_moves_its_childs_collider", any(key.startswith(lid_key + "#") for key in moved), sorted(moved))
    # Round 6: an object moved between two parents at one place; a trigger body's shape child changing.
    for name in ("GroupA", "GroupB"):
        bpy.ops.object.empty_add(location=(30, 0, 0))
        bpy.context.object.name = name
    kid = bpy.data.objects["Box011"]
    kid.parent = bpy.data.objects["GroupA"]
    kid.matrix_parent_inverse = bpy.data.objects["GroupA"].matrix_world.inverted()
    bpy.context.view_layer.update()
    frame()
    before = dict(live._session["digests"])
    kid.parent = bpy.data.objects["GroupB"]
    kid.matrix_parent_inverse = bpy.data.objects["GroupB"].matrix_world.inverted()
    bpy.context.view_layer.update()
    frame()
    moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
    check("coincident_reparent_reaches_the_page", str(kid.session_uid) in moved, sorted(moved))
    before = dict(live._session["digests"])
    bpy.data.objects["CrateLid"].angelo_physics.shape = "NONE"
    bpy.data.objects["Box012"].location.z += 0.01   # A frame follows any edit (physics runs no update).
    bpy.context.view_layer.update()
    frame()
    moved = {key for key in live._session["digests"] if before.get(key) != live._session["digests"][key]}
    check("trigger_shape_change_moves_its_body", str(bpy.data.objects["Crate"].session_uid) in moved, sorted(moved))
    # A still scene after a delta is checked whole once: nothing to send when nothing was missed.
    live._session["verify_due"] = 0.0
    checked = frame()[0]
    check("still_check_sends_nothing_when_right", checked is None,
          checked and sorted(node.get("name") for node in nodes(checked)[0]["nodes"])[:20])

    # The oracle in miniature: what the deltas left the page with is what a whole frame says now.
    before = dict(live._session["digests"])
    live.unsure()
    frame_last = [frame()[0]]
    after = live._session["digests"]
    names = {node["extras"][live.KEY]: node.get("name") for node in nodes(frame_last[0])[0]["nodes"]} \
        if frame_last else {}
    check("deltas_agree_with_a_whole_frame", before == after,
          {"differ": sorted("%s %s" % (key, names.get(key)) for key in set(before) | set(after)
                            if before.get(key) != after.get(key))[:20]})
    check("no_frame_was_refused", not refused, refused)

    # Timings: a whole frame against a one-object delta, 2 untimed and 15 timed each.
    target = bpy.data.objects["Box055"].data.materials[0].node_tree.nodes["Principled BSDF"].inputs["Base Color"]
    for kind in ("full", "delta"):
        times, sizes = [], []
        for round_ in range(17):
            if kind == "full":
                live.unsure()
            else:
                target.default_value = (round_ / 17.0, 0.5, 0.5, 1)
                bpy.context.view_layer.update()
            produced, took = frame()
            if round_ >= 2 and produced is not None:
                times.append(took)
                sizes.append(len(produced[2]))
        times.sort()
        report["timings"][kind] = {"ms_median": round(statistics.median(times), 1),
                                   "ms_p75": round(times[int(len(times) * 0.75)], 1),
                                   "ms_range": [round(times[0], 1), round(times[-1], 1)],
                                   "bytes_median": int(statistics.median(sizes)), "runs": len(times)}
    del assets


try:
    main()
except Exception:
    report["traceback"] = traceback.format_exc()
ok = bool("traceback" not in report and report["checks"] and all(report["checks"].values()))
report["success"] = ok
(OUT / "web-link-delta.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("web link delta:", "PASS" if ok else "FAIL", json.dumps(report["checks"]))
sys.exit(0 if ok else 1)
