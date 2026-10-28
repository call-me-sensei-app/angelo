# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Workers at once (``parallel_setting``, 1.0.6, the maintainer 2026-10-25): the person chooses how many worker agents
run at the same time, Suggested follows the machine, and a forced launch keeps its own count. Beside it, the Sessions
panel row: where the panel stands in the 3D view (Left, Top or Bottom), also the person's.

Without Blender: admission's count (``parallel_access.worker_limit``), the AI Providers page's rows and their choice
buttons at every width, the Sessions panel's accessors and click, and the wiring that sets the preference and the start
tool's launch count. The in-app proof is the GUI suite's Settings check on Mac 1.
"""

import ast
import os
import sys
import types
import unittest
from unittest import mock
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ADDON.parent))
sys.path.insert(0, str(ADDON.parents[1] / "angelo"))
from angelo_agent import (card_kit as kit, parallel_access, parallel_capability as cap,  # noqa: E402
                          providers_page_layout as page, providers_page_model as model, ui_text)
from i18n import scan  # noqa: E402
from test_native_notice_cards import FakeView  # noqa: E402


def _import_setting():
    """``parallel_setting`` declares its Blender properties as it is imported: a stand-in ``bpy.props`` lets the host
    import it, and ``providers_page_rail``, which reads it. No stand-in stays in ``sys.modules``."""
    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "IntProperty"):
        setattr(props, name, lambda **declared: declared)
    stand_in = types.ModuleType("bpy")
    stand_in.props = props
    saved = {name: sys.modules.pop(name, None) for name in ("bpy", "bpy.props")}
    sys.modules.update({"bpy": stand_in, "bpy.props": props})
    try:
        from angelo_agent import parallel_setting, providers_page_rail
    finally:
        for name, module in saved.items():
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module
    return parallel_setting, providers_page_rail


parallel_setting, rail = _import_setting()

ENV = "ANGELO_ENABLE_PARALLEL_AGENTS"
MACHINE = cap.assess(True, "Apple M4 Pro", 51.5, 14)   # Mac 1: 10 at once.


def preferences(value, registered=True, enabled=True, **fields):
    """Blender as ``chosen`` reads it: the add-on's entry in the preferences and its ``parallel_workers`` (and any
    other preference in ``fields``: ``rail_position``)."""
    addon = types.SimpleNamespace(preferences=types.SimpleNamespace(parallel_workers=value, **fields)
                                  if registered else None)
    fake = types.ModuleType("bpy")
    fake.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
        addons={"angelo_agent": addon} if enabled else {}))
    return patch.dict(sys.modules, {"bpy": fake})


def unforced():
    environ = {key: value for key, value in os.environ.items() if key != ENV}
    return patch.dict(os.environ, environ, clear=True)


class LimitTests(unittest.TestCase):
    def test_suggested_follows_the_machine_and_a_number_is_the_persons(self):
        self.assertEqual(MACHINE.workers, 10)
        with unforced(), patch.object(cap, "current", return_value=MACHINE):
            for value, limit in ((0, 10), (1, 1), (4, 4), (16, 16)):   # Above the suggestion too: they decide.
                with preferences(value):
                    self.assertEqual(parallel_access.worker_limit(), limit, value)

    def test_only_a_whole_number_from_1_to_16_counts(self):
        for value in (-1, 17, True, "4", 4.0, None):
            with preferences(value):
                self.assertEqual(parallel_access.chosen(), 0, value)
        with patch.dict(sys.modules, {"bpy": None}):   # No Blender: Suggested.
            self.assertEqual(parallel_access.chosen(), 0)
        with preferences(4, registered=False):   # The add-on still importing: its preferences are not there yet.
            self.assertEqual(parallel_access.chosen(), 0)
        with preferences(4, enabled=False):
            self.assertEqual(parallel_access.chosen(), 0)

    def test_the_count_never_reaches_back_into_a_module_still_importing(self):
        """``tool_specs`` asks for the count while ``ui`` imports; reading it through ``ui`` stopped the add-on
        loading (Mac 1 GUI suite on cf141994dd4: a partially initialized module). Blender's preferences only."""
        source = (ADDON / "parallel_access.py").read_text(encoding="utf-8")
        body = source[source.index("def chosen("):]
        self.assertNotIn("import ui", body)
        self.assertNotIn("ui.", body.replace("bpy.context", ""))

    def test_a_forced_launch_and_a_machine_below_the_bar_ignore_the_choice(self):
        with preferences(2), patch.object(cap, "current", return_value=MACHINE):
            with patch.dict(os.environ, {ENV: "1"}):
                self.assertEqual(parallel_access.worker_limit(), cap.measured_fit())   # A benchmark keeps its count.
            with patch.dict(os.environ, {ENV: "0"}):
                self.assertEqual(parallel_access.worker_limit(), 0)
        with unforced(), preferences(8), patch.object(cap, "current", return_value=cap.assess(False, "", 64, 16)):
            self.assertEqual(parallel_access.worker_limit(), 0)   # No feature, no workers, whatever was chosen.


