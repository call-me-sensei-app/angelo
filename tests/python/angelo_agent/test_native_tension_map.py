# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tension map (1.0.9): the contracts that hold without a running Blender.

Covered: the card's choices (range steps shown as percents, a value set elsewhere chosen by no segment, one rest
source for several meshes, which meshes a click measures); the card's layout and input against a stand-in source
(a corner card inside the region; the controls a state offers; every action reaching the source; Remove and Done
close it; the shape key list closes first); the add-on wiring (+ menu entry, registration, translation scope); and
the native wiring the feature depends on (the modifier's number and registration, its placement after the
deform-only modifiers, Rest Position after generated geometry, the overlay in every phase of the overlay engine, the
draw cache's tension buffer with GPU subdivision, versioning of older files, the Add Modifier menu, the agent's typed
``modifier_add`` reaching the type). The modifier's numbers and the overlay on screen are
``test_blender_tension_map.py`` and ``test_blender_tension_map_gui.py``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
SOURCE = ROOT / "source/blender"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import card_kit as kit, tension_map_logic as logic  # noqa: E402


def read(path):
    return Path(path).read_text(encoding="utf-8")


class LogicTests(unittest.TestCase):
    def test_steps_read_as_percents(self):
        self.assertEqual(logic.percent(0.25), "25%")
        self.assertEqual(logic.percent(1.0), "100%")
        self.assertEqual(logic.percent(0.125), "12.5%")
        self.assertEqual([label for _, label in logic.segments(logic.STRETCH_STEPS)],
                         ["5%", "10%", "25%", "50%", "100%"])

    def test_a_value_set_elsewhere_chooses_no_segment(self):
        self.assertEqual(logic.chosen_step(0.25, logic.STRETCH_STEPS), 0.25)
        self.assertEqual(logic.chosen_step(0.2500001, logic.STRETCH_STEPS), 0.25)   # Float noise from RNA.
        self.assertIsNone(logic.chosen_step(0.3, logic.STRETCH_STEPS))

    def test_one_rest_source_for_several_meshes_only_when_they_agree(self):
        self.assertEqual(logic.rest_choice(["ORIGINAL", "ORIGINAL"]), "ORIGINAL")
        self.assertIsNone(logic.rest_choice(["ORIGINAL", "SHAPE_KEY"]))
        self.assertIsNone(logic.rest_choice([]))

    def test_a_click_measures_local_meshes_active_first(self):
        objects = [("Cloth", 'MESH', True, False), ("Rig", 'ARMATURE', True, False), ("Body", 'MESH', True, True),
                   ("Linked", 'MESH', False, False), ("Body", 'MESH', True, True)]
        self.assertEqual(logic.target_names(objects), ["Body", "Cloth"])
        self.assertEqual(logic.target_names([("Lamp", 'LIGHT', True, True)]), [])

    def test_every_rest_source_explains_itself(self):
        self.assertEqual({value for value, _ in logic.REST_SOURCES}, set(logic.REST_HELP))
        self.assertEqual({value for value, _ in logic.REST_SOURCES}, {"ORIGINAL", "SHAPE_KEY", "ATTRIBUTE"})


class FakeView:
    """chat_view's measuring surface: half an em per character, wrapped greedily at spaces."""
    _font = _mono = 0

    def measure(self, text, size, font=None):
        return len(text) * size * 0.5

    def wrap(self, value, width, size, font):
        rows, line = [], ""
        for word in value.split():
            candidate = (line + " " + word).strip()
            if line and self.measure(candidate, size) > width:
                rows.append((line, None))
                line = word
            else:
                line = candidate
        return rows + [(line, None)]


class FakeSource:
    def __init__(self, **changes):
        values = dict(names=("Body",), rest_sources=("ORIGINAL",), shape_key="Basis", shape_keys=("Basis", "Smile"),
                      attribute="tension", shown=True, values=(0.25, 0.25, 0.85))
        values.update(changes)
        self.state = logic.Snapshot(**values)
        self.calls = []

    def snapshot(self):
        return self.state

    def apply(self, action, value):
        self.calls.append((action, value))


