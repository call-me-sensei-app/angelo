# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A placement's material swap is checked on everything the placement shows, its instances included, without Blender
(``worker_material_check``, ``worker_material_reach``, ``worker_materials``; maintainer audit P2 and its break-it).
Pinned here: what an object instances (a collection, a particle system that shows particles, the members it picks by
count) is read with each instanced object's own result, nested collections included and labelled by their path; each
object is read once per evaluation and compared once per swap however many paths reach it (break-it M4); instancing
deeper than Blender's limit and an instanced object the evaluation does not hold are unread, named by their path; what
Blender renders follows exclusion and render visibility (M2); an instance of another placement's object is refused
before anything is swapped only when that placement swaps the material otherwise (M5); a swap is accepted only on parts
of known-safe kinds, anything else named (P2R6). The in-Blender proof is
``test_blender_parallel_merge_instances.py``.
"""

import itertools
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import worker_material_check as check, worker_material_reach as reach  # noqa: E402
from angelo_agent import worker_materials  # noqa: E402

POINTERS = itertools.count(1000)


class Item:
    """A datablock: hashable by identity, with a pointer, as Blender's are."""

    def __init__(self, name, **fields):
        pointer = next(POINTERS)
        self.name, self.as_pointer = name, lambda: pointer
        self.__dict__.update(fields)


class Object(Item):
    pass


class Collection(Item):
    pass


def material(name):
    found = Item(name, library=None, animation_data=None, node_tree=None)
    found.original = found
    return found


def obj(name, kind="EMPTY", collection=None, mesh=None, particles=(), modifiers=(), evaluated=True, slots=()):
    """An original object and its evaluated copy (``evaluated_get``), as a depsgraph holds them."""
    found = Object(name, type=kind, instance_type="COLLECTION" if collection is not None else "NONE",
                   instance_collection=collection, particle_systems=list(particles), modifiers=list(modifiers),
                   material_slots=[SimpleNamespace(material=m, link="DATA") for m in slots], mesh=mesh,
                   hide_render=False, hide_viewport=False, users_collection=[], animation_data=None, library=None,
                   override_library=None, data=None, parent=None, constraints=[])
    found.original = found
    copy = Object(name, type=kind, is_evaluated=True, original=found, mesh=mesh)
    found.is_evaluated = False
    found.evaluated_get = lambda _depsgraph: copy if evaluated else found
    return found


def collection(name, *objects):
    return Collection(name, all_objects=list(objects), hide_render=False, hide_viewport=False, children_recursive=[])


class FakeGeometrySet:
    reads = []

    @staticmethod
    def from_evaluated_object(evaluated):
        FakeGeometrySet.reads.append(evaluated.name)
        return SimpleNamespace(mesh=evaluated.mesh, curves=None, pointcloud=None, volume=None, grease_pencil=None,
                               instances_pointcloud=lambda: None, instance_references=lambda: [])


def mesh(*materials, faces=3):
    return SimpleNamespace(materials=list(materials), attributes={}, polygons=[0] * faces)


BPY = SimpleNamespace(types=SimpleNamespace(GeometrySet=FakeGeometrySet, Object=Object, Collection=Collection, ID=Item),
                      data=SimpleNamespace(collections=[]))


def view(*tops):
    found = check.View(object(), {})
    with patch.dict(sys.modules, {"bpy": BPY}):
        for top in tops:
            found.reach(found.read(top))
    return found


def hair(name, instance, count=4, shown=(True, True)):
    system = Item(name, settings=SimpleNamespace(render_type="OBJECT", instance_object=instance, count=count))
    modifier = SimpleNamespace(type="PARTICLE_SYSTEM", particle_system=system, show_viewport=shown[0],
                               show_render=shown[1], name=name)
    return system, modifier


