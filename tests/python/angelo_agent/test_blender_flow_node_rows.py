# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Generation Flow node fits its text in all of Angelo's 21 languages, and a fresh flow draws no alert (1.0.5).

Background, no window: each node of both starter templates and of a flow with every node type (a long prompt, a long
Text, a recipe-less Subflow, an unlinked Place) draws into a stand-in layout that keeps what was drawn and in which
row, in every language, with no generation account. Checked against Blender's own sizes (from source, at UI scale 1):
a node's buttons get its width less NODE_DY (20); a centred button's text loses UI_TEXT_CLIP_MARGIN + 1 (6) a side;
a label in a box loses the box inset (5 a side), UI_TEXT_MARGIN_X (8) and the clip margin (6).

* every row of two or more buttons is side by side only when each label (with its icon, arrow or number) fits its
  share; otherwise it must be stacked;
* every wrapped line of a long field (``flow_text_field.drawn``) fits its box, and the lines join to the whole value;
* in German, Russian and Arabic, every node whose name Angelo gave it (a template's step, a new node's type name)
  shows that name translated, in its header and in the waiting line that names it (a name typed by the person stays);
* no label of a fresh flow is drawn in the alert colour (every gap there is setup, not a failure), while a block the
  person caused (a Subflow on a flow with no output) is.

The window's own proof, in pixels, is ``test_blender_flow_setup_states``. Run through
``run_native_suites.py --include test_blender_flow_node_rows``; check ``flow-node-rows.json`` for ``success``.
"""

import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-flow-node-rows-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
import addon_utils  # noqa: E402
import bpy  # noqa: E402

from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
prefs = bpy.context.preferences
prefs.use_preferences_save = False
from angelo_agent import flow_host, flow_nodes, flow_templates, flow_text_field, flow_tree, setup_card  # noqa: E402

REPORT = OUT / "flow-node-rows.json"
NODE_DY, BUTTON_TEXT, LABEL_IN_BOX, ICON = 20, 12, 2 * 5 + 8 + 6, 20
LONG = ("A weathered bronze lighthouse keeper with a long wool coat, brass buttons, a knitted scarf and a lantern held "
        "high, standing on wet rocks at dusk, with sea spray and gulls")


class Layout:
    """Stands in for ``bpy.types.UILayout``: keeps every item, the container it is in and how containers nest."""

    def __init__(self, items, kind="column"):
        self.items, self.kind, self.alert, self.active, self.enabled = items, kind, False, True, True
        self.alignment, self.activate_init, self.id = 'EXPAND', False, len(items)

    def _child(self, kind):
        child = Layout(self.items, kind)
        child.alert = self.alert
        self.items.append(("container", child))
        return child

    def column(self, **_):
        return self._child("column")

    def row(self, **_):
        return self._child("row")

    def box(self):
        return self._child("box")

    def grid_flow(self, columns=0, **_):
        child = self._child("grid")
        child.columns = columns
        return child

    def label(self, text="", icon='NONE', **_):
        self.items.append(("label", self, text, icon, self.alert))

    def prop(self, owner, name, text=None, **_):
        self.items.append(("prop", self, (owner, name, text), 'NONE', self.alert))

    def operator(self, idname, text="", icon='NONE', **_):
        self.items.append(("operator", self, text, icon, self.alert))
        return SimpleNamespace()

    def template_icon(self, **_):
        pass


def catalogue(code):
    """{English: translation} of this checkout's default-context catalogue entries for ``code`` (what the app ships)."""
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "scripts/angelo"))
    from i18n import po
    _, entries = po.read(str(root / "locale/angelo/po" / (code + ".po")))
    return {e.id: e.str for e in entries if not e.obsolete and e.ctxt == "Angelo" and e.str}   # Angelo's own word.


def measured(text):
    import blf
    blf.size(0, prefs.ui_styles[0].widget.points)
    return blf.dimensions(0, text)[0]


def shown(owner, name, text):
    """(label as drawn, room it needs beside the label) of a property button."""
    rna = owner.bl_rna.properties[name]
    if text:
        return text, (ICON if rna.type in {'INT', 'FLOAT'} else 0)   # A number beside its label.
    if rna.type == 'ENUM':
        item = next((i for i in rna.enum_items if i.identifier == getattr(owner, name)), None)
        label = bpy.app.translations.pgettext_iface(item.name, rna.translation_context) if item else ""
        return label, ICON   # The menu's arrow.
    return "", 0


def check_rows(node, items, problems, language, tree):
    rows = {}
    for item in items:
        if item[0] in {"operator", "prop"} and item[1].kind == "row":
            rows.setdefault(id(item[1]), []).append(item)
    for members in rows.values():
        if len(members) < 2:
            continue
        share = (node.width - NODE_DY) / len(members) - 2 * BUTTON_TEXT / 2
        for kind, _, payload, icon, _ in members:
            if kind == "operator":
                label, extra = payload, (ICON if icon != 'NONE' else 0)
            else:
                label, extra = shown(*payload)
                if not label and payload[2] == "":
                    continue   # An icon-only button.
            need = measured(label) + extra
            if need > share + 0.5:
                problems.append([language, tree, node.name, "row", label, round(need, 1), round(share, 1)])