class SettingsRowTests(unittest.TestCase):
    def row(self, value, prefs=True, env=None):
        environ = unforced() if env is None else patch.dict(os.environ, {ENV: env})
        with environ, preferences(value), patch.object(cap, "current", return_value=MACHINE):
            return model._parallel_edit(object() if prefs else None)["rows"][0]

    def test_the_row_offers_suggested_and_the_numbers_with_the_choice_marked(self):
        row = self.row(0)
        self.assertEqual(row["name"], "Workers at once")
        self.assertEqual(row["lines"], ["Up to 10 worker agents at once on this computer."])
        self.assertEqual([(label, value, chosen) for label, _action, (_name, value), chosen in row["choices"]],
                         [("Suggested (10)", 0, True), ("1", 1, False), ("2", 2, False), ("4", 4, False),
                          ("8", 8, False), ("16", 16, False)])
        self.assertEqual({(action, name) for _l, action, (name, _v), _c in row["choices"]}, {("pref", "parallel_workers")})
        self.assertNotIn("facts", row)
        chosen = self.row(4)
        self.assertEqual(chosen["lines"], ["Up to 4 worker agents at once on this computer."])
        self.assertEqual([c[2][1] for c in chosen["choices"] if c[3]], [4])

    def test_more_than_suggested_is_allowed_and_says_what_it_may_cost(self):
        row = self.row(16)
        self.assertEqual(row["lines"], ["Up to 16 worker agents at once on this computer."])
        self.assertEqual(row["facts"], [("alert", "More than this computer suggests: other apps may slow down, or "
                                                  "workers may fail to start.", "amber")])

    def test_a_forced_launch_shows_its_count_without_a_choice(self):
        for row in (self.row(4, env="1"), self.row(4, prefs=False)):
            self.assertNotIn("choices", row)
            self.assertTrue(row["name"].startswith("Up to "), row["name"])

    def test_every_choice_is_reachable_inside_the_column_at_every_width(self):
        row = self.row(4)
        section = {"title": "Parallel edit", "description": "Workers build parts at the same time.", "rows": [row]}
        page_model = {"hero": {"title": "AI Providers", "purpose": "Accounts.", "add": "Add connection"},
                      "attention": [], "team": None, "sections": [section], "trust": "Keys stay on this computer."}
        for width, scale in ((1100, 1.0), (520, 1.0), (380, 1.0), (380, 2.0)):
            plan = page.layout(FakeView(), page_model, width * scale, 4000, scale, kit.UiState())
            x, _y, w, _h = plan.card
            hits = [(rect, value) for rect, action, value in plan.hits if action == "pref"]
            self.assertEqual([value[1] for _rect, value in hits], [0, 1, 2, 4, 8, 16], width)
            for (rx, _ry, rw, _rh), _value in hits:
                self.assertGreaterEqual(rx, x - 1, width)
                self.assertLessEqual(rx + rw, x + w + 1, width)
            styles = {op[2]: op[3] for op in plan.ops if op[0] == "pbutton" and op[2] in ("4", "8")}
            self.assertEqual(styles, {"4": "primary", "8": "secondary"})   # The choice reads as chosen.


    def test_a_stored_number_not_offered_still_shows_chosen(self):
        row = self.row(3)   # Set by a script or an older build: shown, chosen, never silently unchosen.
        self.assertEqual([c[2][1] for c in row["choices"]], [0, 1, 2, 3, 4, 8, 16])
        self.assertEqual([c[2][1] for c in row["choices"] if c[3]], [3])

    def test_a_wrapped_fact_never_covers_what_follows_it(self):
        """Break-it P1-21 M1: a line of facts moved down 20 px whatever its text's height, so the amber line drew
        over the choice buttons once it wrapped (about 500 px, the default Preferences width). Any fact, any width."""
        long = ("Another fact whose words run long enough to wrap onto a second and a third line on a narrow page, "
                "as a translated line can.")
        row = dict(self.row(16), facts=[("alert", self.row(16)["facts"][0][1], "amber"), ("info", long, "muted")])
        section = {"title": "Parallel edit", "description": "Workers build parts.", "rows": [row]}
        page_model = {"hero": {"title": "AI Providers", "purpose": "Accounts.", "add": "Add connection"},
                      "attention": [], "team": None, "sections": [section], "trust": "Keys stay on this computer."}
        words = set(" ".join(fact[1] for fact in row["facts"]).split())
        for width, scale in ((1100, 1.0), (520, 1.0), (380, 1.0), (380, 2.0)):
            plan = page.layout(FakeView(), page_model, width * scale, 4000, scale, kit.UiState())
            tops = [rect[1] + rect[3] for rect, action, _value in plan.hits if action == "pref"]   # y is upward.
            facts = [op for op in plan.ops if op[0] == "text" and op[1].split() and set(op[1].split()) <= words]
            self.assertTrue(facts and tops, width)
            for op in facts:
                self.assertGreaterEqual(op[3], max(tops) + 2 * scale, (width, scale, op[1]))

    def test_one_worker_is_said_as_one(self):
        from angelo_agent import worker_spec
        self.assertIn("At most 1 worker runs at once on this computer;", worker_spec.start_description(1))
        self.assertIn("At most 4 workers run at once on this computer;", worker_spec.start_description(4))
        from angelo_agent import parallel_access   # The refusal past the limit says it the same way (one helper).
        with mock.patch.object(parallel_access, "_forced", return_value=None):
            self.assertIn("At most 1 worker runs at once on this computer; 0 already", parallel_access.limit_text(1, 0))