class ReaderTests(unittest.TestCase):

    def setUp(self):
        FakeGeometrySet.reads = []

    def test_what_an_object_instances_is_listed_unless_it_shows_nothing(self):
        ebony = material("Ebony")
        tile, blade = obj("Tile", "MESH", mesh=mesh(ebony)), obj("Blade", "MESH", mesh=mesh(ebony))
        self.assertEqual(check.instanced(obj("Showcase", collection=collection("Source", tile))),
                         [("collection 'Source'", [tile])])
        systems = [hair("Hair", blade), hair("Bare", blade, count=0), hair("Off", blade, shown=(False, False))]
        field = obj("Field", "MESH", mesh=mesh(ebony), particles=[s for s, _m in systems],
                    modifiers=[m for _s, m in systems])
        self.assertEqual(check.instanced(field), [("particle system 'Hair'", [blade])])
        self.assertEqual(check.instanced(tile), [])

    def test_a_nested_collection_instance_is_compared_and_named_by_its_path(self):
        ebony, red = material("Ebony"), material("Red")
        bolt = obj("Bolt", "MESH", mesh=mesh(ebony))
        frame = obj("Frame", collection=collection("Outer", obj("Mount", collection=collection("Inner", bolt))))
        before = view(frame)
        names = {ebony.as_pointer(): "Ebony", red.as_pointer(): "Red"}
        self.assertEqual(check.judge(before, before, frame, {ebony: red}, names, {}),
                         "3 of its collection 'Outer' (Mount) collection 'Inner' (Bolt) faces show Ebony where Red "
                         "belongs")
        self.assertIsNone(check.judge(before, before, frame, {}, names, {}))
        self.assertEqual(check.materials(before, frame), {ebony.as_pointer()})

    def test_each_object_is_read_once_and_compared_once_however_many_paths_reach_it(self):
        ebony, red = material("Ebony"), material("Red")
        stud = obj("Stud", "MESH", mesh=mesh(ebony))
        level = collection("L3", stud)
        for depth in (2, 1, 0):   # Twenty empties per level, each instancing the level below: 8000 paths per top.
            level = collection("L%d" % depth, *[obj("E%d_%d" % (depth, n), collection=level) for n in range(20)])
        tops = [obj("Top %d" % n, collection=level) for n in range(5)]
        before = view(*tops)
        self.assertEqual(FakeGeometrySet.reads, ["Stud"])
        with patch.object(check, "mismatch", wraps=check.mismatch) as compared:
            cache = {}
            for top in tops:
                self.assertIsNotNone(check.judge(before, before, top, {ebony: red}, {}, cache))
        # Each top's own result and its message once, each of the 61 distinct objects once: never per path.
        self.assertLessEqual(compared.call_count, len(tops) * 2 + 61)

    def test_instancing_deeper_than_blenders_limit_is_unread_by_its_path(self):
        inner = obj("Core", "MESH", mesh=mesh(material("Ebony")))
        for level in range(check.DEPTH + 1, 0, -1):
            inner = obj("Step %d" % level, collection=collection("Level %d" % level, inner))
        found = check.unread(view(inner), inner)
        self.assertTrue(found.startswith("collection 'Level 1' (Step 2) collection 'Level 2' (Step 3)"), found)
        self.assertIn("(Core) nest deeper than the check reads (%d levels)" % check.DEPTH, found)
        shallow = obj("Top", collection=collection("Only", obj("Leaf", "MESH", mesh=mesh(material("Oak")))))
        self.assertIsNone(check.unread(view(shallow), shallow))

    def test_an_instanced_object_the_evaluation_does_not_hold_is_unread(self):
        hidden = obj("Tile", "MESH", mesh=mesh(material("Ebony")), evaluated=False)
        showcase = obj("Showcase", collection=collection("Source", hidden))
        self.assertEqual(check.unread(view(showcase), showcase),
                         "collection 'Source' (Tile) is not evaluated in this view layer")

    def test_a_refusal_says_what_is_instanced_from_outside_the_placement(self):
        tile = obj("Tile", "MESH", mesh=mesh())
        showcase = obj("Showcase", collection=collection("Source", tile))
        self.assertEqual(check.where(showcase, {showcase}),
                         "it comes from what it instances: collection 'Source', whose objects this placement does "
                         "not own")
        geometry = obj("Shell", "MESH", modifiers=[SimpleNamespace(name="GeometryNodes", type="NODES")])
        self.assertEqual(check.where(geometry), "it comes from its modifiers (GeometryNodes)")


