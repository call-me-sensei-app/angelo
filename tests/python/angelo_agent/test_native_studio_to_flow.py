# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Turn into a flow builds a flow that starts where Studio is (1.0.5, ``studio_to_flow``).

On a stand-in ``bpy``. Covered: the flow's first step is Studio's tab's generator with the same account, model and
prompt, one output and no review; the settings kept are the ones that model accepts (Studio's extra quality values
map to a flow's, a polycount becomes a whole number inside the model's limits, an unoffered value is left to the
flow's default); references become Image steps wired to the step (links as links, attached files as library assets,
one for 3D); 3D with a reference is the Image to 3D template's shape, filled in; the result is a canonical
``angelo.flow/1`` flow; the bar says steps can be added after it until one is. The running app's proof is
``test_blender_flow_menu``'s Turn into a flow phase.
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text),
                              handlers=handlers, background=True)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.data = SimpleNamespace(node_groups=[])
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
            "bpy.app.handlers": handlers}


with mock.patch.dict(sys.modules, fake_bpy()):
    from angelo_agent import flow_window, studio_to_flow

ACCOUNT = "0f8fad5b-d9cb-469f-a165-70867728950e"
ASSET = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def nodes(flow):
    return {node["id"]: node for node in flow["nodes"]}


class OptionTests(unittest.TestCase):
    def test_only_what_the_model_accepts_is_kept(self):
        setup = {"aspect": "16:9", "resolution": "2K", "quality": "xhigh", "background": "auto", "size": "",
                 "polycount": "12000", "texture": True}
        openai = studio_to_flow.options_for("openai_images_api", "gpt-image", setup)
        self.assertEqual(openai, {"aspect": "16:9", "resolution": "2K", "quality": "high", "background": "auto"},
                         "Studio's xhigh is a flow's high; an empty size override is not a setting")
        self.assertEqual(studio_to_flow.options_for("gemini_images_api", "x", setup),
                         {"aspect": "16:9", "resolution": "2K"}, "no quality or background where the model has none")
        self.assertEqual(studio_to_flow.options_for("seedream_api", "x", dict(setup, resolution="1K")),
                         {"aspect": "16:9"}, "a resolution the model doesn't offer: the flow's default")
        self.assertEqual(studio_to_flow.options_for("tripo_api", "P2-turbo", dict(setup, polycount="900000")),
                         {"polycount": 50000, "texture": True}, "a whole number inside the model's limits")
        self.assertEqual(studio_to_flow.options_for("meshy_api", "x", dict(setup, polycount="lots")), {"texture": True})


class DefinitionTests(unittest.TestCase):
    chosen = {"connection": ACCOUNT, "id": "gpt-image-1", "mode": "openai_images_api"}

    def test_an_image_setup_starts_the_flow_with_the_same_model_and_prompt(self):
        flow = studio_to_flow.definition("IMAGE", self.chosen, "a red fox, studio light", {"aspect": "1:1"},
                                         [{"kind": "URL", "id": "https://example.com/fox.png"},
                                          {"kind": "ASSET", "id": ASSET}])
        steps = nodes(flow)
        step = steps["Image"]
        self.assertEqual(step["type"], "GENERATE_IMAGE")
        self.assertEqual((step["connection_id"], step["model"], step["prompt"]),
                         (ACCOUNT, "gpt-image-1", "a red fox, studio light"))
        self.assertEqual((step["variants"], step["review"]), (1, False), "Studio asks for one")
        self.assertEqual(step["options"], {"aspect": "1:1"})
        self.assertEqual(steps["Reference"]["source"], {"kind": "URL", "id": "https://example.com/fox.png"})
        self.assertEqual(steps["Reference.001"]["source"], {"kind": "ASSET", "id": ASSET})
        self.assertEqual(sorted((l["from"], l["to"], l["input"]) for l in flow["links"]),
                         [("Reference", "Image", "references"), ("Reference.001", "Image", "references")])

    def test_a_3d_setup_with_a_reference_is_the_image_to_3d_template_filled_in(self):
        flow = studio_to_flow.definition("3D", dict(self.chosen, id="tripo-v3"), "", {"polycount": 10000},
                                         [{"kind": "ASSET", "id": ASSET}, {"kind": "URL", "id": "https://e.com/x.png"}])
        steps = nodes(flow)
        self.assertEqual(set(steps), {"Reference", "3D model", "Place in scene"}, "3D takes one reference")
        self.assertEqual(steps["3D model"]["model"], "tripo-v3")
        self.assertEqual(sorted((l["from"], l["to"], l["input"]) for l in flow["links"]),
                         [("3D model", "Place in scene", "model"), ("Reference", "3D model", "reference")])

    def test_no_account_yet_still_builds_a_flow_the_step_asks_for_one(self):
        flow = studio_to_flow.definition("3D", None, "a chair", {}, [])
        self.assertEqual(nodes(flow)["3D model"]["connection_id"], "")
        self.assertEqual(nodes(flow)["3D model"]["prompt"], "a chair")


