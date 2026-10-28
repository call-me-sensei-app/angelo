# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every way a step is created stores its canonical name, and the interface shows it translated (1.0.5).

A German template-made flow showed English step names (P8 follow-up). The ruling: a step's stored name is its
identity for the agent and its outputs and stays canonical; ``flow_setup.step_label`` (part B) shows a default name in
the interface's language, so switching the language re-labels every flow. So each creating path names steps with the
canonical names that layer recognises:

* the templates (``flow_templates``) and Media Studio's Turn into a flow (``studio_to_flow.definition``; a second
  reference is ``Reference.001``, Blender's suffix, which the layer keeps);
* a node added any other way (Add step, Shift+A, the agent): Blender names it in the interface's language
  (``DATA_`` of its type name), so the flow node's ``init`` stores its canonical type name instead.

Checked with the real German and Arabic catalogues; a default prompt stays English (model-facing text).
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
sys.path.insert(0, str(ROOT / "scripts/angelo"))

from i18n import po  # noqa: E402


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
    import angelo_agent
    from angelo_agent import flow_setup, flow_templates, flow_window, studio_to_flow, ui_text
# The modules above, for the lazy ``from .ui_text import iface_`` inside step_label (patch.dict dropped them).
LOADED = {module.__name__: module for module in (flow_setup, flow_templates, flow_window, studio_to_flow, ui_text)}


def interface(code):
    """A patch making ``ui_text`` return the language's strings as Blender would (its real ``iface_`` fills the
    fields; a context-less lookup tries "Angelo" first), and that ``iface_``."""
    _header, entries = po.read(str(ROOT / "locale/angelo/po" / ("%s.po" % code)))
    table = {entry.key: entry.str for entry in entries if entry.str and not entry.obsolete}

    def gettext(kind, message, context=None):
        if context is None:
            return table.get(("Angelo", message)) or table.get(("", message)) or message
        return table.get((context, message)) or message
    return mock.patch.object(ui_text, "_gettext", gettext), ui_text.iface_


CANONICAL = frozenset(flow_templates.step_names())   # Every name a template gives, Character in parts' included.


def flows():
    """Every flow a creating path makes without Blender: each template and Turn into a flow's two shapes."""
    made = {entry["id"]: flow_templates.instantiate(entry["id"], [])[0] for entry in flow_templates.listing()}
    references = [{"kind": "URL", "id": "https://example.com/%d.png" % i} for i in range(3)]
    made["turn_into_a_flow_3d"] = studio_to_flow.definition("3D", None, "a chair", {}, references[:1])
    made["turn_into_a_flow_image"] = studio_to_flow.definition("IMAGE", None, "", {}, references)
    return made


