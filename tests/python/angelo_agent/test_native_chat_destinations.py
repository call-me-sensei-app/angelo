# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu is one registry (``chat_destinations``), Angelo's main navigation (1.0.5). No Blender.

Covered: every feature Angelo ships (``TOP_LEVEL``) is registered by its own module and appears in the menu as one
top-level row; a feature added by another module (as web export adds its own) appears with no change to the menu;
the launcher's top level (``entries``) lists each feature once with its section and placement, a group's rows come as
its own page (``group_rows``, never inline) and nothing about groups is remembered between openings (1.0.8); the
whole-menu listing (``menu_rows``, which search reads) puts a group's rows after it; the Generation Flows group offers
New, the templates and the person's flows (``flow_open.menu_rows``); a click on a row runs its operator and a click on a
group opens its page; a worker's chat lists no chosen tool.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import chat_destinations as cd  # noqa: E402


def registrations():
    """``{feature id: module}`` for every ``chat_destinations.Feature(...)`` an add-on module constructs."""
    found = {}
    for path in ADDON.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "Feature":
                if node.args and isinstance(node.args[0], ast.Constant):
                    found[node.args[0].value] = path.stem
    return found


class RegistryTests(unittest.TestCase):
    def setUp(self):
        saved = dict(cd._features)
        self.addCleanup(lambda: (cd._features.clear(), cd._features.update(saved)))

    def test_every_shipped_feature_is_registered_by_its_module_and_shown(self):
        found = registrations()
        for feature_id in cd.SHIPPED:
            self.assertIn(feature_id, found, "registered by no module: " + feature_id)
        for feature_id, module in found.items():
            self.assertIn(feature_id, cd.SHIPPED, "%s registers %s; list it in TOP_LEVEL or PARENTS" % (module, feature_id))
        self.assertFalse(set(cd.PARENTS) & set(cd.TOP_LEVEL), "a group's member is not also a row of its own")
        self.assertLessEqual(set(cd.PARENTS.values()), set(cd.TOP_LEVEL), "every group is a top-level row")
        for module in set(found.values()) - {"chat_destinations"}:
            source = (ADDON / (module + ".py")).read_text()
            self.assertIn("chat_destinations.register(", source, module)
            self.assertIn("chat_destinations.unregister(", source, module)
        # Registered as they are when the add-on runs, each feature is one top-level row of the menu.
        cd.register(cd.Feature("media_studio", "Media Studio", glyph="studio", order=10, operator="angelo.toggle_studio"))
        cd.register(cd.Feature("shot_to_video", "Make Video from This Shot", glyph="studio", order=16,
                               operator="angelo.shot_to_video", properties=(("mode", "FRAMES"),)))
        cd.register(cd.Feature("shot_motion", "Video from This Camera Move", glyph="studio", order=17,
                               operator="angelo.shot_to_video", properties=(("mode", "MOTION"),)))
        cd.register(cd.Feature("workflows", "Workflows", glyph="studio", order=25, operator="angelo.open_media_studio",
                               properties=(("kind", "WORKFLOW"),)))
        cd.register(cd.Feature("photo_to_3d", "Photo to 3D model…", glyph="studio", order=12, operator="angelo.photo_drop"))
        cd.register(cd.Feature("video", "Video", glyph="video", order=15, operator="angelo.open_media_studio",
                               properties=(("kind", "VIDEO"),)))
        cd.register(cd.Feature("flows", "Generation Flows", glyph="flow", order=20, children=lambda *_: []))
        cd.register(cd.Feature("render_engine", "Render Engine", glyph="engine", order=21, children=lambda *_: []))
        cd.register(cd.Feature("ai_render", "AI render", glyph="ai_render", order=22, operator="angelo.ai_render"))
        cd.register(cd.Feature("library", "Angelo Library", glyph="library", order=14, operator="angelo.library_open",
                               properties=(("mode", "BROWSE"),)))
        cd.register(cd.Feature("bake_maps", "Bake maps…", glyph="bake_maps", order=24, operator="angelo.bake_maps"))
        cd.register(cd.Feature("render_region", "Render region", glyph="region", order=23,
                               operator="angelo.render_region"))
        cd.register(cd.Feature("select_through", "Select Through", order=46, operator="angelo.select_through"))
        cd.register(cd.Feature("tension_map", "Tension map", glyph="tension", order=47, operator="angelo.tension_map"))
        cd.register(cd.Feature("uv_checker", "UV checker", glyph="uv_checker", order=48, section="Check",
                               operator="angelo.uv_checker"))
        cd.register(cd.Feature("engine_skeleton", "Check a rig for a game engine", glyph="skeleton", order=49,
                               section="Inspect", operator="angelo.skeleton_check"))
        cd.register(cd.Feature("web_export", "Web export", glyph="web", order=50, operator="angelo.web_export"))
        cd.register(cd.Feature("web_preview", "Live browser preview", glyph="web", order=51, operator="angelo.web_preview"))
        cd.register(cd.Feature("godot_link", "Game engine live link", glyph="link", order=52, children=lambda *_: []))
        cd.register(cd.Feature("report_bug", "Report a bug…", glyph="feedback", order=90, section="Feedback",
                               operator="angelo.send_feedback", properties=(("kind", "BUG"),)))
        cd.register(cd.Feature("request_feature", "Request a feature…", glyph="feedback", order=91,
                               section="Feedback", operator="angelo.send_feedback", properties=(("kind", "FEATURE"),)))
        rows = cd.menu_rows()
        top = [row["value"] for row in rows if row["kind"] in {"row", "group"} and not row.get("depth")]
        self.assertEqual(top, list(cd.TOP_LEVEL), "menu order is TOP_LEVEL's")
        # Every member is reachable: its group opens to it, and its row runs its own operator.
        opened = cd.menu_rows(open_groups=set(cd.PARENTS.values()))
        inside = {row["value"]: row for row in opened if row.get("depth")}
        for member, group in cd.PARENTS.items():
            self.assertIn(member, inside, member)
            self.assertEqual((inside[member]["kind"], inside[member]["action"], inside[member]["feature"]),
                             ("row", "popup_add_open", member), member)
            self.assertEqual(inside[member]["operator"], cd.feature(member).operator, member)

    def test_a_member_whose_group_is_missing_is_a_row_of_its_own_and_an_empty_group_is_left_out(self):
        cd.unregister(cd.RENDER_GROUP)
        cd.register(cd.Feature("ai_render", "AI render", glyph="ai_render", order=22, operator="angelo.ai_render"))
        top = [row["value"] for row in cd.menu_rows() if not row.get("depth") and row["kind"] != "section"]
        self.assertIn("ai_render", top)
        self.assertNotIn(cd.VIDEO_GROUP, top, "no video feature registered: no empty Video group")

    def test_another_module_adds_an_entry_without_touching_the_menu(self):
        cd.register(cd.Feature("web_export", "Web export", "Publish the scene as a web page", glyph="web", order=50,
                               section="Share", operator="angelo.web_export"))
        rows = cd.menu_rows()
        row = next(row for row in rows if row.get("value") == "web_export")
        self.assertEqual((row["action"], row["operator"]), ("popup_add_open", "angelo.web_export"))
        self.assertEqual(rows[rows.index(row) - 1], {"kind": "section", "label": "Share"})
        cd.unregister("web_export")
        self.assertFalse(any(row.get("value") == "web_export" for row in cd.menu_rows()))

    def test_a_workers_chat_is_offered_no_tool_its_send_would_not_run(self):
        """Chosen tools act on Angelo's own scene at Send (console.execute skips them in a worker's chat), so a
        worker's chat lists none of them; the rest of the menu is unchanged."""
        lead_only = {item.id for item in cd.features() if item.lead_only}
        self.assertEqual(lead_only, {"base_mesh", "goal", "hands"})
        lead, worker = cd.menu_rows(open_groups={"base_mesh"}), cd.menu_rows(open_groups={"base_mesh"}, worker=True)
        self.assertFalse(any(row.get("feature") in lead_only or row.get("value") in lead_only for row in worker))
        self.assertEqual([row for row in lead if row.get("feature") not in lead_only and row.get("value") not in lead_only
                          and row["kind"] != "section"], [row for row in worker if row["kind"] != "section"])
        self.assertFalse({entry["feature"] for entry in cd.entries(worker=True)} & lead_only)
        self.assertEqual(cd.group_rows("base_mesh", worker=True), [])
        draw = (ADDON / "chat_add_draw.py").read_text(encoding="utf-8")
        self.assertIn("worker = bool(worker_snapshots(controller))", draw)
        for call in ("entries(worker)", "group_rows(popup[\"page\"], chosen, fit_target, worker)",
                     "search(popup[\"query\"], chosen, fit_target, worker"):
            self.assertIn(call, draw)

    def test_a_feature_is_one_row_or_a_group(self):
        for bad in (cd.Feature("x", "X"), cd.Feature("x", "X", operator="a.b", children=lambda *_: [])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cd.register(bad)


class GroupTests(unittest.TestCase):
    def test_groups_are_closed_until_opened_and_their_rows_follow_the_header(self):
        closed = cd.menu_rows()
        group = next(row for row in closed if row.get("value") == "base_mesh")
        self.assertEqual((group["kind"], group["open"]), ("group", False))
        self.assertFalse(any(row.get("depth") for row in closed), "nothing of a closed group is listed")
        opened = cd.menu_rows(open_groups={"base_mesh"})
        index = next(i for i, row in enumerate(opened) if row.get("value") == "base_mesh")
        children = []
        for row in opened[index + 1:]:   # Up to the next top-level row (Work on hands follows the group).
            if not row.get("depth"):
                break
            children.append(row)
        self.assertTrue(children and all(row["feature"] == "base_mesh" for row in children))
        self.assertEqual(len(children), len(opened) - len(closed))

    def test_a_group_is_a_page_of_its_own_and_nothing_is_remembered(self):
        """1.0.8: groups opened inline and remembered open across launches pushed features below the fold."""
        self.assertFalse(hasattr(cd, "GroupMemory"))
        entry = next(entry for entry in cd.entries() if entry["value"] == "base_mesh")
        self.assertEqual((entry["kind"], entry["action"], entry["placement"]), ("group", "popup_add_group", "grid"))
        page = cd.group_rows("base_mesh")
        self.assertTrue(page and all(row["feature"] == "base_mesh" for row in page))
        self.assertFalse(any(row.get("depth") for row in page), "a page's rows are its list, not indented")
        self.assertEqual(cd.group_rows("hands"), [], "a row is no group")
        source = (ADDON / "chat_add_menu.py").read_text() + (ADDON / "chat_add_nav.py").read_text()
        self.assertNotIn("chat-add-menu.json", source)


class MenuSourceTests(unittest.TestCase):
    def test_flows_offer_new_templates_and_yours(self):
        source = (ADDON / "flow_open.py").read_text()
        rows = source.split("\ndef menu_rows(", 1)[1].split("\ndef ", 1)[0]
        for needed in ('leaf("flows", NEW_ROW, NEW', "flow_templates.listing()", '("template", entry["id"])',
                       "flow_trees()[:LISTED]", "translate=False"):
            self.assertIn(needed, rows)

    def test_a_row_runs_its_operator_and_a_group_click_opens_its_page_in_the_open_menu(self):
        source = (ADDON / "chat_add_nav.py").read_text()
        dispatch = source.split("\ndef dispatch(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("('INVOKE_DEFAULT', True, **dict(properties))", dispatch,
                      "an undoable row (New Flow, a template) is its own undo step")
        group = dispatch.split('if action == "popup_add_group":', 1)[1].split("elif ", 1)[0]
        self.assertIn("open_page(popup, value)", group)
        self.assertNotIn('state.pop("popover"', group)


class AppStateCacheTests(unittest.TestCase):
    """Real bounded registry reads; the launcher cache is presentation, never connection authority."""

    def setUp(self):
        import contextlib
        import json
        import os
        import tempfile
        from types import SimpleNamespace
        from unittest import mock
        import angelo_agent
        from angelo_agent import change_clock, external_clients, external_setup
        self.json, self.os, self.clock, self.registry_type = json, os, change_clock, external_clients.AppRegistry
        self.default = external_clients.DEFAULT_CLIENT
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.registry = self.registry_type(self.root)
        self.registry.root.mkdir()
        modules = {"ui": SimpleNamespace(state_directory=lambda: self.root),
                   "external_ui": SimpleNamespace(results={})}
        self.stack.enter_context(mock.patch.dict(sys.modules, {"angelo_agent." + k: v for k, v in modules.items()}))
        for name, module in modules.items():
            self.stack.enter_context(mock.patch.object(angelo_agent, name, module, create=True))
        self.stack.enter_context(mock.patch.object(cd, "_registry_seen", {}))
        self.installed = self.stack.enter_context(mock.patch.object(external_setup, "installed", return_value=True))
        original = self.registry_type.load
        self.load = self.stack.enter_context(mock.patch.object(
            self.registry_type, "load", autospec=True, side_effect=lambda registry: original(registry)))
        self.stack.enter_context(mock.patch.object(change_clock, "settle", side_effect=AssertionError("UI may not wait")))

    def write(self, app, path=None):
        data = self.json.dumps({"apps": {self.default[app]: {"app": app, "levels": ["READ"]}}})
        (path or self.registry.path).write_text(data.ljust(1024), encoding="utf-8")

    def test_coarse_same_signature_rewrite_is_reloaded_without_waiting(self):
        from types import SimpleNamespace
        from unittest import mock
        self.write("codex")
        witness = self.os.stat(self.registry.path)
        original_stat = self.os.stat
        def stat(path, *args, **kwargs):
            return witness if str(path) == str(self.registry.path) else original_stat(path, *args, **kwargs)
        coarse = SimpleNamespace(fine=False, step=10_000_000, below=10_000_000, changes=True)
        with mock.patch.object(self.os, "stat", side_effect=stat), \
                mock.patch.object(self.clock, "volume", return_value=coarse), \
                mock.patch.object(self.clock, "system_time", return_value=self.clock._filetime(
                    max(witness.st_mtime_ns, witness.st_ctime_ns))):
            self.assertEqual(cd._app_states(now=10)["codex"][0], "connected")
            self.write("claude_code")  # Actual bytes change, but every stat field is the same coarse witness.
            states = cd._app_states(now=10.1)
            self.write("codex")
            cd._app_states(now=10.2)
            self.assertEqual(self.installed.call_count, 2, "Each app lookup is reused within the install poll")
            cd._app_states(now=12)
            self.assertEqual(self.installed.call_count, 3, "A racy registry refresh must not postpone the poll")
        self.assertEqual(states["codex"][0], "found")
        self.assertEqual(states["claude_code"][0], "connected")
        self.assertEqual(self.load.call_count, 4)

    def test_settled_cache_still_rereads_real_content_at_the_poll_boundary(self):
        from types import SimpleNamespace
        from unittest import mock
        self.write("codex")
        with mock.patch.object(self.clock, "volume", return_value=SimpleNamespace(fine=True)):
            first = cd._app_states(now=20)
            self.assertEqual(cd._app_states(now=20.1), first)
            self.assertEqual(self.load.call_count, 1)
            cd._app_states(now=22)
        self.assertEqual(self.load.call_count, 2)

    def test_atomic_replacement_with_same_size_and_mtime_invalidates_the_cache(self):
        from types import SimpleNamespace
        from unittest import mock
        self.write("codex")
        stamp = self.os.stat(self.registry.path).st_mtime_ns // 1_000_000 * 1_000_000
        self.os.utime(self.registry.path, ns=(stamp, stamp))
        before = self.os.stat(self.registry.path)
        with mock.patch.object(self.clock, "volume", return_value=SimpleNamespace(fine=True)):
            cd._app_states(now=30)
            replacement = self.registry.path.with_suffix(".replacement")
            self.write("claude_code", replacement)
            self.os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.os.replace(replacement, self.registry.path)
            after = self.os.stat(self.registry.path)
            self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))
            self.assertNotEqual(before.st_ino, after.st_ino)
            self.assertEqual(cd._app_states(now=30.1)["claude_code"][0], "connected")
        self.assertEqual(self.load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
