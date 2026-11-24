# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Camera Look card without Blender (``look_card``): its layout (the presets but Clean, breathing only where the
look's lens breathes, Match Exposure's shutter and ISO and the stops they make, Take Off only with a look on), the
request it hands ``look_system.apply`` (Clean takes the look off; exposure as ``look_spec.exposure_stops`` reads it),
the result, and layouts that fit with no overlapping controls. Contract: doc/angelo/camera_look.rst, "The card".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import card_kit as kit, look_card, look_spec  # noqa: E402


class FakeView:
    _font, _mono = 1, 2

    @staticmethod
    def measure(text, size, font):
        return len(text) * size * (0.60 if font == 2 else 0.52)

    def wrap(self, value, width, size, font):
        out, offset = [], 0
        for paragraph in value.split("\n"):
            line = ""
            for word in paragraph.split(" "):
                trial = (line + " " + word) if line else word
                if line and self.measure(trial, size, font) > width:
                    out.append((line + " ", offset))
                    offset += len(line) + 1
                    line = word
                else:
                    line = trial
            out.append((line, offset))
            offset += len(line) + 1
        return tuple(out)


VIEW = FakeView()


class Host:
    def __init__(self):
        self.closed = None

    def redraw(self):
        pass

    def close(self, how="done"):
        self.closed = how

    def after(self, seconds, function):
        pass

    def window(self):
        return None


def hits(plan, action=None):
    return [(value, rect) for rect, name, value in plan.hits if action is None or name == action]


def texts(plan):
    """The words the card draws: its text, and its kit controls' (a notice's title and body)."""
    words = []
    for op in plan.ops:
        if op[0] in {"text", "mono"}:
            words.append(op[1])
        elif op[0] == "kit":
            words.extend(str(op[3][key]) for key in ("title", "body", "label", "caption") if op[3].get(key))
    return " ".join(words)


EDGE = 1e-6   # Controls that only touch (a segmented control's neighbours) do not overlap.
OVERLAY = {"preset"}   # The open Look list's options: a popover drawn over the content, whose hits win.


def overlap(a, b):
    return (a[0] < b[0] + b[2] - EDGE and b[0] < a[0] + a[2] - EDGE and a[1] < b[1] + b[3] - EDGE
            and b[1] < a[1] + a[3] - EDGE)


class LayoutTests(unittest.TestCase):
    def layout(self, card, region=(1200, 900), scale=1.0):
        plan = card.layout(VIEW, region[0], region[1], scale, kit.UiState())
        x, y, w, h = plan.card
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + w, region[0] + 1e-6)
        rects = [rect for rect, name, _value in plan.hits if name not in OVERLAY]
        for index, first in enumerate(rects):
            for second in rects[index + 1:]:
                self.assertFalse(overlap(first, second), (first, second))
        return plan

    def test_without_a_camera_it_says_how_to_start(self):
        plan = self.layout(look_card.LookCard(None))
        self.assertIn("Add a camera", texts(plan))
        self.assertEqual(hits(plan, "apply"), [])

    def test_presets_breathing_and_take_off(self):
        card = look_card.LookCard("Camera", 2.8)
        plan = self.layout(card)
        self.assertTrue(hits(plan, "apply"))
        self.assertEqual(hits(plan, "take_off"), [])   # No look on the scene yet.
        card.act("preset_open", None, Host())
        options = [value for value, _ in hits(self.layout(card), "preset")]
        self.assertEqual(options, [name for name in look_spec.PRESETS if name != "CLEAN"])
        card.act("preset", "NOIR", Host())
        self.assertFalse(card.breathes())   # Noir's lens does not breathe: the toggle is off and disabled.
        self.assertEqual(hits(self.layout(card), "breathing"), [])
        card.act("preset", "ANAMORPHIC", Host())
        self.assertTrue(hits(self.layout(card), "breathing"))
        self.assertTrue(hits(self.layout(look_card.LookCard("Camera", 2.8, applied="NOIR")), "take_off"))
        self.assertEqual(look_card.LookCard("Camera", 2.8, applied="NOIR").preset, "NOIR")

    def test_match_exposure_shows_shutter_iso_and_stops(self):
        card = look_card.LookCard("Camera", 4.0)
        self.assertEqual(hits(self.layout(card), "iso"), [])
        card.act("match", None, Host())
        plan = self.layout(card)
        self.assertEqual([v for v, _ in hits(plan, "iso")], [str(n) for n in look_card.ISOS])
        self.assertEqual([v for v, _ in hits(plan, "shutter")], [str(n) for n in look_card.SHUTTERS])
        self.assertIn("Lit for f/4", texts(plan))
        self.assertIn("+0.0 stops", texts(plan))
        card.act("iso", "400", Host())
        card.act("shutter", "100", Host())
        self.assertAlmostEqual(card.stops(), 1.0)   # Two stops of ISO, one less of shutter.
        self.assertIn("+1.0 stops", texts(self.layout(card)))

    def test_small_regions_and_scales_fit(self):
        card = look_card.LookCard("Camera", 2.8, applied="VINTAGE")
        card.act("match", None, Host())
        for region, scale in (((900, 760), 1.0), ((1400, 1100), 1.5), ((760, 620), 0.8)):
            self.layout(card, region, scale)

    def test_the_result_names_the_look_and_stages(self):
        card = look_card.LookCard("Camera", 2.8)
        card.phase, card.receipt = "done", {"preset": "VINTAGE", "camera": "Camera", "group": "Angelo Look",
                                            "stages": ["lens", "vignette", "grain"]}
        plan = card.layout(VIEW, 1200, 900, 1.0, kit.UiState())
        self.assertIn("Vintage look on Camera", texts(plan))
        self.assertIn("Lens, Vignette, Grain", texts(plan))


class RequestTests(unittest.TestCase):
    def test_request_follows_the_choices(self):
        card = look_card.LookCard("Camera", 5.6)
        card.act("preset", "VINTAGE", Host())
        self.assertEqual(card.request(), {"preset": "VINTAGE", "breathing": 0.0, "show_in_viewport": True,
                                          "exposure": None})
        card.act("breathing", None, Host())
        card.act("show", None, Host())
        card.act("match", None, Host())
        card.act("iso", "200", Host())
        request = card.request()
        self.assertEqual(request["breathing"], look_spec.PRESETS["VINTAGE"]["camera"]["breathing"])
        self.assertFalse(request["show_in_viewport"])
        self.assertAlmostEqual(look_spec.exposure_stops(request["exposure"], 5.6), 1.0)
        self.assertEqual(card.request("CLEAN"), {"preset": "CLEAN"})

    def test_close_closes(self):
        host = Host()
        look_card.LookCard("Camera").act("close", None, host)
        self.assertEqual(host.closed, "closed")

    def test_every_preset_and_stage_has_a_label(self):
        self.assertEqual(set(look_card.PRESET_LABELS), set(look_spec.PRESETS))
        self.assertEqual(set(look_card.STAGE_LABELS), set(look_spec.ORDER))


if __name__ == "__main__":
    unittest.main()
