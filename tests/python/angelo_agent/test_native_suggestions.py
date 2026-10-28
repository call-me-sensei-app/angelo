# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A detected native sign-in is suggested, never set up.

Runs the actual store, rules and ``ui.py`` operator/draw/routing functions
without Blender: each ``ui.py`` definition is compiled on its own with fakes for
the few names it touches. ``test_blender_native_suggestions.py`` repeats the
flow in the compiled app with a stand-in native status.
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
UI_PATH = ROOT / "scripts/addons_core/angelo_agent/ui.py"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import display_text, document_scene, native_suggestions as suggest, provider_registry, ui_text
from angelo_agent.connections import ConnectionStore, MODES
from angelo_agent import connection_access, saved_keys_ui
from angelo_agent.provider_registry import CODING

UI_NAMES = {"iface_": ui_text.iface_, "n_": ui_text.n_, "notice": ui_text.notice, "rpt_": ui_text.rpt_,
            "Text": display_text.Text, "shown": display_text.shown,
            "document_scene": document_scene,
            "mode_key_usable": connection_access.mode_key_usable}  # What ui.py imports at module level.

SIGNED_IN = {"codex_subscription": "Connected to ChatGPT", "claude_subscription": "Connected to Claude",
             "codex_api": "Native API credential configured"}
UI_TREE = ast.parse(UI_PATH.read_text())


def ui_definition(name, namespace):
    """Compile one top-level ``ui.py`` function or class against ``namespace``."""
    node = next(n for n in UI_TREE.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
    namespace = {"__name__": "angelo_agent.ui", "__package__": "angelo_agent", "sys": sys, "Operator": object,
                 "StringProperty": lambda **_: None, "EnumProperty": lambda **_: None,
                 "BoolProperty": lambda **_: None, **UI_NAMES} | namespace
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(UI_PATH), "exec"), namespace)
    return namespace[name]


def chat(connection_id=None, provider="codex_subscription"):
    return SimpleNamespace(state={"provider": provider, "connection_id": connection_id}, models=[], models_loading=False)


class ReachedSelect(Exception):
    """Stops a fake controller at select(), after the routing decision."""


class Layout:
    def __init__(self):
        self.labels, self.operators = [], []

    def box(self):
        return self

    def row(self):
        return self

    def label(self, text="", **kwargs):
        self.labels.append(text)

    def operator(self, name, **kwargs):
        properties = SimpleNamespace()
        self.operators.append((name, properties))
        return properties


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ConnectionStore(self.directory.name)

    def reloaded(self):
        return ConnectionStore(self.directory.name)

    def offered(self, store=None, statuses=SIGNED_IN):
        return [s["mode"] for s in suggest.suggestions(store or self.store, statuses)]


class DetectionTests(StoreCase):
    def test_detection_never_adds_a_record_or_binds_a_chat(self):
        unbound, other = chat(), chat(provider="claude_subscription")
        self.assertEqual(self.offered(), ["codex_subscription", "claude_subscription"])
        for mode, status in SIGNED_IN.items():
            self.assertEqual(suggest.chats_to_refresh(self.store, [unbound, other], mode, status), (None, []))
        self.assertEqual(self.store.records, {})
        self.assertFalse(self.store.path.exists(), "Detection must not even write the account store")
        self.assertIsNone(unbound.state["connection_id"])
        self.assertIsNone(other.state["connection_id"])
        self.assertFalse(hasattr(ConnectionStore, "discover"), "The creating discovery API is gone")

    def test_suggestion_appears_only_for_a_signed_in_subscription(self):
        for status in ("", "Checking native sign-in…", "Not connected", "Native runtime unavailable",
                       "Different native billing mode; sign in with ChatGPT", "Sign-in complete. Refresh status to confirm."):
            self.assertEqual(self.offered(statuses={"codex_subscription": status}), [])
        self.assertEqual(self.offered(statuses={"codex_subscription": "Connected to ChatGPT"}), ["codex_subscription"])
        # Angelo's older shared Codex API profile is detected but never offered or added.
        self.assertEqual(self.offered(statuses={"codex_api": "Native API credential configured"}), [])
        self.assertEqual(self.store.records, {})

    def test_suggestion_disappears_once_a_connection_reuses_the_sign_in(self):
        record = self.store.add("OPENAI", "SUBSCRIPTION", reuse_native=True)
        self.assertEqual(self.offered(), ["claude_subscription"])
        isolated = self.store.add("ANTHROPIC", "SUBSCRIPTION", "Work Claude", reuse_native=False)
        self.assertEqual(self.offered(), ["claude_subscription"], "An isolated profile is a different account")
        self.store.remove(isolated["id"])
        self.store.remove(record["id"])
        self.assertEqual(self.offered(), ["claude_subscription"], "A removed reuse is not offered back")

    def test_status_change_refreshes_only_chats_already_bound_to_the_reuse(self):
        record = self.store.add("OPENAI", "SUBSCRIPTION", reuse_native=True)
        bound, unbound, loaded = chat(record["id"]), chat(), chat(record["id"])
        loaded.models = [{"id": "model"}]
        found, waiting = suggest.chats_to_refresh(self.store, [bound, unbound, loaded], "codex_subscription",
                                                  "Connected to ChatGPT")
        self.assertEqual((found["id"], waiting), (record["id"], [bound]))
        self.assertIsNone(unbound.state["connection_id"])
        self.assertEqual(suggest.chats_to_refresh(self.store, [bound], "codex_subscription", "Not connected"), (None, []))