class CardTests(unittest.TestCase):
    def card(self, **changes):
        from angelo_agent.tension_map_card import TensionCard
        self.source = FakeSource(**changes)
        return TensionCard(self.source)

    def host(self):
        closed = []
        return SimpleNamespace(close=lambda how="done": closed.append(how), redraw=lambda: None, closed=closed)

    def actions(self, card, region=(1600, 900)):
        plan = card.layout(FakeView(), region[0], region[1], 1.0, kit.UiState())
        return plan, {hit[1] for hit in plan.hits}

    def test_a_non_modal_corner_card_inside_the_region(self):
        card = self.card()
        self.assertFalse(card.modal)   # Orbiting, posing and playback go on while it is open.
        for region in ((1600, 900), (700, 500)):
            plan, _ = self.actions(card, region)
            x, y, w, h = plan.card
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + w, region[0])
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(y + h, region[1])

    def test_a_measured_mesh_offers_every_control(self):
        _, actions = self.actions(self.card())
        self.assertTrue({"show", "rest", "stretch", "compress", "opacity", "remove", "done"} <= actions)
        self.assertNotIn("key_open", actions)   # Original rest: no shape key list.
        self.assertNotIn("capture", actions)

    def test_shape_key_rest_offers_the_keys_and_captured_offers_capture(self):
        _, actions = self.actions(self.card(rest_sources=("SHAPE_KEY",)))
        self.assertIn("key_open", actions)
        card = self.card(rest_sources=("ATTRIBUTE",))
        _, actions = self.actions(card)
        self.assertIn("capture", actions)
        _, actions = self.actions(self.card(rest_sources=("SHAPE_KEY",), shape_keys=()))
        self.assertNotIn("key_open", actions)   # No keys: the card says so instead.

    def test_nothing_measured_offers_no_rest_or_remove(self):
        _, actions = self.actions(self.card(names=(), rest_sources=()))
        self.assertNotIn("rest", actions)
        self.assertNotIn("remove", actions)
        self.assertTrue({"show", "done"} <= actions)

    def test_no_3d_view_hides_the_ranges(self):
        _, actions = self.actions(self.card(values=None))
        self.assertFalse({"stretch", "compress", "opacity"} & actions)

    def test_every_control_reaches_the_source(self):
        card, host = self.card(), self.host()
        for action, value in (("show", None), ("rest", "SHAPE_KEY"), ("capture", None), ("stretch", 0.5),
                              ("compress", 0.1), ("opacity", 1.0)):
            card.act(action, value, host)
        self.assertEqual(self.source.calls, [("show", None), ("rest", "SHAPE_KEY"), ("capture", None),
                                             ("stretch", 0.5), ("compress", 0.1), ("opacity", 1.0)])
        self.assertEqual(host.closed, [])

    def test_the_shape_key_list_closes_before_the_card(self):
        card, host = self.card(rest_sources=("SHAPE_KEY",)), self.host()
        card.act("key_open", None, host)
        self.assertTrue(card.picking)
        card.act("done", None, host)   # Escape with the list open.
        self.assertEqual((card.picking, host.closed), (False, []))
        card.act("key_open", None, host)
        card.act("key_pick", "Smile", host)
        self.assertEqual((card.picking, self.source.calls[-1]), (False, ("shape_key", "Smile")))

    def test_remove_and_done_close_it(self):
        card, host = self.card(), self.host()
        card.act("remove", None, host)
        self.assertEqual((self.source.calls, host.closed), ([("remove", None)], ["done"]))
        card, host = self.card(), self.host()
        card.act("done", None, host)
        self.assertEqual((self.source.calls, host.closed), ([], ["done"]))

    def test_the_names_shown_are_capped(self):
        from angelo_agent import tension_map_card
        state = logic.Snapshot(names=("A", "B", "C", "D"), rest_sources=("ORIGINAL",) * 4)
        self.assertEqual(tension_map_card.measuring(state), "Measuring A, B, C, …")
        self.assertEqual(tension_map_card.measuring(logic.Snapshot()), tension_map_card.NO_MESH)


class AddonWiringTests(unittest.TestCase):
    def test_the_plus_menu_runs_the_one_click_operator(self):
        source = read(ADDON / "tension_map.py")
        self.assertIn('"tension_map", MENU_LABEL, MENU_WHY, glyph="tension"', source)
        self.assertIn('operator="angelo.tension_map"', source)
        self.assertIn('bl_idname = "angelo.tension_map"', source)
        self.assertIn("chat_destinations.unregister(\"tension_map\")", source)
        self.assertIn('glyph == "tension"', read(ADDON / "chat_add_menu.py"))

    def test_registered_with_the_addon(self):
        source = read(ADDON / "__init__.py")
        self.assertIn("tension_map.register()", source)
        self.assertIn("tension_map.unregister()", source)

    def test_translated_ui_modules(self):
        sys.path.insert(0, str(ROOT / "scripts/angelo/i18n"))
        import sources
        self.assertTrue({"tension_map", "tension_map_card", "tension_map_logic"} <= sources.UI_MODULES)

    def test_the_agents_typed_modifier_add_reaches_the_type(self):
        from angelo_agent import action_schemas, generic_settings
        self.assertNotIn("TENSION", action_schemas.MODIFIER_SETTINGS)   # RNA-checked settings, no prompt change.
        self.assertRegex("TENSION", generic_settings.TYPE_PATTERN)
        generic_settings.check_type("TENSION", action_schemas.MODIFIER_SETTINGS)   # Not refused.
        self.assertEqual(generic_settings.bound_factor("TENSION", None), 1)   # Bounded: it keeps the vertex count.

    def test_agent_registries_know_it_writes_attributes_only(self):
        from angelo_agent import material_faces, scene_read_fields, script_kit_geometry
        self.assertIn("TENSION", material_faces._INDEX_KEEPING)
        self.assertIn("TENSION", script_kit_geometry.KEEPS_SHAPE)
        self.assertEqual(set(scene_read_fields.MODIFIER_FIELDS["TENSION"]),
                         {"rest_source", "shape_key", "rest_attribute", "attribute_name", "edge_attribute_name",
                          "smooth_iterations"})

    def test_the_contract_is_documented(self):
        doc = read(ROOT / "doc/angelo/tension_map.rst")
        for term in ("show_tension", "rest_position", "tension_rest", "Attribute node", "modifier_add"):
            self.assertIn(term, doc)
        self.assertIn("tension_map.rst", read(ROOT / "AGENTS.md"))


