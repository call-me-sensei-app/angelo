# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Material identity in the staged kernel, with small RNA stand-ins.

These tests exercise the actual ownership and sharing functions. Blender ID
user counts, commit, and cleanup require the compiled material-sharing suite.
"""

import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import material_sharing, node_edit_spec  # noqa: E402


class Material:
    def __init__(self, name, users=1, **markers):
        self.name, self.users, self.markers = name, users, markers
        self.session_uid = id(self)
        self.id_type = "MATERIAL"
        self.node_tree = object()
        self.animation_data = None

    def keys(self):
        return self.markers.keys()

    def get(self, key, default=None):
        return self.markers.get(key, default)

    def as_pointer(self):
        return id(self)

    def __getitem__(self, key):
        return self.markers[key]

    def __setitem__(self, key, value):
        self.markers[key] = value

    def __delitem__(self, key):
        del self.markers[key]

    def copy(self):
        return Material(self.name + ".001", users=0, **self.markers)


class Group(Material):
    def __init__(self, name, users=1, **markers):
        super().__init__(name, users, **markers)
        self.id_type = "NODETREE"

    def copy(self):
        return Group(self.name + ".001", users=0, **self.markers)


class Slot:
    def __init__(self, data, index, link="DATA"):
        self.data, self.index, self.link = data, index, link
        self.override = data.materials[index]

    @property
    def material(self):
        return self.override if self.link == "OBJECT" else self.data.materials[self.index]

    @material.setter
    def material(self, value):
        if self.link == "OBJECT":
            self.override = value
        else:
            self.data.materials[self.index] = value


def owner(*materials, links=()):
    data = SimpleNamespace(materials=list(materials))
    return SimpleNamespace(data=data, material_slots=[Slot(data, index, links[index] if links else "DATA")
                                                     for index in range(len(materials))])


class Context:
    def __init__(self, objects=(), aliases=(), facts=None):
        self.objects, self.aliases = dict(objects), dict(aliases)
        self.extras = {node_edit_spec.SHARING: facts or {}}
        self.owned_ids = {slot.material for obj in [*self.objects.values(), *self.aliases.values()]
                          for slot in obj.material_slots}
        self.copies = []

    def track_id(self, block):
        self.owned_ids.add(block)
        self.copies.append(block)
        return block

    def resolve(self, target):
        return self.objects.get(target, self.aliases.get(target))


class MaterialIdentityTests(unittest.TestCase):
    def setUp(self):
        native_types = SimpleNamespace(Material=Material, World=type("World", (), {}))
        self.modules = patch.dict(sys.modules, {"bpy": SimpleNamespace(types=native_types)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.kernel = importlib.import_module("angelo_agent.node_edit")

    def edit(self, context, target="b", name="Paint", policy="REFUSE"):
        obj = context.objects.get(target, context.aliases.get(target))
        return self.kernel._material(context, target, obj, {"material": name, "shared": policy}, True)

    def facts(self, others=1):
        return {"b": {"7": {"name": "Paint", "others": others, "who": "Other", "data_shared": False}}}

    def assert_copy_proof(self, context, previous, private):
        provenance = importlib.import_module("angelo_agent.program_material_provenance")
        registered = provenance._registered(context, private)
        self.assertIsNotNone(registered)
        token, _entry = registered
        self.assertTrue(token.startswith("pending:"))
        self.assertEqual(context._obligation_copy_sources[token], (previous.as_pointer(), previous.session_uid))
        self.assertTrue(provenance._from_source(context, private, (previous.as_pointer(), previous.session_uid)))
        unrelated = Material(previous.name, **previous.markers)
        context.owned_ids.add(unrelated)
        self.assertFalse(provenance._from_source(context, private, (unrelated.as_pointer(), unrelated.session_uid)))

    def test_fresh_all_users_keeps_real_aliases_and_unrelated_materials(self):
        paint, green, copied = Material("Paint", 2), Material("Green"), Material("Paint Copy")
        a, b, c, d = owner(paint), owner(paint), owner(green), owner(copied)
        context = Context(objects={"c": c}, aliases={"a": a, "b": b, "d": d})
        self.assertIs(self.edit(context, policy="ALL_USERS"), paint)
        self.assertEqual([obj.material_slots[0].material for obj in [a, b, c, d]], [paint, paint, green, copied])
        self.assertFalse(context.copies)

    def test_live_uid_unification_updates_slots_and_object_linked_backing(self):
        paint = Material("Paint stage", **{node_edit_spec.SOURCE: 7})
        clone = Material("Paint stage other", **{node_edit_spec.SOURCE: 7})
        other = Material("Other", **{node_edit_spec.SOURCE: 8})
        fresh = Material("Fresh")
        a, b, c = owner(paint), owner(clone, clone, links=("DATA", "OBJECT")), owner(other, fresh)
        context = Context(objects={"a": a, "b": b}, aliases={"c": c})
        material_sharing.unify(context, paint, 7)
        self.assertEqual([slot.material for slot in b.material_slots], [paint, paint])
        self.assertEqual(b.data.materials, [paint, paint])
        self.assertEqual(c.data.materials, [other, fresh])

    def test_live_uid_unification_preserves_the_isolated_edit_conflict(self):
        paint = Material("Paint stage", **{node_edit_spec.SOURCE: 7})
        isolated = Material("Paint isolated", **{node_edit_spec.EDITED: 7, node_edit_spec.MODE: "ASSIGN"})
        context = Context(objects={"a": owner(paint), "b": owner(isolated)})
        with self.assertRaisesRegex(node_edit_spec.NodeEditError, "SHARED_CONFLICT"):
            material_sharing.unify(context, paint, 7)

    def test_fresh_shared_refuse_reports_the_policy_before_copying(self):
        paint = Material("Paint", 2)
        a, b = owner(paint), owner(paint)
        context = Context(aliases={"a": a, "b": b})
        with self.assertRaisesRegex(node_edit_spec.NodeEditError, "NODE_TREE_SHARED.*copy_as"):
            self.edit(context)
        self.assertFalse(context.copies)
        self.assertEqual([a.material_slots[0].material, b.material_slots[0].material], [paint, paint])

    def test_fresh_target_only_material_keeps_its_exact_name_and_id(self):
        for policy in ("REFUSE", "ISOLATE", "ALL_USERS"):
            with self.subTest(policy=policy):
                paint = Material("Paint Copy", 2)
                obj = owner(paint, paint)
                context = Context(objects={"b": obj})
                self.assertIs(self.edit(context, name="Paint Copy", policy=policy), paint)
                self.assertEqual(paint.name, "Paint Copy")
                self.assertFalse(context.copies)

    def test_live_uid_isolation_rewires_only_the_target_and_its_backing(self):
        paint = Material("Paint stage", 4, **{node_edit_spec.SOURCE: 7})
        a, b = owner(paint), owner(paint, paint, links=("DATA", "OBJECT"))
        context = Context(objects={"a": a, "b": b}, facts=self.facts())
        private = self.edit(context, policy="ISOLATE")
        self.assertIsNot(private, paint)
        self.assertEqual([slot.material for slot in b.material_slots], [private, private])
        self.assertEqual(b.data.materials, [private, private])
        self.assertIs(a.material_slots[0].material, paint)
        self.assertEqual(private[node_edit_spec.EDITED], 7)
        self.assertEqual(private[node_edit_spec.MODE], "ASSIGN")
        self.assertNotIn(node_edit_spec.SOURCE, private.keys())
        self.assert_copy_proof(context, paint, private)

    def test_fresh_isolation_preserves_logical_lookup_and_only_rewires_its_owner(self):
        paint = Material("Paint", 4)
        a, b = owner(paint), owner(paint, paint, links=("DATA", "OBJECT"))
        context = Context(objects={"a": a, "b": b})
        private = self.edit(context, policy="ISOLATE")
        self.assertEqual([slot.material for slot in b.material_slots], [private, private])
        self.assertEqual(b.data.materials, [private, private])
        self.assertIs(a.material_slots[0].material, paint)
        self.assertEqual(private.name, "Paint.001")
        self.assertEqual(self.kernel.logical_name(context, "b", private), "Paint")
        self.assertNotIn(node_edit_spec.SOURCE, private.keys())
        self.assertNotIn(node_edit_spec.EDITED, private.keys())
        private.users = 3
        self.assertIs(self.edit(context, policy="ISOLATE"), private)
        self.assertEqual(len(context.copies), 1)

    def test_fresh_logical_name_does_not_trust_an_unregistered_marker(self):
        from angelo_agent.program_material_facts import MARKER
        material = Material("Actual Paint", **{MARKER: "invented"})
        context = Context(objects={"b": owner(material)})
        self.assertEqual(self.kernel.logical_name(context, "b", material), "Actual Paint")

    def test_assignment_uses_isolated_identity_with_explicit_source_or_its_target(self):
        # Import the assignment lookup without loading unrelated Blender mesh helpers.
        mesh_helpers = SimpleNamespace(_mesh=None, _face_selection=None, _readback=None)
        with patch.dict(sys.modules, {"angelo_agent.action_context": mesh_helpers}):
            materials = importlib.import_module("angelo_agent.action_materials")
        paint = Material("Paint", 2)
        a, b, c = owner(paint), owner(paint), owner()
        context = Context(objects={"a": a, "b": b, "c": c})
        private = self.edit(context, policy="ISOLATE")
        explicit = {"material": "Paint", "source": "b"}
        implicit = {"material": "Paint", "source": None}
        self.assertIs(materials._find(context, c, explicit, "c"), private)
        self.assertIs(materials._find(context, b, implicit, "b"), private)
        self.assertIs(materials._find(context, c, implicit, "c"), paint)
        with self.assertRaisesRegex(ValueError, "its materials: 'Paint'$"):
            materials._find(context, c, {"material": "Missing", "source": "b"}, "c")

    def staged_live_copy(self, name="Castle pale limestone"):
        """A live material and the staged clone of it (named with a suffix) that staging gave a declared object."""
        live = Material(name, 1)
        sys.modules["bpy"].data = SimpleNamespace(materials=[live])
        return live, Material(name + ".001", **{node_edit_spec.SOURCE: live.session_uid})

    def test_a_staged_live_copy_has_its_live_name_on_any_holder(self):
        live, clone = self.staged_live_copy()
        keep, gatehouse = owner(clone), owner(clone)
        context = Context(objects={"keep": keep, "gatehouse": gatehouse}, aliases={"merlon": owner(clone)})
        for holder in ("keep", "gatehouse", "merlon"):  # No sharing facts: none of them is a node_edit target.
            self.assertEqual(self.kernel.logical_name(context, holder, clone), live.name)

    def test_a_reused_source_material_is_found_again_after_its_first_reuse(self):
        # The castle one-shot: the first material_assign from the keep registered the keep's
        # staged copy under its private name 'Castle pale limestone.001', and the second reuse of the same source
        # in the same program then refused MATERIAL_NOT_FOUND.
        mesh_helpers = SimpleNamespace(_mesh=None, _face_selection=None, _readback=None)
        with patch.dict(sys.modules, {"angelo_agent.action_context": mesh_helpers}):
            materials = importlib.import_module("angelo_agent.action_materials")
        provenance = importlib.import_module("angelo_agent.program_material_provenance")
        live, clone = self.staged_live_copy()
        keep, gatehouse, merlon = owner(clone), owner(), owner()
        context = Context(objects={"keep": keep, "gatehouse": gatehouse}, aliases={"$merlon": merlon})
        params = {"material": live.name, "source": "keep"}
        self.assertIs(materials._find(context, gatehouse, params, "gatehouse"), clone)
        # finish_operation registers the reused copy under the name it reads for the assignment's target.
        token = "live:%d" % live.session_uid
        provenance._register(context, token, clone, self.kernel.logical_name(context, "gatehouse", clone))
        self.assertEqual(context.extras[provenance.REGISTRY][token]["name"], live.name)
        self.assertIs(materials._find(context, merlon, params, "$merlon"), clone)

    def test_a_copied_marker_does_not_grant_another_id_the_logical_name(self):
        from angelo_agent.program_material_facts import MARKER
        paint = Material("Paint", 2)
        context = Context(objects={"a": owner(paint), "b": owner(paint)})
        private = self.edit(context, policy="ISOLATE")
        forged = Material("Unrelated", **{MARKER: private[MARKER]})
        context.owned_ids.add(forged)
        self.assertEqual(self.kernel.logical_name(context, "b", forged), "Unrelated")

    def test_failed_isolation_proof_keeps_slots_and_tracks_the_copy_for_cleanup(self):
        program_material_facts = importlib.import_module("angelo_agent.program_material_facts")
        paint = Material("Paint", 2)
        a, b = owner(paint), owner(paint)
        context = Context(objects={"a": a, "b": b})
        with patch.object(program_material_facts, "record_isolation", side_effect=ValueError("injected proof failure")):
            with self.assertRaisesRegex(ValueError, "proof failure"):
                self.edit(context, policy="ISOLATE")
        self.assertEqual([a.material_slots[0].material, b.material_slots[0].material], [paint, paint])
        self.assertEqual(len(context.copies), 1)
        self.assertIn(context.copies[0], context.owned_ids)
        self.assertNotIn(program_material_facts.REGISTRY, context.extras)

    def test_copy_as_records_exact_source_after_enforcing_the_new_name(self):
        mesh_helpers = SimpleNamespace(_mesh=None, _face_selection=None, _readback=None)
        with patch.dict(sys.modules, {"angelo_agent.action_context": mesh_helpers}):
            materials = importlib.import_module("angelo_agent.action_materials")
        provenance = importlib.import_module("angelo_agent.program_material_provenance")
        paint = Material("Paint stage", **{node_edit_spec.SOURCE: 7, provenance.CLONE_MARKER: "source-copy"})
        context = Context(objects={"b": owner(paint)})
        private = materials._copy(context, paint, "Private Paint")
        self.assertEqual(private.name, "Private Paint")
        self.assertEqual(private[materials.COPY], 1)
        self.assertNotIn(node_edit_spec.SOURCE, private.keys())
        self.assertNotIn(provenance.CLONE_MARKER, private.keys())
        self.assertEqual(self.kernel.logical_name(context, "b", private), "Private Paint")
        self.assert_copy_proof(context, paint, private)

    def test_nested_group_copy_records_lineage_before_replacing_the_group(self):
        paint = Material("Paint stage", **{node_edit_spec.SOURCE: 7})
        group = Group("Group stage", 2, **{node_edit_spec.SOURCE: 8})
        node = SimpleNamespace(bl_idname="ShaderNodeGroup", node_tree=group)
        paint.node_tree = SimpleNamespace(nodes={"Detail": node})
        facts = self.facts(others=0)
        facts["b"]["8"] = {"name": "Group", "others": 0, "who": "", "data_shared": False}
        context = Context(objects={"b": owner(paint)}, facts=facts)
        context.owned_ids.add(group)
        root, private = self.kernel._nested(context, "b", paint,
                                            {"material": "Paint", "shared": "REFUSE", "group": "Detail"})
        self.assertIs(root, paint)
        self.assertIs(node.node_tree, private)
        self.assertIsNot(private, group)
        self.assertEqual(private[node_edit_spec.EDITED], 8)
        self.assertEqual(private[node_edit_spec.MODE], "REMAP")
        self.assert_copy_proof(context, group, private)

    def test_live_uid_refuse_still_rejects_other_live_users(self):
        paint = Material("Paint stage", **{node_edit_spec.SOURCE: 7})
        context = Context(objects={"b": owner(paint)}, facts=self.facts())
        with self.assertRaisesRegex(node_edit_spec.NodeEditError, "NODE_TREE_SHARED.*other user"):
            self.edit(context)

    def test_assigned_live_material_still_refuses_a_private_edit(self):
        paint = Material("Paint stage", 2, **{node_edit_spec.SOURCE: 7})
        context = Context(objects={"a": owner(paint), "b": owner(paint)}, facts=self.facts(others=0))
        material_sharing.note_assignment(context, paint)
        with self.assertRaisesRegex(node_edit_spec.NodeEditError, "NODE_TREE_SHARED.*assigned"):
            self.edit(context, policy="ISOLATE")
        self.assertFalse(context.copies)


if __name__ == "__main__":
    unittest.main()
