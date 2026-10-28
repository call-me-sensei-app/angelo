# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Generation Flow step that only lacks setup is marked so, and its node says what to do instead of "Can't run".

1.0.5 review of the first-open state: once an account and a model were chosen, the image to reference template still
drew red "Can't run · 'Image': pick an image", and a new Generate Image or Place in Scene node from the Add menu was
red before anything was typed or linked. ``flow_graph`` marks such a BLOCKED state with ``setup``; ``flow_setup`` has
the interface's words for every mark; blocks the person's own choices caused carry none and stay red. Pure.
"""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import flow_templates  # noqa: E402
from angelo_agent.flow_spec import FORMAT  # noqa: E402
from angelo_agent.flow_graph import LibraryIndex, connection_needs, evaluate, need_for  # noqa: E402
from angelo_agent.flow_setup import SETUP_TEXT  # noqa: E402
from angelo_agent.flow_spec import normalize  # noqa: E402

IMG, MESH = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
CATALOG = {(IMG, "img"): {"kind": "IMAGE", "ready": True, "name": "Img", "references_max": 1},
           (MESH, "mesh"): {"kind": "3D", "ready": True, "name": "Mesh", "references_max": 1},
           (IMG, "img4"): {"kind": "IMAGE", "ready": True, "name": "Img four", "references_max": 4}}
IMAGE_CHOICE, MESH_CHOICE = {"connection_id": IMG, "model": "img"}, {"connection_id": MESH, "model": "mesh"}


def states(definition):
    return evaluate(definition, LibraryIndex({}), catalog=lambda c, m: CATALOG.get((c, m)))


def starter(starter_id, **fields):
    template = flow_templates.template(starter_id)["flow"]
    definition = {"format": FORMAT, "nodes": copy.deepcopy(template["nodes"]), "links": copy.deepcopy(template["links"])}
    for node in definition["nodes"]:
        node.pop("role", None)   # No account: each generator asks for one (a test chooses below).
        node["fields"] = {**node.get("fields", {}), **fields.get(node["id"], {})}
    return normalize(definition)


def build(nodes, links=()):
    return normalize({"nodes": [{"id": n, "type": t, "fields": f} for n, t, f in nodes],
                      "links": [{"from": a, "to": b, "input": i} for a, b, i in links]})


class SetupTests(unittest.TestCase):
    def test_templates_with_models_chosen_only_wait_for_the_image(self):
        for result, step in ((states(starter("image_reference_3d", Reference=IMAGE_CHOICE, **{"3D model": MESH_CHOICE})), "Reference"),
                             (states(starter("image_to_3d", **{"3D model": MESH_CHOICE})), "3D model")):
            self.assertEqual((result[step]["state"], result[step].get("setup")), ("BLOCKED", "upstream"))
            self.assertEqual(result["Image"].get("setup"), "image")

    def test_every_template_as_first_opened_only_waits_for_setup(self):
        """No account chosen, none connected or some connected: every blocked step of every template has an account need
        or a setup mark with words, which is what flow_nodes draws neutrally (1.0.6: Character in parts' per-part 3D
        step drew red "Can't run" because connection_needs skipped its step type)."""
        for template_id in flow_templates.TEMPLATES:
            flow, _ = flow_templates.instantiate(template_id, [])
            result = states(flow)
            for connected in (set(), {"IMAGE", "3D"}):
                needs = connection_needs(flow, lambda c, m: CATALOG.get((c, m)), connected=connected)
                for step, state in result.items():
                    if state["state"] == "BLOCKED":
                        with self.subTest(template=template_id, step=step, connected=sorted(connected)):
                            self.assertTrue(need_for(needs, step) or state.get("setup") in SETUP_TEXT, state["reason"])

    def test_new_nodes_from_the_add_menu_say_what_they_need(self):
        from angelo_agent.flow_subflows import expand
        result = states(expand(build([("Look", "GENERATE_IMAGE", IMAGE_CHOICE), ("Shape", "GENERATE_3D", MESH_CHOICE),
                                      ("Place", "PLACE_IN_SCENE", {}), ("Hero", "SUBFLOW", {})]), {}))
        self.assertEqual({n: result[n].get("setup") for n in ("Look", "Shape", "Place", "Hero")},
                         {"Look": "prompt", "Shape": "prompt_or_reference", "Place": "link", "Hero": "recipe"})

    def test_blocks_the_persons_choices_caused_stay_failures(self):
        result = states(build([("A", "IMAGE", {"source": {"kind": "URL", "id": "https://example.com/a.png"}}),
                               ("B", "IMAGE", {"source": {"kind": "URL", "id": "https://example.com/b.png"}}),
                               ("Too many", "GENERATE_IMAGE", {"prompt": "a cat", **IMAGE_CHOICE}),
                               ("Wrong kind", "GENERATE_3D", {"prompt": "a cat", **IMAGE_CHOICE})],
                              [("A", "Too many", "references"), ("B", "Too many", "references")]))
        for step in ("Too many", "Wrong kind"):
            self.assertEqual(result[step]["state"], "BLOCKED")
            self.assertNotIn("setup", result[step])

    def test_a_stopped_attempt_is_stopped_and_a_failed_one_failed(self):
        from angelo_agent.flow_graph import input_key, plan
        definition = build([("Look", "GENERATE_IMAGE", {"prompt": "a cat", **IMAGE_CHOICE})])
        key = input_key(definition["nodes"][0], "a cat", [])
        for status, expected, why in (("stopped", "STOPPED", "run again after a stop"),
                                      ("failed", "FAILED", "retry after failure")):
            with self.subTest(status=status):
                assets = {"a": {"id": "a", "status": status, "lineage": {"key": key}}}
                result = evaluate(definition, LibraryIndex(assets), catalog=lambda c, m: CATALOG.get((c, m)))
                self.assertEqual(result["Look"]["state"], expected)
                line = plan(definition, result)["lines"][0]
                self.assertEqual((line["action"], line["reason"]), ("GENERATE", why), "running again is offered")

    def test_a_waiting_step_names_what_it_waits_for_once(self):
        from angelo_agent.flow_setup import waiting_text
        place = states(starter("image_to_3d", **{"3D model": MESH_CHOICE}))["Place in scene"]
        self.assertEqual((place["state"], place["waits"]), ("WAITING", ["3D model"]))
        self.assertEqual(waiting_text(place["waits"]), "Waiting for '3D model'")
        both = states(build([("A", "GENERATE_IMAGE", {"prompt": "a", **IMAGE_CHOICE}),
                             ("B", "GENERATE_IMAGE", {"prompt": "b", **IMAGE_CHOICE}),
                             ("C", "GENERATE_IMAGE", {"prompt": "c", "connection_id": IMG, "model": "img4"})],
                            [("A", "C", "references"), ("B", "C", "references")]))["C"]
        self.assertEqual((both["state"], both["waits"]), ("WAITING", ["A", "B"]))
        self.assertEqual(waiting_text(both["waits"]), "Waiting for 2 steps")
        self.assertEqual(both["reason"], "waits for 'A', 'B'", "the agent's reason is unchanged")

    def test_the_waiting_words_have_every_plural_form_their_language_uses(self):
        sys.path.insert(0, str(ROOT / "scripts/angelo"))
        from i18n import po
        from angelo_agent.ui_text import plural_category
        for code in ("de", "ru", "pl", "ar", "ja", "fr"):
            path = ROOT / "locale/angelo" / ("src/ar.po" if code == "ar" else f"po/{code}.po")
            _, entries = po.read(str(path))
            forms = {e.ctxt: e.str for e in entries if not e.obsolete and e.id.startswith("Waiting for {count} step")}
            for count in (1, 2, 3, 5, 11, 21, 102):
                category = plural_category(code, count)
                with self.subTest(code=code, count=count):
                    self.assertTrue(forms.get("plural:" + category), category)
            single = next(e.str for e in entries if e.id == "Waiting for '{step}'" and not e.obsolete)
            self.assertIn("{step}", single)

    def test_default_step_names_are_shown_translated_and_typed_names_as_typed(self):
        from unittest import mock
        from angelo_agent import flow_setup, ui_text
        with mock.patch.object(ui_text, "iface_", lambda text, *args, **fields: "<" + text + ">"):
            self.assertEqual(flow_setup.step_label("3D model"), "<3D model>")
            self.assertEqual(flow_setup.step_label("3D model.001"), "<3D model>.001", "Blender's suffix stays")
            self.assertEqual(flow_setup.step_label("Hero/Reference"), "Hero/<Reference>", "a use is the person's")
            self.assertEqual(flow_setup.step_label("My castle"), "My castle")
            self.assertEqual(flow_setup.step_label("3D model v2"), "3D model v2", "a renamed step stays as typed")

    def test_every_default_step_name_has_angelos_own_word_in_every_language(self):
        """Fails on an untranslated default label, or on one Blender's catalogue would answer instead of Angelo's
        (Russian "Эталон", a measurement standard, for "Reference"): every template step and node type name has an
        entry in Angelo's context, which iface_ asks first, in all 20 catalogues."""
        sys.path.insert(0, str(ROOT / "scripts/angelo"))
        import ast
        from i18n import po
        from angelo_agent.flow_setup import NODE_STEP_NAMES, default_names
        source = (ROOT / "scripts/addons_core/angelo_agent/flow_nodes.py").read_text()
        labels = {node.value.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Assign)
                  and any(getattr(t, "id", "") == "bl_label" for t in node.targets) and isinstance(node.value, ast.Constant)}
        labels -= {"Generation Flow"}   # The tree type, not a step.
        self.assertEqual(sorted(labels - set(NODE_STEP_NAMES)), [], "every node type name is marked")
        names = set(default_names()) | labels
        for path in sorted((ROOT / "locale/angelo/po").glob("*.po")):
            _, entries = po.read(str(path))
            own = {e.id for e in entries if not e.obsolete and e.ctxt == "Angelo" and e.str}
            with self.subTest(language=path.stem):
                self.assertEqual(sorted(names - own), [])
        _, entries = po.read(str(ROOT / "locale/angelo/po/ru.po"))
        self.assertEqual(next(e.str for e in entries if e.ctxt == "Angelo" and e.id == "Reference"), "Референс")

    def test_every_setup_mark_has_words_and_reasons_are_unchanged(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/flow_graph.py").read_text()
        for mark in ("prompt", "prompt_or_reference", "image", "link", "upstream", "recipe"):
            self.assertIn(mark, SETUP_TEXT)
        self.assertNotIn("ui_text", source, "Reasons stay model-facing English")
        result = states(starter("image_reference_3d", Reference=IMAGE_CHOICE))
        self.assertEqual(result["Reference"]["reason"], "'Image': pick an image")


if __name__ == "__main__":
    unittest.main()
