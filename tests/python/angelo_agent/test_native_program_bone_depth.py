# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nesting depth in the program dependency walks: an armature's bone chains are not deep nesting; no app launched.

Every Bone is an element of ``Armature.bones`` (and every PoseBone of ``Object.pose.bones``), one or two levels below
its datablock, yet depth-first walks reached a chain of n bones through ``parent``/``children`` about n levels deep:
``_walk_links`` refused at 15 and ``_rna_values`` at 17, so every typed program on a humanoid with fingers was refused
(doc/angelo/native_coverage.rst, "Dependency walk depth"). These stand-ins have the same shape: a flat list plus
parent and child pointers. Breadth first with those bounds kept, structs that really nest (bone collections nested
under each other, reached from the object through a pose bone) still refused; neither walk bounds nesting now.
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
from angelo_agent import program_dependencies as dependencies  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402


def declaration(name, kind):
    return SimpleNamespace(identifier=name, type=kind, is_readonly=False)


class RNA:
    pointers = itertools.count(1)

    def __init__(self, name, fields=(), **values):
        self.name, self.pointer, self.values = name, next(self.pointers), values
        self.bl_rna = SimpleNamespace(identifier=type(self).__name__, properties=list(fields))

    def as_pointer(self):
        return self.pointer

    def __getattr__(self, name):
        try:
            return self.__dict__["values"][name]
        except KeyError:
            raise AttributeError(name) from None


class ID(RNA):
    def keys(self):   # No custom properties.
        return []


class Object(ID):
    pass


class Armature(ID):
    pass


class Bone(RNA):
    pass


TYPES = {name: type(name, (ID,), {}) for name in
         ("Mesh", "Material", "NodeTree", "Action", "Key", "Curve", "Lattice", "Camera", "Light", "ParticleSettings",
          "Scene", "Collection", "Curves", "Attribute", "MetaBall", "GreasePencil", "PointCloud", "Volume",
          "LightProbe", "Speaker")}
BPY = SimpleNamespace(types=SimpleNamespace(ID=ID, Object=Object, Armature=Armature, **TYPES))

BONE_FIELDS = [declaration("parent", "POINTER"), declaration("children", "COLLECTION"),
               declaration("shape", "POINTER"), declaration("roll", "FLOAT")]


def armature(length, leaf=None):
    """A datablock whose bones are all listed flat and also chained parent -> child, ``length`` long."""
    bones = [Bone("B%d" % index, BONE_FIELDS, parent=None, children=[], shape=None, roll=0.1 * index)
             for index in range(length)]
    for parent, child in zip(bones, bones[1:]):
        child.values["parent"] = parent
        parent.values["children"] = [child]
    bones[-1].values["shape"] = leaf
    return Armature("Rig", [declaration("bones", "COLLECTION")], bones=list(bones)), bones


def nested(length, leaf):
    """Structs reachable ONLY through each other: genuinely nested ``length`` levels below the datablock."""
    nodes = [RNA("N%d" % index, [declaration("child", "POINTER"), declaration("value", "FLOAT")], child=None,
                 value=float(index)) for index in range(length)]
    for parent, child in zip(nodes, nodes[1:]):
        parent.values["child"] = child
    nodes[-1].values["child"] = leaf
    return Armature("Deep", [declaration("first", "POINTER")], first=nodes[0]), nodes


def links(root):
    return sorted((holder.name, prop.identifier, target.name)
                  for holder, prop, target in dependencies._embedded_links(root))


def unbounded_links(root, visited=None):
    """The depth-first walk as it was, without its depth bound: the oracle for WHICH links exist."""
    visited = set() if visited is None else visited
    if root.as_pointer() in visited:
        return
    visited.add(root.as_pointer())
    for name, kind, prop in dependencies.link_fields(root.bl_rna):
        item = getattr(root, name, None)
        if kind == "POINTER":
            if item is not None:
                if isinstance(item, ID):
                    yield root, prop, item
                elif hasattr(item, "bl_rna"):
                    yield from unbounded_links(item, visited)
        elif kind == "COLLECTION":
            for child in item:
                if hasattr(child, "bl_rna"):
                    yield from unbounded_links(child, visited)
        else:
            for child in item:
                if child is not None:
                    yield root, prop, child


def nesting(value):
    """The nesting depth of a JSON value, iteratively."""
    deepest, stack = 0, [(value, 0)]
    while stack:
        item, level = stack.pop()
        deepest = max(deepest, level)
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        stack.extend((child, level + 1) for child in children)
    return deepest


