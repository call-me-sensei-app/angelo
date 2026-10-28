# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Particle systems and Distribute Points on Faces: schemas, rejections, bounds, derived checks and predicates.

Dependency-free, with RNA stand-ins. Private staging, commit, rollback,
revisions, evaluation and Undo are covered by test_blender_particles.py and
test_blender_program_commit_effects.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import action_particles, generic_settings, node_generic, particle_rules  # noqa: E402
from angelo_agent.action_catalog import affected_references, resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
OTHER = "scn:" + "a" * 32 + ":9"
LIMIT = 500000


def particle_set(target=OBJ, modifier="Grass", **settings):
    return {"kind": "particle_set", "target": target, "parameters": {"modifier": modifier, "settings": settings}}


def settings(**fields):
    """A default ParticleSettings stand-in (Blender 5.2's defaults)."""
    values = dict(type="EMITTER", count=1000, distribution="JIT", grid_resolution=10, child_type="NONE",
                  child_percent=10, rendered_child_count=100, render_type="HALO", display_method="RENDER",
                  display_step=2, render_step=3, kink="NO", kink_extra_steps=4, trail_count=0, hair_step=5,
                  use_adaptive_subframes=False, subframes=0, frame_start=1.0, physics_type="NEWTON",
                  force_field_1=SimpleNamespace(type="NONE"), force_field_2=SimpleNamespace(type="NONE"),
                  instance_collection=None, collision_collection=None, instance_object=None,
                  effector_weights=SimpleNamespace(collection=None), texture_slots=[None] * 18, animation_data=None)
    values.update(fields)
    return SimpleNamespace(**values)


def system(**fields):
    cache = SimpleNamespace(use_disk_cache=False, use_external=False)
    return SimpleNamespace(point_cache=cache, use_hair_dynamics=fields.pop("use_hair_dynamics", False), **fields)


def code(callable_, *args):
    try:
        callable_(*args)
    except ValueError as error:
        return str(error)
    return None


class CatalogTests(unittest.TestCase):
    def test_particle_set_keeps_json_types_and_declares_instanced_objects(self):
        operation = validate_operation(particle_set(type="HAIR", count=500, hair_length=.25,
                                                    instance_object={"object": OTHER}))
        values = operation["parameters"]["settings"]
        self.assertIsInstance(values["count"], int)  # RNA, not the schema, types it.
        self.assertEqual(list(values), ["type", "count", "hair_length", "instance_object"])  # Write order kept.
        self.assertEqual(affected_references(operation), (OBJ, OTHER))
        self.assertEqual(resource_dependencies(operation)["mutates"], ["object", "particles"])
        self.assertEqual(resource_dependencies(operation)["properties"], [])  # Not object paths at commit.
        self.assertIn("particle_set", DEFERRED_ACTIONS)
        self.assertIn("particle_values", DEFERRED_CHECKS)

    def test_rejections(self):
        cases = [(particle_set(filepath="x"), "FILE_PATH"), (particle_set(**{"texture.x": 1}), "ID_POINTER"),
                 (particle_set(instance_object={"object": OBJ}), "its own object"),
                 (particle_set(), "takes 1 to 64 fields; 0 given"), (particle_set(**{"Count": 1}), "field name"),
                 ({"kind": "particle_set", "target": OBJ, "parameters": {"settings": {"count": 1}}}, "missing"),
                 (particle_set(**{"drivers.x": 1}), "SCRIPT_OR_DRIVER")]
        for operation, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, code(validate_operation, operation) or "accepted")

    def test_particle_system_is_a_generic_modifier_type(self):
        generic_settings.check_type("PARTICLE_SYSTEM", {"BEVEL"})
        self.assertEqual(generic_settings.bound_factor("PARTICLE_SYSTEM", None), 1)  # The mesh passes through.
        self.assertIn("MODIFIER_TYPE_DENIED", code(generic_settings.check_type, "PARTICLE_INSTANCE", set()))
        # action_modifiers imports bpy; read its UNREMOVABLE set from the source.
        import ast
        source = (Path(generic_settings.__file__).parent / "action_modifiers.py").read_text(encoding="utf-8")
        assignment = next(node for node in ast.parse(source).body if isinstance(node, ast.Assign)
                          and getattr(node.targets[0], "id", "") == "UNREMOVABLE")
        self.assertIn("PARTICLE_SYSTEM", ast.literal_eval(assignment.value.args[0]))  # Never removed or applied.


