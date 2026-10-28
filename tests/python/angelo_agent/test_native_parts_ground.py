# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fit parts to the character image (parts_ground): the request, the numbered parts image and the reply, without Blender."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import parts_ground  # noqa: E402

SIZE = (768, 1024)
IDS = ["body", "hair", "outer", "accessories-1"]


def reply(**parts):
    return json.dumps({"parts": parts})


class RequestTests(unittest.TestCase):
    def test_the_request_is_the_measured_one(self):
        """The wording was measured (compose-eval PREREG-2026-10-23): a change needs a new measurement."""
        self.assertEqual(hashlib.sha256((parts_ground.PROMPT + "\n").encode()).hexdigest()[:8], "d6bd564b")

    def test_the_request_names_nothing_about_a_character(self):
        """Only what the images are made of: the research characters' names, garments and items never appear."""
        text = parts_ground.PROMPT.lower()
        for word in ("orc", "chef", "kimono", "fisher", "wizard", "knight", "fox", "beard", "apron", "scarf", "sandal"):
            self.assertNotIn(word, text)

    def test_only_the_measured_connection_and_model_are_offered(self):
        """1.0.6: the gate was the Codex runtime, which also runs OpenAI API keys, Grok, MiniMax, Kimi and team
        gateways; only GPT 6.1 Sol on a ChatGPT connection was measured (Opus failed). Every connection kind Angelo
        has, bound to AI Enhance with each of its models (and the measured one), is offered only on that pair."""
        from angelo_agent.provider_registry import SPECS
        for mode, model in parts_ground.MEASURED:
            self.assertEqual(SPECS[mode]["group"], "CODING", "AI Enhance runs it as a coding connection")
        offered = set()
        for mode, spec in SPECS.items():
            record = {"id": "00000000-0000-0000-0000-000000000001", "mode": mode, "name": spec.get("name", mode)}
            for model in [entry["id"] for entry in spec.get("models", ())] + ["gpt-6.1-sol", "claude-opus-5-5"]:
                if parts_ground.offered(record, {"connection": record["id"], "model": model}):
                    offered.add((mode, model))
        self.assertEqual(offered, {("codex_subscription", "gpt-6.1-sol")})
        self.assertFalse(parts_ground.measured("codex_api", "gpt-6.1-sol"), "the same model on an API key: unmeasured")

    def test_any_binding_shape_reads_as_not_offered_never_an_error(self):
        """The card draws with it (1.0.6 slot: a bound Gemini key made the merged gate raise while the card drew)."""
        for record, config in (({}, {}), (None, None), ({"mode": "no_such_mode"}, {"model": "gpt-6.1-sol"}),
                               ({"mode": "codex_subscription"}, None), ({"mode": "codex_subscription"}, "gpt-6.1-sol"),
                               ({"id": "x", "mode": "gemini_images_api"}, {"model": "gemini-3.8-flash"}), ([], {})):
            with self.subTest(record=record, config=config):
                self.assertFalse(parts_ground.offered(record, config))
        self.assertTrue(parts_ground.offered({"mode": "codex_subscription"}, {"model": "gpt-6.1-sol"}))
    def test_the_body_is_numbered_first(self):
        parts = [{"id": "hair", "kind": "HAIR"}, {"id": "body", "kind": "BODY"}, {"id": "outer", "kind": "OUTER"}]
        self.assertEqual([p["id"] for p in parts_ground.ordered(parts)], ["body", "hair", "outer"])


class ContactSheetTests(unittest.TestCase):
    def test_each_cut_out_on_white_under_its_number(self):
        cutouts = [np.zeros((900, 300, 4), np.uint8), np.zeros((50, 40, 4), np.uint8), np.zeros((1, 400, 4), np.uint8)]
        cutouts[0][..., :3], cutouts[0][..., 3] = (0, 0, 255), 255       # a tall opaque blue part
        cutouts[1][10:40, 10:30] = (0, 255, 0, 255)                     # a small part with transparent edges
        sheet = parts_ground.contact_sheet(cutouts)
        self.assertEqual(sheet.dtype, np.uint8)
        self.assertEqual(sheet.shape[0], 440)
        self.assertEqual(sheet.shape[1], (120 + 30) + (120 + 30) + (400 + 30), "tall parts shrink to 360 px; a sliver stays")
        first = sheet[:, :150]
        self.assertTrue((first[200, 60] == (0, 0, 255)).all(), "the first part's pixels")
        self.assertTrue((sheet[100, 150 + 15 + 2] == 255).all(), "transparent shows white")
        self.assertTrue((first[10:50, 12:40] == (217, 0, 0)).all(axis=-1).any(), "its number, in red")

    def test_no_parts_is_refused(self):
        with self.assertRaises(ValueError):
            parts_ground.contact_sheet([])


class ParseTests(unittest.TestCase):
    def test_boxes_become_pixels_of_the_image_as_sent(self):
        found = parts_ground.parse("```json\n" + reply(**{"0": [250, 50, 750, 950], "1": [400, 40, 600, 200],
                                                          "2": [200, 150, 800, 700], "3": None}) + "\n```", IDS, SIZE)
        self.assertEqual(found["size"], [768, 1024])
        self.assertEqual(found["body"], [192.0, 51.2, 576.0, 972.8])
        self.assertEqual(set(found["boxes"]), {"hair", "outer"}, "a part not found keeps its sheet box")
        self.assertEqual(found["boxes"]["hair"], [307.2, 41.0, 460.8, 204.8])

    def test_bad_boxes_are_left_out_and_out_of_range_is_clamped(self):
        found = parts_ground.parse(reply(**{"0": [250, 10, 750, 990], "1": [600, 40, 400, 200], "2": ["a", 1, 2, 3],
                                            "3": [-50, 10, 10.5, 30]}), IDS, SIZE)
        self.assertEqual(set(found["boxes"]), {"accessories-1"})
        self.assertEqual(found["boxes"]["accessories-1"][0], 0.0)

    def test_a_figure_running_off_the_image_is_a_failed_fit(self):
        """Its height would set the frame and rescale every part: refused, Place keeps the sheet's layout."""
        for body in ([250, 10, 750, 1000], [250, 0, 750, 900], [250, -40, 750, 960]):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parts_ground.parse(reply(**{"0": body, "1": [400, 40, 600, 200]}), IDS, SIZE)

    def test_a_reply_without_a_usable_body_is_a_failed_fit(self):
        for text in ("", "no json here", reply(**{"1": [1, 2, 3, 4]}), reply(**{"0": [250, 500, 750, 550]}),
                     json.dumps({"parts": [1, 2]}), "{not json}"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parts_ground.parse(text, IDS, SIZE)


if __name__ == "__main__":
    unittest.main()