class DismissalTests(StoreCase):
    def test_dismissal_persists_across_reload(self):
        self.store.dismiss_native("claude_subscription")
        self.assertEqual(self.offered(), ["codex_subscription"])
        self.assertEqual(self.offered(self.reloaded()), ["codex_subscription"])
        self.assertIn("claude_subscription", json.loads(self.store.path.read_text())["ignored_native_modes"])
        with self.assertRaises(ValueError):
            self.store.dismiss_native("grok_api")

    def test_removing_a_connection_never_resurrects_a_dismissed_suggestion(self):
        self.store.dismiss_native("claude_subscription")
        isolated = self.store.add("ANTHROPIC", "SUBSCRIPTION", "Isolated", reuse_native=False)
        self.store.remove(isolated["id"])
        self.assertEqual(self.offered(self.reloaded()), ["codex_subscription"])
        # Explicitly adding the reuse clears the dismissal while it exists; removing it dismisses again.
        reuse = self.store.add("ANTHROPIC", "SUBSCRIPTION", reuse_native=True)
        self.store.remove(reuse["id"])
        self.assertEqual(self.offered(self.reloaded()), ["codex_subscription"])

    def test_a_stale_instance_cannot_drop_a_dismissal(self):
        account = self.store.add("ANTHROPIC", "API_KEY", "Key")
        stale = self.reloaded()
        self.store.dismiss_native("codex_subscription")
        stale.remove(account["id"])
        self.assertEqual(self.offered(self.reloaded()), ["claude_subscription"])

    def test_dismiss_operator_accepts_only_suggested_sign_ins(self):
        redraws = []
        operator = ui_definition("ANGELO_OT_dismiss_native_suggestion", {
            "connections": lambda: self.store, "native_suggestions": suggest,
            "preferences_ui": SimpleNamespace(redraw=lambda: redraws.append(True))})
        reports = []
        rejected = SimpleNamespace(mode="codex_api", report=lambda *args: reports.append(args))
        self.assertEqual(operator.execute(rejected, None), {'CANCELLED'})
        self.assertEqual(self.store.ignored_native_modes, set())
        accepted = SimpleNamespace(mode="codex_subscription", report=lambda *args: reports.append(args))
        self.assertEqual(operator.execute(accepted, None), {'FINISHED'})
        self.assertEqual(self.offered(self.reloaded()), ["claude_subscription"])
        self.assertEqual((len(reports), redraws), (1, [True]))


class ExistingDataTests(StoreCase):
    def test_connections_created_by_the_old_discovery_are_kept_unchanged(self):
        legacy = {"id": "1b2f5a0e-8c1d-4e0f-9a33-6f3b6f1f6a10", "name": "ChatGPT", "provider": "OPENAI",
                  "method": "SUBSCRIPTION", "mode": "codex_subscription", "model": "", "reuse_native": True,
                  "group": CODING, "native_profile": None}
        self.store.path.write_text(json.dumps({"version": 3, "active": legacy["id"], "connections": [legacy],
                                               "ignored_native_modes": []}))
        store = self.reloaded()
        self.assertEqual(store.records[legacy["id"]], legacy)
        self.assertEqual(self.offered(store), ["claude_subscription"])
        store.dismiss_native("claude_subscription")
        self.assertEqual(self.reloaded().records, {legacy["id"]: legacy})
        self.assertEqual(self.reloaded().active, legacy["id"])

    def test_isolated_store_with_every_mode_ignored_offers_and_creates_nothing(self):
        selected = self.store.add("OPENAI", "SUBSCRIPTION", "Selected", reuse_native=False)
        self.store.ignored_native_modes = set(MODES.values())  # What the live eval harness writes.
        self.store.save()
        store = self.reloaded()
        unbound = chat()
        self.assertEqual(self.offered(store), [])
        for mode, status in SIGNED_IN.items():
            self.assertFalse(store.suggests_native(mode))
            self.assertEqual(suggest.chats_to_refresh(store, [unbound], mode, status), (None, []))
        self.assertEqual(list(self.reloaded().records), [selected["id"]])
        self.assertIsNone(unbound.state["connection_id"])


