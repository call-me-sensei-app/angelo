# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A photo dropped on the 3D view (1.0.8): the contracts that hold without a running Blender.

Covered: the card's rows for every kind of drop (a photo the providers take or not, no 3D account ready, a busy chat,
a drop on empty space or on an object), each disabled row saying why; which 3D model bills (Media Studio's choice,
then the last drop's, then the first ready, never one that is not ready); the price on the row (unknown, never $0);
when a made model is placed, held or reported (``decide``); the card's layout (stands beside the drop point inside the
region, the picker only with several models, an error keeps it open, Cancel changes nothing); and the wiring the
real drop depends on (the file handler takes every Blender image, the stock image drop is set aside and given back,
movies keep their own drop, the + menu entry). The real drop is ``test_blender_photo_drop_gui``.
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import card_kit as kit, photo_drop_choices as choices  # noqa: E402

MESHY = {"id": "meshy-7", "mode": "meshy_api", "name": "Meshy 7", "account": "My Meshy", "connection": "c-meshy",
         "ready": True, "team": False, "verified": True}
TRIPO = {"id": "v3.1-20260211", "mode": "tripo_api", "name": "Tripo 3.1", "account": "Studio team",
         "connection": "c-team", "ready": True, "team": True}
UNREADY = dict(MESHY, connection="c-old", ready=False)


def facts(**changes):
    values = dict(name="chair.jpg", model_photo=True, models=[MESHY], model=MESHY, chat_busy=False, target="Table")
    values.update(changes)
    return choices.Facts(**values)


def by_action(rows):
    return {row.action: row for row in rows}


class RowTests(unittest.TestCase):
    def test_a_photo_on_an_object_offers_all_four(self):
        rows = choices.rows(facts())
        self.assertEqual([r.action for r in rows], [choices.MAKE_3D, choices.REFERENCE, choices.MATERIAL, choices.PICTURE])
        self.assertTrue(all(r.enabled for r in rows))
        make = rows[0]
        self.assertEqual(make.subtitle, choices.MODEL_LINE)
        self.assertEqual(dict(make.fields), {"model": "Meshy 7", "account": "My Meshy"})
        self.assertAlmostEqual(make.price, 0.60)   # flow_prices: Meshy 7, textured.
        self.assertEqual(choices.price_label(make), (choices.PRICE, (("amount", "0.60"),)))
        self.assertEqual(dict(rows[2].fields), {"object": "Table"})

    def test_a_team_model_says_the_team_pays(self):
        make = choices.rows(facts(models=[TRIPO], model=TRIPO))[0]
        self.assertEqual(make.subtitle, choices.TEAM_LINE)
        self.assertAlmostEqual(make.price, 0.30)   # Tripo 3.1 from an image, textured.

    def test_a_key_not_checked_yet_says_so_as_generate_does(self):
        unchecked = dict(MESHY, verified=False)
        make = choices.rows(facts(models=[unchecked], model=unchecked))[0]
        self.assertEqual((make.subtitle, make.enabled), (choices.UNCHECKED_LINE, True))

    def test_pricing_assumes_media_studios_own_3d_defaults(self):
        from angelo_agent.provider_registry import SPECS, model_kind
        from angelo_agent.studio_controls import generation_options
        self.assertEqual(choices.GENERATION_OPTIONS, {"polycount": 10000, "texture": True})
        checked = set()
        for mode, spec in SPECS.items():   # Every personal 3D model Studio offers, fal's TRELLIS included.
            for model in spec.get("models", ()):
                if spec.get("group") == "GENAI" and "personal" not in spec and model_kind(mode, model["id"]) == "3D":
                    checked.add(mode)
                    with self.subTest(mode=mode, model=model["id"]):
                        self.assertEqual(generation_options(mode, model["id"], {}), choices.GENERATION_OPTIONS)
        self.assertLessEqual({"tripo_api", "meshy_api", "fal_api"}, checked)
        self.assertIn('generation_options(model["mode"], model["id"], {})',
                      (ADDON / "photo_drop.py").read_text(encoding="utf-8"))

    def test_no_3d_account_asks_to_connect_and_bills_nothing(self):
        make = choices.rows(facts(models=[UNREADY], model=None))[0]
        self.assertEqual((make.action, make.subtitle, make.enabled), (choices.CONNECT, choices.CONNECT_FIRST, True))
        self.assertIsNone(choices.price_label(make))

    def test_a_format_the_providers_cannot_take_disables_model_and_reference_only(self):
        rows = by_action(choices.rows(facts(name="scan.tif", model_photo=False)))
        self.assertEqual((rows[choices.MAKE_3D].enabled, rows[choices.MAKE_3D].subtitle), (False, choices.NOT_A_PHOTO_3D))
        self.assertEqual((rows[choices.REFERENCE].enabled, rows[choices.REFERENCE].subtitle),
                         (False, choices.NOT_A_PHOTO_CHAT))
        self.assertTrue(rows[choices.MATERIAL].enabled and rows[choices.PICTURE].enabled)
        # Without an account the format still decides: there is nothing to connect for a TIFF.
        self.assertFalse(choices.rows(facts(model_photo=False, model=None))[0].enabled)
        self.assertIsNone(choices.price_label(rows[choices.MAKE_3D]))   # A row that can't bill shows no price.

    def test_webp_goes_to_the_chat_but_not_to_the_3d_providers(self):
        rows = by_action(choices.rows(facts(name="cup.webp", model_photo=False, chat_photo=True)))
        self.assertEqual((rows[choices.MAKE_3D].enabled, rows[choices.MAKE_3D].subtitle), (False, choices.NOT_A_PHOTO_3D))
        self.assertTrue(rows[choices.REFERENCE].enabled)
        source = (ADDON / "studio_generation.py").read_text(encoding="utf-8")
        self.assertIn('MODEL_REFERENCE_MIMES = frozenset({"image/png", "image/jpeg"})', source)
        self.assertEqual(source.count("if mime not in MODEL_REFERENCE_MIMES:"), 2)   # Tripo's and Meshy's checks.
        self.assertIn("mime in MODEL_REFERENCE_MIMES", (ADDON / "photo_drop.py").read_text(encoding="utf-8"))

    def test_busy_chat_and_empty_space_say_why(self):
        rows = by_action(choices.rows(facts(chat_busy=True, target="")))
        self.assertEqual((rows[choices.REFERENCE].enabled, rows[choices.REFERENCE].subtitle), (False, choices.CHAT_BUSY))
        self.assertEqual((rows[choices.MATERIAL].enabled, rows[choices.MATERIAL].subtitle), (False, choices.NO_TARGET))

    def test_unknown_price_is_unknown_never_zero(self):
        other = dict(MESHY, id="meshy-9", name="Meshy 9")
        make = choices.rows(facts(models=[other], model=other))[0]
        self.assertIsNone(make.price)
        self.assertEqual(choices.price_label(make), (choices.PRICE_UNKNOWN, ()))

    def test_every_row_text_is_a_translatable_constant(self):
        source = (ADDON / "photo_drop_choices.py").read_text(encoding="utf-8")
        for name in ("NOT_A_PHOTO_3D", "NOT_A_PHOTO_CHAT", "CONNECT_FIRST", "MODEL_LINE", "TEAM_LINE", "UNCHECKED_LINE", "CHAT_BUSY",
                     "CHAT_LINE", "MATERIAL_ON", "NO_TARGET", "PICTURE_LINE", "PRICE", "PRICE_UNKNOWN"):
            self.assertRegex(source, r"\n%s = n_\(" % name)