class Area:
    """A stand-in 3D view (or another editor) that counts its redraws."""

    def __init__(self, kind):
        self.type, self.redraws = kind, 0

    def tag_redraw(self):
        self.redraws += 1


def rail_blender(addon_preferences, windows=(), context=True):
    """Blender as the Sessions panel's setting and its click see it: the add-on's preferences (or None), whether the
    preferences changed (``is_dirty``) and the window manager's windows; ``context=False``: a stand-in without them."""
    fake = types.ModuleType("bpy")
    if context:
        addon = types.SimpleNamespace(preferences=addon_preferences)
        fake.context = types.SimpleNamespace(
            preferences=types.SimpleNamespace(is_dirty=False, addons={"angelo_agent": addon}),
            window_manager=types.SimpleNamespace(windows=list(windows)))
    return fake


def window(*areas):
    return types.SimpleNamespace(screen=types.SimpleNamespace(areas=list(areas), is_temporary=False))


PLACES = ("LEFT", "TOP", "BOTTOM")


class RailSettingTests(unittest.TestCase):
    """``parallel_setting``'s Sessions panel accessors: they never raise, read as the left column shown whenever the
    preferences cannot be read, keep only a place, and a change is saved as one made in Preferences is."""

    def test_the_places_are_left_top_and_bottom(self):
        self.assertEqual(parallel_setting.RAIL_POSITIONS, PLACES)

    def test_unreadable_preferences_read_as_the_left_column_shown(self):
        older = types.SimpleNamespace(parallel_workers=0)   # An add-on build from before the Sessions panel's place.
        disabled = rail_blender(None)
        disabled.context.preferences.addons = {}
        cases = {"no Blender": None, "a stand-in bpy without preferences": rail_blender(None, context=False),
                 "the add-on not enabled": disabled, "its preferences not registered yet": rail_blender(None),
                 "an older build's preferences": rail_blender(older)}
        for case, fake in cases.items():
            with self.subTest(case), patch.dict(sys.modules, {"bpy": fake}):
                self.assertEqual(parallel_setting.rail_position(), "LEFT")
                self.assertIs(parallel_setting.rail_minimized(), False)
                parallel_setting.set_rail_position("TOP")   # Changes nothing, and never raises.
                parallel_setting.set_rail_minimized(True)
                self.assertEqual(parallel_setting.rail_position(), "LEFT")
                self.assertIs(parallel_setting.rail_minimized(), False)
                if hasattr(fake, "context"):
                    self.assertFalse(fake.context.preferences.is_dirty)
        self.assertFalse(hasattr(older, "rail_position") or hasattr(older, "rail_minimized"))

    def test_only_a_stored_place_counts(self):
        for stored, read in (("LEFT", "LEFT"), ("TOP", "TOP"), ("BOTTOM", "BOTTOM"), ("RIGHT", "LEFT"),
                             ("top", "LEFT"), ("", "LEFT"), (None, "LEFT")):
            prefs = types.SimpleNamespace(rail_position=stored, rail_minimized=False)
            with self.subTest(stored=stored), patch.dict(sys.modules, {"bpy": rail_blender(prefs)}):
                self.assertEqual(parallel_setting.rail_position(), read)
        for stored, read in ((True, True), (False, False), (1, False), ("yes", False), (None, False)):
            prefs = types.SimpleNamespace(rail_position="LEFT", rail_minimized=stored)
            with self.subTest(minimized=stored), patch.dict(sys.modules, {"bpy": rail_blender(prefs)}):
                self.assertIs(parallel_setting.rail_minimized(), read)

    def test_a_place_is_kept_and_saved_and_anything_else_is_refused(self):
        prefs = types.SimpleNamespace(rail_position="LEFT", rail_minimized=False)
        fake = rail_blender(prefs)
        with patch.dict(sys.modules, {"bpy": fake}):
            for place in ("TOP", "BOTTOM", "LEFT"):
                fake.context.preferences.is_dirty = False
                parallel_setting.set_rail_position(place)
                self.assertEqual((prefs.rail_position, fake.context.preferences.is_dirty), (place, True), place)
            fake.context.preferences.is_dirty = False
            parallel_setting.set_rail_position("LEFT")   # Already there: nothing to save.
            self.assertFalse(fake.context.preferences.is_dirty)
            for value in ("RIGHT", "top", "", None, 0, ["TOP"], ("TOP",)):
                parallel_setting.set_rail_position(value)
                self.assertEqual((prefs.rail_position, fake.context.preferences.is_dirty), ("LEFT", False), value)
        self.assertIs(prefs.rail_minimized, False)   # The place never minimizes the panel.

    def test_minimizing_is_kept_and_saved(self):
        prefs = types.SimpleNamespace(rail_position="TOP", rail_minimized=False)
        fake = rail_blender(prefs)
        with patch.dict(sys.modules, {"bpy": fake}):
            parallel_setting.set_rail_minimized(True)
            self.assertEqual((prefs.rail_minimized, fake.context.preferences.is_dirty), (True, True))
            fake.context.preferences.is_dirty = False
            parallel_setting.set_rail_minimized(1)   # The same state: nothing to save.
            self.assertEqual((prefs.rail_minimized, fake.context.preferences.is_dirty), (True, False))
            parallel_setting.set_rail_minimized(False)
            self.assertEqual((prefs.rail_minimized, fake.context.preferences.is_dirty), (False, True))
        self.assertIs(type(prefs.rail_minimized), bool)
        self.assertEqual(prefs.rail_position, "TOP")

    def test_the_preference_and_the_page_say_the_same_words(self):
        """The enum's labels and description are the page's own messages (one translation each), in its order."""
        tree = ast.parse((ADDON / "parallel_setting.py").read_text(encoding="utf-8"))
        declared = {node.target.id: node.annotation for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)}
        position = {k.arg: ast.literal_eval(k.value) for k in declared["rail_position"].keywords}
        minimized = {k.arg: ast.literal_eval(k.value) for k in declared["rail_minimized"].keywords}
        self.assertEqual(tuple(item[0] for item in position["items"]), parallel_setting.RAIL_POSITIONS)
        self.assertEqual([item[1] for item in position["items"]], [rail.LABELS[p] for p in parallel_setting.RAIL_POSITIONS])
        self.assertEqual((position["name"], position["default"]), ("Sessions panel", "LEFT"))
        self.assertEqual(position["description"], "Where the Sessions panel stands in the 3D view")
        self.assertIs(minimized["default"], False)