class NativeWiringTests(unittest.TestCase):
    def test_the_modifier_number_stays_clear_of_upstream(self):
        dna = read(SOURCE / "makesdna/DNA_modifier_types.h")
        self.assertIn("eModifierType_Tension = 120,\n  NUM_MODIFIER_TYPES,", dna)
        numbers = [int(n) for n in re.findall(r"eModifierType_\w+ = (\d+),", dna)]
        self.assertEqual(numbers.count(120), 1)
        self.assertLess(max(n for n in numbers if n != 120), 100)
        for field in ("attribute_name", "edge_attribute_name", "shape_key", "rest_attribute", "rest_source",
                      "smooth_iterations"):
            self.assertIn(field, dna)

    def test_the_modifier_is_registered_everywhere_blender_looks(self):
        self.assertIn("INIT_TYPE(Tension);", read(SOURCE / "modifiers/intern/MOD_util.cc"))
        self.assertIn("modifierType_Tension;", read(SOURCE / "modifiers/MOD_modifiertypes.hh"))
        self.assertIn("intern/MOD_tension.cc", read(SOURCE / "modifiers/CMakeLists.txt"))
        rna = read(SOURCE / "makesrna/intern/rna_modifier.cc")
        self.assertIn('{eModifierType_Tension,\n     "TENSION",', rna)
        self.assertIn("rna_def_modifier_tension(brna);", rna)
        self.assertIn("'TENSION'", read(ROOT / "scripts/startup/bl_ui/properties_data_modifier.py"))

    def test_it_is_placed_after_the_deform_only_modifiers(self):
        bke = read(SOURCE / "blenkernel/intern/modifier.cc")
        self.assertIn("else if (mt == eModifierType_Tension) {", bke)
        self.assertIn("ModifierTypeType::OnlyDeform", bke[bke.index("eModifierType_Tension"):])
        ed = read(SOURCE / "editors/object/object_modifier.cc")
        block = ed[ed.index("else if (type == eModifierType_Tension) {"):][:700]
        self.assertIn("OB_MODIFIER_FLAG_ADD_REST_POSITION", block)

    def test_the_modifier_never_overwrites_built_in_data(self):
        mod = read(SOURCE / "modifiers/intern/MOD_tension.cc")
        self.assertIn("attributes.is_builtin(name)", mod)
        self.assertIn('from_attribute("rest_position")', mod)
        self.assertIn("ModifierTypeType::NonGeometrical", mod)

    def test_the_overlay_runs_in_every_phase_and_only_when_on(self):
        instance = read(SOURCE / "draw/engines/overlay/overlay_instance.cc")
        for call in ("layer.tension.begin_sync(", "layer.tension.object_sync(", "layer.tension.pre_draw(",
                     "layer.tension.draw_line("):
            self.assertIn(call, instance)
        overlay = read(SOURCE / "draw/engines/overlay/overlay_tension.hh")
        self.assertIn("state.show_tension()", overlay)
        self.assertIn("!res.is_selection()", overlay)
        self.assertIn("V3D_OVERLAY_TENSION", read(SOURCE / "draw/engines/overlay/overlay_private.hh"))
        draw_cmake = read(SOURCE / "draw/CMakeLists.txt")
        for path in ("overlay_tension.hh", "overlay_tension_infos.hh", "overlay_tension_vert.glsl",
                     "overlay_tension_frag.glsl", "extract_mesh_vbo_tension.cc"):
            self.assertIn(path, draw_cmake)

    def test_the_draw_cache_extracts_tension_with_and_without_gpu_subdivision(self):
        extract = read(SOURCE / "draw/intern/draw_cache_extract_mesh.cc")
        self.assertIn("extract_tension(mr, cache.tension_used)", extract)
        self.assertIn("extract_tension_subdiv(mr, subdiv_cache, cache.tension_used)", extract)
        impl = read(SOURCE / "draw/intern/draw_cache_impl_mesh.cc")
        self.assertIn("cache.tension_needed != cache.tension_used", impl)
        self.assertIn("{VBOType::Position, VBOType::Tension}", impl)

    def test_older_files_get_the_overlay_defaults(self):
        versioning = read(SOURCE / "blenloader/intern/versioning_520.cc")
        self.assertIn('"View3DOverlay", "float", "tension_stretch_max"', versioning)
        body = versioning[versioning.index("void blo_do_versions_520("):]
        self.assertIn("version_angelo_tension_overlay(fd, bmain);", body[:200])
        rna = read(SOURCE / "makesrna/intern/rna_space.cc")
        for name in ("show_tension", "tension_stretch_max", "tension_compress_max", "tension_opacity"):
            self.assertIn('"%s"' % name, rna)


if __name__ == "__main__":
    unittest.main()