class StepNameTests(unittest.TestCase):
    def test_a_german_flow_shows_german_step_names_and_stores_canonical_ones(self):
        for code in ("de", "ar"):
            language, iface_ = interface(code)
            for kind, flow in flows().items():
                with self.subTest(code=code, flow=kind), mock.patch.dict(sys.modules, LOADED), language:
                    stored = [node["id"] for node in flow["nodes"]]
                    bases = {name.rpartition(".")[0] if name[-4:-3] == "." else name for name in stored}
                    self.assertLessEqual(bases, CANONICAL, "stored names are canonical (the agent's identity)")
                    self.assertLessEqual(bases, flow_setup.default_names(), "the layer recognises every one")
                    shown = [flow_setup.step_label(name) for name in stored]
                    for name, label in zip(stored, shown):
                        base = name.rpartition(".")[0] if name[-4:-3] == "." else name
                        self.assertNotEqual(label, name, "%s is shown in the interface's language" % name)
                        self.assertTrue(label.startswith(iface_(base)), (name, label))
                        if base != name:
                            self.assertTrue(label.endswith(name[len(base):]), "Blender's suffix is kept")

    def test_a_default_prompt_stays_english(self):
        flow = flow_templates.instantiate("image_reference_3d", [])[0]
        prompts = [node["prompt"] for node in flow["nodes"] if node.get("prompt")]
        self.assertEqual(prompts, [flow_templates.REFERENCE_PROMPT], "model-facing text is never translated")
        self.assertNotIn(flow_templates.REFERENCE_PROMPT[:60], (ROOT / "locale/angelo/angelo.pot").read_text())

    def test_a_second_reference_takes_blenders_suffix(self):
        flow = flows()["turn_into_a_flow_image"]
        self.assertEqual([n["id"] for n in flow["nodes"] if n["type"] == "IMAGE"],
                         ["Reference", "Reference.001", "Reference.002"])

    def test_a_node_added_any_other_way_stores_its_canonical_type_name(self):
        source = (ADDON / "flow_nodes.py").read_text()
        init = source.split("    def init(self, context):\n", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("self.name, self.width = self.bl_label, NODE_WIDTHS[self.angelo_type]", init,
                      "the base init replaces Blender's DATA_ name (and sizes the node by its type)")
        # The base init (flow_nodes' first) is the only place a node's width is set.
        for name, first, assigned in (("flow_nodes.py", 2, 1), ("flow_character_nodes.py", 1, 0)):
            text = (ADDON / name).read_text()
            for cls_init in text.split("    def init(self, context):\n")[first:]:
                self.assertIn("super().init(context)", cls_init.split("\n    def ", 1)[0],
                              f"every subclass runs it ({name})")
            self.assertEqual(text.count("self.width ="), assigned,
                             f"{name}: a node's width comes only from flow_spec.NODE_WIDTHS (templates lay out by it)")


class QuotedStepsAndFlowNamesTests(unittest.TestCase):
    """Wherever a step or a flow is quoted, it is quoted as shown (German here), never as stored."""

    def test_the_waiting_line_and_the_bar_status_quote_the_shown_step_name(self):
        language, iface_ = interface("de")
        tree = SimpleNamespace(name="Image to 3D", nodes=[SimpleNamespace(name="3D model",
                                                                         bl_idname="ANGELO_FlowGenerate3DNode")])
        host = SimpleNamespace(node_state=lambda t, name: {"state": "BLOCKED", "setup": "model"}, _cache={},
                               active_runs=lambda name: [])
        nodes = SimpleNamespace(TYPE_BY_IDNAME={"ANGELO_FlowGenerate3DNode": "GENERATE_3D"},
                                setup_need=lambda t, n: "choose", SETUP_LABELS={"choose": "Choose a model to run"},
                                SETUP_TEXT={})
        stubs = {"angelo_agent.flow_host": host, "angelo_agent.flow_nodes": nodes}
        with mock.patch.dict(sys.modules, dict(LOADED, **stubs)), language, \
                mock.patch.object(angelo_agent, "flow_host", host, create=True), \
                mock.patch.object(angelo_agent, "flow_nodes", nodes, create=True):
            waiting = flow_setup.waiting_text(["3D model"])
            line, problem = flow_window.status(tree)
            label, expected = flow_window.flow_label(tree.name), iface_("Image to 3D")
        self.assertEqual(waiting, "Wartet auf „3D-Modell“")
        self.assertEqual((line, problem), ("3D-Modell: Modell wählen, um auszuführen", True), "the bar and Run all's tip")
        self.assertEqual(label, expected)
        self.assertNotEqual(label, "Image to 3D")

    def test_flow_names_angelo_gives_are_shown_translated_and_typed_ones_as_typed(self):
        language, iface_ = interface("de")
        with mock.patch.dict(sys.modules, LOADED), language:
            for name in [flow_templates.NEW_FLOW, flow_templates.STUDIO_FLOW] + [e["title"] for e in flow_templates.listing()]:
                self.assertEqual(flow_templates.flow_label(name), iface_(name))
                self.assertNotEqual(flow_templates.flow_label(name), name, name)
                self.assertEqual(flow_templates.flow_label(name + ".001"), iface_(name) + ".001", "the suffix stays")
            self.assertEqual(flow_templates.flow_label("Castle flow"), "Castle flow")
        self.assertEqual(studio_to_flow.FLOW_NAME, flow_templates.STUDIO_FLOW)


if __name__ == "__main__":
    unittest.main()