class PrefilledFormTests(StoreCase):
    def form_class(self, status=SIGNED_IN):
        self.logins = []
        return ui_definition("ANGELO_OT_new_connection", {
            "GROUPS": provider_registry.GROUPS, "CODING": CODING, "new_connection_group_changed": None,
            "new_connection_providers": None, "new_connection_provider_changed": None,
            "connections": lambda: self.store, "methods": provider_registry.methods, "spec_for": provider_registry.spec_for,
            "is_coding": provider_registry.is_coding, "runtime_kind": provider_registry.runtime_kind,
            "HTTP_MODES": provider_registry.HTTP_MODES, "preferences": lambda: None,
            "preferences_ui": SimpleNamespace(redraw=lambda: None),
            "_authentication": SimpleNamespace(status=dict(status), start=lambda *a, **k: self.logins.append((a, k))),
            "connection_settings": lambda *a, **k: {}, "state_directory": lambda: self.directory.name,
            # Python 3.13 evaluates the class annotations (the Remember label) while compiling.
            "saved_keys_ui": saved_keys_ui})

    def form(self, mode, **overrides):
        values = dict(connection_id="", connection_name="", api_key="", remember_key=True, model="", runtime_path="",
                      advanced=False, report=lambda *args: self.fail(args))
        return SimpleNamespace(**(values | suggest.form_properties(mode) | overrides))

    def test_form_properties_reuse_the_suggested_sign_in(self):
        self.assertEqual(suggest.form_properties("codex_subscription"),
                         {"group": CODING, "provider": "OPENAI", "method": "SUBSCRIPTION", "reuse_existing": True})
        self.assertEqual(list(suggest.form_properties("claude_subscription")), ["group", "provider", "method", "reuse_existing"])

    def test_draw_offers_the_prefilled_form_and_dismiss(self):
        draw = ui_definition("draw_native_suggestions", {"native_suggestions": suggest,
                                                         "_authentication": SimpleNamespace(status=SIGNED_IN)})
        layout = Layout()
        draw(layout, self.store)
        self.assertIn("Found your ChatGPT sign-in (Codex)", layout.labels)
        self.assertIn("Found your Claude Code sign-in", layout.labels)
        forms = [vars(p) for name, p in layout.operators if name == "angelo.new_connection"]
        self.assertEqual(forms, [suggest.form_properties("codex_subscription"), suggest.form_properties("claude_subscription")])
        dismiss = [p.mode for name, p in layout.operators if name == "angelo.dismiss_native_suggestion"]
        self.assertEqual(dismiss, ["codex_subscription", "claude_subscription"])
        self.assertEqual(self.store.records, {})

    def test_prefilled_form_creates_one_reuse_record_only_on_confirmation(self):
        form_class = self.form_class()
        manager = SimpleNamespace(invoke_props_dialog=lambda *a, **k: {'RUNNING_MODAL'})
        operator = self.form("codex_subscription")
        self.assertEqual(form_class.invoke(operator, SimpleNamespace(window_manager=manager), None), {'RUNNING_MODAL'})
        form_class.cancel(operator, None)
        self.assertEqual(self.store.records, {}, "Opening or cancelling the form adds nothing")
        self.assertEqual(form_class.execute(operator, None), {'FINISHED'})
        (record,) = self.reloaded().records.values()
        self.assertEqual((record["mode"], record["reuse_native"], record["name"]), ("codex_subscription", True, "ChatGPT"))
        self.assertEqual(self.logins, [], "Reusing a sign-in opens no browser login")
        self.assertEqual(self.offered(self.reloaded()), ["claude_subscription"])

    def test_confirming_after_the_sign_in_disappears_uses_the_ordinary_new_sign_in(self):
        form_class = self.form_class(status={"claude_subscription": "Not connected"})
        self.assertEqual(form_class.execute(self.form("claude_subscription"), None), {'FINISHED'})
        (record,) = self.store.records.values()
        self.assertFalse(record["reuse_native"])
        self.assertEqual(len(self.logins), 1)