class BoneDepthTests(unittest.TestCase):
    def setUp(self):
        patched = patch.object(dependencies, "_bpy", return_value=BPY)
        patched.start()
        self.addCleanup(patched.stop)
        self.addCleanup(exact_fence.end)

    def test_a_long_bone_chain_listed_flat_is_walked_completely(self):
        for length in (10, 11, 16, 64, 300):
            with self.subTest(length=length):
                leaf = Object("leaf")
                root, _ = armature(length, leaf)
                self.assertEqual(links(root), [("B%d" % (length - 1), "shape", "leaf")])

    def test_genuine_nesting_is_walked_completely_without_recursion(self):
        """Structs reachable only through each other (bone collections nested under each other, reached from the
        object through a pose bone's bone) are walked however deep they nest; the walk is iterative."""
        for length in (14, 15, 40, 5000):   # The deepest struct is ``length`` levels.
            with self.subTest(length=length):
                root, _ = nested(length, Object("leaf"))
                self.assertEqual(links(root), [("N%d" % (length - 1), "child", "leaf")])

    def test_links_equal_the_unbounded_depth_first_walk_on_random_graphs(self):
        generator = random.Random(20261105)
        for trial in range(200):
            ids = [Object("id%d" % index) for index in range(4)]
            fields = [declaration("a", "POINTER"), declaration("b", "POINTER"), declaration("items", "COLLECTION"),
                      declaration("target", "POINTER")]
            structs = [RNA("s%d" % index, fields, a=None, b=None, items=[], target=None) for index in range(12)]
            for struct in structs:   # Cycles, aliases and shared children, as RNA has.
                struct.values["a"] = generator.choice(structs + [None])
                struct.values["b"] = generator.choice(structs + [None, None])
                struct.values["items"] = generator.sample(structs, generator.randrange(0, 4))
                struct.values["target"] = generator.choice(ids + [None, None])
            root = Armature("root", [declaration("bones", "COLLECTION"), declaration("first", "POINTER")],
                            bones=list(structs), first=structs[0])
            expected = sorted((h.name, p.identifier, t.name) for h, p, t in unbounded_links(root))
            with self.subTest(trial=trial):
                self.assertEqual(links(root), expected)

    def test_breadth_first_order_is_pinned(self):
        """Consumers are order-consistent (the same build walks bind and commit alike): the order is deterministic and
        reviewed here, shallower links first."""
        c_target, b_target = Object("c"), Object("b")
        inner = RNA("C", [declaration("target", "POINTER")], target=c_target)
        first = RNA("A", [declaration("child", "POINTER")], child=inner)
        second = RNA("B", [declaration("target", "POINTER")], target=b_target)
        root = Armature("root", [declaration("a", "POINTER"), declaration("b", "POINTER")], a=first, b=second)
        order = [(holder.name, target.name) for holder, _, target in dependencies._embedded_links(root)]
        self.assertEqual(order, [("B", "b"), ("C", "c")])   # Depth first gave [("C", "c"), ("B", "b")].

    def test_fingerprint_of_a_long_chain_listed_flat_is_complete_and_exact(self):
        root, bones = armature(64, Object("leaf"))
        first = json.dumps(dependencies._rna_values(root), sort_keys=True)
        self.assertEqual(json.dumps(dependencies._rna_values(root), sort_keys=True), first)   # Deterministic.
        bones[-1].values["roll"] = 7.5   # The deepest bone's setting is part of the fingerprint.
        changed = json.dumps(dependencies._rna_values(root), sort_keys=True)
        self.assertNotEqual(changed, first)
        bones[-1].values["roll"] = 0.1 * 63
        self.assertEqual(json.dumps(dependencies._rna_values(root), sort_keys=True), first)
        bones[40].values["children"], bones[41].values["parent"] = [], None   # Re-parenting changes it too.
        self.assertNotEqual(json.dumps(dependencies._rna_values(root), sort_keys=True), first)

    def test_fingerprint_of_genuine_nesting_is_a_flat_exact_table(self):
        for length in (17, 5000):
            with self.subTest(length=length):
                root, nodes = nested(length, Object("leaf"))
                values = dependencies._rna_values(root)
                self.assertLessEqual(nesting(values), 5)   # Recursive consumers meet a constant depth.
                text = json.dumps(values, sort_keys=True)
                self.assertEqual(json.dumps(dependencies._rna_values(root), sort_keys=True), text)
                nodes[-1].values["value"] = -1.0   # The deepest struct's setting is part of the fingerprint.
                self.assertNotEqual(json.dumps(dependencies._rna_values(root), sort_keys=True), text)
                nodes[-1].values["value"] = float(length - 1)
                nodes[-2].values["child"], nodes[-1].values["child"] = None, Object("leaf")   # Re-linking too.
                self.assertNotEqual(json.dumps(dependencies._rna_values(root), sort_keys=True), text)

    def test_fingerprint_keeps_aliases_and_ordinals(self):
        shared = RNA("shared", [declaration("value", "FLOAT")], value=1.0)
        holder = Armature("alias", [declaration("x", "POINTER"), declaration("y", "POINTER")], x=shared, y=shared)
        values = dependencies._rna_values(holder)
        self.assertEqual(values["x"], {"rna_reference": ["RNA", 1]})   # Both paths name the struct's ordinal.
        self.assertEqual(values["y"], {"rna_reference": ["RNA", 1]})
        self.assertEqual(values["#structs"], [{"value": 1.0}])
        other = Armature("alias", [declaration("x", "POINTER"), declaration("y", "POINTER")],
                         x=shared, y=RNA("copy", [declaration("value", "FLOAT")], value=1.0))
        self.assertNotEqual(dependencies._rna_values(other), values)   # Aliasing is part of the fingerprint.
        cycle = RNA("cycle", [declaration("back", "POINTER")], back=None)
        looped = Armature("loop", [declaration("first", "POINTER")], first=cycle)
        cycle.values["back"] = cycle
        self.assertEqual(dependencies._rna_values(looped)["#structs"], [{"back": {"rna_reference": ["RNA", 1]}}])

    def test_a_setting_cannot_collide_with_the_table(self):
        """The table's keys are not identifiers, so a setting named like one (an add-on's) is just a setting."""
        for name in ("rna_structs", "custom_properties", "rna_reference"):
            with self.subTest(name=name):
                holder = Armature("odd", [declaration(name, "FLOAT"), declaration("x", "POINTER")], x=None)
                holder.values[name] = 1.0
                self.assertEqual(dependencies._rna_values(holder)[name], 1.0)

    def test_arrays_nested_past_the_bound_still_refuse(self):
        """The one recursion left: arrays inside one field's value."""
        value = 1.0
        for _ in range(16):   # A field's value nested 16 arrays deep: its plain values sit 17 levels down.
            value = [value]
        with self.assertRaises(ProgramError) as caught:
            dependencies._rna_values(Armature("arrays", [declaration("deep", "FLOAT")], deep=value))
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        inner = dependencies._rna_values(Armature("arrays", [declaration("deep", "FLOAT")], deep=value[0]))["deep"]
        self.assertEqual(nesting(inner), 15)   # As before at a datablock's own fields.

    def test_fingerprint_budget_still_bounds_the_walk(self):
        root, _ = armature(8, Object("leaf"))
        budget = dependencies.fingerprint_budget()
        dependencies._rna_values(root, budget=budget)
        spent = dependencies.MAX_FINGERPRINT_ITEMS - budget[0]
        self.assertGreater(spent, 8)
        dependencies._rna_values(root, budget=[spent, dependencies.MAX_FINGERPRINT_PACKED_BYTES])   # Exactly enough.
        with self.assertRaises(ProgramError) as caught:
            dependencies._rna_values(root, budget=[spent - 1, dependencies.MAX_FINGERPRINT_PACKED_BYTES])
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")

    def test_the_link_walk_has_a_work_bound(self):
        root, bones = armature(50, Object("leaf"))
        with self.assertRaises(ProgramError) as caught:
            list(dependencies._walk_links(root, budget=[20]))
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertIn("bounded dependency walk", str(caught.exception))
        self.assertEqual(len(list(dependencies._walk_links(root, budget=[51]))), 1)   # The datablock and 50 bones.

    def test_a_struct_inside_an_array_is_still_fingerprinted(self):
        inner = RNA("inner", [declaration("value", "FLOAT")], value=2.5)
        holder = Armature("array", [declaration("matrix", "FLOAT")], matrix=[1.0, inner, [2.0, 3.0]])
        values = dependencies._rna_values(holder)
        self.assertEqual(values["matrix"], [1.0, {"rna_reference": ["RNA", 1]}, [2.0, 3.0]])
        self.assertEqual(values["#structs"], [{"value": 2.5}])

if __name__ == "__main__":
    unittest.main()
