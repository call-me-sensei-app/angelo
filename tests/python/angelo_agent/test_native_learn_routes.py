# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The hand routes behind learn mode's how-to cards are complete and true to Blender's own menus. No Blender.

Covered: every typed operation kind (``action_schemas.ACTION_SCHEMAS``) is answered by ``learn_routes`` with a hand
route or a reason, and nothing else is; every route's menus exist in Blender's menu sources
(``scripts/startup/bl_ui``), each menu holds the next, the last one holds the route's operator under the text the
route gives for it (the menu's own ``text=``, the operator's label, an enum's items or a submenu of them), and the
first is one the editor's header shows; the Add Modifier pages are the source's own; every allow-listed ``operator_run``
operator gets a mode, and its header menu path when the menus hold it (``learn_menu_paths``, as the record sets it); a
card (``learn_cards``) shows only what its lookup found (no label, menu or key is guessed) and survives records
another build wrote. The keys themselves are the running app's and are proved there (``test_blender_learn_routes``).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BL_UI = ROOT / "scripts/startup/bl_ui"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import action_operator_spec, action_schemas, learn_cards, learn_routes  # noqa: E402
from angelo_agent.learn_routes import EDIT, OBJECT, POSE, PROPERTIES, Route  # noqa: E402
import learn_menu_paths_source  # noqa: E402  (the header menus each editor and mode show)

# Menus the header draws under a name it builds at run time ("VIEW3D_MT_edit_" + the object's type), so no
# ``layout.menu("...")`` literal names them (space_view3d.py, VIEW3D_MT_editor_menus).
BUILT_NAMES = {"VIEW3D_MT_edit_mesh"} | {
    menu for name in action_operator_spec.ALLOWED for (route,) in [learn_routes.hand(
        {"kind": "operator_run", "parameters": {"operator": name}}).routes]
    for menu in learn_menu_paths_source.roots(route.editor, route.mode, name)}
# Menus a panel opens with its own button, not a header (the Modifiers tab's Add Modifier).
PANEL_MENUS = {learn_routes.MODIFIER_MENU}
_ITEM_CALLS = {"operator", "operator_enum", "operator_menu_enum"}

# Parameters that reach every route a kind can answer with (a kind left out is tried with none).
SAMPLES = {
    "create_primitive": [{"primitive": kind} for kind in learn_routes._PRIMITIVES],
    "create_object": [{"type": "EMPTY"}, {"type": "EMPTY", "display": "CUBE"}, {"type": "TEXT"},
                      {"type": "CURVE"}, {"type": "CURVE", "shape": "CIRCLE"}],
    "light_create": [{}, *({"settings": {"light_type": kind}} for kind in learn_routes._LIGHTS)],
    "transform": [{"location": [0, 0, 1]}, {"rotation": [0, 0, 1]}, {"scale": [2, 2, 2]},
                  {"location": [0, 0, 1], "rotation": [0, 0, 1], "scale": [2, 2, 2]}],
    "origin_set": [{"mode": mode} for mode in learn_routes._ORIGIN],
    "parent_set": [{"parent": None}, {"parent": None, "keep_transform": False}, {"parent": "scn:x:1"}],
    "transform_apply": [{"location": True}, {"rotation": True}, {"scale": True}, {"rotation": True, "scale": True},
                        {"location": True, "rotation": True, "scale": True}, {"location": True, "scale": True}],
    "uv_unwrap": [{}, {"method": "CONFORMAL"}],
    "object_duplicate": [{"linked": False}, {"linked": True}],
    "collection_link": [{"mode": "LINK"}, {"mode": "MOVE"}],
    "visibility_set": [{"hidden": True}, {"hidden": False}],
    "mesh_delete": [{"mode": "FACES"}, {"mode": "FACES_ONLY"}, {"mode": "FACES_KEEP_BOUNDARY"}],
    "mesh_separate": [{"mode": mode} for mode in learn_routes._SEPARATE],
    "modifier_add": [{"type": kind} for kind in learn_routes.MODIFIER_PAGE] + [{"type": "NODES"}],
    "material_assign": [{"face_indices": [0]}],
    "uv_project": [{"method": method} for method in learn_routes._UV_PROJECT],
    "constraint_add": [{"type": "TRACK_TO"}],
    "operator_run": [{"operator": name} for name in sorted(action_operator_spec.ALLOWED)],
}


def menu_sources():
    """{menu class: {"label", "items" (operators it draws), "calls" ((layout call, operator, its ``text=``) of
    each), "menus" (submenus it draws), "bases"}} of ``bl_ui``."""
    found = {}
    for path in sorted(BL_UI.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef) or "_MT_" not in node.name:
                continue
            info = found.setdefault(node.name, {"label": "", "items": set(), "calls": set(), "menus": set(),
                                                "bases": set()})
            info["bases"] |= {base.id for base in node.bases if isinstance(base, ast.Name)}
            for item in node.body:
                if (isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
                        and any(getattr(target, "id", "") == "bl_label" for target in item.targets)):
                    info["label"] = item.value.value
            for call in ast.walk(node):
                if isinstance(call, ast.Attribute) and call.attr == "draw" and isinstance(call.value, ast.Name):
                    info["bases"].add(call.value.id)   # ``Base.draw(self, context)``: it draws the base's items too.
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                    continue
                texts = [arg.value for arg in call.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                if call.func.attr in _ITEM_CALLS and texts:
                    info["items"].add(texts[0])
                    shown = next((keyword.value.value for keyword in call.keywords if keyword.arg == "text"
                                  and isinstance(keyword.value, ast.Constant)), "")
                    info["calls"].add((call.func.attr, texts[0], shown))
                elif call.func.attr == "menu" and texts:
                    info["menus"].add(texts[0])
                elif call.func.attr == "menu_contents" and texts:
                    info["bases"].add(texts[0])   # The menu's items drawn in place, under no title of their own.
                elif call.func.attr == "operator_modifier_add" and texts:   # The Add Modifier pages' own helper.
                    info["items"].add(("object.modifier_add", texts[0]))
    return found


def drawn(sources, menu, field, seen=()):
    """What ``menu`` draws of ``field``, its bases' included."""
    info = sources.get(menu)
    if info is None or menu in seen:
        return set()
    found = set(info[field])
    for base in info["bases"]:
        found |= drawn(sources, base, field, seen + (menu,))
    return found


def label(sources, menu, seen=()):
    """A menu's title: its own ``bl_label``, or the one a base gives it."""
    info = sources.get(menu)
    if info is None or menu in seen:
        return ""
    return info["label"] or next((found for found in (label(sources, base, seen + (menu,)) for base in sorted(info["bases"]))
                                  if found), "")


def header_menus(sources):
    """The menus an editor's header draws (its ``*_MT_editor_menus``), with the ones named at run time."""
    found = set(BUILT_NAMES)
    for name in sources:
        if name.endswith("_MT_editor_menus"):
            found |= drawn(sources, name, "menus")
    return found


def all_routes():
    """(kind, parameters, route) for every route the samples reach."""
    for kind, build in learn_routes.ROUTES.items():
        for parameters in SAMPLES.get(kind, [{}]):
            for route in learn_routes.hand({"kind": kind, "parameters": parameters}).routes:
                yield kind, parameters, route


class Lookup:
    """A stand-in for the running app: it knows only what the test gives it."""

    def __init__(self, operators=(), menus=(), keys=None, menu_keys=None):
        self.operators, self.menus = set(operators), set(menus)
        self.bound, self.menu_bound = keys or {}, menu_keys or {}
        self.free = {}   # idname -> the settings a step left to the person after the key.

    def operator_label(self, idname):
        return "<%s>" % idname if idname in self.operators else None

    def enum_label(self, idname, name, value):
        return "<%s>" % value if idname in self.operators else None

    def setting(self, idname, name, value):
        return ("<%s>" % name, repr(value))

    def menu_label(self, menu):
        return "<%s>" % menu if menu in self.menus else None

    def item_label(self, text):
        return "<%s>" % text

    def editor_label(self, editor):
        return "<%s>" % editor

    def tab_label(self, tab):
        return "<%s>" % tab

    def mode_label(self, mode):
        return "<%s>" % mode

    def keys(self, idname, properties, editor, mode, free=()):
        self.free[idname] = set(free) - {""}
        return self.bound.get(idname, ())

    def menu_key(self, menu, editor, mode):
        return self.menu_bound.get(menu, "")


def row(*operations, tool="angelo_scene_program"):
    return {"role": "activity", "tool": tool, "arguments": {"mode": "APPLY", "operations": list(operations)}}


BEVEL = {"kind": "mesh_bevel", "target": "scn:x:1", "parameters": {"edge_indices": [1, 2, 3], "offset": 0.02, "segments": 3}}


class Coverage(unittest.TestCase):
    def test_every_typed_kind_is_answered_and_nothing_else(self):
        kinds = set(action_schemas.ACTION_SCHEMAS)
        self.assertEqual(kinds - learn_routes.answered(), set(), "a typed kind has neither a hand route nor a reason")
        self.assertEqual(learn_routes.answered() - kinds, set(), "the table answers a kind Angelo does not have")

    def test_every_route_kind_reaches_a_route_and_a_kind_without_one_has_its_own_reason(self):
        reached = {kind for kind, _, _ in all_routes()}
        self.assertEqual(set(learn_routes.ROUTES) - reached, set(), "a ROUTES kind no sample reaches")
        for kind, build in learn_routes.ROUTES.items():   # A builder that can answer with none names why.
            if not learn_routes.hand({"kind": kind, "parameters": {}}).routes:
                with self.subTest(kind=kind):
                    self.assertTrue(kind in learn_routes.NO_ROUTE or kind in SAMPLES)

    def test_a_route_is_identifiers_and_marked_text_only(self):
        for kind, parameters, route in all_routes():
            with self.subTest(kind=kind, parameters=parameters):
                self.assertTrue(route.operator or route.title, "a route is a command or a titled settings change")
                self.assertIn(route.mode, learn_routes.MODES)
                self.assertRegex(route.operator, r"^([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)?$")
                for menu in route.menus:
                    self.assertRegex(menu, r"^[A-Z][A-Z0-9]*_MT_[a-z0-9_]+$")
                if route.choice:
                    self.assertIn(route.choice, dict(route.properties))

    def test_every_allow_listed_operator_gets_the_kernels_mode(self):
        for name in action_operator_spec.ALLOWED:
            (route,) = learn_routes.hand({"kind": "operator_run", "parameters": {"operator": name}}).routes
            module = name.split(".", 1)[0]
            expected = POSE if module in action_operator_spec.POSE_MODULES else (
                EDIT if action_operator_spec.edit_only(name) else OBJECT)
            self.assertEqual((route.operator, route.mode), (name, expected))

    def test_an_operator_runs_properties_are_what_make_it_that_action(self):
        # A key bound to the same operator with another value is not this action's key (learn_resolver.Lookup._is).
        (route,) = learn_routes.hand({"kind": "operator_run", "parameters": {
            "operator": "mesh.select_mode", "properties": {"type": "EDGE", "use_extend": False}}}).routes
        self.assertEqual(dict(route.properties), {"type": "EDGE", "use_extend": False})
        self.assertEqual(route.settings, (("type", "type"), ("use_extend", "use_extend")))
        (route,) = learn_routes.hand({"kind": "operator_run", "parameters": {
            "operator": "transform.translate", "properties": {"value": [0, 0, 1]}}}).routes
        self.assertEqual(route.properties, (("value", (0, 0, 1)),))
        hash(route)   # A route stays hashable whatever the record holds.
        (route,) = learn_routes.hand({"kind": "operator_run", "parameters": {"operator": "mesh.fill", "properties": 7}}).routes
        self.assertEqual((route.properties, route.settings), ((), ()))

    def test_a_mode_no_menu_entry_does_is_angelos_own_tool(self):
        # Never a route that does something else: delete keeping the boundary is no entry of the Delete menu.
        hand = learn_routes.hand({"kind": "mesh_delete", "parameters": {"mode": "FACES_KEEP_BOUNDARY"}})
        self.assertEqual((hand.routes, hand.reason), ((), learn_routes.OWN_TOOL))

    def test_a_record_another_build_wrote_never_raises(self):
        for operation in (None, {}, {"kind": 7}, {"kind": "mesh_bevel"}, {"kind": "create_primitive", "parameters": {}},
                          {"kind": "create_primitive", "parameters": {"primitive": "KLEIN_BOTTLE"}},
                          {"kind": "a_later_kind", "parameters": {}}, {"kind": "operator_run", "parameters": {}}):
            hand = learn_routes.hand(operation)
            self.assertTrue(hand.routes or hand.reason, operation)


class BlenderMenus(unittest.TestCase):
    """Each route against Blender's own menu sources in this checkout."""

    @classmethod
    def setUpClass(cls):
        cls.sources = menu_sources()
        cls.headers = header_menus(cls.sources)

    def test_the_menus_exist_and_each_holds_the_next(self):
        for kind, parameters, route in all_routes():
            for parent, child in zip(route.menus, route.menus[1:]):
                with self.subTest(kind=kind, parent=parent, child=child):
                    self.assertIn(child, drawn(self.sources, parent, "menus"))
            for menu in route.menus:
                with self.subTest(kind=kind, menu=menu):
                    self.assertIn(menu, self.sources)
                    self.assertTrue(label(self.sources, menu), "a menu a person can read")

    def test_the_last_menu_holds_the_operator(self):
        for kind, parameters, route in all_routes():
            if not (route.operator and route.menus):
                continue
            items = drawn(self.sources, route.menus[-1], "items")
            with self.subTest(kind=kind, operator=route.operator, menu=route.menus[-1]):
                if route.operator == "object.modifier_add" and len(route.menus) > 1:
                    self.assertIn((route.operator, dict(route.properties)["type"]), items)
                elif route.operator == "object.modifier_add":   # A type no page lists: the path stops at the menu.
                    self.assertNotIn(dict(route.properties)["type"], learn_routes.MODIFIER_PAGE)
                else:
                    self.assertIn(route.operator, items)

    def test_the_menus_own_text_for_a_command_is_the_sources(self):
        """A route's last breadcrumb is what the menu itself shows: its ``text=``, the operator's label when it gives
        none, the enum's items when it lists them, or a submenu of them."""
        for kind, parameters, route in all_routes():
            if not (route.operator and route.menus) or route.operator == "object.modifier_add":
                continue   # A modifier's entry is its type's name, drawn by the pages' own helper (the test above).
            calls = [call for call in drawn(self.sources, route.menus[-1], "calls") if call[1] == route.operator]
            with self.subTest(kind=kind, operator=route.operator, parameters=parameters, calls=sorted(calls)):
                if route.submenu:
                    self.assertIn(("operator_menu_enum", route.operator, route.item), calls)
                    self.assertTrue(route.choice)
                elif route.choice:
                    self.assertIn(("operator_enum", route.operator, ""), calls)
                    self.assertEqual(route.item, "")
                else:
                    self.assertIn(("operator", route.operator, route.item), calls)

    def test_the_first_menu_is_one_the_header_or_its_panel_shows(self):
        for kind, parameters, route in all_routes():
            if route.menus:
                with self.subTest(kind=kind, menu=route.menus[0]):
                    self.assertIn(route.menus[0], self.headers | PANEL_MENUS)
                    self.assertEqual(route.menus[0] in PANEL_MENUS, route.editor == PROPERTIES)

    def test_the_add_modifier_pages_are_the_sources_own(self):
        pages = {}
        for name in self.sources:
            if name.startswith(learn_routes.MODIFIER_MENU + "_"):
                for item in drawn(self.sources, name, "items"):
                    if isinstance(item, tuple):
                        pages[item[1]] = name
        self.assertEqual(learn_routes.MODIFIER_PAGE, pages)
        for page in set(pages.values()):
            self.assertIn(page, drawn(self.sources, learn_routes.MODIFIER_MENU, "menus"))


class Cards(unittest.TestCase):
    def test_a_step_shows_what_the_app_reports(self):
        lookup = Lookup(operators={"mesh.bevel"}, menus={"VIEW3D_MT_edit_mesh_edges"}, keys={"mesh.bevel": ("Ctrl B",)})
        (step,) = learn_cards.steps(row(BEVEL), lookup)
        self.assertEqual((step.kind, step.index, step.title, step.keys), ("mesh_bevel", 0, "<mesh.bevel>", ("Ctrl B",)))
        self.assertEqual(step.path, ("<VIEW_3D>", "<VIEW3D_MT_edit_mesh_edges>", "<Bevel Edges>"))   # The menu's own text.
        self.assertEqual(step.settings, (("<offset>", "0.02"), ("<segments>", "3")))
        self.assertEqual(step.needs[0], "<EDIT>")
        self.assertIn("3", step.needs[1])

    def test_a_key_may_leave_to_the_person_only_the_choice_and_the_settings_the_card_lists(self):
        # learn_resolver.Lookup._is: a key that leaves one of the action's settings at a default other than the
        # action's value runs another action, unless the person sets it after the key (the menu the key opens, or
        # Adjust Last Operation with the card's settings).
        lookup = Lookup(operators={"mesh.bevel", "object.parent_clear", "mesh.select_mode"})
        learn_cards.steps(row(BEVEL), lookup)
        (bevel,) = learn_routes.hand(BEVEL).routes
        self.assertEqual(lookup.free["mesh.bevel"], {name for _, name in bevel.settings})   # The card's settings,
        self.assertNotIn("affect", lookup.free["mesh.bevel"])   # and not affect: the key must be the edge one.
        learn_cards.steps(row({"kind": "parent_set", "target": "scn:x:1", "parameters": {"parent": None}}), lookup)
        self.assertEqual(lookup.free["object.parent_clear"], {"type"})   # Alt P asks which clear.
        learn_cards.steps(row({"kind": "operator_run", "target": "", "parameters": {
            "operator": "mesh.select_mode", "properties": {"type": "EDGE", "use_extend": True}}}), lookup)
        self.assertEqual(lookup.free["mesh.select_mode"], {"type", "use_extend"})   # Both listed on the card.

    def test_nothing_the_app_did_not_report_is_shown(self):
        # No such menu in this build: no path at all, never a partial one. No key bound: none shown.
        (step,) = learn_cards.steps(row(BEVEL), Lookup(operators={"mesh.bevel"}))
        self.assertEqual((step.path, step.keys, step.opens), ((), (), ()))
        # And no search hint: Blender's search finds a command only through the header's menus, so a command with
        # no menu path is one search cannot find (interface_template_search_menu.cc).
        self.assertNotIn("search", {field.name for field in dataclasses.fields(step)})
        # No such command in this build: the step says Angelo's own tool did it.
        (step,) = learn_cards.steps(row(BEVEL), Lookup())
        self.assertEqual((step.keys, step.path, step.settings), ((), (), ()))
        self.assertEqual(step.note, learn_routes.OWN_TOOL)

    def test_a_key_that_opens_a_menu_is_named_with_that_menu(self):
        apply = {"kind": "transform_apply", "target": "scn:x:1", "parameters": {"scale": True}}
        lookup = Lookup(operators={"object.transform_apply"}, menus={"VIEW3D_MT_object", "VIEW3D_MT_object_apply"},
                        menu_keys={"VIEW3D_MT_object_apply": "Ctrl A"})
        (step,) = learn_cards.steps(row(apply), lookup)
        self.assertEqual(step.opens, ("<VIEW3D_MT_object_apply>", "Ctrl A"))
        self.assertEqual(step.keys, ())
        self.assertEqual((step.path[-1], step.settings), ("<Scale>", ()))   # Apply's own entry for scale alone.
        mixed = {"kind": "transform_apply", "target": "scn:x:1", "parameters": {"location": True, "scale": True}}
        (step,) = learn_cards.steps(row(mixed), lookup)   # No entry of its own: the first's, with what it applies.
        self.assertEqual(step.path[-1], "<Location>")
        self.assertEqual([label for label, _ in step.settings], ["<location>", "<scale>"])

    def test_a_submenu_of_choices_is_named_before_its_item(self):
        origin = {"kind": "origin_set", "target": "scn:x:1", "parameters": {"mode": "MEDIAN"}}
        lookup = Lookup(operators={"object.origin_set", "mesh.separate"}, menus={"VIEW3D_MT_object", "VIEW3D_MT_edit_mesh"})
        (step,) = learn_cards.steps(row(origin), lookup)
        self.assertEqual(step.path, ("<VIEW_3D>", "<VIEW3D_MT_object>", "<Set Origin>", "<ORIGIN_GEOMETRY>"))
        separate = {"kind": "mesh_separate", "target": "scn:x:1", "parameters": {"mode": "LOOSE"}}
        (step,) = learn_cards.steps(row(separate), lookup)   # The submenu is named by the command itself.
        self.assertEqual(step.path, ("<VIEW_3D>", "<VIEW3D_MT_edit_mesh>", "<mesh.separate>", "<LOOSE>"))

    def test_a_choice_ends_the_path_with_its_item(self):
        add = {"kind": "modifier_add", "target": "scn:x:1", "parameters": {"type": "BEVEL"}}
        lookup = Lookup(operators={"object.modifier_add"},
                        menus={learn_routes.MODIFIER_MENU, learn_routes.MODIFIER_PAGE["BEVEL"]})
        (step,) = learn_cards.steps(row(add), lookup)
        self.assertEqual(step.path[-3:], ("<OBJECT_MT_modifier_add>", "<OBJECT_MT_modifier_add_generate>", "<BEVEL>"))
        self.assertEqual(step.path[:2], ("<PROPERTIES>", "<MODIFIER>"))

    def test_one_operation_can_be_several_commands_and_a_kind_without_one_gives_its_reason(self):
        move = {"kind": "transform", "target": "scn:x:1", "parameters": {"location": [0, 0, 1], "scale": [2, 2, 2]}}
        bend = {"kind": "mesh_bend", "target": "scn:x:1", "parameters": {"rise": 1}}
        lookup = Lookup(operators={"transform.translate", "transform.resize"})
        found = learn_cards.steps(row(move, bend), lookup)
        self.assertEqual([(step.index, step.title) for step in found],
                         [(0, "<transform.translate>"), (0, "<transform.resize>"), (1, learn_cards.T_OWN)])
        self.assertEqual(found[2].note, learn_routes.OWN_TOOL)
        self.assertEqual(found[0].note, learn_routes.BY_MOUSE)

    def test_only_typed_steps_have_a_card_and_a_card_is_bounded(self):
        self.assertFalse(learn_cards.has_card({"tool": "angelo_execute_python", "arguments": {"code": "pass"}}))
        self.assertFalse(learn_cards.has_card({"tool": "angelo_scene_program", "arguments": {"operations": "x"}}))
        self.assertFalse(learn_cards.has_card(None))
        self.assertTrue(learn_cards.has_card(row(BEVEL)))
        recipe = {"tool": "angelo_apply_recipe", "arguments": {"recipe": "r", "program": {"operations": [BEVEL]}}}
        self.assertTrue(learn_cards.has_card(recipe))
        many = row(*[{"kind": "transform", "parameters": {"location": [0, 0, 1], "rotation": [0, 0, 1], "scale": [1, 1, 2]}}] * 32)
        lookup = Lookup(operators={"transform.translate", "transform.rotate", "transform.resize"})
        self.assertEqual(len(learn_cards.steps(many, lookup)), learn_cards.MAX_STEPS)

    def test_an_operator_run_step_is_the_recorded_operator_with_its_own_settings(self):
        bridge = {"kind": "operator_run", "target": "scn:x:1",
                  "parameters": {"operator": "mesh.bridge_edge_loops", "properties": {"number_cuts": 2}}}
        (step,) = learn_cards.steps(row(bridge), Lookup(operators={"mesh.bridge_edge_loops"}))
        self.assertEqual((step.title, step.settings, step.needs), ("<mesh.bridge_edge_loops>", (("<number_cuts>", "2"),), ("<EDIT>",)))

    def test_an_operator_run_step_names_where_the_header_holds_it_in_the_menus_words(self):
        # The operator's label is not what its menu says ("Multi Select Edge Loops" is Select > Select Loops > Edge
        # Loops), and search finds only what the menus say: the path ends with the menu's own text.
        loops = {"kind": "operator_run", "target": "scn:x:1", "parameters": {"operator": "mesh.select_edge_loop_multi"}}
        menus = {"VIEW3D_MT_select_edit_mesh", "VIEW3D_MT_edit_mesh_select_loops"}
        (step,) = learn_cards.steps(row(loops), Lookup(operators={"mesh.select_edge_loop_multi"}, menus=menus))
        self.assertEqual(step.path, ("<VIEW_3D>", "<VIEW3D_MT_select_edit_mesh>", "<VIEW3D_MT_edit_mesh_select_loops>",
                                     "<Edge Loops>"))

    def test_a_menu_entry_that_sets_a_property_is_this_action_only_with_the_records_value(self):
        def route(properties):
            (found,) = learn_routes.hand({"kind": "operator_run", "parameters": {
                "operator": "mesh.edge_rotate", "properties": properties}}).routes
            return found.menus, found.item
        self.assertEqual(route({"use_ccw": True}), (("VIEW3D_MT_edit_mesh_edges",), "Rotate Edge CCW"))
        self.assertEqual(route({"use_ccw": False}), (("VIEW3D_MT_edit_mesh_edges",), "Rotate Edge CW"))
        self.assertEqual(route({}), ((), ""))   # Not recorded: which entry it was is unknown, so no path.
        self.assertEqual(route({"use_ccw": 7}), ((), ""))

    def test_an_operator_no_header_menu_holds_has_no_path(self):
        (found,) = learn_routes.hand({"kind": "operator_run", "parameters": {"operator": "mesh.select_mode"}}).routes
        self.assertEqual((found.menus, found.item), ((), ""))   # Only the Ctrl Tab menu, which search leaves out.


if __name__ == "__main__":
    unittest.main()
