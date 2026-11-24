# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Scatter card without Blender (``scatter_card``): every shipped biome and layer has a translatable label; the
card's layout for a selection and for a biome (one Use Selection and one Find in Library per layer, Scatter off
until every layer has objects), Library results (at most four, Add per item, a notice when there are none), a new
biome starting empty, and the request it hands ``angelo.scatter``. Its layouts fit the region with no overlapping
controls. Contract: doc/angelo/scatter.rst, "The card".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import card_kit as kit, scatter_card, scatter_spec, scatter_text  # noqa: E402


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
        self.redraws, self.closed, self.later = 0, None, []

    def redraw(self):
        self.redraws += 1

    def close(self, how="done"):
        self.closed = how

    def after(self, seconds, function):
        self.later.append(function)

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
OVERLAY = {"biome"}   # The open Biome list's options: a popover drawn over the content, whose hits win.


def overlap(a, b):
    return (a[0] < b[0] + b[2] - EDGE and b[0] < a[0] + a[2] - EDGE and a[1] < b[1] + b[3] - EDGE
            and b[1] < a[1] + a[3] - EDGE)


class LabelTests(unittest.TestCase):
    def test_every_preset_and_layer_has_a_label(self):
        presets = scatter_card.presets()
        self.assertEqual(set(presets), set(scatter_text.BIOME_LABELS))
        for stem, biome in presets.items():
            self.assertEqual(scatter_text.BIOME_LABELS[stem][0], biome["name"], stem)
            self.assertEqual(scatter_text.BIOME_LABELS[stem][1], biome["description"], stem)
            for layer in biome["layers"]:
                self.assertEqual(scatter_text.LAYER_LABELS.get(layer["name"]), layer["name"], (stem, layer["name"]))
                self.assertTrue(scatter_card.layer_words(layer), (stem, layer["name"]))   # Find in Library has words.


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

    def test_without_a_mesh_it_says_how_to_start(self):
        plan = self.layout(scatter_card.ScatterCard(None, []))
        self.assertIn("Make a mesh active", texts(plan))
        self.assertEqual([value for value, _ in hits(plan, "scatter")], [])

    def test_the_selection_is_one_layer(self):
        card = scatter_card.ScatterCard("Terrain", ["Terrain", "Rock", "Bush"])
        self.assertEqual((card.mode, card.selected), ("selection", ["Rock", "Bush"]))
        plan = self.layout(card)
        self.assertIn("Rock, Bush", texts(plan))
        self.assertTrue(hits(plan, "scatter"))
        self.assertEqual(sorted(value for value, _ in hits(plan, "density")), sorted("%g" % d for d in
                                                                                      scatter_card.DENSITIES))

    def test_a_biome_has_one_row_per_layer_and_waits_for_every_layer(self):
        card = scatter_card.ScatterCard("Terrain", [])
        self.assertEqual(card.mode, "biome")
        host = Host()
        card.act("biome", "meadow", host)
        layers = [layer["name"] for layer in card.layers()]
        plan = self.layout(card)
        self.assertEqual([value for value, _ in hits(plan, "use_selection")], layers)
        self.assertEqual([value for value, _ in hits(plan, "find")], layers)
        self.assertEqual(hits(plan, "scatter"), [])
        self.assertFalse(card.ready())
        for name in layers[:-1]:
            card.assets[name] = ["Obj " + name]
        self.assertFalse(card.ready())
        card.assets[layers[-1]] = ["Obj last"]
        self.assertTrue(card.ready())
        self.assertTrue(hits(self.layout(card), "scatter"))

    def test_another_biome_starts_empty(self):
        card = scatter_card.ScatterCard("Terrain", [])
        card.act("biome", "meadow", Host())
        card.assets["Grass"] = ["Clump"]
        card.found = ("Grass", [])
        card.act("biome", "rocky-scree", Host())
        self.assertEqual((card.biome, card.assets, card.found), ("rocky-scree", {}, None))

    def test_library_results_are_rows_to_add(self):
        card = scatter_card.ScatterCard("Terrain", [])
        card.act("biome", "meadow", Host())
        items = [{"id": "qa:%d" % n, "title": "Grass %d" % n, "source": "fixture"} for n in range(scatter_card.RESULTS)]
        card.found = ("Grass", items)
        plan = self.layout(card)
        self.assertEqual([value for value, _ in hits(plan, "add")], [item["id"] for item in items])
        card.found = ("Grass", [])
        self.assertIn("The Library has no models for grass", texts(self.layout(card)))
        card.found = ("Grass", None)
        self.assertIn("Loading the Library", texts(self.layout(card)))
        card.found, card.fetching = None, ("Grass", "Grass 1")
        plan = self.layout(card)
        self.assertIn("Adding Grass 1", texts(plan))
        self.assertEqual(hits(plan, "find"), [])   # One download at a time.

    def test_small_regions_and_scales_fit(self):
        card = scatter_card.ScatterCard("Terrain", [])
        card.act("biome", "mixed-temperate-forest", Host())
        for region, scale in (((900, 700), 1.0), ((1400, 1000), 1.5), ((700, 500), 0.8)):
            self.layout(card, region, scale)

    def test_the_result_lists_each_layer(self):
        card = scatter_card.ScatterCard("Terrain", [])
        card.phase, card.receipt = "done", {"instances": 12, "layers": [{"name": "Grass", "instances": 12},
                                                                      {"name": "Flowers", "instances": 0}]}
        plan = card.layout(VIEW, 1200, 900, 1.0, kit.UiState())
        self.assertIn("Scattered 12 instances", texts(plan))
        self.assertIn("Nothing was placed for: Flowers", texts(plan))


class RequestTests(unittest.TestCase):
    def test_a_biome_request_names_every_layers_objects(self):
        card = scatter_card.ScatterCard("Terrain", [])
        card.act("biome", "dune-grass", Host())
        for layer in card.layers():
            card.assets[layer["name"]] = [layer["name"] + " A", layer["name"] + " B"]
        request = card.request()
        self.assertEqual(request["biome"], "dune-grass")
        self.assertEqual(json.loads(request["biome_assets"]),
                         {layer["name"]: [layer["name"] + " A", layer["name"] + " B"] for layer in card.layers()})

    def test_close_closes(self):
        host = Host()
        scatter_card.ScatterCard("Terrain", []).act("close", None, host)
        self.assertEqual(host.closed, "closed")

    def test_the_presets_are_the_shipped_files(self):
        self.assertEqual(sorted(scatter_card.presets()),
                         sorted(p.name.split(".")[0] for p in scatter_spec.PRESET_DIR.glob("*.angelo-biome.json")))


if __name__ == "__main__":
    unittest.main()