class DerivedCheckTests(unittest.TestCase):
    def test_particle_values_are_derived_merged_and_valid(self):
        operations = [validate_operation(particle_set(count=400, hair_length=.2)),
                      validate_operation(particle_set(count=300, type="HAIR")),
                      validate_operation(particle_set(modifier="Other", count=10))]
        derived = derive_checks(operations)
        self.assertEqual([check["kind"] for check in derived], ["particle_values", "particle_values"])
        self.assertEqual(derived[0]["values"], {"count": 300, "hair_length": .2, "type": "HAIR"})
        self.assertNotIn("tolerance", derived[0])  # Each number at its own float32 precision.
        self.assertEqual(derived[1]["name"], "Other")
        for check in derived:
            self.assertEqual(validate_check(check), check)
        with self.assertRaises(ValueError):
            validate_check({"kind": "particle_values", "target": OBJ, "name": "Grass", "values": {"filepath": "x"}})


class BudgetTests(unittest.TestCase):
    def test_points_and_steps(self):
        self.assertEqual(particle_rules.system_bound(settings()), (1000, 1000))
        self.assertEqual(particle_rules.system_bound(settings(trail_count=4)), (4000, 1000))
        # Hair: parents x (1 + max children) x (2^max step + 1) path points, plus each parent's keys.
        hair = settings(type="HAIR", render_type="PATH", child_type="INTERPOLATED")
        self.assertEqual(particle_rules.system_bound(hair), (1000 * 101 * 9 + 1000 * 6, 0))
        self.assertEqual(particle_rules.system_bound(settings(type="HAIR", render_type="PATH", kink="SPIRAL"))[0],
                         1000 * 13 + 6000)
        self.assertEqual(particle_rules.system_bound(settings(distribution="GRID", grid_resolution=20))[0], 8000)
        self.assertEqual(particle_rules.system_bound(settings(use_adaptive_subframes=True))[1], 101000)
        self.assertEqual(particle_rules.system_bound(settings(subframes=3, frame_start=-9.0))[1], 1000 * 4 * 11)
        self.assertEqual(particle_rules.system_bound(settings(render_type="OBJECT"), 30)[0], 1000 + 1000 * 30)

    def test_refusals_are_coded(self):
        cases = [(settings(physics_type="BOIDS"), {}, "PARTICLE_PHYSICS_UNSUPPORTED"),
                 (settings(force_field_2=SimpleNamespace(type="WIND")), {}, "PARTICLE_FORCE_FIELD"),
                 (settings(instance_collection=object()), {}, "particle_collection_dependency"),
                 (settings(effector_weights=SimpleNamespace(collection=object())), {}, "particle_collection_dependency"),
                 (settings(texture_slots=[SimpleNamespace(texture=object())]), {}, "particle_texture_dependency"),
                 (settings(animation_data=SimpleNamespace(action=object(), drivers=[], nla_tracks=[])), {},
                  "particle_settings_animation"),
                 (settings(), {"use_hair_dynamics": True}, "PARTICLE_HAIR_DYNAMICS"),
                 (settings(count=600000), {}, "PARTICLE_BUDGET"),
                 (settings(frame_start=-1000000.0), {}, "PARTICLE_BUDGET")]
        for value, fields, expected in cases:
            with self.subTest(expected=expected):
                reason, _points = particle_rules.refusal(system(**fields), value, LIMIT)
                self.assertIn(expected, reason or "accepted")
        disk = system()
        disk.point_cache.use_disk_cache = True
        self.assertIn("particle_disk_cache", particle_rules.refusal(disk, settings(), LIMIT)[0])
        self.assertEqual(particle_rules.refusal(system(), settings(), LIMIT), (None, 1000))

    def test_budget_hook_declares_every_pointer_and_counts_instances(self):
        staged = []
        rock = SimpleNamespace(type="MESH")
        context = SimpleNamespace(assert_staged=staged.append)
        value = settings(render_type="OBJECT", instance_object=rock)
        modifier = SimpleNamespace(particle_system=system(settings=value))
        points = action_particles.modifier_points(context, object(), modifier, lambda target: 24)
        self.assertEqual(points, 1000 + 1000 * 24)
        value.count = 100000
        self.assertIn("PARTICLE_BUDGET", code(action_particles.modifier_points, context, object(), modifier,
                                              lambda target: 24))


