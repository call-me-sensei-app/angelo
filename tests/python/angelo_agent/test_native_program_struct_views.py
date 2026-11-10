# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the program walks treat as one struct, and what they may skip; no app launched.

Blender returns several RNA views over one pointer: ``Object.display`` and ``Object.light_linking`` are the Object
itself, ``Object.collision`` shares ``Object.field``'s data, ``World.light_settings`` and ``World.mist_settings`` are the
World. Identified by pointer alone, the second view read was a reference to the first, so its own settings were never
fingerprinted: an edit of a collision setting, a display setting or a World's mist between bind and commit went
unseen. A pose bone's or bone's ID properties (a rig's IK/FK switch) were not fingerprinted either: only IDs' were.
(doc/angelo/native_coverage.rst, "Dependency walk depth" and "Program struct identity".)

Also here: the flat fingerprint tells apart exactly the states the nested one did (a random-graph oracle), unreadable
structs refuse as a field read would, and link walks skip collections whose items can hold no link, derived from the
declarations (rna_fields.link_free_classifier).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import itertools
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import exact_fence  # noqa: E402
from angelo_agent import program_admission  # noqa: E402
from angelo_agent import program_dependencies as dependencies  # noqa: E402
from angelo_agent import rna_fields  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402


def declaration(name, kind, fixed_type=None):
    return SimpleNamespace(identifier=name, type=kind, is_readonly=False, fixed_type=fixed_type)


class RNA:
    pointers = itertools.count(1)

    def __init__(self, type_name, fields=(), pointer=None, properties=None, **values):
        self.pointer = next(self.pointers) if pointer is None else pointer
        self.values, self.properties = values, properties
        self.bl_rna = SimpleNamespace(identifier=type_name, properties=list(fields))

    def as_pointer(self):
        return self.pointer

    def keys(self):
        if self.properties is None:
            raise TypeError("This type doesn't support IDProperties")
        return list(self.properties)

    def __getitem__(self, key):
        return self.properties[key]

    def __getattr__(self, name):
        try:
            return self.__dict__["values"][name]
        except KeyError:
            raise AttributeError(name) from None


class ID(RNA):
    pass


class Object(ID):
    pass


TYPES = {name: type(name, (ID,), {}) for name in
         ("Mesh", "Material", "NodeTree", "Action", "Key", "Curve", "Lattice", "Camera", "Light", "ParticleSettings",
          "Scene", "Collection", "Curves", "Attribute", "MetaBall", "GreasePencil", "PointCloud", "Volume",
          "LightProbe", "Speaker", "Armature")}
BPY = SimpleNamespace(types=SimpleNamespace(ID=ID, Object=Object, **TYPES))


def text(value):
    return json.dumps(value, sort_keys=True)


class StructViewTests(unittest.TestCase):
    def setUp(self):
        for module in (dependencies, program_admission):
            patched = patch.object(module, "_bpy", return_value=BPY)
            patched.start()
            self.addCleanup(patched.stop)
        self.addCleanup(exact_fence.end)
        dependencies._NO_ID_PROPERTIES.clear()

    def object_with_views(self):
        """An object whose ``display`` view is its own pointer and whose ``collision`` shares ``field``'s data."""
        obj = Object("Object", [declaration("display", "POINTER"), declaration("field", "POINTER"),
                                declaration("collision", "POINTER")], properties={})
        shared = 7000 + obj.pointer
        obj.values["display"] = RNA("ObjectDisplay", [declaration("show_shadows", "BOOLEAN")], pointer=obj.pointer,
                                    show_shadows=True)
        obj.values["field"] = RNA("FieldSettings", [declaration("strength", "FLOAT")], pointer=shared, strength=1.0)
        obj.values["collision"] = RNA("CollisionSettings", [declaration("thickness_outer", "FLOAT")], pointer=shared,
                                      thickness_outer=.02)
        return obj

    def test_every_view_over_one_pointer_is_fingerprinted(self):
        obj = self.object_with_views()
        before = text(dependencies._rna_values(obj))
        for view, name, value in (("display", "show_shadows", False), ("collision", "thickness_outer", .5),
                                  ("field", "strength", 2.0)):
            with self.subTest(view=view):
                old = obj.values[view].values[name]
                obj.values[view].values[name] = value
                self.assertNotEqual(text(dependencies._rna_values(obj)), before)
                obj.values[view].values[name] = old
                self.assertEqual(text(dependencies._rna_values(obj)), before)

    def test_the_same_view_reached_twice_is_still_one_struct(self):
        data = RNA("FieldSettings", [declaration("strength", "FLOAT")], strength=1.0)
        holder = Object("Object", [declaration("a", "POINTER"), declaration("b", "POINTER")], properties={},
                        a=data, b=data)
        values = dependencies._rna_values(holder)
        self.assertEqual(values["a"], values["b"])
        self.assertEqual(values["#structs"], [{"strength": 1.0}])

    def test_views_are_walked_for_links_and_custom_links(self):
        target = Object("Object", properties={})
        obj = Object("Object", [declaration("display", "POINTER"), declaration("other", "POINTER")], properties={})
        obj.values["display"] = RNA("ObjectDisplay", [declaration("target", "POINTER")], pointer=obj.pointer,
                                    target=target)
        obj.values["other"] = None
        self.assertEqual([(prop.identifier, found) for _, prop, found in dependencies._embedded_links(obj)],
                         [("target", target)])
        obj.values["display"].properties = {"link": target}   # An ID-valued ID property on the view.
        with self.assertRaises(ProgramError) as caught:
            program_admission._walk_custom_links(obj)
        self.assertEqual(caught.exception.code, "UNSUPPORTED")

    def test_id_properties_of_any_struct_are_fingerprinted(self):
        """A pose bone's custom property (an IK/FK switch) changes the fingerprint; a type without ID properties
        records none, and asking it costs one call per type."""
        bone = RNA("PoseBone", [declaration("rotation_mode", "ENUM")], properties={"IK_FK": 0.0},
                   rotation_mode="QUATERNION")
        pose = Object("Object", [declaration("bones", "COLLECTION")], properties={}, bones=[bone])
        before = text(dependencies._rna_values(pose))
        bone.properties["IK_FK"] = 1.0
        self.assertNotEqual(text(dependencies._rna_values(pose)), before)
        bone.properties["IK_FK"] = 0.0
        self.assertEqual(text(dependencies._rna_values(pose)), before)
        self.assertEqual(dependencies._rna_values(pose)["#structs"][0]["#custom"], {"IK_FK": 0.0})
        plain = RNA("Keyframe", [declaration("x", "FLOAT")], x=1.0)
        holder = Object("Object", [declaration("k", "POINTER")], properties={}, k=plain)
        self.assertEqual(dependencies._rna_values(holder)["#structs"], [{"x": 1.0}])
        self.assertIn("Keyframe", dependencies._NO_ID_PROPERTIES)

    def test_an_unreadable_struct_in_a_queued_collection_refuses_as_a_read(self):
        class Freed:
            def __iter__(self):
                raise ReferenceError("StructRNA of type Bone has been removed")
        holder = Object("Object", [declaration("bones", "COLLECTION")], properties={}, bones=Freed())
        with self.assertRaises(ProgramError) as caught:
            dependencies._rna_values(holder)
        self.assertIn("could not be read", str(caught.exception))


def nested_values(value, seen):
    """The fingerprint as it was through 1.0.10: nested, depth first, structs by pointer alone, unbounded."""
    if not hasattr(value, "bl_rna"):
        return [nested_values(item, seen) for item in value] if isinstance(value, list) else value
    if value.as_pointer() in seen:
        return {"rna_reference": [value.bl_rna.identifier, seen[value.as_pointer()]]}
    seen[value.as_pointer()] = len(seen)
    result = {}
    for prop in value.bl_rna.properties:
        item = getattr(value, prop.identifier)
        if prop.type == "POINTER":
            result[prop.identifier] = (None if item is None else [item.bl_rna.identifier, item.pointer]
                                       if isinstance(item, ID) else nested_values(item, seen))
        elif prop.type == "COLLECTION":
            result[prop.identifier] = [nested_values(child, seen) for child in item]
        else:
            result[prop.identifier] = nested_values(item, seen)
    return result


class InjectivityOracleTests(unittest.TestCase):
    """For random struct graphs with one type per pointer (cycles, aliases against equal copies, shared children,
    structs inside arrays, ID elements, reordered collections), two states fingerprint alike exactly when the nested
    encoding called them alike."""

    def setUp(self):
        patched = patch.object(dependencies, "_bpy", return_value=BPY)
        patched.start()
        self.addCleanup(patched.stop)
        self.addCleanup(exact_fence.end)

    @staticmethod
    def graph(seed, mutation):
        generator = random.Random(seed)
        fields = [declaration("a", "POINTER"), declaration("b", "POINTER"), declaration("items", "COLLECTION"),
                  declaration("value", "FLOAT"), declaration("array", "FLOAT"), declaration("owner", "POINTER")]
        ids = [Object("Object", pointer=10 ** 6 + index, properties={}) for index in range(3)]
        structs = [RNA("S", fields, a=None, b=None, items=[], value=float(index), array=[1.0, 2.0], owner=None)
                   for index in range(8)]
        for struct in structs:
            struct.values["a"] = generator.choice(structs + [None])
            struct.values["b"] = generator.choice(structs + [None, None])
            struct.values["items"] = generator.sample(structs + ids, generator.randrange(0, 4))
            struct.values["owner"] = generator.choice(ids + [None])
            if generator.random() < .3:
                struct.values["array"] = [1.0, generator.choice(structs)]
        root = Object("Root", [declaration("first", "POINTER"), declaration("all", "COLLECTION")], properties={},
                      first=structs[0], all=list(structs[:4]))
        kind, index = mutation
        target = structs[index]
        if kind == "value":
            target.values["value"] += 1.0
        elif kind == "rewire":
            target.values["a"] = structs[(index + 1) % len(structs)]
        elif kind == "copy":   # An alias replaced by an equal copy.
            copy = RNA("S", fields, **dict(target.values))
            for struct in structs:
                if struct.values["b"] is target:
                    struct.values["b"] = copy
        elif kind == "reorder":
            target.values["items"] = list(reversed(target.values["items"]))
        elif kind == "owner":
            target.values["owner"] = ids[(ids.index(target.values["owner"]) + 1) % 3] if target.values["owner"] else ids[0]
        return root

    def test_the_flat_table_separates_exactly_what_the_nested_encoding_did(self):
        generator = random.Random(20261105)
        agreed = differed = 0
        for trial in range(300):
            seed = generator.randrange(10 ** 9)
            mutation = (generator.choice(("none", "value", "rewire", "copy", "reorder", "owner")),
                        generator.randrange(8))
            first, second = self.graph(seed, ("none", 0)), self.graph(seed, mutation)
            old_equal = text(nested_values(first, {})) == text(nested_values(second, {}))
            new_equal = text(dependencies._rna_values(first)) == text(dependencies._rna_values(second))
            with self.subTest(trial=trial, mutation=mutation):
                self.assertEqual(new_equal, old_equal)
            agreed += old_equal
            differed += not old_equal
        self.assertGreater(agreed, 30)   # Both outcomes are exercised, not only "different".
        self.assertGreater(differed, 100)


class LinkFreeTests(unittest.TestCase):
    """rna_fields.link_free_classifier on stand-in declarations."""

    @staticmethod
    def struct(identifier, properties, base=None):
        return SimpleNamespace(identifier=identifier, properties=properties, base=base)

    def classifier(self, *types):
        namespace = SimpleNamespace(**{rna.identifier: SimpleNamespace(bl_rna=rna) for rna in types})
        return rna_fields.link_free_classifier(namespace)

    def test_values_and_collections_of_link_free_items_are_free(self):
        key = self.struct("ParticleHairKey", [declaration("co", "FLOAT")])
        particle = self.struct("Particle", [declaration("hair_keys", "COLLECTION", key), declaration("size", "FLOAT")])
        free = self.classifier(key, particle)
        self.assertTrue(free(declaration("particles", "COLLECTION", particle)))
        self.assertTrue(free(declaration("hair_keys", "COLLECTION", key)))

    def test_pointers_subtypes_cycles_and_unknown_types_are_not(self):
        target = self.struct("Object", [])
        modifier = self.struct("Modifier", [declaration("name", "STRING")])
        armature_modifier = self.struct("ArmatureModifier", [declaration("object", "POINTER", target)], base=modifier)
        node = self.struct("Linked", [])
        node.properties = [declaration("next", "COLLECTION", node)]
        pointed = self.struct("Pointed", [declaration("owner", "POINTER", target)])
        free = self.classifier(target, modifier, armature_modifier, node, pointed)
        self.assertFalse(free(declaration("modifiers", "COLLECTION", modifier)))   # A subtype holds a pointer.
        self.assertFalse(free(declaration("chain", "COLLECTION", node)))           # Unproven: a cycle.
        self.assertFalse(free(declaration("pointed", "COLLECTION", pointed)))
        self.assertFalse(free(declaration("bare", "COLLECTION")))                  # No declared item type.

    def test_python_subclasses_answer_subtyped_after_one_priming_scan(self):
        touched = []

        class Types:
            def __dir__(self):
                return ["Modifier", "ArmatureModifier", "Keyframe"]

            def __getattr__(self, name):
                touched.append(name)
                return {"Modifier": Modifier, "ArmatureModifier": ArmatureModifier, "Keyframe": Keyframe}[name]

        Modifier = type("Modifier", (), {})
        ArmatureModifier = type("ArmatureModifier", (Modifier,), {})
        Keyframe = type("Keyframe", (), {})
        primed = [False]
        subtyped = rna_fields.python_subtyped(Types(), primed)
        self.assertTrue(primed[0])
        self.assertEqual(sorted(touched), ["ArmatureModifier", "Keyframe", "Modifier"])   # Every class built once.
        self.assertTrue(subtyped("Modifier"))
        self.assertFalse(subtyped("ArmatureModifier"))
        self.assertFalse(subtyped("Keyframe"))
        touched.clear()
        rna_fields.python_subtyped(Types(), primed)
        self.assertEqual(touched, [])   # Primed once per process.
        unknown = rna_fields.python_subtyped(SimpleNamespace(), [True])
        self.assertTrue(unknown("Missing"))   # Not proven free.

    def test_a_placeholder_item_type_is_never_proven_free(self):
        unknown = self.struct("UnknownType", [])   # ShapeKey.data declares its points so.
        free = self.classifier(unknown)
        self.assertFalse(free(declaration("data", "COLLECTION", unknown)))

    def test_the_link_walk_skips_link_free_collections_inside_and_outside_a_fence(self):
        calls = []

        class Counted(list):
            def __iter__(self):
                calls.append(1)
                return super().__iter__()

        key = self.struct("ParticleHairKey", [declaration("co", "FLOAT")])
        holder = Object("Object", [declaration("hair_keys", "COLLECTION", key)], properties={},
                        hair_keys=Counted(RNA("ParticleHairKey", [declaration("co", "FLOAT")], co=1.0) for _ in range(5)))
        with patch.object(dependencies, "_bpy", return_value=BPY), \
                patch.object(dependencies, "link_free_classifier", side_effect=lambda types, subtyped=None: self.classifier(key)):
            self.assertEqual(list(dependencies._walk_links(holder)), [])   # Outside a fence (a stage's finish).
            with exact_fence.fence():
                self.assertEqual(list(dependencies._walk_links(holder)), [])
            self.assertEqual(calls, [])   # Never iterated, inside a fence or outside one.


if __name__ == "__main__":
    unittest.main()
