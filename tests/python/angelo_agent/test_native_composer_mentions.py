# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""@-mentions: the @ word at the caret, ranking, the pick, and what a request carries (``composer_mentions``,
``composer_mention_ui``). Stand-in scene data; the GUI proof is ``test_blender_composer_mentions``.
"""
import suite_isolation  # noqa: F401

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import composer_mention_ui as ui, composer_mentions as m, context_ledger  # noqa: E402

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def item(name, uid, library=None, objects=()):
    return SimpleNamespace(name=name, session_uid=uid, library=library, all_objects=list(objects))


class Scene:
    def __init__(self, objects, collections):
        self.objects = objects
        self.collection = SimpleNamespace(children_recursive=collections)


class QueryTests(unittest.TestCase):
    def test_the_at_word_at_the_caret(self):
        self.assertEqual(m.query_at("Make @Cu", 8), (5, "Cu"))
        self.assertEqual(m.query_at("@", 1), (0, ""))
        self.assertEqual(m.query_at("line\n@Sw", 8), (5, "Sw"))
        self.assertIsNone(m.query_at("mail@host", 9), "An @ inside a word is not a mention")
        self.assertIsNone(m.query_at("@Cube done", 10), "A space ends it")
        self.assertIsNone(m.query_at("Make @Cu", 3))
        self.assertIsNone(m.query_at("@" + "x" * (m.MAX_QUERY + 1), m.MAX_QUERY + 2))
        self.assertIsNone(m.query_at("", 0))

    def test_ranking_prefix_first_case_and_width_insensitive(self):
        items = [("OBJECT", "Big Cube", 1), ("OBJECT", "Cube", 2), ("MATERIAL", "ＣＵＢＥ Steel", 3),
                 ("COLLECTION", "Props", 4)]
        self.assertEqual([i[1] for i in m.candidates(items, "cu")], ["Cube", "ＣＵＢＥ Steel", "Big Cube"])
        self.assertEqual(len(m.candidates(items, "")), 4)
        self.assertEqual(m.candidates(items, "zz"), [])
        many = [("OBJECT", "Cube.%03d" % i, i) for i in range(50)]
        self.assertEqual(len(m.candidates(many, "cube")), m.MAX_SHOWN)

    def test_scene_items_list_each_kind_and_skip_linked_materials(self):
        scene = Scene([item("Cube", 1)], [item("Props", 2)])
        bpy = SimpleNamespace(data=SimpleNamespace(materials=[item("Steel", 3), item("Linked", 4, library=object())]))
        self.assertEqual(m.scene_items(bpy, scene),
                         [("OBJECT", "Cube", 1), ("COLLECTION", "Props", 2), ("MATERIAL", "Steel", 3)])

    def test_accept_replaces_the_at_word(self):
        self.assertEqual(m.accept("Paint @st now", 9, 6, "Steel"), ("Paint @Steel  now", 13))


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.cube, self.props = item("Cube", 1), item("Props", 2, objects=[1, 2, 3])
        self.scene = Scene([self.cube], [self.props])
        self.steel = item("Steel", 3)
        self.bpy = SimpleNamespace(data=SimpleNamespace(materials=[self.steel]))
        self.controller = SimpleNamespace()
        for kind, thing in (("OBJECT", self.cube), ("COLLECTION", self.props), ("MATERIAL", self.steel)):
            m.remember(self.controller, kind, thing.name, thing.session_uid)

    def resolve(self, message):
        return m.resolve(self.bpy, self.scene, message, self.controller.mention_picks, lambda obj: "scn:7:%d" % obj.session_uid)

    def test_picks_written_in_the_message_in_order(self):
        items = self.resolve("Give @Steel to @Cube in @Props.")
        self.assertEqual([i["kind"] for i in items], ["MATERIAL", "OBJECT", "COLLECTION"])
        self.assertEqual(items[1], {"kind": "OBJECT", "name": "Cube", "handle": "scn:7:1"})
        self.assertEqual(items[2]["objects"], 3)

    def test_a_rename_after_the_pick_resolves_to_the_same_item(self):
        self.cube.name = "Crate"
        self.assertEqual(self.resolve("Scale @Cube up"), [{"kind": "OBJECT", "name": "Crate", "as_written": "Cube",
                                                           "handle": "scn:7:1"}])

    def test_a_deleted_item_is_said_missing(self):
        self.scene.objects = []
        self.assertEqual(self.resolve("Scale @Cube"), [{"kind": "OBJECT", "as_written": "Cube", "missing": True}])

    def test_a_pick_removed_from_the_text_or_inside_a_longer_name_is_not_sent(self):
        self.assertEqual(self.resolve("Scale it"), [])
        # Audit 2026-11-14 F6: "." "-" "_" carry a name on (Blender writes "Cube.001"); punctuation that ends it doesn't.
        for longer in ("Scale @Cube.001", "Scale @Cube-2 too", "Scale @Cube_2", "Scale @CubeX"):
            self.assertEqual(self.resolve(longer), [], longer)
        for ended in ("Scale @Cube.", "Scale @Cube, then stop", "Scale @Cube!", "(@Cube)"):
            self.assertEqual([item["name"] for item in self.resolve(ended)], ["Cube"], ended)

    def test_picks_are_bounded_and_newest_kept(self):
        for index in range(m.MAX_PICKS + 3):
            m.remember(self.controller, "OBJECT", "O%d" % index, 100 + index)
        self.assertEqual(len(self.controller.mention_picks), m.MAX_PICKS)
        self.assertNotIn("@Cube", self.controller.mention_picks)

    def test_mentioned_items_are_an_event_field_every_message_sends(self):
        self.assertIn("mentioned_items", context_ledger.EVENT_OBSERVATION_KEYS)


class PickerTests(unittest.TestCase):
    ITEMS = [("OBJECT", "Cube", 1), ("OBJECT", "Cubert", 2), ("MATERIAL", "Steel", 3)]

    def setUp(self):
        self.state = {}
        self.line = SimpleNamespace(body="Paint @Cu", current_character=9)
        self.controller = SimpleNamespace()
        self.inserted = []

    def insert(self, state, line, text):
        start, end = state["selection"]
        line.body = line.body[:start] + text + line.body[end:]
        line.current_character = start + len(text)
        self.inserted.append(text)

    def key(self, kind, shift=False):
        return ui.key(SimpleNamespace(type=kind, value='PRESS', shift=shift), self.state, self.line, self.controller,
                      self.insert)

    def test_opens_moves_and_picks_through_the_composers_insert(self):
        ui.refresh(self.state, self.line, self.controller, items=self.ITEMS)
        self.assertEqual([row[1] for row in self.state["mention"]["rows"]], ["Cube", "Cubert"])
        self.assertTrue(self.key('DOWN_ARROW'))
        self.assertTrue(self.key('RET'))
        self.assertEqual(self.line.body, "Paint @Cubert ")
        self.assertEqual(self.inserted, ["@Cubert "], "One insert: one Undo step")
        self.assertEqual(self.controller.mention_picks, {"@Cubert": ("OBJECT", 2)})
        self.assertNotIn("mention", self.state)

    def test_escape_closes_and_other_keys_stay_the_composers(self):
        ui.refresh(self.state, self.line, self.controller, items=self.ITEMS)
        self.assertFalse(self.key('A'))
        self.assertFalse(self.key('RET', shift=True), "Shift+Return is a new line, as always")
        self.assertTrue(self.key('ESC'))
        self.assertNotIn("mention", self.state)
        self.assertFalse(self.key('RET'), "Closed: Return sends, as always")

    def test_no_match_or_no_at_word_closes(self):
        ui.refresh(self.state, self.line, self.controller, items=[("OBJECT", "Sphere", 1)])
        self.assertNotIn("mention", self.state)
        self.line.body, self.line.current_character = "Paint", 5
        ui.refresh(self.state, self.line, self.controller, items=self.ITEMS)
        self.assertNotIn("mention", self.state)

    def test_never_on_the_rules_page(self):
        self.state["rules_sheet"] = {"line": self.line}
        ui.refresh(self.state, self.line, self.controller, items=self.ITEMS)
        self.assertNotIn("mention", self.state)

    def test_a_click_on_a_row_picks_it(self):
        ui.refresh(self.state, self.line, self.controller, items=self.ITEMS)
        self.assertTrue(ui.pick(self.state, self.line, self.controller, 0, self.insert))
        self.assertEqual(self.line.body, "Paint @Cube ")
        self.assertFalse(ui.pick(self.state, self.line, self.controller, 0, self.insert), "Nothing open: nothing picked")


class WiringTests(unittest.TestCase):
    def test_chat_view_and_console_wire_the_list_and_the_send(self):
        view = (ADDON / "chat_view.py").read_text(encoding="utf-8")
        self.assertIn("composer_mention_ui.key(event, state, line, controller, typed)", view)
        self.assertIn("composer_mention_ui.refresh(state, line, controller)", view)
        self.assertLess(view.index('state.pop("mention", None) is not None'), view.index("Escape ends editing"),
                        "Escape closes the list before it ends editing")
        console = (ADDON / "console.py").read_text(encoding="utf-8")
        self.assertIn('turn.observation["mentioned_items"] = picked', console)


if __name__ == "__main__":
    unittest.main()
