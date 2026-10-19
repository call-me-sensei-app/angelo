# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dependency-edge parity and declared getter canaries; no native app launched."""
import itertools
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_dependencies as dependencies
from angelo_agent.program_jobs import ProgramError


def property_decl(name, kind):
    return SimpleNamespace(identifier=name, type=kind, is_readonly=False)


class Getter:
    def __init__(self, function):
        self.function, self.calls = function, 0

    def __call__(self):
        self.calls += 1
        return self.function()


class RNA:
    pointers = itertools.count(1)

    def __init__(self, name, fields=(), **values):
        self.name, self.pointer = name, next(self.pointers)
        self.values, self.reads = values, []
        self.bl_rna = SimpleNamespace(identifier=type(self).__name__, properties=list(fields))

    def as_pointer(self):
        return self.pointer

    def __getattr__(self, name):
        if name not in self.values:
            raise AttributeError(name)
        self.reads.append(name)
        value = self.values[name]
        return value() if isinstance(value, Getter) else value


class ID(RNA):
    pass


class Mesh(ID):
    pass


class Material(ID):
    pass


class NodeTree(ID):
    pass


class Object(ID):
    pass


OTHER_TYPES = {name: type(name, (ID,), {}) for name in
               ("Action", "Armature", "Key", "Curve", "Lattice", "Camera", "Light",
                "ParticleSettings", "Scene", "Collection", "Curves", "Attribute", "MetaBall", "GreasePencil",
                "PointCloud", "Volume", "LightProbe", "Speaker")}
BPY = SimpleNamespace(types=SimpleNamespace(ID=ID, Mesh=Mesh, Material=Material,
                                           NodeTree=NodeTree, Object=Object, **OTHER_TYPES))


def reference_links(root, depth=0, visited=None):
    """Retained pre-filter walker; the parity oracle includes its original reads."""
    bpy = dependencies._bpy()
    if visited is None:
        visited = set()
    pointer = root.as_pointer() if hasattr(root, "as_pointer") else id(root)
    if pointer in visited:
        return
    visited.add(pointer)
    if depth > 14:
        raise ProgramError("An input dependency graph is too deep to isolate completely", code='LIMIT_EXCEEDED')
    for prop in root.bl_rna.properties:
        name = prop.identifier
        if name in dependencies._RNA_SKIP:
            continue
        item = getattr(root, name, None)
        if prop.type == "POINTER" and item is not None:
            if isinstance(item, bpy.types.ID):
                yield root, prop, item
            elif hasattr(item, "bl_rna"):
                yield from reference_links(item, depth + 1, visited)
        elif prop.type == "COLLECTION" and not isinstance(root, bpy.types.Mesh):
            for child in item:
                if isinstance(child, bpy.types.ID):
                    yield root, prop, child
                elif hasattr(child, "bl_rna"):
                    yield from reference_links(child, depth + 1, visited)


def edges(root, walk=dependencies._embedded_links, **kwargs):
    return [(owner.name, prop.identifier, target.name) for owner, prop, target in walk(root, **kwargs)]