class WiringTests(unittest.TestCase):
    def test_the_bar_says_steps_can_be_added_until_one_is(self):
        tree = SimpleNamespace(name="Media Studio flow", nodes=[1, 2])
        flow_window.hint(tree, studio_to_flow.HINT)
        with mock.patch.dict(sys.modules, {"angelo_agent.flow_host": SimpleNamespace(_cache={}, active_runs=lambda name: []),
                                           "angelo_agent.flow_open": SimpleNamespace(size_text=lambda t: "%d steps" % len(t.nodes))}), \
                mock.patch.object(flow_window, "blocking", lambda tree: None):
            self.assertEqual(flow_window.status(tree), (studio_to_flow.HINT, False))
            tree.nodes.append(3)
            self.assertNotEqual(flow_window.status(tree)[0], studio_to_flow.HINT, "a step was added")
            tree.nodes.pop()
            self.assertNotEqual(flow_window.status(tree)[0], studio_to_flow.HINT, "said once")

    def test_it_opens_through_the_flow_opener_and_runs_nothing(self):
        source = (ADDON / "studio_to_flow.py").read_text()
        turn = source.split("\ndef turn_into_flow(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("flow_open.show(window, tree)", turn, "Manual's tab, Director's window")
        self.assertNotIn("flow_run", turn, "running is Run all's, with its itemized approval")
        self.assertIn("studio.selected(state)", turn, "the same catalog entry Studio would generate with")
        self.assertIn('chosen if chosen and chosen.get("id") else None', turn,
                      "Studio's model even before an account is connected (the step asks for the account)")
        flow = studio_to_flow.definition("IMAGE", {"id": "img-model", "connection": ""}, "a fox", {}, [])
        self.assertEqual((nodes(flow)["Image"]["model"], nodes(flow)["Image"]["connection_id"]), ("img-model", ""))

    def test_the_pill_draws_as_studio_calls_it(self):
        call = (ADDON / "studio.py").read_text().split("def top_row_end(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("studio_to_flow.draw_pill(state, width, height, canvas=canvas)", call, "Studio's hook")
        drawn = []
        canvas = SimpleNamespace(measure=lambda text, size: 7.0 * len(text), text=lambda *a: drawn.append("text"),
                                 shape=lambda *a: drawn.append("shape"), stroke=lambda *a: drawn.append("stroke"))
        state = {"hits": [], "hover": None}
        with mock.patch.dict(sys.modules, {"angelo_agent.chat_view": canvas}):
            rect = studio_to_flow.draw_pill(state, 1200, 800)   # No canvas given: the chat canvas.
        self.assertEqual(state["hits"], [(rect, studio_to_flow.ACTION, None)])
        self.assertAlmostEqual(rect[0] + rect[2], 1200 - 14)
        self.assertIn("text", drawn)
        click = (ADDON / "studio.py").read_text()
        self.assertIn("studio_to_flow.turn_into_flow(context, state)", click)
        self.assertIn('"to_flow"', click, "kept by create_panel's clipping (TOP_ROW_ACTIONS)")

    def test_the_pill_is_the_shared_pill(self):
        source = (ADDON / "studio_to_flow.py").read_text()
        pill = source.split("\ndef draw_pill(", 1)[1].split("\ndef ", 1)[0]
        for token in ("kit.LINE", "kit.PANEL_HOVER", "kit.PANEL", "tk.TEAL_SOFT", "state[\"hits\"].append"):
            self.assertIn(token, pill)
        self.assertNotIn("PURPLE", source)


if __name__ == "__main__":
    unittest.main()