def _bracketed(message, context=None):
    """A language in which every message reads differently."""
    return f"[{message}]"


class SessionsPanelRowTests(unittest.TestCase):
    """The Sessions panel row on the AI Providers page, after Workers at once: Left, Top and Bottom, the current one
    primary; a click keeps the place and redraws every 3D view."""

    def rows(self, place="LEFT", prefs=True, env=None, machine=MACHINE):
        environ = unforced() if env is None else patch.dict(os.environ, {ENV: env})
        with environ, preferences(0, rail_position=place), patch.object(cap, "current", return_value=machine):
            return model._parallel_edit(object() if prefs else None)["rows"]

    def test_the_row_follows_workers_at_once_with_left_top_and_bottom(self):
        rows = self.rows()
        self.assertEqual([row["name"] for row in rows], ["Workers at once", "Sessions panel"])
        panel = rows[1]
        self.assertEqual(panel["lines"], ["Where the Sessions panel stands in the 3D view."])
        self.assertEqual([(label, action, value) for label, action, value, _chosen in panel["choices"]],
                         [("Left", "rail_position", "LEFT"), ("Top", "rail_position", "TOP"),
                          ("Bottom", "rail_position", "BOTTOM")])

    def test_the_current_place_is_the_one_chosen(self):
        for place in PLACES:
            chosen = [value for _label, _action, value, on in self.rows(place)[1]["choices"] if on]
            self.assertEqual(chosen, [place], place)
        for stored in ("RIGHT", None):   # Unreadable: the left column, shown chosen.
            chosen = [value for _label, _action, value, on in self.rows(stored)[1]["choices"] if on]
            self.assertEqual(chosen, ["LEFT"], stored)

    def test_the_row_shows_whenever_parallel_edit_runs_and_only_then(self):
        self.assertEqual([row["name"] for row in self.rows(env="1")][1:], ["Sessions panel"])   # A forced launch too.
        for rows in (self.rows(prefs=False), self.rows(env="0"), self.rows(machine=cap.assess(False, "", 64, 16))):
            self.assertEqual(len(rows), 1, rows[0]["name"])

    def test_every_place_is_a_button_in_the_column_below_the_counts_the_chosen_one_primary(self):
        labels = {"LEFT": "Left", "TOP": "Top", "BOTTOM": "Bottom"}
        for place in PLACES:
            section = {"title": "Parallel edit", "description": "Workers build parts at the same time.",
                       "rows": self.rows(place)}
            page_model = {"hero": {"title": "AI Providers", "purpose": "Accounts.", "add": "Add connection"},
                          "attention": [], "team": None, "sections": [section], "trust": "Keys stay on this computer."}
            for width, scale in ((1100, 1.0), (520, 1.0), (380, 1.0), (380, 2.0)):
                where = (place, width, scale)
                plan = page.layout(FakeView(), page_model, width * scale, 4000, scale, kit.UiState())
                x, _y, w, _h = plan.card
                hits = [(rect, value) for rect, action, value in plan.hits if action == rail.ACTION]
                self.assertEqual([value for _rect, value in hits], list(PLACES), where)
                for (rx, _ry, rw, _rh), _value in hits:
                    self.assertGreaterEqual(rx, x - 1, where)
                    self.assertLessEqual(rx + rw, x + w + 1, where)
                counts = [rect for rect, action, _value in plan.hits if action == "pref"]
                self.assertLess(max(r[1] + r[3] for r, _v in hits), min(r[1] for r in counts), where)   # y is upward.
                styles = {op[2]: op[3] for op in plan.ops if op[0] == "pbutton" and op[2] in labels.values()}
                self.assertEqual(styles, {label: "primary" if value == place else "secondary"
                                          for value, label in labels.items()}, where)
                self.assertLessEqual({(rail.ACTION, value) for value in PLACES}, set(plan.focus_order), where)

    def test_a_click_keeps_the_place_and_redraws_every_3d_view(self):
        prefs = types.SimpleNamespace(rail_position="LEFT", rail_minimized=False)
        views, others = [Area("VIEW_3D"), Area("VIEW_3D"), Area("VIEW_3D")], [Area("PREFERENCES"), Area("CONSOLE")]
        between = types.SimpleNamespace(screen=None)   # A window being opened or closed: skipped, never read.
        fake = rail_blender(prefs, [window(views[0], others[0]), between, window(views[1], views[2], others[1])])
        with patch.dict(sys.modules, {"bpy": fake}):
            rail.act(rail.ACTION, "BOTTOM")
            self.assertEqual((prefs.rail_position, fake.context.preferences.is_dirty), ("BOTTOM", True))
            self.assertEqual([area.redraws for area in views + others], [1, 1, 1, 0, 0])
            fake.context.preferences.is_dirty = False
            rail.act(rail.ACTION, "RIGHT")   # Not a place: kept as it was.
            rail.act("pref", ("rail_position", "TOP"))   # Another row's action: not this row's.
            self.assertEqual((prefs.rail_position, fake.context.preferences.is_dirty), ("BOTTOM", False))
            self.assertEqual([area.redraws for area in views + others], [2, 2, 2, 0, 0])
        self.assertIs(prefs.rail_minimized, False)

    def test_the_text_is_marked_and_translated_where_it_is_shown(self):
        source = (ADDON / "providers_page_rail.py").read_text(encoding="utf-8")
        findings, marked = scan.scan(source, "providers_page_rail.py")
        self.assertEqual(findings, [])
        self.assertLessEqual({"Sessions panel", "Where the Sessions panel stands in the 3D view", "Left", "Top", "Bottom"},
                             {message for message, _line, _how in marked})
        with preferences(0, rail_position="TOP"), patch.dict(ui_text._pgettext, {"iface": _bracketed}):
            row = rail.row()
        self.assertEqual(row["name"], "[Sessions panel]")
        self.assertEqual(row["lines"], ["[Where the Sessions panel stands in the 3D view]."])
        self.assertEqual([label for label, _action, _value, _on in row["choices"]], ["[Left]", "[Top]", "[Bottom]"])
        self.assertEqual([value for _label, _action, value, on in row["choices"] if on], ["TOP"])   # Values stay.


