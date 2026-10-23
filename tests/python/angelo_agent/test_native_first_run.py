# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""First-ten-minutes rules: one-step connect, examples, readable cards and notices, voice visibility.

Host-only: the connect state machine runs against a real ConnectionStore and
stand-in authentication/catalog owners; ``bpy`` is a minimal stub because
only the operator classes need it. The window behaviour is proven by
test_blender_first_run.py, test_blender_chat_cards.py and
test_blender_director_navigation.py.
"""

import importlib
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import first_run  # noqa: E402
from angelo_agent.approvals import approval_message  # noqa: E402
from angelo_agent.chat_titles import tool_title  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402

JARGON = ("native action", "Native action", "$ui_cube", "declared revision", "journaled", "Recorded Snapshot",
          "isolated stage", "atomic Blender kernel", "Host-expanded")


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object)
    bpy.props = SimpleNamespace(StringProperty=lambda **_: None, IntProperty=lambda **_: None)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props}


class FakeAuthentication:
    def __init__(self, statuses):
        self.status, self.running, self.started, self.cancelled = dict(statuses), set(), [], []

    def start(self, mode, settings, state_dir, login=False, identity=None, api_key=""):
        identity = identity or mode
        self.started.append((mode, identity, login))
        self.running.add(identity)
        self.status[identity] = "Complete sign-in in your browser…" if login else "Checking native sign-in…"

    def finish(self, identity, status):
        self.running.discard(identity)
        self.status[identity] = status

    def cancel(self, identity):
        self.cancelled.append(identity)


class FakeUI:
    """The ui owners quick_connect uses, with the same readiness rule as ui.connection_ready."""

    def __init__(self, directory, statuses):
        self.store = ConnectionStore(directory)
        self._authentication = FakeAuthentication(statuses)
        self.catalog = SimpleNamespace(entries={}, errors={}, invalidate=lambda identity: None)
        self.controller = SimpleNamespace(state={"id": "chat-1"}, busy=False, version=0, selection_models=[])
        self._controllers = {"chat-1": self.controller}
        self.bound = []

    def connections(self):
        return self.store

    def catalogs(self):
        return self.catalog

    def connection_settings(self, mode, record=None):
        return {}

    def state_directory(self):
        return "/nonexistent"

    def connection_ready(self, record):
        identity = record["mode"] if record["reuse_native"] else record["id"]
        return str(self._authentication.status.get(identity, "")).startswith("Connected to ")

    def choose_model_connection(self, controller, connection_id, model=""):
        self.bound.append(connection_id)


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        with mock.patch.dict(sys.modules, fake_bpy()):
            sys.modules.pop("angelo_agent.quick_connect", None)
            self.qc = importlib.import_module("angelo_agent.quick_connect")
        self.qc.reset()

    def tearDown(self):
        self.qc.reset()
        sys.modules.pop("angelo_agent.quick_connect", None)
        self.directory.cleanup()

    def ui(self, **statuses):
        return FakeUI(self.directory.name, statuses)

    def test_existing_sign_in_is_reused_without_a_login_and_lands_on_loaded_models(self):
        ui = self.ui(claude_subscription="Connected to Claude", codex_subscription="Not connected")
        self.qc.start(ui, ui.controller, "claude_subscription")
        record = next(iter(ui.store.records.values()))
        self.assertTrue(record["reuse_native"])
        self.assertEqual(ui._authentication.started, [], "A signed-in CLI needs no browser sign-in")
        self.assertEqual(ui.bound, [record["id"]])
        self.assertEqual(self.qc.flow("claude_subscription")["phase"], "loading")
        ui.catalog.entries[record["id"]] = [{"id": "default"}]
        self.assertTrue(self.qc.pump(ui))
        self.assertIsNone(self.qc.flow("claude_subscription"))

    def test_signed_out_cli_runs_its_own_login_in_a_private_profile_then_binds(self):
        ui = self.ui(codex_subscription="Not connected")
        self.qc.start(ui, ui.controller, "codex_subscription")
        record = next(iter(ui.store.records.values()))
        self.assertFalse(record["reuse_native"])
        self.assertEqual(ui._authentication.started, [("codex_subscription", record["id"], True)])
        self.assertEqual(self.qc.flow("codex_subscription")["phase"], "signing_in")
        ui._authentication.finish(record["id"], "Connected to ChatGPT")
        self.qc.pump(ui)
        self.assertEqual(ui.bound, [record["id"]])
        self.assertEqual(self.qc.flow("codex_subscription")["phase"], "loading")

    def test_failed_sign_in_stays_in_the_card_and_leaves_no_dead_connection(self):
        ui = self.ui(codex_subscription="Not connected")
        self.qc.start(ui, ui.controller, "codex_subscription")
        identity = next(iter(ui.store.records))
        ui._authentication.finish(identity, "Sign-in did not complete. Retry or use the native Terminal command.")
        self.qc.pump(ui)
        flow = self.qc.flow("codex_subscription")
        self.assertEqual(flow["phase"], "failed")
        self.assertIn("Connect ChatGPT to try again", flow["reason"])
        self.assertEqual(ui.store.records, {})
        self.assertEqual(ui.bound, [])

    def test_a_later_successful_connect_clears_an_earlier_failure(self):
        ui = self.ui(codex_subscription="Not connected", claude_subscription="Connected to Claude")
        self.qc.start(ui, ui.controller, "codex_subscription")
        codex = next(iter(ui.store.records))
        ui._authentication.finish(codex, "Sign-in did not complete.")
        self.qc.pump(ui)
        self.assertEqual(self.qc.flow("codex_subscription")["phase"], "failed")
        self.qc.start(ui, ui.controller, "claude_subscription")
        ui.catalog.entries[ui.bound[-1]] = [{"id": "default"}]
        self.qc.pump(ui)
        self.assertIsNone(self.qc.flow("claude_subscription"))
        self.assertIsNone(self.qc.flow("codex_subscription"))

    def test_waits_for_the_start_up_status_check_before_choosing_reuse(self):
        ui = self.ui(claude_subscription="Checking native sign-in…")
        ui._authentication.running.add("claude_subscription")
        self.qc.start(ui, ui.controller, "claude_subscription")
        self.assertEqual(ui.store.records, {})
        self.assertEqual(self.qc.flow("claude_subscription")["phase"], "checking")
        ui._authentication.finish("claude_subscription", "Connected to Claude")
        self.qc.pump(ui)
        self.assertTrue(next(iter(ui.store.records.values()))["reuse_native"])

    def test_an_existing_ready_connection_is_reused_not_duplicated(self):
        ui = self.ui(claude_subscription="Connected to Claude")
        existing = ui.store.add("ANTHROPIC", "SUBSCRIPTION", reuse_native=True)
        self.qc.start(ui, ui.controller, "claude_subscription")
        self.assertEqual(list(ui.store.records), [existing["id"]])
        self.assertEqual(ui.bound, [existing["id"]])

    def test_cancel_stops_the_sign_in_and_removes_only_what_it_created(self):
        ui = self.ui(claude_subscription="Not connected")
        self.qc.start(ui, ui.controller, "claude_subscription")
        identity = next(iter(ui.store.records))
        self.qc.cancel(ui, "claude_subscription")
        self.assertEqual(ui._authentication.cancelled, [identity])
        self.assertEqual(ui.store.records, {})
        self.assertIsNone(self.qc.flow("claude_subscription"))

    def test_missing_cli_and_busy_chat_refuse_without_side_effects(self):
        ui = self.ui(claude_subscription="Native runtime unavailable")
        with self.assertRaisesRegex(RuntimeError, "Install Claude Code"):
            self.qc.start(ui, ui.controller, "claude_subscription")
        ui = self.ui(claude_subscription="Connected to Claude")
        ui.controller.busy = True
        with self.assertRaisesRegex(RuntimeError, "Stop the current turn"):
            self.qc.start(ui, ui.controller, "claude_subscription")
        self.assertEqual(ui.store.records, {})


class OfferAndNoticeTests(unittest.TestCase):
    def test_connect_options_follow_detected_clis_then_the_api_key(self):
        rows = first_run.connect_options({"codex_subscription": "Not connected", "claude_subscription": "Connected to Claude"})
        self.assertEqual([(r["id"], r["label"], r["action"]) for r in rows],
                         [("claude_subscription", "Connect Claude", "welcome_connect"),
                          ("codex_subscription", "Connect ChatGPT", "welcome_connect"),
                          ("api_key", "Use an API key", "welcome_api_key")])
        self.assertIn("already on this computer", rows[0]["note"])
        self.assertIn("browser", rows[1]["note"])
        connected = first_run.connect_options({"claude_subscription": "Connected to Claude"}, ready_modes={"claude_subscription"})
        self.assertEqual((connected[0]["label"], connected[0]["note"]), ("Use Claude", first_run.ALREADY_CONNECTED))
        none = first_run.connect_options({"claude_subscription": "Native runtime unavailable; check its executable path"})
        self.assertEqual([r["id"] for r in none], ["api_key"])

    def test_an_organisation_sign_in_slots_in_first(self):
        team = {"label": "Your team", "note": "Ask your admin for access", "action": "welcome_team", "value": None}
        rows = first_run.connect_options({"claude_subscription": "Not connected"}, team=team)
        self.assertEqual([r["id"] for r in rows], ["team", "claude_subscription", "api_key"])
        self.assertEqual(rows[0]["action"], "welcome_team")
        self.assertEqual(set(rows[0]), set(rows[1]), "Every row has the same shape, so one draw path serves all")

    def test_intro_shows_once_on_first_launch_only(self):
        self.assertTrue(first_run.should_show_intro(seen=False, mode_saved=False, disabled=False))
        self.assertFalse(first_run.should_show_intro(seen=True, mode_saved=False, disabled=False))
        self.assertFalse(first_run.should_show_intro(seen=False, mode_saved=True, disabled=False), "Chosen in an older build")
        self.assertFalse(first_run.should_show_intro(seen=False, mode_saved=False, disabled=True))

    def test_splash_follows_blenders_startup_rule(self):
        self.assertTrue(first_run.splash_on_startup(enabled=True, file_opened=False, preferences_saved=True))
        self.assertTrue(first_run.splash_on_startup(enabled=True, file_opened=True, preferences_saved=False),
                        "Without saved preferences Quick Setup shows even when a file was opened")
        self.assertFalse(first_run.splash_on_startup(enabled=True, file_opened=True, preferences_saved=True))
        self.assertFalse(first_run.splash_on_startup(enabled=False, file_opened=False, preferences_saved=False),
                         "Turned off in Preferences")

    def test_setup_card_replaces_quick_setup(self):
        source = (ADDON / "splash_setup.py").read_text()
        menu = source.split("class ANGELO_MT_splash_quick_setup", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('bl_idname = "WM_MT_splash_quick_setup"', menu)
        self.assertIn("if pending():", menu, "Blender's form never shows while Angelo's setup card follows")
        self.assertIn("setup_card.SetupCard(", source)
        self.assertIn("bpy.ops.wm.save_userpref()", source, "Continue saves the preferences, as Quick Setup did")
        self.assertIn("settings_import_ui.after_setup(card.source", source, "Use my Blender settings imports")
        apply = (ADDON / "settings_import_apply.py").read_text()
        self.assertIn("bpy.ops.preferences.copy_prev()", apply, "An earlier Angelo's settings: Blender's own import")

    def test_fake_clis_report_the_tested_versions(self):
        # Window fixtures connect through fake_provider_cli; outside the tested range they would be refused or noticed.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import fake_provider_cli
        from angelo_agent import cli_runtime
        self.assertEqual(fake_provider_cli.CLAUDE_VERSION, cli_runtime.format_version(cli_runtime.RUNTIMES["claude"]["ceiling"]))
        self.assertEqual(fake_provider_cli.CODEX_VERSION, cli_runtime.format_version(cli_runtime.RUNTIMES["codex"]["ceiling"]))
        self.assertEqual(cli_runtime.codex_version({"userAgent": "codex_cli_rs/%s (fake)" % fake_provider_cli.CODEX_VERSION}),
                         cli_runtime.RUNTIMES["codex"]["ceiling"])

    def test_demo_table_is_one_place_with_the_bridge_first(self):
        self.assertTrue(first_run.DEMOS[0][0].startswith("Build a bridge"))
        for title, url in first_run.DEMOS:
            self.assertTrue(url.startswith("https://angelo.studio/demos/"), url)
            self.assertTrue(title)

    def test_examples_are_few_short_and_use_the_default_scene(self):
        self.assertTrue(3 <= len(first_run.EXAMPLES) <= 4)
        for example in first_run.EXAMPLES:
            self.assertLessEqual(len(example), 60)
            self.assertIn("cube", example.lower())

    def test_generation_suggestions_follow_the_scene_examples(self):
        """Media Studio and Flows are taught in the empty chat: a person can just ask (1.0.5)."""
        self.assertEqual(first_run.suggestions(), first_run.EXAMPLES + first_run.GENERATION_EXAMPLES)
        self.assertEqual(len(first_run.GENERATION_EXAMPLES), 2)
        for example in first_run.GENERATION_EXAMPLES:
            self.assertLessEqual(len(example), 60)
        self.assertIn("image", first_run.GENERATION_EXAMPLES[0].lower())
        self.assertIn("3d model", first_run.GENERATION_EXAMPLES[1].lower())
        source = (ADDON / "quick_connect.py").read_text()
        self.assertIn("examples = first_run.suggestions()", source, "A click fills the box with any suggestion")
        self.assertIn("first_run.suggestions()", (ADDON / "chat_welcome.py").read_text())

    def test_limit_notices_show_the_reset_time_the_provider_gave(self):
        codex = "Provider usage limit reached (usageLimitExceeded): You've hit your usage limit. Upgrade to Pro or try again at 4:12 PM."
        self.assertEqual(first_run.notice(codex), {"kind": "limit", "reset": "4:12 PM", "headline": "Usage limit reached · resets 4:12 PM"})
        claude = "You've hit your session limit · resets 4:30pm (UTC)"
        self.assertEqual(first_run.notice(claude)["reset"], "4:30pm (UTC)")
        now = time.mktime((2026, 10, 9, 9, 0, 0, 0, 0, -1))
        epoch = int(time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)))
        self.assertEqual(first_run.reset_time("Claude AI usage limit reached|%d" % epoch, now=now), "16:05")
        later = int(time.mktime((2026, 10, 10, 8, 30, 0, 0, 0, -1)))
        self.assertEqual(first_run.reset_time("usage limit reached|%d" % later, now=now), "2026-10-10 08:30")
        self.assertEqual(first_run.notice("Rate limit reached")["headline"], "Usage limit reached")

    def test_offline_notices_and_ordinary_errors(self):
        self.assertEqual(first_run.notice("API Error: Connection error.")["kind"], "offline")
        self.assertEqual(first_run.notice("stream disconnected before completion: error sending request")["kind"], "offline")
        for text in ("Native Blender observation timed out", "Select an image-capable model", "", None):
            self.assertIsNone(first_run.notice(text), text)

    def test_voice_is_off_unless_opted_in_and_only_where_it_can_work(self):
        self.assertFalse(first_run.voice_supported(False, "darwin", "codex_subscription", True), "Hidden at 1.0 by default")
        self.assertTrue(first_run.voice_supported(True, "darwin", "codex_subscription", True))
        self.assertFalse(first_run.voice_supported(True, "win32", "codex_subscription", True))
        self.assertFalse(first_run.voice_supported(True, "linux", "codex_subscription", True))
        self.assertFalse(first_run.voice_supported(True, "darwin", "claude_subscription", True))
        self.assertFalse(first_run.voice_supported(True, "darwin", "codex_subscription", False))


class PlainLanguageTests(unittest.TestCase):
    def test_transcript_titles_read_as_plain_language(self):
        for status, result in (("running", {}), ("approval", {}), ("completed", {"status": "preview"}),
                               ("completed", {"status": "committed"}), ("failed", {})):
            for tool in ("angelo_scene_program", "angelo_apply_recipe", "angelo_expand_recipe", "angelo_accept_preview"):
                title = tool_title({"tool": tool, "status": status, "result": result})
                for word in ("Native", "native", "PREVIEW", "staging", "isolated", "committed"):
                    self.assertNotIn(word, title, (tool, status, title))

    def test_approval_cards_name_objects_and_avoid_internal_terms(self):
        program = {"mode": "APPLY", "operations": [
            {"kind": "create_object", "target": None, "parameters": {"alias": "$ui_cube", "name": "Cube.001"}},
            {"kind": "set_material", "target": "$ui_cube"}, {"kind": "modifier_add", "target": "scn:42"}]}
        cards = [approval_message("angelo_scene_program", program, target_labels={"$ui_cube": "Cube.001", "scn:42": "Cube"}),
                 approval_message("angelo_scene_program", dict(program, mode="PREVIEW")),
                 approval_message("angelo_apply_recipe", {"recipe": "bevel-and-shade", "program": program}),
                 approval_message("angelo_expand_recipe", {"recipe": "bevel-and-shade", "target": "Cube"}),
                 approval_message("angelo_accept_preview", {"preview_id": "p1"}),
                 approval_message("angelo_discard_preview", {"preview_id": "p1"}),
                 approval_message("angelo_write_file", {"path": "a.txt", "content": "x"}),
                 approval_message("angelo_delete_file", {"path": "a.txt"}),
                 approval_message("angelo_execute_python", {"description": "Bevel", "code": "pass"})]
        for card in cards:
            text = card["title"] + card["body"] + card["warning"]
            for word in JARGON:
                self.assertNotIn(word, text, card)
        self.assertIn("“Cube.001”", cards[0]["body"])
        self.assertIn("“Cube”", cards[0]["body"])
        self.assertEqual(cards[0]["title"], "Apply 3 changes")


class TranslationReadyTests(unittest.TestCase):
    """New text is extractable whole: n_/iface_ take one literal, never a joined or computed string."""

    MODULES = ("first_run.py", "chat_welcome.py", "chat_titles.py", "quick_connect.py", "intro.py", "welcome_screen.py", "voice_ui.py",
               "splash_setup.py", "setup_card.py", "start_card.py", "canvas_controls.py", "team_access.py", "connect_hero.py", "approvals.py", "checkpoint_review_ui.py", "checkpoint_settings_ui.py", "agent_workspace_ui.py")

    def test_marked_strings_are_single_literals(self):
        import ast
        for name in self.MODULES:
            tree = ast.parse((ADDON / name).read_text())
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id in {"n_", "iface_"}]
            with self.subTest(module=name):
                for call in calls:
                    argument = call.args[0] if call.args else None
                    literal = isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                    # A variable is allowed only where it holds a marked constant (tables in first_run/chat_titles).
                    self.assertTrue(literal or isinstance(argument, (ast.Name, ast.Subscript, ast.Call, ast.Attribute)),
                                    (name, ast.unparse(call)))
                    if literal:
                        self.assertNotRegex(argument.value, r"^\s|\s$", "Translators get whole phrases, not padded fragments")

    def test_templates_fill_named_placeholders(self):
        from angelo_agent.ui_text import iface_
        self.assertEqual(iface_("Usage limit reached · resets {time}", time="4pm"), "Usage limit reached · resets 4pm")
        self.assertEqual(tool_title({"tool": "angelo_delegate", "status": "completed", "result": {"collaborator": "Opus"}}),
                         "Consulted Opus")
        self.assertEqual(tool_title({"tool": "angelo_scene_program", "status": "running", "result": {}}), "Scene edit · working")


class NeverTakeControlSourceTests(unittest.TestCase):
    """Source guards for paths a window fixture cannot reach on every platform."""

    def test_preferences_never_open_on_the_persons_behalf(self):
        ui_source = (ADDON / "ui.py").read_text()
        pump = ui_source.split("\ndef pump():", 1)[1].split("\n@persistent", 1)[0]
        self.assertNotIn("angelo.settings", pump)
        self.assertNotIn("userpref_show", pump)
        self.assertNotIn("angelo.settings", (ADDON / "voice_ui.py").read_text())
        dispatch = (ADDON / "chat_view.py").read_text().split("\ndef dispatch_action(", 1)[1].split("\ndef dispatch_popup(", 1)[0]
        self.assertEqual(dispatch.count("bpy.ops.angelo.settings("), 1, "Only the explicit Preferences link opens it")

    def test_the_chat_card_is_the_only_approve_button(self):
        header = (ADDON / "ui.py").read_text().split("\ndef draw_header(", 1)[1].split("\ndef pump(", 1)[0]
        self.assertNotIn('"angelo.approve"', header)
        self.assertNotIn('"angelo.decline"', header)

    def test_model_visible_text_is_not_owned_by_these_modules(self):
        # Wording changed here is drawn in the chat only; tool descriptions, schemas and results are elsewhere.
        for name in ("first_run.py", "chat_welcome.py", "chat_titles.py", "quick_connect.py", "intro.py", "welcome_screen.py",
                     "splash_setup.py", "canvas_controls.py", "chat_drop.py", "team_access.py", "pump_guard.py",
                     "connect_hero.py", "chat_composer.py",
                     "voice_ui.py", "ui_text.py"):
            source = (ADDON / name).read_text()
            for owner in ("tool_specs", "plan_schema", "agent_guidance", "tool_content"):
                self.assertNotIn(owner, source, name)


class WelcomeModeTests(unittest.TestCase):
    """The welcome names the modes as the rest of Angelo does and marks the one the window is in (1.0.4: it said
    "Standard editor", and opened from Help in Manual mode it still said Director was "You're here")."""

    def test_manual_has_one_name(self):
        source = (ADDON / "welcome_screen.py").read_text()
        self.assertIn('iface_("Manual", "Angelo")', source)
        self.assertNotIn("Standard editor", source.split("modes = ", 1)[1].split("mode_blocks", 1)[0])
        self.assertIn('iface_("Try Manual mode")', source)
        self.assertIn("with Blender's menus at the top", source, "Director keeps the menus: say so")
        self.assertIn("Hide the chat and Angelo waits in the 3D view's", source, "corner Angelo, the way back")

    def test_marks_the_mode_the_window_is_in(self):
        source = (ADDON / "welcome_screen.py").read_text()
        plan = source.split("\ndef _plan(", 1)[1].split("\ndef ", 1)[0]
        self.assertEqual(plan.count("if key == here:"), 2, "the outline and the badge")
        self.assertIn('if key == "standard" != here:', plan, "no Try Manual mode for a person already in it")
        draw = (ADDON / "intro.py").read_text().split("\ndef _draw(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('here = "director" if is_director(bpy.context.window) else "standard"', draw)
        self.assertIn('_open.get("hover"), here,', draw)


if __name__ == "__main__":
    unittest.main()