class ChooseTests(unittest.TestCase):
    def test_preference_order_and_readiness(self):
        models = [UNREADY, MESHY, TRIPO]
        self.assertIs(choices.choose(models), MESHY)
        self.assertIs(choices.choose(models, [choices.key(TRIPO)]), TRIPO)
        self.assertIs(choices.choose(models, ["c-gone|x", choices.key(TRIPO), choices.key(MESHY)]), TRIPO)
        self.assertIs(choices.choose(models, [choices.key(UNREADY)]), MESHY)   # Never one that can't run.
        self.assertIsNone(choices.choose([UNREADY]))


class DecideTests(unittest.TestCase):
    def placement(self, document=3):
        return SimpleNamespace(document=document)

    def test_states(self):
        decide = choices.decide
        self.assertEqual(decide(self.placement(), None, 3, True, True), "gone")
        for status in ("failed", "stopped"):
            self.assertEqual(decide(self.placement(), {"status": status}, 3, True, True), "failed")
        for status in ("generating", "texturing"):
            self.assertEqual(decide(self.placement(), {"status": status}, 3, True, True), "wait")
        ready = {"status": "ready"}
        self.assertEqual(decide(self.placement(), ready, 3, True, True), "place")
        self.assertEqual(decide(self.placement(), ready, 3, True, False), "wait")      # Edit Mode or a tool runs.
        self.assertEqual(decide(self.placement(2), ready, 3, True, True), "elsewhere")  # Another file was opened.
        self.assertEqual(decide(self.placement(), ready, 3, False, True), "elsewhere")  # Its scene is gone.


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