class WiringTests(unittest.TestCase):
    def test_the_page_sets_the_preference_and_the_start_tool_counts_it_at_launch(self):
        host = (ADDON / "providers_page_host.py").read_text(encoding="utf-8")
        self.assertIn('elif action in ("toggle", "pref") and prefs is not None:', host)
        self.assertIn('if action == "toggle" else value', host)
        ui = (ADDON / "ui.py").read_text(encoding="utf-8")
        self.assertIn("parallel_setting.ParallelPreferences, AddonPreferences", ui)
        init = (ADDON / "__init__.py").read_text(encoding="utf-8")
        self.assertLess(init.index("ui.register()"), init.index("tool_specs.refresh_worker_count()"))
        self.assertLess(init.index("tool_specs.refresh_worker_count()"), init.index("session_rail_input.register()"))
        setting = (ADDON / "parallel_setting.py").read_text(encoding="utf-8")
        self.assertIn("parallel_workers: IntProperty(", setting)
        self.assertIn("min=0, max=MAX_WORKERS", setting)

    def test_the_refresh_states_the_count_admission_uses(self):
        from angelo_agent import tool_specs, worker_spec
        name = "angelo_workers_start"
        added = name not in tool_specs.TOOL_MAP   # Off on this machine: the family is not in the list; add its entry.
        start = tool_specs.TOOL_MAP.setdefault(name, dict(worker_spec.WORKER_TOOLS[0]))
        before = start["description"]
        try:
            with unforced(), preferences(3), patch.object(cap, "current", return_value=MACHINE):
                tool_specs.refresh_worker_count()
                self.assertIn("At most 3 workers run at once", start["description"])
                self.assertIs(tool_specs.TOOL_MAP[name], start)   # The list's own entry, changed in place.
        finally:
            start["description"] = before
            if added:
                del tool_specs.TOOL_MAP[name]

    def test_a_sessions_panel_click_reaches_the_setting(self):
        host = (ADDON / "providers_page_host.py").read_text(encoding="utf-8")
        self.assertIn("elif action in providers_page_rail.ACTIONS:", host)
        self.assertIn("providers_page_rail.act(action, value)", host)
        source = (ADDON / "providers_page_model.py").read_text(encoding="utf-8")
        body = source[source.index("def _parallel_edit("):source.index("def _enhance(")]
        self.assertIn("rows.append(providers_page_rail.row())", body)


if __name__ == "__main__":
    unittest.main()
