# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web link's live frames hold only what changed (``web_link_live``; doc/angelo/web_link.rst, "Live frames").
Without Blender: stand-in exporter trees and .glb documents.

Covered: a node's digest moves with its mesh data, material, images and transform and with nothing else; an image
every page has goes as a stub, by its bytes' digest; the export keeps only changed objects, their ancestors as
empties, and the armature and bones a changed skin needs, and records what a full export would hold; keys are unique
inside collection instances and stable. The frame decisions are in ``test_native_web_link_frames``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import web_link_live as live  # noqa: E402


def document_with(nodes, colors=None, positions=None, images=()):
    """A .glb (document, binary) with one mesh per node: ``positions[i]`` bytes and a material of ``colors[i]``."""
    colors = colors or {}
    positions = positions or {}
    binary, views, accessors, meshes, materials, out = b"", [], [], [], [], []
    for index, (key, name) in enumerate(nodes):
        data = positions.get(key, struct.pack("<9f", *range(9)))
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        binary += data
        accessors.append({"bufferView": len(views) - 1, "componentType": 5126, "count": 3, "type": "VEC3"})
        materials.append({"pbrMetallicRoughness": {"baseColorFactor": colors.get(key, [1, 1, 1, 1])}})
        meshes.append({"primitives": [{"attributes": {"POSITION": len(accessors) - 1}, "material": index}]})
        out.append({"name": name, "mesh": index, "extras": {live.KEY: key, live.ROLE: live.CONTENT}})
    document = {"asset": {"version": "2.0"}, "nodes": out, "meshes": meshes, "materials": materials,
                "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(binary)}],
                "images": [{"extras": {live.DIGEST: digest}} for digest in images]}
    return document, binary


