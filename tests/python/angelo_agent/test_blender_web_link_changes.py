# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the web link's live frames count as changed, with Blender's real glTF exporter and Angelo's native pixel
revision (``web_link_live``; doc/angelo/web_link.rst, "Live frames"; break-it round 8). Run inside Angelo without a
window:

    Angelo --background --factory-startup --python tests/python/angelo_agent/test_blender_web_link_changes.py -- --out DIR

The link is driven as ``engine_link`` drives it: the depsgraph handler decides whether an update matters and notes it,
and while nothing is pending the tick polls the images (``pixels_changed``). After each edit the page model (what
the frames sent, by key and digest) must equal a fresh whole export, with no other edit to carry it and no still-scene
check. Edits: pixels written through the API (slice, ``foreach_set``, ``update``, ``scale``) and unsaved pixels
dropped (``buffers_free``), also while a still-scene check waits; data whose users only the depsgraph knows
(``users_scene``); files rewritten on disk: a UDIM tile's, a sequence's frame, a file replaced by a rename keeping its
size and time (``files_scene``); a particle emitter added mid-session, whose copies must settle before the frame
goes. Each must start a publish (``sent`` not None) and leave the page right. Writes ``web-link-changes.json`` to DIR (``ANGELO_QA_DIR`` under run_native_suites.py),
whose ``success`` is the verdict.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(ARGS[ARGS.index("--out") + 1]) if "--out" in ARGS else Path(
    os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-web-link-changes-qa"))
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "cases": {}}


def check(name, value, detail=None):
    report["checks"][name] = bool(value)
    if not value and detail is not None:
        report.setdefault("details", {})[name] = detail


def cube(name, location, collection=None):
    import bmesh
    mesh = bpy.data.meshes.new(name)
    shape = bmesh.new()
    bmesh.ops.create_cube(shape, size=0.5)
    shape.to_mesh(mesh)
    shape.free()
    mesh.uv_layers.new()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    (collection or bpy.context.scene.collection).objects.link(obj)
    material = bpy.data.materials.new(name + "Paint")
    material.use_nodes = True
    mesh.materials.append(material)
    return obj


def textured(obj, image):
    material = obj.data.materials[0]
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    material.node_tree.links.new(texture.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs[0])