class OwnershipTests(unittest.TestCase):
    def test_shared_settings_are_refused_before_any_write(self):
        class Block(dict):  # ID-property access like a ParticleSettings datablock.
            users = 1

        private = Block({action_particles.SOURCE: 42})
        context = SimpleNamespace(owned_ids=[private], extras={action_particles.SHARED: [42]})
        self.assertIn("PARTICLE_SETTINGS_SHARED",
                      code(action_particles.private_settings, context, None, SimpleNamespace(settings=private)))
        context.extras = {}
        context.require_private = lambda block: block
        self.assertIs(action_particles.private_settings(context, None, SimpleNamespace(settings=private)), private)
        self.assertIn("staged", code(action_particles.private_settings, SimpleNamespace(owned_ids=[], extras={}), None,
                                     SimpleNamespace(settings=private)))

    def test_rollback_entry_remaps_the_committed_copy_back(self):
        calls = []
        committed = SimpleNamespace(user_remap=calls.append)
        entry = action_particles._RemapBack(committed)
        self.assertIs(entry.settings, committed)
        setattr(entry, "settings", "previous")  # What program_commit's rollback does with its journal.
        self.assertEqual(calls, ["previous"])

    def test_missing_system_names_the_existing_ones(self):
        stack = type("Stack", (list,), {"get": lambda self, name: next((m for m in self if m.name == name), None)})
        obj = SimpleNamespace(modifiers=stack([SimpleNamespace(name="Hair", type="PARTICLE_SYSTEM"),
                                               SimpleNamespace(name="Bevel", type="BEVEL")]))
        message = code(action_particles._system, obj, "Grass")
        self.assertIn("'Hair'", message)
        self.assertNotIn("Bevel", message)
        self.assertIn("no PARTICLE_SYSTEM", code(action_particles._system, obj, "Bevel"))
        self.assertEqual(action_particles._system(obj, "Hair").name, "Hair")


class PredicateTests(unittest.TestCase):
    def test_particle_values_read_the_systems_settings(self):
        prop = SimpleNamespace(type="INT", identifier="count", is_readonly=False, is_runtime=False, is_hidden=False,
                               subtype="NONE", array_length=0, array_dimensions=(0, 0, 0), hard_min=0, hard_max=10 ** 9)
        value = SimpleNamespace(count=300, bl_rna=SimpleNamespace(properties={"count": prop}))
        modifier = SimpleNamespace(type="PARTICLE_SYSTEM", particle_system=SimpleNamespace(settings=value))
        obj = SimpleNamespace(modifiers={"Grass": modifier})
        check = {"name": "Grass", "values": {"count": 300}}
        self.assertTrue(action_particles.evaluate_check(obj, check, lambda reference: None)[0])
        value.count = 299
        passed, observed = action_particles.evaluate_check(obj, check, lambda reference: None)
        self.assertFalse(passed)
        self.assertEqual(observed["mismatched"], ["count"])
        self.assertFalse(action_particles.evaluate_check(obj, dict(check, name="Other"), lambda reference: None)[0])