def prop(identifier, kind):
    return SimpleNamespace(type="POINTER", fixed_type=SimpleNamespace(identifier=kind), identifier=identifier)


def modifier(name, kind="BOOLEAN", viewport=True, render=True, **pointers):
    found = SimpleNamespace(name=name, type=kind, show_viewport=viewport, show_render=render, node_group=None,
                            bl_rna=SimpleNamespace(identifier=name, properties=[
                                prop(key, "Collection" if isinstance(value, Collection) else "Object")
                                for key, value in pointers.items()]),
                            **pointers)
    return found


class ReferenceTests(unittest.TestCase):

    def test_rendered_follows_exclusion_and_render_visibility(self):
        tile, hidden = obj("Tile", "MESH"), obj("Hidden", "MESH")
        tile.hide_render, hidden.hide_render = False, True
        kept = SimpleNamespace(objects=[tile, hidden], hide_render=False)

        def layer(collection_, exclude=False, children=()):
            return SimpleNamespace(collection=collection_, exclude=exclude, children=list(children))
        master = SimpleNamespace(objects=[], hide_render=False)
        for children, expected in (([layer(kept)], {tile.as_pointer()}), ([layer(kept, exclude=True)], set()),
                                   ([layer(SimpleNamespace(objects=[], hide_render=True), children=[layer(kept)])],
                                    set())):
            self.assertEqual(reach.rendered(SimpleNamespace(layer_collection=layer(master, children=children))),
                             expected)


class SameSwapTests(unittest.TestCase):

    def test_an_instance_of_another_placements_object_is_refused_only_when_it_swaps_otherwise(self):
        ebony, red, brass = material("Ebony"), material("Red"), material("Brass")
        tile = obj("Tile", "MESH", slots=[ebony])
        showcase = obj("Showcase", collection=collection("Source", tile))
        refusal = worker_materials.refusal
        self.assertIsNone(refusal([showcase], {showcase}, {ebony: red}, {}, swap_of=lambda o: {ebony: red}))
        for other in ({}, {ebony: brass}):
            self.assertIn("which this placement does not own", refusal([showcase], {showcase}, {ebony: red}, {},
                                                                        swap_of=lambda o, other=other: other))



def particles(members, count=True, whole=False, weights=None):
    """A hair system instancing a collection of ``members``; ``weights``: {name: count} as Blender lists them."""
    settings = SimpleNamespace(render_type="COLLECTION", instance_collection=collection("Members", *members), count=6,
                               use_collection_count=count, use_whole_collection=whole,
                               instance_weights=[SimpleNamespace(name="%s: %d" % item, count=item[1])
                                                 for item in (weights or {}).items()])
    system = Item("Hair", settings=settings)
    return system, SimpleNamespace(type="PARTICLE_SYSTEM", particle_system=system, show_viewport=True,
                                   show_render=True, name="Hair", bl_rna=SimpleNamespace(properties=[]))


class RoundThreeTests(unittest.TestCase):
    """Break-it P2R2 L-1: a particle system instances the members Blender picks by count."""

    def test_a_particle_system_instances_the_members_blender_picks(self):
        ebony, red = material("Ebony"), material("Red")
        a, b = obj("A", "MESH", slots=[material("Oak")]), obj("B", "MESH", slots=[ebony])
        for options, picked in (({"weights": {"A": 1, "B": 0}}, [a]), ({"weights": {"A": 1}}, [a, b]),
                                ({"weights": {"A": 1, "B": 0}, "whole": True}, [a, b]),
                                ({"weights": {"A": 1, "B": 0}, "count": False}, [a, b])):
            system, mod = particles([a, b], **options)
            field = obj("Field", "MESH", particles=[system], modifiers=[mod])
            self.assertEqual(check.instanced(field), [("particle system 'Hair'", picked)], options)
            why = worker_materials.refusal([field], {field}, {ebony: red}, {})
            self.assertEqual(why is None, b not in picked, (options, why))