class DigestTests(unittest.TestCase):
    def digests(self, **changes):
        nodes = [("1", "Chair"), ("2", "Table")]
        return live.node_digests(*document_with(nodes, **changes))

    def test_a_node_moves_with_its_own_data_only(self):
        base = self.digests()
        moved = self.digests(positions={"1": struct.pack("<9f", *range(1, 10))})
        self.assertNotEqual(base["1"], moved["1"])
        self.assertEqual(base["2"], moved["2"], "the other object's digest stays")
        recolored = self.digests(colors={"2": [1, 0, 0, 1]})
        self.assertEqual(base["1"], recolored["1"])
        self.assertNotEqual(base["2"], recolored["2"])

    def test_a_textures_digest_follows_its_image_not_its_index(self):
        document, binary = document_with([("1", "Chair")], images=("aaa", "bbb"))
        document["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
        document["textures"] = [{"source": 0}]
        first = live.node_digests(document, binary)["1"]
        document["images"][0]["extras"][live.DIGEST] = "ccc"
        self.assertNotEqual(first, live.node_digests(document, binary)["1"])

    def test_sparse_morph_values_and_target_names_count(self):
        """Break-it N1, N5: shape keys are sparse accessors (no buffer view of their own), and three.js names morph
        targets from the mesh's extras."""
        def digest(values=(1.0, 2.0, 3.0), names=("Bulge",)):
            document, binary = document_with([("1", "Face")])
            sparse_indices, sparse_values = struct.pack("<I", 0), struct.pack("<3f", *values)
            start = len(binary)
            binary += sparse_indices + sparse_values
            document["bufferViews"] += [{"buffer": 0, "byteOffset": start, "byteLength": 4},
                                        {"buffer": 0, "byteOffset": start + 4, "byteLength": 12}]
            document["accessors"].append({"componentType": 5126, "count": 3, "type": "VEC3", "sparse": {
                "count": 1, "indices": {"bufferView": 1, "componentType": 5125}, "values": {"bufferView": 2}}})
            document["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 1}]
            document["meshes"][0]["extras"] = {"targetNames": list(names)}
            return live.node_digests(document, binary)["1"]
        self.assertNotEqual(digest(), digest(values=(1.0, 2.0, 4.0)))
        self.assertNotEqual(digest(), digest(names=("Swell",)))

    def test_a_digest_never_moves_with_an_index(self):
        """Break-it N4: another light or accessor coming first in the file moved an untouched node's digest."""
        def digest(lights, light_index, padding):
            document, binary = document_with([("1", "Lamp")])
            binary = b"\x00" * padding + binary
            document["bufferViews"][0]["byteOffset"] += padding
            document["extensions"] = {"KHR_lights_punctual": {"lights": lights}}
            document["nodes"][0]["extensions"] = {"KHR_lights_punctual": {"light": light_index}}
            return live.node_digests(document, binary)["1"]
        mine = {"type": "point", "intensity": 5}
        self.assertEqual(digest([mine], 0, 0), digest([{"type": "spot", "intensity": 9}, mine], 1, 64))
        self.assertNotEqual(digest([mine], 0, 0), digest([dict(mine, intensity=6)], 0, 0))

    def test_physics_shape_meshes_count_and_their_place_never_does(self):
        """Break-it round 3, M3: a convex shape's mesh index was hashed raw, so its vertices didn't count and its
        place in the file did."""
        def digest(vertices, before=0):
            document, binary = document_with([("1", "Wall")], positions={"1": struct.pack("<9f", *vertices)})
            mesh = document["meshes"][0]
            document["meshes"] = [{"primitives": []}] * before + [mesh]
            document["nodes"][0].pop("mesh")
            document["extensions"] = {"OMI_physics_shape": {"shapes": [{"type": "convex",
                                                                         "convex": {"mesh": before}}]}}
            document["nodes"][0]["extensions"] = {"OMI_physics_body": {"collider": {"shape": 0}}}
            return live.node_digests(document, binary)["1"]
        base = tuple(range(9))
        self.assertEqual(digest(base), digest(base, before=3))
        self.assertNotEqual(digest(base), digest(tuple(range(1, 10))))

    def test_an_element_that_names_itself_stops(self):
        document, binary = document_with([("1", "Odd")])
        document["extensions"] = {"OMI_physics_shape": {"shapes": [{"type": "x"}]}}
        document["extensions"]["KHR_lights_punctual"] = {"lights": [{"light": 0}]}
        document["nodes"][0]["extensions"] = {"KHR_lights_punctual": {"light": 0}}
        self.assertIn("1", live.node_digests(document, binary))

    def test_nodes_written_after_the_hooks_get_their_own_keys(self):
        """Break-it round 3, H1: a physics visual or collider has no key; particle copies repeat their object's."""
        document = {"scene": 0, "scenes": [{"nodes": [0, 3]}], "nodes": [
            {"name": "Body", "children": [1, 2], "extras": {live.KEY: "5", live.ROLE: live.CONTENT}},
            {"name": "Body"}, {"name": "BodyCollider"},
            {"name": "Emitter", "children": [4, 5], "extras": {live.KEY: "7", live.ROLE: live.PARENT}},
            {"name": "Copy", "extras": {live.KEY: "9", live.ROLE: live.CONTENT}},
            {"name": "Copy", "extras": {live.KEY: "9", live.ROLE: live.CONTENT}}]}
        owners = {"5": 5, "9": 9}
        live.complete_keys(document, owners)
        keys = [node["extras"][live.KEY] for node in document["nodes"]]
        self.assertEqual(keys, ["5", "5#0", "5#1", "7", "9", "9~1"])
        self.assertEqual(document["nodes"][1]["extras"][live.ROLE], live.CONTENT, "its parent's role")
        self.assertEqual((owners["5#0"], owners["9~1"]), ({5}, 9))

    def test_a_collider_belongs_to_the_object_whose_shape_it_is(self):
        """Break-it round 4, M1: a child's collider hangs under the body, so it took the body's role and owner."""
        document = {"scene": 0, "scenes": [{"nodes": [0]}], "nodes": [
            {"name": "Crate", "children": [1, 2], "extras": {live.KEY: "5", live.ROLE: live.PARENT}},
            {"name": "Lid", "extras": {live.KEY: "6", live.ROLE: live.CONTENT}},
            {"name": "LidCollider", "extras": {"angelo_from": "6"}}]}
        owners = {"5": {5}, "6": {6}}
        live.complete_keys(document, owners)
        collider = document["nodes"][2]["extras"]
        self.assertEqual((collider[live.KEY], collider[live.ROLE]), ("6#0", live.CONTENT))
        self.assertNotIn("angelo_from", collider, "the page never sees the marker")
        self.assertEqual(owners["6#0"], {5, 6}, "its shape's object and its body both change it (round 5, M2)")

    def test_physics_settings_count_without_any_update(self):
        """Break-it round 4, M1: game physics settings run no Python, so a changed shape reached no update."""
        stored = {6: {"shape": 2}}
        objects = [SimpleNamespace(session_uid=6), SimpleNamespace(session_uid=7)]
        with mock.patch.object(live, "_physics_stamp", lambda obj: repr(stored.get(obj.session_uid)) if
                               obj.session_uid in stored else None):
            live.reset()
            self.assertEqual(live.physics_changes(objects), {6}, "settings appearing count")
            self.assertEqual(live.physics_changes(objects), set())
            stored[6] = {"shape": 1}
            self.assertEqual(live.physics_changes(objects), {6})
            del stored[6]
            self.assertEqual(live.physics_changes(objects), {6}, "settings cleared count too")
            self.assertEqual(live.physics_changes(objects), set())

    def test_a_parents_digest_never_depends_on_which_children_a_frame_holds(self):
        """Break-it round 5, M1: a delta holds only some children, so their keys made its digest differ from the
        next full frame's."""
        document, binary = document_with([("1", "Parent"), ("2", "Child")])
        alone = live.node_digests(document, binary)["1"]
        document["nodes"][0]["children"] = [1]
        self.assertEqual(live.node_digests(document, binary)["1"], alone)

    def test_a_rotation_and_its_negation_are_one_digest(self):
        """Break-it round 5: the exporter writes a particle copy's quaternion with either sign; float noise too."""
        document, binary = document_with([("1", "Dot")])
        document["nodes"][0]["rotation"] = [0.0, 0.70710678, 0.0, 0.70710678]
        first = live.node_digests(document, binary)["1"]
        document["nodes"][0]["rotation"] = [-0.0, -0.70710678, -0.0, -0.707106780000001]
        self.assertEqual(live.node_digests(document, binary)["1"], first)
        document["nodes"][0]["rotation"] = [0.0, 0.0, 0.70710678, 0.70710678]
        self.assertNotEqual(live.node_digests(document, binary)["1"], first, "another rotation is another digest")

    def test_a_nodes_parent_is_in_its_digest(self):
        """Break-it round 6, H1: an object moved between two parents at one place changes no transform."""
        document, binary = document_with([("1", "P1"), ("2", "P2"), ("3", "Kid")])
        document["nodes"][0]["children"] = [2]
        first = live.node_digests(document, binary)["3"]
        document["nodes"][0]["children"], document["nodes"][1]["children"] = [], [2]
        self.assertNotEqual(live.node_digests(document, binary)["3"], first)

    def test_parent_nodes_carry_no_digest(self):
        document, binary = document_with([("1", "Chair"), ("2", "Room")])
        document["nodes"][1]["extras"][live.ROLE] = live.PARENT
        self.assertEqual(set(live.node_digests(document, binary)), {"1"})

    def test_a_glb_round_trips_with_digests_stamped(self):
        document, binary = document_with([("1", "Chair")])
        digests = live.node_digests(document, binary)
        live.stamp_digests(document, digests)
        again, data = live.read_glb(live.write_glb(document, binary))
        self.assertEqual(data, binary)
        self.assertEqual(again["nodes"][0]["extras"][live.DIGEST], digests["1"])


class ImageTests(unittest.TestCase):
    def setUp(self):
        live._export.update(active=True, known_images=frozenset())
        self.addCleanup(live._export.update, active=False, known_images=frozenset())

    def image(self, data=b"\x89PNG pixels"):
        return SimpleNamespace(buffer_view=SimpleNamespace(data=data), extras=None, mime_type="image/jpeg")

    def test_every_image_is_named_by_its_bytes(self):
        found = self.image()
        live.image(found)
        self.assertEqual(len(found.extras[live.DIGEST]), 64)
        self.assertNotIn(live.STUB, found.extras)

    def test_an_image_every_page_has_goes_as_a_stub(self):
        first = self.image()
        live.image(first)
        live._export["known_images"] = frozenset({first.extras[live.DIGEST]})
        again = self.image()
        live.image(again)
        self.assertEqual(again.buffer_view.data, live.stub_png(first.extras[live.DIGEST]))
        self.assertEqual((again.mime_type, again.extras[live.STUB]), ("image/png", True))
        self.assertEqual(again.extras[live.DIGEST], first.extras[live.DIGEST], "the stub names the real image")

    def test_a_stub_is_a_whole_picture_of_its_own(self):
        """The npm side's e2e: the first stub's pixels inflated to 4 bytes (a 1 x 1 RGBA row is 5), so browsers
        refused it; and equal stubs share one buffer view, which three.js decodes once for all of them."""
        import zlib
        first, second = live.stub_png("a" * 64), live.stub_png("b" * 64)
        self.assertNotEqual(first, second)
        index = first.index(b"IDAT")
        length = int.from_bytes(first[index - 4:index], "big")
        self.assertEqual(zlib.decompress(first[index + 4:index + 4 + length]), b"\x00" * 5)
        offset = 8
        while offset < len(first):   # Every chunk's CRC holds.
            size = int.from_bytes(first[offset:offset + 4], "big")
            body = first[offset + 4:offset + 8 + size]
            self.assertEqual(int.from_bytes(first[offset + 8 + size:offset + 12 + size], "big"),
                             zlib.crc32(body) & 0xFFFFFFFF)
            offset += 12 + size

    def test_other_exports_are_untouched(self):
        live._export["active"] = False
        found = self.image()
        live.image(found)
        self.assertIsNone(found.extras)


class VNode:
    OBJECT, ARMATURE, BONE, INSTANCE, CHILDREN_IS_IN_COLLECTION = 1, 2, 3, 7, 91

    def __init__(self, uuid, uid, parent=None, kind=1, bone=None, armature=None, modifiers=(), instancer=False):
        self.uuid, self.parent_uuid, self.blender_type = uuid, parent, kind
        self.blender_object = SimpleNamespace(session_uid=uid, modifiers=list(modifiers), is_instancer=instancer,
                                              parent=None) if uid else None
        self.blender_bone = SimpleNamespace(name=bone) if bone else None
        self.armature, self.leaf_reference = armature, None
        self.keep_tag, self.force_as_empty, self.children_type, self.children = True, False, {}, []
        self.node = SimpleNamespace(extras=None)


def tree(*nodes):
    return SimpleNamespace(nodes={node.uuid: node for node in nodes})


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(live._export.update, active=False, content=None)

    def run_filter(self, vtree, content):
        live._export.update(active=True, content=content, objects=set(), owners={}, filtered=False, marked=False)
        live.filter_tree(vtree)
        return {uuid for uuid, node in vtree.nodes.items() if node.keep_tag}

    def test_only_changed_objects_and_their_ancestors_as_empties(self):
        vtree = tree(VNode("room", 10), VNode("chair", 11, parent="room"), VNode("table", 12, parent="room"),
                     VNode("lamp", 13))
        kept = self.run_filter(vtree, {11})
        self.assertEqual(kept, {"room", "chair"})
        self.assertTrue(vtree.nodes["room"].force_as_empty, "the ancestor carries no mesh")
        self.assertFalse(vtree.nodes["chair"].force_as_empty)
        self.assertEqual(live._export["objects"], {10, 11, 12, 13}, "what a full export holds, for removals")
        self.assertTrue(live._export["filtered"])

    def test_a_changed_skin_keeps_its_armature_and_bones(self):
        rig = SimpleNamespace(type="ARMATURE", object=SimpleNamespace(session_uid=20))
        vtree = tree(VNode("arm", 20, kind=VNode.ARMATURE), VNode("hip", 20, parent="arm", kind=VNode.BONE,
                                                                  bone="Hip", armature="arm"),
                     VNode("hair", 21, modifiers=[rig]), VNode("prop", 22))
        self.assertEqual(self.run_filter(vtree, {21}), {"arm", "hip", "hair"})

    def test_a_full_export_keeps_everything(self):
        vtree = tree(VNode("a", 1), VNode("b", 2))
        self.assertEqual(self.run_filter(vtree, None), {"a", "b"})

    def test_everything_under_an_instance_is_keyed_inside_it(self):
        """Break-it N2: a child of an instanced object, an instanced armature's bones and a geometry-nodes instance
        were keyed as if outside the instance, so two copies shared keys."""
        nodes = []
        for copy in (1, 2):
            nodes += [VNode("i%d" % copy, 30 + copy), VNode("cone%d" % copy, 40, parent="i%d" % copy),
                      VNode("child%d" % copy, 41, parent="cone%d" % copy),
                      VNode("rig%d" % copy, 42, parent="i%d" % copy, kind=VNode.ARMATURE),
                      VNode("spine%d" % copy, 42, parent="rig%d" % copy, kind=VNode.BONE, bone="Spine",
                            armature="rig%d" % copy)]
        gn = [VNode("gn%d" % index, None, parent="cone1", kind=VNode.INSTANCE) for index in range(2)]
        for node in gn:
            node.data = SimpleNamespace(session_uid=77)
        vtree = tree(*nodes, *gn)
        for copy in (1, 2):
            vtree.nodes["i%d" % copy].children = ["cone%d" % copy, "rig%d" % copy]
            vtree.nodes["i%d" % copy].children_type = {"cone%d" % copy: VNode.CHILDREN_IS_IN_COLLECTION,
                                                       "rig%d" % copy: VNode.CHILDREN_IS_IN_COLLECTION}
        vtree.nodes["cone1"].children = ["child1", "gn0", "gn1"]
        live._export.update(active=True, content=None, owners={}, marked=False)
        live.mark_nodes(vtree)
        keys = {uuid: node.node.extras[live.KEY] for uuid, node in vtree.nodes.items()}
        self.assertEqual(len(set(keys.values())), len(keys), keys)
        self.assertEqual((keys["child1"], keys["child2"]), ("31>41", "32>41"))
        self.assertEqual((keys["spine1"], keys["spine2"]), ("31>42/Spine", "32>42/Spine"))
        self.assertEqual((keys["gn0"], keys["gn1"]), ("31>40>d77#0", "31>40>d77#1"), "never the export's uuid")

    def test_particle_copies_belong_to_their_instancer(self):
        """Break-it round 4, H1: a particle system's copies carry the copied object's uid, so a changed emitter sent
        none of them, a lowered count left them on the page, and two emitters' copies shared numbering."""
        nodes = [VNode("emitter", 1, instancer=True), VNode("emitter2", 2, instancer=True)]
        for index in range(2):
            nodes += [VNode("c%d" % index, 9, parent="emitter"), VNode("d%d" % index, 9, parent="emitter2")]
        child = VNode("label", 5, parent="emitter")
        vtree = tree(*nodes, child)
        child.blender_object.parent = vtree.nodes["emitter"].blender_object   # A real child, not a copy.
        self.assertEqual(live.owners_of(vtree, "c0"), {9, 1})
        self.assertEqual(live.owners_of(vtree, "label"), {5}, "a real child isn't the instancer's copy")
        self.assertEqual(self.run_filter(vtree, {1}), {"emitter", "c0", "c1"}, "the changed emitter's copies go")
        live._export.update(active=True, content={1}, owners={}, marked=False)
        live.mark_nodes(vtree)
        keys = {uuid: node.node.extras[live.KEY] for uuid, node in vtree.nodes.items()}
        self.assertEqual((keys["c0"], keys["d0"]), ("1>9", "2>9"), "each emitter numbers its own copies")
        self.assertEqual(vtree.nodes["c0"].node.extras[live.ROLE], live.CONTENT)
        self.assertEqual(live._export["owners"]["1>9"], {9, 1})

    def test_vertex_and_face_instancing_children_are_copies(self):
        """Break-it round 5, H1: their copied object is the instancer's own child."""
        for kind in ("VERTS", "FACES"):
            dupper = VNode("dupper", 1, instancer=True)
            dupper.blender_object.instance_type = kind
            copy = VNode("copy", 9, parent="dupper")
            vtree = tree(dupper, copy)
            copy.blender_object.parent = dupper.blender_object
            self.assertEqual(live.owners_of(vtree, "copy"), {9, 1}, kind)

    def test_keys_are_unique_inside_instances_and_stable(self):
        vtree = tree(VNode("i1", 30), VNode("i2", 31), VNode("c1", 40, parent="i1"), VNode("c2", 40, parent="i2"),
                     VNode("arm", 50, kind=VNode.ARMATURE),
                     VNode("bone", 50, parent="arm", kind=VNode.BONE, bone="Hand.L", armature="arm"))
        vtree.nodes["i1"].children_type = {"c1": VNode.CHILDREN_IS_IN_COLLECTION}
        vtree.nodes["i2"].children_type = {"c2": VNode.CHILDREN_IS_IN_COLLECTION}
        live._export.update(active=True, content=None, owners={}, marked=False)
        live.mark_nodes(vtree)
        keys = {uuid: node.node.extras[live.KEY] for uuid, node in vtree.nodes.items()}
        self.assertEqual(keys, {"i1": "30", "i2": "31", "c1": "30>40", "c2": "31>40", "arm": "50",
                                "bone": "50/Hand.L"})
        self.assertTrue(live._export["marked"])


class WrittenFrameTests(unittest.TestCase):
    """``_export_glb`` refuses a frame its hooks didn't finish: a node without its own key, an image without a
    digest (break-it N2, N6). The exporter logs a failed hook and goes on."""

    def frame(self, nodes, images=()):
        from angelo_agent import engine_link_assets, web_link_assets

        def write(window, path, options, export_id):
            live._export.update(filtered=True, marked=True)
            document = {"asset": {"version": "2.0"}, "nodes": nodes, "images": list(images)}
            Path(path).write_bytes(live.write_glb(document, b""))
        path = str(Path(self.folder.name) / "live.glb")
        with mock.patch.object(engine_link_assets, "export", write), \
                mock.patch.object(web_link_assets, "live_options", lambda params: {}):
            return live._export_glb(None, path, {"params": {}}, None, frozenset())

    def setUp(self):
        import tempfile
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_good_frames_pass_and_bad_ones_are_refused(self):
        node = {"extras": {live.KEY: "1", live.ROLE: live.CONTENT}}
        self.frame([node], [{"extras": {live.DIGEST: "d"}}])
        for nodes, images in (([node, dict(node)], ()), ([{"extras": {}}], ()), ([node], [{"extras": {}}])):
            with self.subTest(nodes=nodes, images=images), self.assertRaises(RuntimeError):
                self.frame(nodes, images)


if __name__ == "__main__":
    unittest.main()
