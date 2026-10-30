# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An empty chat that looks ready never has a dead click (1.0.2), without Blender.

Evidence: a 1.0.0/1.0.1 user's first launch showed the connected welcome ("What would you like to make?", the
example prompts, "Choose model") and nothing they clicked did anything; a restart fixed it. Two causes, each fixed as
a class and tested here per state:

* Input. The chat passed every event on while any modal operator other than its own ran in the window, and
  Angelo's own passive overlays (the welcome screen, notice cards, the AI Providers page) counted as one: with the
  first-launch welcome or a What's New card open over the 3D view, the drawn chat did nothing. Now only a real tool
  holds the chat's input (``input_policy.canvas_blocking``), and a click it holds gets a notice naming it
  (``chat_start_states.blocked_card``), even when the tool was running before the chat's handler started.
* Model. A chat never bound to a connection made elsewhere had no model list ("Choose model") and said nothing. It
  stays unbound (a connection added elsewhere binds no chat: ai_providers.rst), and a connection with exactly one
  model has it chosen; every no-model state shows a card with its one action: loading
  (none needed), failed (Retry, the provider's text under Details), empty (Retry), or no models of its own while
  connections offer some (Choose a model, which adopts the connection). Its own models and no default: Default.

``test_blender_chat_start_states`` proves the same in the running app, with screenshots.
"""

import importlib
import sys
import types
import uuid
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # test_native_welcome_card
import angelo_agent  # noqa: E402
from angelo_agent import chat_start_states as states, input_policy  # noqa: E402
from angelo_agent.chat_settings import selected_model  # noqa: E402


def operator(identity, name=""):
    return SimpleNamespace(bl_idname=identity, name=name)


def window(*operators):
    return SimpleNamespace(modal_operators=list(operators))


def model(identity, default=False):
    return {"id": identity, "name": identity.title(), "default": default, "efforts": [], "fast_tier": None,
            "images": True, "default_effort": "low"}


class InputTests(unittest.TestCase):
    """Which modal operators hold a canvas's input."""

    def test_angelo_overlays_leave_the_chat_usable(self):
        for identity in sorted(input_policy.PASSIVE_OVERLAYS):
            with self.subTest(identity=identity):
                shown = window(operator(identity, "Overlay"), operator("angelo.chat_input"))
                self.assertEqual(input_policy.canvas_blocking(shown), [], "the chat beside it takes its clicks")
                self.assertTrue(input_policy.interaction_active(shown),
                                "renders, captures and layout changes still wait for it")

    def test_a_real_tool_holds_the_chat_and_is_named(self):
        held = window(operator("angelo.intro", "Welcome"), operator("TRANSFORM_OT_translate", "Move"),
                      operator("SOME_OT_addon_modal", ""))
        self.assertEqual(input_policy.canvas_blocking(held), ["Move", "some.addon_modal"],
                         "named as the person sees it, else by its identity; the overlay is not named")

    def test_the_canvases_own_handlers_never_block(self):
        self.assertEqual(input_policy.canvas_blocking(window(*(operator(i) for i in input_policy.CANVAS_INPUTS))), [])


class ClassificationTests(unittest.TestCase):
    """Every Angelo modal operator is classified once, so a new overlay can never silently freeze the chat."""

    def test_every_angelo_modal_operator_is_classified_exactly_once(self):
        import ast
        found = {}
        for path in sorted(ADDON.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ClassDef) and any(isinstance(b, ast.FunctionDef) and b.name == "modal"
                                                         for b in node.body):
                    for item in node.body:
                        if isinstance(item, ast.Assign) and any(getattr(t, "id", "") == "bl_idname" for t in item.targets):
                            found[item.value.value] = path.name
        self.assertTrue(found)
        sets = (input_policy.CANVAS_INPUTS, input_policy.PASSIVE_OVERLAYS, input_policy.HOLDING_TOOLS)
        for identity, module in sorted(found.items()):
            with self.subTest(operator=identity, module=module):
                self.assertEqual(sum(identity in group for group in sets), 1,
                                 "add it to input_policy.CANVAS_INPUTS, PASSIVE_OVERLAYS or HOLDING_TOOLS")
        self.assertTrue(set().union(*sets) <= set(found), "no stale names")

    def test_an_approval_still_holds_the_chat(self):
        self.assertEqual(input_policy.canvas_blocking(window(operator("angelo.accept_native_preview", "Accept"))),
                         ["Accept"], "Return and Esc belong to the approval")

    def test_a_handler_that_ended_without_its_cancel_is_seen(self):
        self.assertTrue(input_policy.handler_running(window(operator("angelo.chat_input")), "angelo.chat_input"))
        self.assertFalse(input_policy.handler_running(window(operator("angelo.intro")), "angelo.chat_input"))
        self.assertFalse(input_policy.handler_running(SimpleNamespace(), "angelo.chat_input"))

    def test_offscreen_tools_never_wait_for_an_overlay(self):
        shown = window(operator("angelo.intro"), operator("angelo.card_host"))
        self.assertFalse(input_policy.tool_running(shown), "a welcome left open never makes a turn's tools refuse")
        self.assertTrue(input_policy.tool_running(window(operator("TRANSFORM_OT_translate", "Move"))))
        for name in ("view_capture.py", "view_render.py", "view_motion.py", "sculpt.py", "native_programs.py",
                     "marble_host.py"):
            with self.subTest(module=name):
                source = (ADDON / name).read_text()
                self.assertIn("tool_running(window)", source)
                self.assertNotIn("interaction_active", source)
        self.assertIn("interaction_active(shown)", (ADDON / "view_editor.py").read_text(),
                      "Blender's window screenshot would hold the overlay's pixels: it still waits")


class BlockedCardTests(unittest.TestCase):
    def test_names_the_tool_and_how_to_end_it_without_a_dead_button(self):
        card = states.blocked_card(["Move"])
        self.assertEqual(card["kind"], "warn")
        self.assertIn("“Move”", card["body"])
        self.assertIn("Esc", card["body"])
        self.assertIn("Add-ons", card["body"], "an add-on that keeps running does not end on Esc")
        self.assertEqual(card["actions"], (), "the chat's clicks are what the tool holds: a button there would be dead")

    def test_shown_for_a_while_after_the_click(self):
        self.assertEqual(states.blocked_shown((("Move",), 100.0), 100.5), ("Move",))
        self.assertEqual(states.blocked_shown((("Move",), 100.0), 100.0 + states.BLOCKED_SECONDS), ("Move",))
        self.assertIsNone(states.blocked_shown((("Move",), 100.0), 100.1 + states.BLOCKED_SECONDS))
        self.assertIsNone(states.blocked_shown(None, 5.0))


class ModelStateTests(unittest.TestCase):
    """Every way a connected chat can be without a model, and its card."""

    def test_only_when_the_model_button_would_say_choose_model(self):
        self.assertTrue(states.needs_model(None, "", False))
        self.assertFalse(states.needs_model(model("opus"), "", False))
        self.assertFalse(states.needs_model(None, "claude-opus-5-5", False), "a typed model ID is a choice")
        self.assertFalse(states.needs_model(None, "", True), "a team connection names itself")

    def test_each_state_has_its_one_action(self):
        cases = [  # ((own, choices, loading, error), (state, kind, action))
            (((), (), True, ""), (states.LOADING, "info", None)),
            (((), (), False, "Claude: initialize timed out"), (states.FAILED, "error", states.RETRY_MODELS)),
            (((), (), False, ""), (states.EMPTY, "warn", states.RETRY_MODELS)),
            (((), (model("a"), model("b")), False, ""), (states.CHOOSE, "info", states.CHOOSE_MODEL)),
            (((), (model("a"),), True, "elsewhere failed"), (states.CHOOSE, "info", states.CHOOSE_MODEL)),
        ]
        self.assertEqual(states.model_problem([], [model("a")], True, "", own_loading=True), states.LOADING,
                         "its own models still loading: it waits, never asks")
        for (own, choices, loading, error), (expected, kind, action) in cases:
            with self.subTest(choices=len(choices), loading=loading, error=error):
                problem = states.model_problem(list(own), list(choices), loading, error)
                self.assertEqual(problem, expected)
                card = states.model_card(problem, error)
                self.assertEqual(card["kind"], kind)
                self.assertEqual([a[0] for a in card["actions"]], [action] if action else [])
                self.assertTrue(card["title"] and card["body"])
                for _, value, label, style in card["actions"]:
                    self.assertEqual((value, style), (None, "primary"))
                    self.assertTrue(label)

    def test_models_of_its_own_need_nothing_and_say_default(self):
        """Several models and none flagged default: the connection's default runs, as the model list's checked
        Default row says (chat_popovers.model_rows), so the button says Default and no card asks for a choice."""
        own = [model("a"), model("b")]
        self.assertIsNone(states.model_problem(own, own, False, ""))
        self.assertIsNone(states.model_problem(own, own, True, "another connection failed"))
        self.assertEqual(states.default_label(own), "Default")
        self.assertEqual(states.default_label([]), "Choose model")
        from angelo_agent.chat_popovers import model_rows
        self.assertTrue(model_rows(own, "")[0]["selected"], "the list's Default row is the one checked")

    def test_a_failure_keeps_the_providers_text_under_details(self):
        card = states.model_card(states.FAILED, "Claude: initialize timed out", expanded=True)
        self.assertEqual((card["details"], card["details_action"], card["expanded"]),
                         ("Claude: initialize timed out", states.DETAILS, True))
        self.assertNotIn("initialize", card["body"], "the sentence is plain language")
        self.assertIsNone(states.model_card(states.EMPTY)["details"])


class NewChatModelTests(unittest.TestCase):
    """A New Chat on a connection that has models never asks for one (release-1.0.6 bug sweep: with one connected
    account and Default checked, a new chat showed "Choose a model to start"). Root cause: ``ui.create_session``
    bound the new chat's connection but not its models, so its own list (``selection_models``) stayed empty while
    the catalogue offered that same connection's models as choices. The actual ``ui.create_session`` (read as
    source) and ``chat_start_states.bind_models`` run here over a real Controller."""

    A, B = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002"

    def setUp(self):
        import ast
        import tempfile
        from angelo_agent.controller import Controller
        self.folder = tempfile.TemporaryDirectory()
        self.Controller = Controller
        self.refreshed = []

        def refresh(controller):   # The real one starts a provider process; here it only starts loading.
            self.refreshed.append(controller.state["id"])
            controller.models_loading = True
        self._refresh = mock.patch.object(Controller, "refresh_models", refresh)
        self._refresh.start()
        self.records = {self.A: {"id": self.A, "mode": "codex_subscription", "name": "ChatGPT"},
                        self.B: {"id": self.B, "mode": "claude_subscription", "name": "Claude"}}
        self.ready = [self.A]
        self.catalog = SimpleNamespace(entries={}, loading=set(), errors={})
        self.current = Controller(self.folder.name, session_id=str(uuid.uuid4()))
        self.chats = []
        source = (ADDON / "ui.py").read_text()
        wanted = {"create_session"}
        body = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        self.assertEqual({node.name for node in body}, wanted)
        self.ui = {"__name__": "angelo_agent.ui", "__package__": "angelo_agent", "chat_start_states": states,
                   "get_controller": lambda: self.current, "find_connection": self.records.get,
                   "connection_ready": lambda record: record["id"] in self.ready,
                   "ready_connections": lambda: [self.records[i] for i in self.ready],
                   "preferences": lambda: None, "_memory_keys": {}, "catalogs": lambda: self.catalog,
                   "connection_settings": lambda mode, prefs=None, record=None: {"connection_id": record["id"]},
                   "_sessions": SimpleNamespace(create=lambda mode: str(uuid.uuid4())),
                   "show_session": self.show}
        editor_types = types.ModuleType("angelo_agent.editor_types")
        editor_types.require_chat_draft_access = lambda: None
        self._modules = mock.patch.dict(sys.modules, {"angelo_agent.editor_types": editor_types})
        self._modules.start()
        exec(compile(ast.Module(body=body, type_ignores=[]), "ui.py", "exec"), self.ui)

    def tearDown(self):
        self._modules.stop()
        self._refresh.stop()
        for chat in [self.current] + self.chats:
            chat.close()
        self.folder.cleanup()

    def show(self, identity):
        """``show_session``: the new chat's Controller, as ``get_controller`` first builds it (no connection yet)."""
        chat = self.Controller(self.folder.name, session_id=identity)
        self.chats.append(chat)
        return chat

    def problem(self, chat):
        choices = [m for connection in self.ready for m in self.catalog.entries.get(connection, [])]
        loading = bool(self.catalog.loading) or chat.models_loading
        return states.model_problem(chat.selection_models, choices or chat.selection_models, loading, "",
                                    own_loading=states.own_models_loading(chat, self.catalog.loading))

    def test_one_connected_account_with_its_models_known_needs_no_choice(self):
        self.catalog.entries[self.A] = [model("gpt-a"), model("gpt-b")]
        chat = self.ui["create_session"]()
        self.assertEqual(chat.state["connection_id"], self.A)
        self.assertEqual([m["id"] for m in chat.selection_models], ["gpt-a", "gpt-b"])
        self.assertIsNone(self.problem(chat), "no card: its connection's Default runs")
        self.assertEqual(states.default_label(chat.selection_models), "Default")
        self.assertEqual(self.refreshed, [], "known models: no extra model process")

    def test_models_not_known_yet_load_and_the_chat_waits_for_them(self):
        self.ready = [self.A, self.B]
        self.catalog.entries[self.B] = [model("opus")]   # Another account's models are choices meanwhile.
        chat = self.ui["create_session"](connection_id=self.A)
        self.assertEqual(self.refreshed, [chat.state["id"]])
        self.assertEqual(self.problem(chat), states.LOADING, "never Choose while its own models arrive")
        chat.models_loading, chat.models = False, [model("gpt-a")]   # They arrive.
        self.assertIsNone(self.problem(chat))

    def test_a_new_chat_inherits_the_current_chats_connection_and_its_models(self):
        self.ready = [self.A, self.B]
        self.catalog.entries.update({self.A: [model("gpt-a")], self.B: [model("opus"), model("sonnet")]})
        self.current.state["connection_id"] = self.B
        chat = self.ui["create_session"]()
        self.assertEqual((chat.state["connection_id"], [m["id"] for m in chat.selection_models]),
                         (self.B, ["opus", "sonnet"]))
        self.assertIsNone(self.problem(chat))

    def test_a_connection_that_offers_no_models_still_asks_when_others_do(self):
        self.ready = [self.A, self.B]
        self.catalog.entries[self.B] = [model("opus")]
        chat = self.ui["create_session"](connection_id=self.A)
        chat.models_loading = False   # Its own list loaded empty.
        self.assertEqual(self.problem(chat), states.CHOOSE, "a choice genuinely needed: the card asks")

    def test_no_ready_connection_binds_nothing(self):
        self.ready = []
        chat = self.ui["create_session"]()
        self.assertEqual((chat.state["connection_id"], chat.selection_models, self.refreshed), (None, [], []))


class PreselectTests(unittest.TestCase):
    def chat(self, models, chosen=""):
        return SimpleNamespace(state={"options": {"model": chosen}}, settings={}, models=models,
                               selection_models=models, selection_settings={})

    def test_the_default_then_a_single_model(self):
        self.assertEqual(selected_model(self.chat([model("a"), model("b", True)]))["id"], "b")
        self.assertEqual(selected_model(self.chat([model("only")]))["id"], "only",
                         "a connection with exactly one model has it chosen")
        self.assertIsNone(selected_model(self.chat([model("a"), model("b")])), "several, no default: the person picks")
        self.assertIsNone(selected_model(self.chat([])))
        self.assertIsNone(selected_model(self.chat([model("only")], chosen="gone")), "an unknown choice is never guessed")


class NoBindingTests(unittest.TestCase):
    """A chat is never bound behind the person's back: a connection added elsewhere (AI Providers, a confirmed
    suggestion) binds no chat; an unbound chat adopts the store's active connection of its mode when it sends or
    opens the model list (doc/angelo/ai_providers.rst; test_blender_native_suggestions pins it in the app)."""

    def test_the_pump_binds_no_chat(self):
        pump = (ADDON / "quick_connect.py").read_text().split("\ndef pump(ui):", 1)[1]
        self.assertNotIn("choose_model_connection", pump.split("for mode in list(_flows):", 1)[0])
        self.assertNotIn("bind_fresh_chat", (ADDON / "quick_connect.py").read_text())
        self.assertNotIn("fresh_chat_connection", (ADDON / "native_suggestions.py").read_text())


class WiringTests(unittest.TestCase):
    """Where the pieces meet (Blender-only code, read as source)."""

    def read(self, name):
        return (ADDON / name).read_text()

    def test_the_chat_names_a_held_click_even_when_the_tool_came_first(self):
        source = self.read("chat_view.py")
        modal = source.split("class ANGELO_OT_chat_input", 1)[1].split("    def modal(self, context, event):", 1)[1]
        guard = modal.index("blocking = canvas_blocking(window)")
        self.assertLess(guard, modal.index("note_blocked_click(window, event, blocking)"))
        self.assertLess(modal.index("note_blocked_click("), modal.index("return {'PASS_THROUGH'}", guard))
        self.assertIn("clear_blocked_notice(window)", modal)
        ensure = source.split("\ndef ensure_input(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("interaction_active", ensure, "the handler starts beside a running tool, passing its events")
        self.assertNotIn("canvas_blocking", ensure)
        self.assertIn("_draw_blocked(state, width,", source)

    def test_the_chat_input_survives_a_failed_event_and_restarts_if_it_ended(self):
        source = self.read("chat_view.py")
        self.assertIn("    @_kept_alive\n    def modal(self, context, event):", source)
        kept = source.split("\ndef _kept_alive(", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("return modal(self, context, event)", kept)
        self.assertIn("except Exception:", kept)
        self.assertIn("return {'PASS_THROUGH'}", kept)
        ensure = source.split("\ndef ensure_input(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('handler_running(window, "angelo.chat_input")', ensure)
        studio = self.read("studio_input.py").split("\ndef ensure_input(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('not handler_running(window, "angelo.studio_input")', studio)

    def test_keys_over_the_welcome_or_a_card_are_theirs(self):
        source = self.read("chat_view.py")
        modal = source.split("    def modal(self, context, event):", 1)[1]
        self.assertLess(modal.index("over_overlay(window, event)"), modal.index("area = next("))
        helper = source.split("\ndef over_overlay(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('intro._open.get("area") == pointer', helper)
        self.assertIn("card_host.takes_keys(pointer)", helper)   # A modal card or one with a field (P1-3 H2).

    def test_the_setup_cards_import_waits_for_a_running_turn(self):
        after = self.read("settings_import_ui.py").split("\ndef after_setup(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(after.index("if apply.agent_busy():"), after.index("_run_import("))

    def test_a_single_claude_model_reaches_the_runtime(self):
        session = self.read("claude_transport.py").split("\ndef claude_session(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('model = chosen["id"] if chosen and not chosen.get("runtime_default_alias") else ""', session)
        # The chosen model is the launch value ``claude_arguments`` turns into ``--model``.
        self.assertLess(session.index("selected_model(context, options)"), session.index('"model": model or ""'))

    def test_a_failed_draw_closes_an_invisible_picker(self):
        """The chat draw's own failure (the handler that records it in ``last_error``), however it records it."""
        import ast
        source = self.read("chat_view.py")
        draw = next(node for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.FunctionDef) and node.name == "_draw_chat")
        handlers = [handler for node in ast.walk(draw) if isinstance(node, ast.Try) for handler in node.handlers
                    if any(isinstance(target, ast.Name) and target.id == "last_error"
                           for statement in handler.body for target in getattr(statement, "targets", []))]
        self.assertEqual(len(handlers), 1, "one handler records the draw's failure")
        self.assertIn('state.pop("popover", None)', ast.get_source_segment(source, handlers[0]))

    def test_the_model_pill_and_card_share_one_guarded_path(self):
        view, welcome = self.read("chat_view.py"), self.read("chat_welcome.py")
        self.assertIn("if chat_welcome.open_models(ui, controller):", view)
        opener = welcome.split("\ndef open_models(", 1)[1]
        self.assertIn("except (RuntimeError, ValueError, OSError) as error:", opener)
        self.assertIn('controller.emit("message", {"role": "error"', opener, "a failure is a notice, never nothing")
        dispatch = welcome.split("\ndef dispatch(", 1)[1]
        self.assertIn('action == "welcome_choose_model"', dispatch)
        self.assertIn('action == "welcome_retry_models"', dispatch)
        self.assertIn("bpy.ops.angelo.refresh_models()", dispatch)

    def test_the_welcome_draws_the_card_above_the_examples(self):
        draw = self.read("chat_welcome.py").split("\ndef draw_empty(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("card = model_card(ui, ui.get_controller(), state) if connected else None", draw)
        self.assertIn("card_height", draw.split("total = (", 1)[1].split("\n    y = ", 1)[0], "it counts toward the layout")
        self.assertLess(draw.index("_notice(view, card, left, y, column, state, True)"), draw.index('kind == "example"'))

    def test_studio_shares_the_rule(self):
        studio = self.read("studio_input.py")
        self.assertIn("if canvas_blocking(window):", studio.split("def modal(self, context, event):", 1)[1])
        self.assertIn("if canvas_blocking(window):", studio.split("\ndef ensure_input(", 1)[1].split("\ndef ", 1)[0])


class WelcomePlacementTests(unittest.TestCase):
    """The card sits between the subtitle and the example prompts, and its button is a hit."""

    def test_card_then_examples_and_hits(self):
        welcome_tests = importlib.import_module("test_native_welcome_card")
        case = welcome_tests.WelcomeCardRowTests
        case.setUpClass()
        try:
            card = states.model_card(states.CHOOSE)
            fake = types.ModuleType("angelo_agent.ui_kit_status")

            def notice(view, x, top, width, scale, ui=None, draw=True, **params):
                hits = [((x + 40, top - 70, 120, 26), action, value) for action, value, _, _ in params["actions"]]
                if draw:
                    view.text(params["title"], x + 40, top - 20, 14)
                return 80, hits
            fake.notice = notice
            with mock.patch.dict(sys.modules, {"angelo_agent.ui_kit_status": fake}), \
                    mock.patch.object(angelo_agent, "ui_kit_status", fake, create=True):
                runner = case("test_examples_then_one_tip_that_links_to_the_docs")
                state, view = runner.draw(model_card=card)
        finally:
            case.tearDownClass()
        actions = [action for _, action, _ in state["hits"]]
        self.assertEqual(actions[0], states.CHOOSE_MODEL)
        self.assertEqual(state["drawn_model_card"], card["title"])
        button = state["hits"][0][0]
        first_example = next(rect for rect, action, _ in state["hits"] if action == "welcome_example")
        self.assertGreaterEqual(button[1], first_example[1] + first_example[3], "above the examples")
        self.assertTrue(all(rect[1] >= 120 for rect, _, _ in state["hits"]), "all above the composer")


if __name__ == "__main__":
    unittest.main()
