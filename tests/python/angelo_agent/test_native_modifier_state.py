# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""modifier_state's rules on stand-in RNA structs (the real modifiers: test_blender_modifier_rollback_inputs):
every owned store is walked and written back; a store that holds data RNA cannot write back is refused by name,
never captured as "no values"; a reference another data-block owns is not walked."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import modifier_state  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402

OWNER = SimpleNamespace(name="object")


class Prop(SimpleNamespace):
    def __init__(self, identifier, kind, readonly=False, fixed=None, **extra):
        super().__init__(identifier=identifier, type=kind, is_readonly=readonly, is_array=False, array_dimensions=(),
                         fixed_type=SimpleNamespace(identifier=fixed or ""), **extra)


class Struct:
    """A stand-in RNA struct: ``fields`` {name: (Prop, value)}; it belongs to ``owner`` (its id_data)."""

    def __init__(self, kind, fields, owner=OWNER):
        object.__setattr__(self, "bl_rna", SimpleNamespace(identifier=kind,
                                                           properties=[prop for prop, _ in fields.values()]))
        object.__setattr__(self, "id_data", owner)
        for name, (_prop, value) in fields.items():
            object.__setattr__(self, name, value)


class Modifier(Struct):
    def __init__(self, fields, kind="NODES"):
        super().__init__("NodesModifier", fields)
        object.__setattr__(self, "name", "QA")
        object.__setattr__(self, "type", kind)


class Items(list):
    pass


def socket(value, kind="value"):
    return Struct("Socket_1", {"name": (Prop("name", "STRING", readonly=True), "Amount"),
                               "value": (Prop("value", "FLOAT"), value),
                               "type": (Prop("type", "ENUM"), kind),
                               "attribute_name": (Prop("attribute_name", "STRING"), "")})


def nodes_modifier(value=2.5, extra=None):
    inputs = Struct("GeometryNodesInterfaceInputs", {"Socket_1": (Prop("Socket_1", "POINTER", readonly=True), socket(value))})
    properties = Struct("NodesModifierProperties", {"inputs": (Prop("inputs", "POINTER", readonly=True), inputs)})
    fields = {"properties": (Prop("properties", "POINTER", readonly=True), properties),
              "persistent_uid": (Prop("persistent_uid", "INT", readonly=True), 991)}
    fields.update(extra or {})
    return Modifier(fields)


class CaptureTests(unittest.TestCase):
    def test_a_geometry_nodes_input_is_captured_from_its_5_3_store(self):
        state = modifier_state._walk(nodes_modifier(2.5), "QA", 0, top=True)
        socket_state = state["properties"]["__struct__"]["inputs"]["__struct__"]["Socket_1"]["__struct__"]
        self.assertEqual(socket_state, {"value": 2.5, "type": "value", "attribute_name": ""})
        self.assertNotIn("persistent_uid", state)   # Blender assigns it; nothing to write back.

    def test_a_read_only_value_holding_data_is_refused_by_name(self):
        for name, value in (("is_bound", True), ("total_levels", 2), ("vertex_indices", (1, 4)), ("is_baked", True)):
            with self.subTest(name=name):
                modifier = nodes_modifier(extra={name: (Prop(name, "INT", readonly=True), value)})
                with self.assertRaises(modifier_state.Unrestorable) as raised:
                    modifier_state._walk(modifier, "QA", 0, top=True)
                self.assertIn(name, str(raised.exception))

    def test_the_same_read_only_values_unset_hold_nothing(self):
        for name, value in (("is_bound", False), ("total_levels", 0), ("vertex_indices", ())):
            with self.subTest(name=name):
                modifier_state._walk(nodes_modifier(extra={name: (Prop(name, "INT", readonly=True), value)}),
                                     "QA", 0, top=True)

    def test_bakes_and_opaque_structs_are_refused(self):
        bakes = nodes_modifier(extra={"bakes": (Prop("bakes", "COLLECTION", readonly=True, fixed="NodesModifierBake"),
                                                Items([Struct("NodesModifierBake", {})]))})
        with self.assertRaises(modifier_state.Unrestorable) as raised:
            modifier_state._walk(bakes, "QA", 0, top=True)
        self.assertIn("bakes", raised.exception.where)
        system = Struct("ParticleSystem", {})
        particles = nodes_modifier(extra={"particle_system": (Prop("particle_system", "POINTER", readonly=True), system)})
        with self.assertRaises(modifier_state.Unrestorable):
            modifier_state._walk(particles, "QA", 0, top=True)

    def test_an_unknown_collection_with_items_is_refused_and_an_empty_one_is_kept(self):
        unknown = Prop("things", "COLLECTION", readonly=True, fixed="Thing")
        with self.assertRaises(modifier_state.Unrestorable):
            modifier_state._walk(nodes_modifier(extra={"things": (unknown, Items([Struct("Thing", {})]))}), "QA", 0, top=True)
        state = modifier_state._walk(nodes_modifier(extra={"things": (unknown, Items())}), "QA", 0, top=True)
        self.assertEqual(state["things"], {"__items__": []})

    def test_a_struct_another_data_block_owns_is_a_reference_not_a_store(self):
        node = Struct("Node", {"is_bound": (Prop("is_bound", "BOOLEAN", readonly=True), True)},
                      owner=SimpleNamespace(name="node group"))
        state = modifier_state._walk(nodes_modifier(extra={"node": (Prop("node", "POINTER", readonly=True), node)}),
                                     "QA", 0, top=True)
        self.assertNotIn("node", state)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        import angelo_agent.program_transfers as transfers
        self.transfers = transfers

    def test_restore_writes_every_store_and_proves_it(self):
        state = {"name": "QA", "type": "NODES", "settings": {},
                 "stores": modifier_state._walk(nodes_modifier(2.5), "QA", 0, top=True)}
        fresh = nodes_modifier(0.0)
        modifier_state.restore(fresh, state, {})
        self.assertEqual(fresh.properties.inputs.Socket_1.value, 2.5)

    def test_a_value_that_does_not_stick_fails_loudly(self):
        state = {"name": "QA", "type": "NODES", "settings": {},
                 "stores": modifier_state._walk(nodes_modifier(2.5), "QA", 0, top=True)}

        class Stubborn(Struct):
            def __setattr__(self, name, value):
                pass   # Blender clamping or ignoring the write.

        fresh = nodes_modifier(0.0)
        stubborn = Stubborn("Socket_1", {"name": (Prop("name", "STRING", readonly=True), "Amount"),
                                         "value": (Prop("value", "FLOAT"), 0.0), "type": (Prop("type", "ENUM"), "value"),
                                         "attribute_name": (Prop("attribute_name", "STRING"), "")})
        object.__setattr__(fresh.properties.inputs, "Socket_1", stubborn)
        with self.assertRaises(ProgramError):
            modifier_state.restore(fresh, state, {})

    def test_nothing_reads_modifier_id_properties(self):
        """5.3 modifiers have none; reading them answered every Geometry Nodes input with its default."""
        source = (Path(self.transfers.__file__).parent / "program_transfers.py").read_text(encoding="utf-8")
        self.assertNotIn(".keys()", source)
        self.assertNotIn("id_properties", source)


if __name__ == "__main__":
    unittest.main()