def geometry_nodes(obj, name, build):
    """Give ``obj`` a geometry-nodes modifier whose tree ``build(tree, group_in, group_out)`` links."""
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    build(tree, tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput"))
    obj.modifiers.new(name, "NODES").node_group = tree


def file_image(path, colour, size=16):
    """Write a BMP (uncompressed: the same size for any colour) of ``colour`` at ``path``."""
    made = bpy.data.images.new("Writer", size, size)
    made.generated_color = colour
    made.filepath_raw, made.file_format = str(path), "BMP"
    made.save()
    bpy.data.images.remove(made)


def files_scene():
    """Images Blender reads from files: a UDIM image of two tiles, a two-frame sequence read at frame 2, and a single
    file (break-it round 9, M2 and L2)."""
    for tile in (1001, 1002):
        file_image(OUT / ("tiles.%d.bmp" % tile), (0.2, 0.3, 0.4, 1))
    tiles = bpy.data.images.load(str(OUT / "tiles.1001.bmp"))
    tiles.source = "TILED"
    tiles.filepath = str(OUT / "tiles.<UDIM>.bmp")
    textured(cube("TiledUser", (0, -4, 0)), tiles)
    for frame in (1, 2):
        file_image(OUT / ("shot.%04d.bmp" % frame), (0.5, 0.5, 0.1 * frame, 1))
    shot = bpy.data.images.load(str(OUT / "shot.0001.bmp"))
    shot.source = "SEQUENCE"
    user = cube("ShotUser", (2, -4, 0))
    textured(user, shot)
    node = [node for node in user.data.materials[0].node_tree.nodes if node.type == "TEX_IMAGE"][0]
    node.image_user.frame_duration, node.image_user.frame_start = 2, 1
    file_image(OUT / "chair.bmp", (0.9, 0.9, 0.9, 1))
    textured(cube("ChairUser", (4, -4, 0)), bpy.data.images.load(str(OUT / "chair.bmp")))
    bpy.context.scene.frame_set(2)


def add_emitter():
    """A particle system added mid-session, rendering an object of a collection not in the scene: its copies settle
    over more than one evaluation (break-it rounds 11 and 12)."""
    hidden = bpy.data.collections.new("Hidden")
    copy = cube("Seed", (0, 0, 0), hidden)
    emitter = cube("Emitter", (6, -4, 0))
    settings = emitter.modifiers.new("Particles", "PARTICLE_SYSTEM").particle_system.settings
    settings.count, settings.frame_start, settings.frame_end = 4, 1, 1
    settings.render_type, settings.instance_object = "OBJECT", copy


def replace_keeping_time(path, colour):
    """Replace ``path`` by a rename with a same-size file carrying the old modification time (an atomic save)."""
    old = os.stat(path)
    file_image(Path(str(path) + ".next.bmp"), colour)
    os.utime(str(path) + ".next.bmp", ns=(old.st_atime_ns, old.st_mtime_ns))
    os.replace(str(path) + ".next.bmp", path)


def users_scene():
    """Objects whose changed data only the depsgraph connects to them (break-it round 8, M2): a material set by
    geometry nodes, one a Boolean transfers from its cutter, a collection instanced twice but not in the scene (its
    object with an image and an object-linked material), and a collection geometry nodes instance."""
    scene = bpy.context.scene
    set_material = bpy.data.materials.new("SetByNodes")
    set_material.use_nodes = True

    def set_by_nodes(tree, group_in, group_out):
        node = tree.nodes.new("GeometryNodeSetMaterial")
        node.inputs["Material"].default_value = set_material
        tree.links.new(group_in.outputs[0], node.inputs["Geometry"])
        tree.links.new(node.outputs[0], group_out.inputs[0])
    geometry_nodes(cube("NodesPainted", (0, 4, 0)), "SetMaterial", set_by_nodes)
    base, cutter = cube("Carved", (4, 4, 0)), cube("Cutter", (4.2, 4.2, 0.2))
    carve = base.modifiers.new("Carve", "BOOLEAN")
    carve.object, carve.material_mode = cutter, "TRANSFER"
    cutter.hide_set(True)
    kit = bpy.data.collections.new("Kit")   # Instanced, never linked to the scene.
    part = cube("KitPart", (0, 0, 0), kit)
    picture = bpy.data.images.new("KitPicture", 16, 16)
    textured(part, picture)
    part.material_slots[0].link = "OBJECT"
    part.material_slots[0].material = bpy.data.materials.new("KitOwn")
    part.material_slots[0].material.use_nodes = True
    part.data.materials[0].use_nodes = True
    textured(part, picture)   # The image is on the object's own material too.
    for index in range(2):
        instancer = bpy.data.objects.new("KitCopy%d" % index, None)
        instancer.instance_type, instancer.instance_collection = "COLLECTION", kit
        instancer.location = (index * 2.0, 8, 0)
        scene.collection.objects.link(instancer)
    crate = bpy.data.collections.new("Crates")
    stack = cube("CratePart", (0, 0, 0), crate)

    def collection_on_points(tree, group_in, group_out):
        info, place = tree.nodes.new("GeometryNodeCollectionInfo"), tree.nodes.new("GeometryNodeInstanceOnPoints")
        info.inputs["Collection"].default_value = crate
        tree.links.new(group_in.outputs[0], place.inputs["Points"])
        tree.links.new(info.outputs[0], place.inputs["Instance"])
        tree.links.new(place.outputs[0], group_out.inputs[0])
    geometry_nodes(cube("Shelf", (8, 4, 0)), "Shelf", collection_on_points)
    return {"set_material": set_material, "cutter": cutter, "picture": picture, "own": part.material_slots[0].material,
            "crate": stack.data.materials[0]}


def colour(material, value):
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = value


class Link:
    """``engine_link``'s scheduling around ``web_link_live``, with a page that applies frames by key and digest."""

    def __init__(self, live, scene_changes):
        self.live, self.changes = live, scene_changes
        self.state = {"server": SimpleNamespace(clients=lambda: {1: {}}, lagging=lambda client: False),
                      "last_ms": 0.0, "dirty_at": None}
        self.link = SimpleNamespace(_state=self.state,
                                    _followed=lambda window: (bpy.context.scene, bpy.context.view_layer))
        live._clients = lambda link, found: [1]
        self.found, self.path = {"asset": "Shop", "number": 1, "params": {}}, str(OUT / "live.glb")
        self.version, self.page, self.key, self.exporting = 0, {}, None, False
        bpy.app.handlers.depsgraph_update_post.append(self.on_depsgraph)

    def on_depsgraph(self, scene, depsgraph):   # engine_link._on_depsgraph
        if self.exporting:
            return
        changed, self.key = self.changes.matters(depsgraph, scene, bpy.context.view_layer, self.key)
        if changed:
            self.state["dirty_at"] = time.monotonic()
            self.live.note(depsgraph)

    def tick(self):
        """``engine_link._tick`` with the stillness reached: while nothing is pending it asks ``web_link_live.due``
        (a build before it polled pixels only); a publish waits until ``dirty_at``, which may be in the future."""
        if self.state["dirty_at"] is None:
            self.live._session["poll_at"] = 0.0
            due = getattr(self.live, "due", None)
            if due is not None:
                self.state["dirty_at"] = due(bpy.context.scene)
            elif getattr(self.live, "pixels_changed", lambda: False)():
                self.state["dirty_at"] = time.monotonic()
        if self.state["dirty_at"] is None or time.monotonic() < self.state["dirty_at"]:
            return None
        self.state["dirty_at"] = None
        self.exporting = True
        try:
            frames = self.live.live_frames(self.link, None, self.path, self.found, self.version)
        finally:
            self.exporting = False
            self.key = self.changes.scene_key(bpy.context.scene, bpy.context.view_layer)
        if not frames:
            return []
        self.version += 1
        _clients, fields, body = frames[0]
        document, _binary = self.live.read_glb(body)
        content = {node["extras"][self.live.KEY]: node["extras"].get(self.live.DIGEST)
                   for node in document.get("nodes", []) if node["extras"][self.live.ROLE] == self.live.CONTENT}
        if fields["kind"] == self.live.FULL:
            self.page.clear()
        self.page.update(content)
        for key in fields.get("removed", []):
            self.page.pop(key, None)
        return sorted(node.get("name", "") for node in document.get("nodes", [])
                      if node["extras"][self.live.KEY] in content)

    def truth(self):
        self.exporting = True
        try:
            document, binary, _present = self.live._export_glb(None, self.path + ".whole.glb", self.found, None,
                                                               frozenset())
        finally:
            self.exporting = False
        return self.live.node_digests(document, binary)

    def stale(self):
        """The keys whose digest the page lacks or has wrong, against a whole export now."""
        truth = self.truth()
        return sorted(key for key in truth if self.page.get(key) != truth[key])


def main():
    import addon_utils
    if not addon_utils.check("angelo_agent")[1]:
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import scene_changes, web_link_live as live
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for index in range(6):
        cube("Filler%d" % index, (index, 20, 0))
    painted = bpy.data.images.new("Painted", 32, 32)
    textured(cube("PaintedUser", (0, 0, 0)), painted)
    used = users_scene()
    files_scene()
    bpy.context.view_layer.update()
    link = Link(live, scene_changes)
    check("native_pixel_revision", hasattr(painted, "angelo_pixel_revision"))
    link.state["dirty_at"] = 0.0
    link.tick()
    check("first_frame_matches", not link.stale(), link.stale())
    import numpy

    def write(colour):
        painted.pixels.foreach_set(numpy.tile(numpy.array(colour, dtype=numpy.float32),
                                              painted.size[0] * painted.size[1]))

    cases = (
        ("pixels_slice", lambda: painted.pixels.__setitem__(slice(0, 4), [1.0, 0.0, 1.0, 1.0])),
        ("pixels_foreach_set", lambda: write((0.9, 0.1, 0.1, 1.0))),
        ("update", lambda: (write((0.1, 0.9, 0.1, 1.0)), painted.update())),
        ("scale", lambda: painted.scale(16, 16)),
        ("buffers_free", painted.buffers_free),   # Drops the unsaved pixels the page was sent first.
        ("material_set_by_geometry_nodes", lambda: colour(used["set_material"], (0.1, 0.8, 0.2, 1))),
        ("material_a_boolean_transfers", lambda: colour(used["cutter"].data.materials[0], (0.9, 0.5, 0.1, 1))),
        ("image_of_an_instanced_object_outside_the_scene", lambda: setattr(used["picture"], "generated_color",
                                                                            (1, 0, 0, 1))),
        ("object_linked_material_of_an_instanced_object", lambda: colour(used["own"], (0.2, 0.2, 0.9, 1))),
        ("material_of_a_collection_geometry_nodes_instance", lambda: colour(used["crate"], (0.7, 0.1, 0.7, 1))),
        ("pixels_while_a_check_waits", lambda: (live._session.update(verify_due=time.monotonic() + 50.0),
                                                 write((0.3, 0.6, 0.9, 1.0)))),
        ("udim_tile_file_rewritten", lambda: file_image(OUT / "tiles.1002.bmp", (0.9, 0.1, 0.1, 1))),
        ("sequence_frame_file_rewritten", lambda: file_image(OUT / "shot.0002.bmp", (0.1, 0.9, 0.1, 1))),
        ("file_replaced_by_a_rename_keeping_its_time", lambda: replace_keeping_time(OUT / "chair.bmp",
                                                                                    (0.1, 0.1, 0.9, 1))),
        ("particle_emitter_added_then_a_page_connects", lambda: (add_emitter(), link.found.update(
            number=link.found["number"] + 1))),   # A new link: the page's first, whole frame holds unsettled copies.
    )
    for name, edit in cases:
        if name == "buffers_free":   # The page first gets unsaved pixels by way of another edit.
            write((0.2, 0.2, 0.9, 1.0))
            bpy.data.objects["Filler0"].location.z += 0.25
            bpy.context.view_layer.update()
            link.tick()
            check("unsaved_pixels_reach_the_page_with_another_edit", not link.stale(), link.stale())
        edit()
        bpy.context.view_layer.update()
        sent = link.tick()
        stale = link.stale()
        report["cases"][name] = {"sent": sent, "stale": stale}
        check("%s_reaches_the_page_on_its_own" % name, not stale and sent is not None, report["cases"][name])
    check("an_unchanged_scene_sends_nothing", not link.tick())


try:
    main()
except Exception:
    report["traceback"] = traceback.format_exc()
ok = bool("traceback" not in report and report["checks"] and all(report["checks"].values()))
report["success"] = ok
(OUT / "web-link-changes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("web link changes:", "PASS" if ok else "FAIL", json.dumps(report["checks"]))
sys.exit(0 if ok else 1)
