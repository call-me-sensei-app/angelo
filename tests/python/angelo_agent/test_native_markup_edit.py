# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remove BG and Erase on a picture (``markup_edit``): which image model runs it, with what options and prompt, and
what the card says before it runs (its estimated cost, that it takes a few seconds, that the original is kept).
No Blender here."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_edit as me  # noqa: E402
from angelo_agent.studio_generation import validate_generation  # noqa: E402


def model(mode, identity, ready=True, team=False):
    return {"mode": mode, "id": identity, "name": identity, "connection": "c-" + identity, "account": "Account",
            "ready": ready, "verified": True, "team": team}


GEMINI = model("gemini_images_api", "gemini-3.1-flash-image")
OPENAI = model("openai_images_api", "gpt-image-2.5-flare")
SEEDREAM = model("seedream_api", "dola-seedream-5-0-pro-260628")


class ChoiceTests(unittest.TestCase):
    def test_the_persons_chosen_image_model_runs_it(self):
        self.assertEqual(me.choose([OPENAI, GEMINI], "erase", preferred=GEMINI), GEMINI)

    def test_with_no_choice_remove_bg_prefers_a_model_that_returns_transparency(self):
        self.assertEqual(me.choose([GEMINI, OPENAI], "remove_bg"), OPENAI)
        self.assertEqual(me.choose([GEMINI, OPENAI], "erase"), GEMINI)
        self.assertTrue(me.transparent(OPENAI))
        self.assertFalse(me.transparent(GEMINI))

    def test_the_persons_choice_wins_for_remove_bg_too(self):
        self.assertEqual(me.choose([GEMINI, OPENAI], "remove_bg", preferred=GEMINI), GEMINI)

    def test_a_chosen_model_that_is_not_ready_falls_back_to_the_rule(self):
        offline = dict(GEMINI, ready=False)
        self.assertEqual(me.choose([offline, OPENAI], "remove_bg", preferred=offline), OPENAI)

    def test_the_choice_is_found_by_its_account_and_model(self):
        other_account = dict(GEMINI, connection="c-other")
        models = [OPENAI, GEMINI, other_account]
        self.assertEqual(me.preferred(models, ("c-other", GEMINI["id"])), other_account)
        self.assertIsNone(me.preferred(models, ("c-gone", GEMINI["id"])))
        self.assertIsNone(me.preferred(models, None))

    def test_without_a_transparent_model_remove_bg_still_runs_and_says_so(self):
        chosen = me.choose([GEMINI], "remove_bg")
        self.assertEqual(chosen, GEMINI)
        lines = me.card_lines("remove_bg", chosen, (1024, 768))
        self.assertIn(me.PLAIN_BACKGROUND, [line[0] if isinstance(line, tuple) else line for line in lines])

    def test_no_ready_model_is_no_choice(self):
        self.assertIsNone(me.choose([model("gemini_images_api", "gemini-3.1-flash-image", ready=False)], "erase"))

    def test_unknown_actions_are_refused(self):
        with self.assertRaises(ValueError):
            me.choose([GEMINI], "upscale")


class RequestTests(unittest.TestCase):
    def test_options_follow_the_pictures_shape_and_pass_the_studios_own_check(self):
        for chosen in (GEMINI, OPENAI, SEEDREAM):
            for size in ((1920, 1080), (1080, 1920), (1000, 1000), (1200, 900)):
                for action in me.ACTIONS:
                    options = me.options(chosen, action, size)
                    validate_generation(chosen["mode"], chosen["id"], me.prompt(action), options, [{"path": "x"}])
        self.assertEqual(me.options(GEMINI, "erase", (1920, 1080))["aspect"], "16:9")
        self.assertEqual(me.options(GEMINI, "erase", (1200, 900))["aspect"], "4:3")

    def test_remove_bg_on_a_transparent_model_asks_for_a_transparent_background(self):
        self.assertEqual(me.options(OPENAI, "remove_bg", (1000, 1000))["background"], "transparent")
        self.assertNotEqual(me.options(OPENAI, "erase", (1000, 1000)).get("background"), "transparent")

    def test_prompts_are_fixed_english_for_the_model(self):
        self.assertIn("transparent", me.prompt("remove_bg"))
        self.assertIn(me.ERASE_HEX, me.prompt("erase"))


class CardTests(unittest.TestCase):
    def texts(self, action, chosen, size=(1024, 768)):
        return [line if isinstance(line, str) else line[0] for line in me.card_lines(action, chosen, size)]

    def test_the_card_names_the_cost_the_wait_and_that_the_original_is_kept(self):
        texts = self.texts("erase", GEMINI)
        self.assertIn(me.COST, texts)
        self.assertIn(me.WAIT, texts)
        self.assertIn(me.KEPT, texts)
        cost = next(line for line in me.card_lines("erase", GEMINI, (1024, 768))
                    if isinstance(line, tuple) and line[0] == me.COST)
        self.assertTrue(cost[1]["price"].startswith("$"))

    def test_an_unpriced_model_says_its_price_is_unknown_never_zero(self):
        unpriced = model("gemini_images_api", "gemini-nano-banana-2.1")
        self.assertIn(me.UNKNOWN_COST, self.texts("erase", unpriced))

    def test_a_team_model_says_whose_key_pays(self):
        self.assertIn(me.TEAM_PAYS, self.texts("erase", dict(GEMINI, team=True)))
        self.assertIn(me.ACCOUNT_PAYS, self.texts("erase", GEMINI))


if __name__ == "__main__":
    unittest.main()