def animation(drivers=1):
    return SimpleNamespace(drivers=[object()] * drivers, action=None, nla_tracks=[])


class AllowListTests(unittest.TestCase):
    """Break-it P2R6: a swap is accepted only on parts of known-safe kinds (``worker_material_reach.unsafe``)."""

    def unsafe(self, target, placement=None):
        with patch.dict(sys.modules, {"bpy": BPY}):
            return reach.unsafe(target, placement or {target}, {})

    def plain(self, *modifiers):
        found = obj("Body", "MESH", modifiers=modifiers, slots=[material("Ebony")])
        found.data = SimpleNamespace(library=None, animation_data=None, shape_keys=None)
        return found

    def test_a_plain_mesh_with_material_keeping_modifiers_is_safe(self):
        self.assertIsNone(self.unsafe(self.plain(modifier("Round", "BEVEL"), modifier("Smooth", "SUBSURF"),
                                                 modifier("Twin", "MIRROR", mirror_object=None))))
        self.assertIsNone(self.unsafe(obj("Lamp", "LIGHT")))

    def test_anything_not_proven_safe_is_named(self):
        cases = []
        found = self.plain(); found.type = "CURVE"; cases.append((found, "the object type CURVE"))
        cases.append((obj("Showcase", collection=collection("Source")), "instancing (collection)"))
        found = self.plain(); found.particle_systems = [Item("Hair")]; cases.append((found, "particle system 'Hair'"))
        found = self.plain(); found.animation_data = animation(); cases.append((found, "on its object"))
        found = self.plain(); found.data.animation_data = animation(); cases.append((found, "on its data"))
        found = self.plain(); found.data.shape_keys = Item("Key", animation_data=animation())
        cases.append((found, "on its shape keys"))
        found = self.plain(); found.material_slots[0].material.animation_data = animation()
        cases.append((found, "material 'Ebony'"))
        inner = Item("Inner", nodes=[], animation_data=animation())   # A driver in a nested shader group.
        found = self.plain(); found.material_slots[0].material.node_tree = Item("Shader", animation_data=None,
            nodes=[SimpleNamespace(node_tree=inner)])
        cases.append((found, "material 'Ebony'"))
        cases.append((self.plain(modifier("Build", "NODES")), "modifier 'Build' (Nodes)"))
        cases.append((self.plain(modifier("Cut", "BOOLEAN")), "modifier 'Cut' (Boolean)"))
        cases.append((self.plain(modifier("Repeat", "ARRAY", offset_object=obj("Gizmo"))),
                      "modifier 'Repeat', which uses object 'Gizmo'"))
        found = self.plain(); found.parent = obj("Elsewhere"); cases.append((found, "a parent outside its placement"))
        found = self.plain(); found.constraints = [modifier("Follow", "CHILD_OF", target=obj("Elsewhere"))]
        cases.append((found, "constraint 'Follow', which uses object 'Elsewhere' outside its placement"))
        found = self.plain(); found.library = Item("Library"); cases.append((found, "linked from a library"))
        for target, says in cases:
            why = self.unsafe(target)
            self.assertTrue(why is not None and says in why, (says, why))

    def test_keys_on_its_own_transform_alone_are_safe(self):
        moved = self.plain()
        moved.animation_data = SimpleNamespace(drivers=[], nla_tracks=[], action=SimpleNamespace(
            fcurves=[SimpleNamespace(data_path="location"), SimpleNamespace(data_path="rotation_euler")], layers=[]))
        self.assertIsNone(self.unsafe(moved))
        moved.animation_data.action.fcurves.append(SimpleNamespace(data_path='modifiers["Round"].show_render'))
        self.assertIn("keys on modifiers", self.unsafe(moved))

    def test_a_material_swapped_to_follows_the_same_rules(self):
        reactive = material("Reactive")
        self.assertIsNone(reach.destination(reactive))
        reactive.animation_data = animation()
        self.assertIn("drivers on material 'Reactive'", reach.destination(reactive))

    def test_a_driver_reading_what_a_swap_changes_is_found(self):
        owner, scene, body = Item("Observer"), Item("Scene"), Item("Body")

        def driver(kind="SINGLE_PROP", target=None, path="", expression=True, self_=False, driver_type="AVERAGE"):
            variable = SimpleNamespace(name="var", type=kind, targets=[SimpleNamespace(id=target, data_path=path)])
            return SimpleNamespace(type=driver_type, use_self=self_, is_simple_expression=expression,
                                   variables=[variable])
        for found, says in ((driver(target=scene, path='objects["Body"].active_material.diffuse_color[0]'),
                             "reading 'objects"), (driver(target=owner, path="parent.location"), "by path"),
                            (driver(target=owner, path='["weight"]'), None), (driver(target=owner, path="location[0]"),
                                                                              None),
                            (driver(kind="TRANSFORMS", target=Item("Other")), None),
                            (driver(kind="TRANSFORMS", target=body), "of this merge"),
                            (driver(kind="CONTEXT_PROP", target=None), "the context"),
                            (driver(kind="TRANSFORMS", target=None, driver_type="SCRIPTED", expression=False),
                             "a Python expression"),
                            (driver(kind="TRANSFORMS", target=None, driver_type="SCRIPTED"), None)):
            why = reach._reads(found, owner, {body.as_pointer()})
            self.assertTrue(why is None if says is None else says in why, (says, why))

    def test_only_renders_showing_each_slots_material_the_standard_way_are_allowed(self):
        def scene(engine="BLENDER_EEVEE", color="MATERIAL", tree=None, strips=None, **layer_settings):
            layer = SimpleNamespace(**dict(dict(name="ViewLayer", material_override=None, aovs=[]), **layer_settings))
            return SimpleNamespace(view_layers=[layer], compositing_node_group=tree, use_nodes=True,
                                   sequence_editor=SimpleNamespace(strips_all=strips) if strips is not None else None,
                                   render=SimpleNamespace(engine=engine, use_compositing=True, use_sequencer=True),
                                   display=SimpleNamespace(shading=SimpleNamespace(color_type=color)))

        def node(kind, label, inner=None):
            return SimpleNamespace(bl_idname=kind, bl_label=label, name=label, node_tree=inner)
        plain = Item("Compositing", nodes=[node("CompositorNodeRLayers", "Render Layers"),
                                           node("CompositorNodeColorBalance", "Color Balance"),
                                           node("NodeGroupOutput", "Group Output")])
        for found, says in ((scene(), None), (scene("CYCLES", tree=plain), None),
                            (scene("BLENDER_WORKBENCH"), None),
                            (scene("BLENDER_WORKBENCH", "SINGLE"), "Workbench coloured by single"),
                            (scene("SOME_ADDON_ENGINE"), "render engine SOME_ADDON_ENGINE"),
                            (scene(material_override=material("Clay")), "material override (Clay)"),
                            (scene(use_pass_material_index=True), "Material Index pass"),
                            (scene(use_pass_cryptomatte_material=True), "Cryptomatte material"),
                            (scene(aovs=[object()]), "shader AOVs"), (scene(strips=[]), None),
                            (scene(strips=[Item("Color")]), "Sequencer with strips (Color)"),
                            (scene(tree=Item("Compositing", nodes=[node("CompositorNodeGroup", "Group", Item(
                                "Inner", nodes=[node("CompositorNodeNormalize", "Normalize")]))])),
                             "Normalize node 'Normalize'")):   # Nested groups included.
            why = reach.render_unsafe(found)
            self.assertTrue(why is None if says is None else says in why, (says, why))

    def test_parents_and_constraint_targets_within_the_placement_are_safe(self):
        anchor, body = obj("Anchor"), self.plain()
        body.parent, body.constraints = anchor, [modifier("Follow", "CHILD_OF", target=anchor)]
        self.assertIsNone(self.unsafe(body, {body, anchor}))


if __name__ == "__main__":
    unittest.main()