def check_fields(tree, node, problems, language):
    for (tree_name, node_name, prop), lines in flow_text_field.drawn.items():
        if tree_name != tree.name or node_name != node.name or len(lines) < 2:
            continue
        room = node.width - NODE_DY - LABEL_IN_BOX
        for line in lines:
            if measured(line) > room + 0.5:
                problems.append([language, tree.name, node.name, "wrapped line", line, round(measured(line), 1), room])
        value = getattr(node, prop)
        if "".join("".join(lines).split()) != "".join(value.split()):
            problems.append([language, tree.name, node.name, "not whole", prop])


def trees():
    names = []
    for entry in flow_templates.listing():
        tree = bpy.data.node_groups.new(entry["title"], flow_nodes.TREE)
        flow_tree.write_flow(tree, flow_templates.instantiate(entry["id"], [])[0])
        names.append(tree.name)
    tree = bpy.data.node_groups.new("Every node", flow_nodes.TREE)
    nodes = {kind: tree.nodes.new(idname) for kind, idname in flow_nodes.IDNAME_BY_TYPE.items()}
    nodes["TEXT"].text = LONG
    nodes["GENERATE_IMAGE"].prompt = LONG
    names.append(tree.name)
    # A block the person caused (a Subflow whose flow has no output) must still draw red ("Conflict" is checked apart).
    conflict = bpy.data.node_groups.new("Conflict", flow_nodes.TREE)
    use = conflict.nodes.new(flow_nodes.ANGELO_FlowSubflowNode.bl_idname)
    use.recipe = bpy.data.node_groups[names[0]]
    return names + [conflict.name]


def main():
    state = {"success": False, "checks": {}, "problems": [], "alerts": [], "languages": []}
    checks = state["checks"]
    try:
        checks["fonts_measure_in_the_background"] = measured("Run this step") > 20
        names = trees()
        identifiers = [i.identifier for i in prefs.view.bl_rna.properties["language"].enum_items]
        prefs.view.use_translate_interface = True
        for code, _ in setup_card.LANGUAGES:
            language = setup_card.resolve(code, identifiers)
            if not language:
                continue
            prefs.view.language = language
            state["languages"].append(language)
            if language.split("_")[0] in {"de", "ru", "ar"}:
                # Each default name must be drawn as Angelo's own catalogue entry (its Angelo context), never as the
                # English nor as Blender's word for the same English (Russian "Эталон" for "Reference").
                state.setdefault("labels", {})[language] = untranslated = []
                from angelo_agent.flow_setup import waiting_text
                from angelo_agent import flow_character_nodes
                defaults = (flow_templates.step_names()
                            | {cls.bl_label for cls in flow_nodes.NODES + flow_character_nodes.CLASSES})   # Names Angelo gives, found here.
                expected = catalogue(language.split("_")[0])
                for name in names:
                    for node in bpy.data.node_groups[name].nodes:
                        want = expected.get(node.name)
                        if node.name in defaults and node.draw_label() != want:
                            untranslated.append([name, node.name, node.draw_label(), expected.get(node.name)])
                if expected.get("3D model") not in waiting_text(["3D model"]):
                    untranslated.append(["waiting line", waiting_text(["3D model"])])
            for name in names:
                tree = bpy.data.node_groups[name]
                flow_host.refresh(tree)
                for node in tree.nodes:
                    items = []
                    flow_text_field.drawn.clear()
                    node.draw_buttons(SimpleNamespace(preferences=prefs, window=None, region=None), Layout(items))
                    check_rows(node, items, state["problems"], language, name)
                    check_fields(tree, node, state["problems"], language)
                    red = [[language, name, node.name, item[2]] for item in items
                           if item[0] in {"label", "operator"} and item[4]]
                    if name == "Conflict":
                        state.setdefault("conflict_red", []).append(bool(red))
                    else:
                        state["alerts"] += red
        checks["every_language_measured"] = len(state["languages"]) == len(setup_card.LANGUAGES)
        checks["every_row_and_wrapped_line_fits_in_every_language"] = not state["problems"]
        checks["a_fresh_flow_draws_no_alert"] = not state["alerts"]
        checks["default_step_names_are_translated_in_de_ru_ar"] = (
            len(state.get("labels", {})) == 3 and not any(state["labels"].values()))
        checks["a_block_the_person_caused_is_red"] = bool(state.get("conflict_red")) and all(state["conflict_red"])
        state["success"] = all(checks.values())
    except Exception:
        state["error"] = traceback.format_exc()
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str))
    print("ANGELO_FLOW_NODE_ROWS", json.dumps({"success": state["success"]}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