class DependencyLinksTests(unittest.TestCase):
    def setUp(self):
        patched = patch.object(dependencies, "_bpy", return_value=BPY)
        patched.start()
        self.addCleanup(patched.stop)

    def test_pointer_null_non_rna_and_native_id_order_matches_preimage(self):
        first, second = Object("first"), Material("second")
        root = RNA("root", [property_decl("label", "STRING"), property_decl("first", "POINTER"),
            property_decl("missing", "POINTER"), property_decl("opaque", "POINTER"),
            property_decl("second", "POINTER")], label="stored", first=first, missing=None,
            opaque=object(), second=second)
        expected = [("root", "first", "first"), ("root", "second", "second")]
        self.assertEqual(edges(root, reference_links), expected)
        root.reads.clear()
        self.assertEqual(edges(root), expected)
        self.assertEqual(root.reads, ["first", "missing", "opaque", "second"])

    def test_socket_modifier_driver_nested_edges_keep_exact_order(self):
        source, material = Object("user object"), Material("material")
        socket = RNA("socket", [property_decl("default_value", "POINTER")], default_value=source)
        modifier = RNA("modifier", [property_decl("object", "POINTER")], object=source)
        driver = RNA("driver", [property_decl("target", "POINTER")], target=modifier)
        root = RNA("root", [property_decl("material", "POINTER"), property_decl("sockets", "COLLECTION"),
                           property_decl("driver", "POINTER")],
                   material=material, sockets=[socket, object(), socket], driver=driver)
        expected = [("root", "material", "material"), ("socket", "default_value", "user object"),
                    ("modifier", "object", "user object")]
        self.assertEqual(edges(root, reference_links), expected)
        self.assertEqual(edges(root), expected)

    def test_eligible_pointer_and_collection_callbacks_keep_one_call_per_walk(self):
        target = Object("target")
        child = RNA("child", [property_decl("id", "POINTER")], id=target)
        pointer, collection = Getter(lambda: target), Getter(lambda: [child])
        root = RNA("root", [property_decl("reference", "POINTER"), property_decl("children", "COLLECTION")],
                   reference=pointer, children=collection)
        expected = edges(root, reference_links)
        self.assertEqual((pointer.calls, collection.calls), (1, 1))
        self.assertEqual(edges(root), expected)
        self.assertEqual((pointer.calls, collection.calls), (2, 2))

    def test_relevant_getter_error_is_never_defaulted_or_suppressed(self):
        for kind in ("POINTER", "COLLECTION"):
            error = RuntimeError("selected getter failure")
            def fail():
                raise error
            getter = Getter(fail)
            root = RNA("root", [property_decl("relevant", kind)], relevant=getter)
            for walk in (reference_links, dependencies._embedded_links):
                with self.subTest(kind=kind, walk=walk.__name__), self.assertRaises(RuntimeError) as caught:
                    edges(root, walk)
                self.assertIs(caught.exception, error)
            self.assertEqual(getter.calls, 2)

    def test_unused_scalar_and_array_getters_never_run_for_link_enumeration(self):
        def fail():
            raise AssertionError("An unrelated scalar or array getter was fetched")
        getters = {kind: Getter(fail) for kind in ("BOOLEAN", "INT", "FLOAT", "STRING", "ENUM")}
        target = Object("linked")
        fields = [property_decl(kind.lower(), kind) for kind in getters] + [property_decl("id", "POINTER")]
        root = RNA("root", fields, **{kind.lower(): value for kind, value in getters.items()}, id=target)
        self.assertEqual(edges(root), [("root", "id", "linked")])
        self.assertTrue(all(value.calls == 0 for value in getters.values()))

    def test_scalar_fingerprint_still_reads_and_rejects_nonfinite_values(self):
        getter = Getter(lambda: .75)
        root = RNA("root", [property_decl("strength", "FLOAT")], strength=getter)
        self.assertEqual(edges(root), [])
        self.assertEqual(getter.calls, 0)
        self.assertEqual(dependencies._rna_values(root), {"strength": .75})
        self.assertEqual(getter.calls, 1)
        root.values["strength"] = float("nan")
        with self.assertRaises(ProgramError):
            dependencies._rna_values(root)

    def test_mesh_collections_are_not_fetched_but_mesh_pointers_remain_edges(self):
        def fail():
            raise AssertionError("Excluded Mesh collection getter was fetched")
        getter = Getter(fail)
        target = OTHER_TYPES["Key"]("shape keys")
        mesh = Mesh("mesh", [property_decl("vertices", "COLLECTION"), property_decl("shape_keys", "POINTER")],
                    vertices=getter, shape_keys=target)
        self.assertEqual(edges(mesh), [("mesh", "shape_keys", "shape keys")])
        self.assertEqual(getter.calls, 0)

    def test_mesh_material_users_and_nested_node_tree_are_still_collected(self):
        user = Object("socket user")
        socket = RNA("socket", [property_decl("default_value", "POINTER")], default_value=user)
        tree = NodeTree("tree", [property_decl("nodes", "COLLECTION")], nodes=[socket])
        material = Material("material", [property_decl("node_tree", "POINTER")], node_tree=tree)
        mesh = Mesh("mesh", [property_decl("materials", "COLLECTION")], materials=[material])
        obj = Object("owner", [property_decl("data", "POINTER")], data=mesh)
        expected = [obj, mesh, material, tree]
        self.assertEqual(dependencies._mutable_dependencies(obj, reference_links), expected)
        self.assertEqual(dependencies._mutable_dependencies(obj), expected)
        self.assertIn(("socket", "default_value", "socket user"), edges(tree))

    def test_an_id_a_collection_holds_is_a_link_and_is_never_entered(self):
        """A curve's materials: walked as embedded structs, a staged curve reached a borrowed material's tree."""
        def fail():
            raise AssertionError("The walk entered a material a collection holds")
        tree = Getter(fail)
        material = Material("material", [property_decl("node_tree", "POINTER")], node_tree=tree)
        materials = property_decl("materials", "COLLECTION")
        materials.fixed_type = SimpleNamespace(identifier="Material", base=SimpleNamespace(identifier="ID", base=None))
        curve = OTHER_TYPES["Curve"]("curve", [materials], materials=[material, None])
        self.assertEqual(edges(curve), [("curve", "materials", "material")])
        self.assertEqual(edges(curve, reference_links), edges(curve))
        self.assertEqual(tree.calls, 0)

    def test_element_views_and_attribute_values_are_never_iterated(self):
        def fail():
            raise AssertionError("A per-element view was iterated")
        surface = Object("surface")
        views = {name: Getter(fail) for name in dependencies.ELEMENT_VIEWS["Curves"]}
        hair = OTHER_TYPES["Curves"]("hair", [property_decl(name, "COLLECTION") for name in sorted(views)]
                                     + [property_decl("surface", "POINTER")], surface=surface, **views)
        self.assertEqual(edges(hair), [("hair", "surface", "surface")])
        values = Getter(fail)
        attribute = RNA("radius", [property_decl("data", "COLLECTION")], data=values)
        attribute.bl_rna.identifier = "FloatAttribute"
        attribute.bl_rna.base = SimpleNamespace(identifier="Attribute", base=None)
        self.assertEqual(edges(attribute), [])
        self.assertEqual((sum(getter.calls for getter in views.values()), values.calls), (0, 0))

    def test_shared_and_cyclic_non_id_nodes_keep_preimage_visited_behavior(self):
        target = Object("target")
        root = RNA("root", [property_decl("child", "POINTER")])
        child = RNA("child", [property_decl("parent", "POINTER"), property_decl("id", "POINTER")],
                    parent=root, id=target)
        root.values["child"] = child
        root.bl_rna.properties.append(property_decl("again", "POINTER"))
        root.values["again"] = child
        expected = [("child", "id", "target")]
        self.assertEqual(edges(root, reference_links), expected)
        self.assertEqual(edges(root), expected)
        self.assertEqual(edges(root, visited={root.as_pointer()}), [])

    def test_depth_boundary_and_limit_refusal_match_preimage(self):
        for depth in (14, 15):
            nodes = [RNA(str(i), [property_decl("child", "POINTER")]) for i in range(depth + 1)]
            for left, right in zip(nodes, nodes[1:]):
                left.values["child"] = right
            nodes[-1].values["child"] = Object("leaf")
            for walk in (reference_links, dependencies._embedded_links):
                with self.subTest(depth=depth, walk=walk.__name__):
                    if depth == 14:
                        self.assertEqual(edges(nodes[0], walk), [("14", "child", "leaf")])
                    else:
                        with self.assertRaises(ProgramError) as caught:
                            edges(nodes[0], walk)
                        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")

    def test_rna_skip_does_not_fetch_even_a_pointer_getter(self):
        def fail():
            raise AssertionError("Skipped pointer getter was fetched")
        getter = Getter(fail)
        root = RNA("root", [property_decl("original", "POINTER")], original=getter)
        self.assertEqual(edges(root, reference_links), [])
        self.assertEqual(edges(root), [])
        self.assertEqual(getter.calls, 0)

    def test_second_walk_observes_replacement_without_cross_pass_cache(self):
        first, second = Object("first"), Object("second")
        root = RNA("root", [property_decl("target", "POINTER")], target=first)
        self.assertEqual(edges(root), [("root", "target", "first")])
        root.values["target"] = second
        self.assertEqual(edges(root), [("root", "target", "second")])

    def test_existing_mutable_dependency_budget_still_refuses(self):
        second = Material("second")
        first = Material("first", [property_decl("next", "POINTER")], next=second)
        with patch.object(dependencies, "MAX_STAGE_IDS", 1), self.assertRaises(ProgramError) as caught:
            dependencies._mutable_dependencies(first)
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