class DistributeTests(unittest.TestCase):
    @staticmethod
    def node(name, inputs=(), outputs=("GEOMETRY",), **fields):
        sockets = [SimpleNamespace(identifier=key, name=key, enabled=True, is_linked=linked, default_value=value)
                   for key, value, linked in inputs]
        made = type("Node", (), {})()  # Hashable, like a bpy node.
        made.bl_idname, made.inputs, made.outputs = name, sockets, [SimpleNamespace(type=kind) for kind in outputs]
        for key, value in fields.items():
            setattr(made, key, value)
        return made

    @staticmethod
    def tree(nodes, links=()):
        made = [SimpleNamespace(from_node=a, to_node=b, from_socket=SimpleNamespace(type="GEOMETRY"),
                                to_socket=SimpleNamespace(type="GEOMETRY")) for a, b in links]
        return SimpleNamespace(nodes=nodes, links=made)

    def scatter(self, source, density=10, linked=False, method="RANDOM"):
        name = "Density Max" if method == "POISSON" else "Density"
        scatter = self.node("GeometryNodeDistributePointsOnFaces", [(name, density, linked)], distribute_method=method)
        output = self.node("NodeGroupOutput", outputs=())
        return self.tree([source, scatter, output], [(source, scatter), (scatter, output)])

    def test_density_times_area_plus_rounding(self):
        group = self.node("NodeGroupInput")
        self.assertEqual(node_generic.tree_bound(self.scatter(group), 8, lambda: 4.0), 40 + 8)
        self.assertEqual(node_generic.tree_bound(self.scatter(group, 3, method="POISSON"), 8, lambda: 4.0), 12 + 8)
        grid = self.node("GeometryNodeMeshGrid", [("Size X", 2.0, False), ("Size Y", 3.0, False),
                                                  ("Vertices X", 3, False), ("Vertices Y", 3, False)])
        self.assertEqual(node_generic.tree_bound(self.scatter(grid, 2), 0), 12 + 9)
        cube = self.node("GeometryNodeMeshCube", [("Size", (1.0, 2.0, 3.0), False), ("Vertices X", 2, False),
                                                  ("Vertices Y", 2, False), ("Vertices Z", 2, False)])
        self.assertEqual(node_generic.tree_bound(self.scatter(cube, 1), 0), 22 + 24)

    def test_area_flows_only_through_area_keeping_nodes(self):
        group, smooth = self.node("NodeGroupInput"), self.node("GeometryNodeSetShadeSmooth")
        scatter = self.node("GeometryNodeDistributePointsOnFaces", [("Density", 1, False)])
        chain = self.tree([group, smooth, scatter], [(group, smooth), (smooth, scatter)])
        self.assertEqual(node_generic.tree_bound(chain, 4, lambda: 2.0), 2 + 4)
        moved = self.node("GeometryNodeSetPosition")
        chain = self.tree([group, moved, scatter], [(group, moved), (moved, scatter)])
        self.assertIn("DISTRIBUTE_AREA_UNBOUNDED", code(node_generic.tree_bound, chain, 4, lambda: 2.0))

    def test_unknown_area_and_linked_density_are_refused(self):
        group = self.node("NodeGroupInput")
        self.assertIn("DISTRIBUTE_AREA_UNBOUNDED", code(node_generic.tree_bound, self.scatter(group), 8))
        self.assertIn("AMPLIFIER_INPUT_LINKED", code(node_generic.tree_bound, self.scatter(group, linked=True), 8,
                                                    lambda: 4.0))
        node_generic.check_type("GeometryNodeDistributePointsOnFaces", "GEOMETRY")
        self.assertIn("NODE_TYPE_DENIED", code(node_generic.check_type, "GeometryNodeDistributePointsInVolume",
                                               "GEOMETRY"))


if __name__ == "__main__":
    unittest.main()