class CardTests(unittest.TestCase):
    def card(self, **changes):
        from angelo_agent.photo_drop_card import PhotoDropCard
        self.calls = []

        def run(action, card):
            self.calls.append(action)
            return self.answer

        self.answer = None
        return PhotoDropCard(facts(**changes), (300, 200), run, area=1)

    def host(self):
        closed = []
        return SimpleNamespace(close=lambda how="done": closed.append(how), closed=closed)

    def test_stands_beside_the_drop_point_inside_the_region(self):
        for point, region in (((300, 200), (1600, 900)), ((1580, 880), (1600, 900)), ((5, 5), (1600, 900))):
            card = self.card()
            card.point = point
            plan = card.layout(FakeView(), region[0], region[1], 1.0, kit.UiState())
            x, y, w, h = plan.card
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + w, region[0])
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(y + h, region[1])
            self.assertFalse(x <= point[0] <= x + w and y <= point[1] <= y + h, "the card covers the drop point")
            shapes = [op for op in plan.ops if op[0] == "shape" and op[2] == kit.TEAL_SOFT]
            self.assertEqual(len(shapes), 1)   # The marker, centred on the point.
            disc = shapes[0][1]
            self.assertAlmostEqual(disc[0] + disc[2] / 2, point[0])

    def test_rows_are_clickable_only_when_enabled_and_the_picker_shows_for_several_models(self):
        plan = self.card(target="").layout(FakeView(), 1600, 900, 1.0, kit.UiState())
        actions = {hit[1] for hit in plan.hits}
        self.assertTrue({choices.MAKE_3D, choices.REFERENCE, choices.PICTURE, "cancel"} <= actions)
        self.assertNotIn(choices.MATERIAL, actions)
        self.assertNotIn("model_open", actions)
        plan = self.card(models=[MESHY, TRIPO]).layout(FakeView(), 1600, 900, 1.0, kit.UiState())
        self.assertIn("model_open", {hit[1] for hit in plan.hits})

    def test_choices_run_once_and_an_error_keeps_the_card_open(self):
        card, host = self.card(), self.host()
        self.answer = "Use up to four references"
        card.act(choices.MAKE_3D, None, host)
        self.assertEqual((self.calls, host.closed, card.error), ([choices.MAKE_3D], [], "Use up to four references"))
        self.answer = None
        card.act(choices.PICTURE, None, host)
        self.assertEqual((host.closed, card.done, card.error), (["done"], choices.PICTURE, ""))

    def test_cancel_runs_nothing_and_closes_the_picker_first(self):
        card, host = self.card(models=[MESHY, TRIPO]), self.host()
        card.act("model_open", None, host)
        card.act("cancel", None, host)
        self.assertEqual((card.picking, host.closed), (False, []))
        card.act("model_open", None, host)
        card.act("model_pick", choices.key(TRIPO), host)
        self.assertIs(card.facts.model, TRIPO)
        card.act("cancel", None, host)
        self.assertEqual((self.calls, host.closed, card.done), ([], ["done"], None))
        self.assertIsNone(card.default_action)   # Return never bills by itself.


class WiringTests(unittest.TestCase):
    source = (ADDON / "photo_drop.py").read_text(encoding="utf-8")

    def test_handler_takes_every_blender_image_and_movies_keep_the_stock_drop(self):
        tree = ast.parse(self.source)
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        self.assertIn("ANGELO_FH_photo_drop", classes)
        self.assertIn("ANGELO_FH_movie_empty", classes)
        self.assertIn('bl_import_operator = "angelo.photo_drop"', self.source)
        self.assertIn('bl_import_operator = "OBJECT_OT_empty_image_add"', self.source)
        self.assertIn("bpy.path.extensions_image", self.source)
        self.assertIn("set(bpy.path.extensions_movie) - set(bpy.path.extensions_image)", self.source)

    def test_stock_image_drop_is_set_aside_and_given_back(self):
        register = self.source[self.source.index("def register():"):self.source.index("def unregister():")]
        unregister = self.source[self.source.index("def unregister():"):]
        self.assertIn('getattr(bpy.types, "VIEW3D_FH_empty_image", None)', register)
        self.assertIn("bpy.utils.unregister_class(_stock)", register)
        self.assertIn("bpy.utils.register_class(_stock)", unregister)
        stock = (ROOT / "scripts/startup/bl_operators/view3d.py").read_text(encoding="utf-8")
        self.assertIn("class VIEW3D_FH_empty_image(FileHandler):", stock)   # The name register() looks up.

    def test_registered_by_the_addon_and_in_the_plus_menu(self):
        init = (ADDON / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("photo_drop.register()", init)
        self.assertIn("photo_drop.unregister()", init)
        from angelo_agent import chat_destinations
        self.assertIn("photo_to_3d", chat_destinations.TOP_LEVEL)

    def test_choices_in_the_scene_push_an_undo_step(self):
        for source in (self.source, (ADDON / "photo_drop_jobs.py").read_text(encoding="utf-8")):
            self.assertIn("bpy.ops.ed.undo_push(", source)


if __name__ == "__main__":
    unittest.main()