class UnboundChatTests(StoreCase):
    """A chat without a connection never runs on a detected default sign-in."""

    def controller(self, **state):
        selected = []

        def select(*args):
            selected.append(args)
            raise ReachedSelect

        controller = SimpleNamespace(
            selection_provider="codex_subscription", selection_connection_id=None, connection_error=False,
            provider=None, state={"connection_id": None, "runtime_sessions": {}} | state, setup_required=False,
            set_document=lambda key: None, apply_next_connection=lambda: None, remember_runtime=lambda: None,
            select=select)
        return controller, selected

    def namespace(self, controller, keys=None, ready=()):
        return {"get_controller": lambda: controller, "connection_record": lambda c=None: None,
                "connections": lambda: self.store, "native_suggestions": suggest, "_memory_keys": keys or {},
                "connection_ready": lambda record: True, "preferences": lambda: SimpleNamespace(provider="codex_subscription"),
                "document_key": lambda: ("document",), "main_window": lambda: None,
                "ready_connections": lambda: list(ready),
                "connection_settings": lambda *args: {}, "catalogs": lambda: SimpleNamespace(entries={})}

    def run_prepare(self, controller, **namespace):
        prepare = ui_definition("prepare_controller", self.namespace(controller, **namespace))
        fake = ModuleType("angelo_agent.memory_project_ui")
        fake.bind_current = lambda *args: None
        modules = {"angelo_agent.memory_project_ui": fake, "angelo_agent.ui": ModuleType("angelo_agent.ui")}
        with patch.dict(sys.modules, modules):
            prepare()

    def test_detected_sign_in_does_not_make_an_unbound_chat_available(self):
        controller, _ = self.controller()
        available = ui_definition("connection_available", self.namespace(controller))
        self.assertFalse(available())
        self.assertFalse(ui_definition("connection_available", self.namespace(controller, keys={"codex_subscription": "x"}))())
        api, _ = self.controller()
        api.selection_provider = "codex_api"
        self.assertTrue(ui_definition("connection_available", self.namespace(api, keys={"codex_api": "typed"}))())

    def test_a_modes_own_key_is_refused_while_the_policy_refuses_personal_connections(self):
        from angelo_agent import managed_policy
        self.addCleanup(managed_policy.reset)
        api, selected = self.controller()
        api.selection_provider = "codex_api"
        managed_policy.reset(managed_policy.Policy(allow_personal_connections=False))
        self.assertFalse(ui_definition("connection_available", self.namespace(api, keys={"codex_api": "typed"}))())
        with self.assertRaisesRegex(RuntimeError, "no connection"):   # Nor does a send run on it.
            self.run_prepare(api, keys={"codex_api": "typed"})
        self.assertEqual(selected, [])

    def test_unbound_chat_refuses_to_send_without_a_connection(self):
        controller, selected = self.controller()
        with self.assertRaisesRegex(RuntimeError, "Connect Claude or ChatGPT in the chat"):
            self.run_prepare(controller)
        self.assertEqual(selected, [])
        self.assertTrue(controller.setup_required)
        ready, _ = self.controller()
        with self.assertRaisesRegex(RuntimeError, "choose a model"):
            self.run_prepare(ready, ready=[{"id": "other"}])
        self.assertFalse(ready.setup_required, "A ready connection is picked in chat, not in Preferences")
        self.assertEqual(self.store.records, {})

    def test_an_explicitly_added_active_connection_is_still_adopted(self):
        record = self.store.add("OPENAI", "SUBSCRIPTION", reuse_native=True)
        controller, selected = self.controller()
        self.assertTrue(ui_definition("connection_available", self.namespace(controller))())
        with self.assertRaises(ReachedSelect):
            self.run_prepare(controller)
        self.assertEqual(selected[0][0], "codex_subscription")
        self.assertEqual(selected[0][3], record["id"])
        other, _ = self.controller(provider="claude_subscription")
        other.selection_provider = "claude_subscription"
        self.assertFalse(ui_definition("connection_available", self.namespace(other))(),
                         "Only a connection of the chat's own mode is adopted")

    def test_availability_and_pump_no_longer_consult_native_status_or_bind(self):
        functions = {n.name: n for n in UI_TREE.body if isinstance(n, ast.FunctionDef)}
        names = {node.id for node in ast.walk(functions["connection_available"]) if isinstance(node, ast.Name)}
        self.assertNotIn("_authentication", names)
        pump = functions["pump"]
        calls = {node.func.attr for node in ast.walk(pump) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertNotIn("discover", calls)
        self.assertIn("chats_to_refresh", calls)
        for node in ast.walk(pump):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self.assertFalse(isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                                     and target.slice.value == "connection_id", "The pump must never bind a chat")


if __name__ == "__main__":
    unittest.main()
