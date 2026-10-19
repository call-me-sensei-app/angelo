# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import base64
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.chat_settings import codex_turn_options, normalize_models
from angelo_agent.connections import ConnectionStore
from angelo_agent.controller import Controller
from angelo_agent.providers import CodexProvider, profile_environment
from angelo_agent.references import ReferenceStore
from angelo_agent.sessions import SessionStore
from angelo_agent.voice import NativeVoiceSession
from angelo_agent.chat_layout import composer_layout
from angelo_agent.chat_popovers import (effective_effort, effort_choices, model_name, model_rows, model_row_rect,
                                        popup_layout, reveal_model, slider_geometry, slider_motion, slider_value, sparkles, supports_fast)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
MODELS = [{"id": "test", "model": "native-model", "displayName": "Native model", "isDefault": True,
           "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}],
           "defaultReasoningEffort": "low", "inputModalities": ["text", "image"],
           "serviceTiers": [{"id": "fast", "name": "Fast"}]}]


class ChatControlsTests(unittest.TestCase):
    def test_native_claude_default_and_context_suffix_preserve_capabilities(self):
        models = normalize_models([{"value": "default"}, {"value": "opus[1m]"}, {"value": "sonnet"}], "claude")
        self.assertTrue(models[0]["default"])
        self.assertIsNone(models[0]["fast_tier"], "Don't guess the native default model family")
        self.assertEqual(models[1]["fast_tier"], "native-fast")
        self.assertIsNone(models[2]["fast_tier"])

    def test_default_model_has_exactly_one_right_side_checkmark(self):
        models = normalize_models(MODELS)
        rows = model_rows(models, "")
        self.assertEqual([r["id"] for r in rows if r["selected"]], [""])
        self.assertEqual(rows[0]["subtitle"], "Recommended set of models")
        self.assertEqual([r["id"] for r in model_rows(models, "native-model") if r["selected"]], ["native-model"])

    def test_popovers_never_escape_reserved_chat_region(self):
        for width, height in ((531, 850), (300, 400), (190, 240), (850, 120)):
            for kind in ("controls", "models", "fast_confirm", "approval"):
                layout = popup_layout(kind, width, height, (width - 200, 28, 150, 28), 20, busy=True, notice=True, claude=True)
                x, y, w, h = layout["rect"]
                self.assertGreaterEqual(x, 8)
                self.assertGreaterEqual(y, 8)
                self.assertLessEqual(x + w, width - 8)
                self.assertLessEqual(y + h, height - 8)

    def test_compact_reasoning_popup_and_model_list_are_separate(self):
        anchor = (280, 28, 170, 28)
        compact = popup_layout("controls", 531, 850, anchor)
        self.assertEqual(compact["rect"], (194, 64, 256, 100))
        self.assertEqual(compact["slider"], (208, 78, 228, 28))
        models = popup_layout("models", 531, 850, anchor, 8)
        self.assertNotIn("slider", models)
        self.assertEqual(models["content_height"], 54 + 8 * 29)

    def test_fast_controls_only_exist_for_supported_models(self):
        self.assertFalse(supports_fast(None))
        self.assertFalse(supports_fast({"fast_tier": None}))
        self.assertTrue(supports_fast({"fast_tier": "priority"}))
        self.assertTrue(supports_fast({"fast_tier": "native-fast"}))
        self.assertEqual(popup_layout("controls", 531, 850, (280, 28, 170, 28))["rect"][3], 100)

    def test_slider_blue_stops_at_selected_thumb_not_at_max(self):
        track = (14, 14, 228, 28)
        choices = ["low", "medium", "high", "xhigh", "max"]
        for effort in choices[:-1]:
            geometry = slider_geometry(track, choices, effort)
            self.assertLess(geometry["unselected_start"], track[0] + track[2])
            self.assertEqual(geometry["fill"][0] + geometry["fill"][2], geometry["thumb"][0] + geometry["thumb"][2])
        self.assertEqual(slider_geometry(track, choices, "max")["fill"], track)

    def test_slider_animation_moves_only_inside_selected_fill(self):
        track = (14, 14, 228, 28)
        fill = slider_geometry(track, ["low", "high", "max"], "high")["fill"]
        first, second = sparkles(track, fill[2], 0), sparkles(track, fill[2], 0.2)
        self.assertNotEqual(first, second)
        for x, y, alpha in first + second:
            self.assertTrue(track[0] <= x <= fill[0] + fill[2])
            self.assertTrue(track[1] <= y <= track[1] + track[3])
            self.assertTrue(0 <= alpha <= 1)
        self.assertTrue(0.5 < slider_motion(0.5, 1, 0.05) < 1)
        self.assertEqual(slider_motion(0.5, 1, 0.05, reduce_motion=True), 1)

    def test_runtime_display_names_match_reference_without_changing_ids(self):
        model = {"id": "gpt-6-astra", "name": "GPT-6-Astra"}
        self.assertEqual(model_name(model), "GPT-6 Astra")
        self.assertEqual(model["id"], "gpt-6-astra")
        self.assertEqual(model_name({"name": "GPT-5.6-Terra"}), "GPT-5.6 Terra")
        self.assertEqual(model_name({"name": "A custom model"}), "A custom model")

    def test_catalog_capability_loss_turns_fast_off_only_for_future_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.models = normalize_models(MODELS)
            controller.set_options(model="native-model", fast=True)
            controller.turn_options = dict(controller.state["options"])
            controller.busy = True
            controller.emit("models", {"models": [{**controller.models[0], "fast_tier": None}]})
            controller.pump_events()
            self.assertFalse(controller.state["options"]["fast"])
            self.assertTrue(controller.turn_options["fast"])
            self.assertIn("future requests", controller.state["messages"][-1]["text"])
            controller.set_options(fast=True)
            controller.emit("models", {"error": "Can't refresh", "models": []})
            controller.pump_events()
            self.assertTrue(controller.state["options"]["fast"], "A catalog error must not silently change billing settings")
            controller.busy = False
            controller.close()

    def test_model_list_scroll_is_bounded_and_keyboard_row_revealed(self):
        layout = popup_layout("models", 531, 400, (280, 28, 170, 28), 100)
        last_scroll = reveal_model(layout, 100)
        self.assertEqual(last_scroll, layout["max_scroll"])
        layout = popup_layout("models", 531, 400, (280, 28, 170, 28), 100, last_scroll)
        _, y, _, h = model_row_rect(layout, 100)
        _, bottom, _, visible = layout["list_rect"]
        self.assertGreaterEqual(y, bottom)
        self.assertLessEqual(y + h, bottom + visible)
        self.assertEqual(reveal_model(layout, 0), 0)
        self.assertEqual(popup_layout("models", 531, 400, (280, 28, 170, 28), 100, -500)["scroll"], 0)

    def test_reasoning_slider_only_offers_advertised_levels(self):
        model = {"efforts": ["high", "low", "invented", "low"], "default_effort": "high"}
        self.assertEqual(effort_choices(model), ["low", "high"])
        self.assertEqual(effective_effort(model, "max"), "high")
        self.assertEqual(effort_choices(None), [])
        self.assertEqual(slider_value((10, 20, 228, 28), -50, ["low", "high"]), "low")
        self.assertEqual(slider_value((10, 20, 228, 28), 900, ["low", "high"]), "high")
        self.assertEqual(slider_value((10, 20, 228, 28), 900, ["high"]), "high")
        self.assertIsNone(slider_value((10, 20, 228, 28), 900, []))

    def test_codex_reference_component_positions(self):
        layout = composer_layout(531)
        self.assertEqual(layout["rect"], (15, 20, 501, 98))
        self.assertEqual(layout["plus_x"], 36)
        self.assertEqual(layout["mic_x"], 459)
        self.assertEqual(layout["send_x"], 495)
        self.assertEqual(layout["approval_x"], 66)
        self.assertAlmostEqual(layout["bubble_max_width"], 349.3)
        self.assertEqual(composer_layout(531, 3, 1)["rect"][3], 192)

    def test_attached_text_is_data_and_cannot_execute_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            references = ReferenceStore(directory, SessionStore(directory).new()["id"])
            source = Path(directory) / "example.py"
            source.write_text("raise RuntimeError('never execute this reference')\n", encoding="utf-8")
            record = references.import_images([source])[0]
            self.assertEqual(references.codex_inputs([record]), [])
            self.assertEqual(references.prompt_context([record])[0]["text_excerpt"], source.read_text())
            self.assertEqual(references.read(record, 6, 12)["text"], source.read_text()[6:18])
            self.assertEqual(record["kind"], "text")

    def test_activity_status_and_only_public_thinking_summaries_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            provider = CodexProvider(controller, {})
            provider.thread_id = "thinking-fixture-thread"
            provider.bind_request(controller)
            provider._scope.bind_turn("thinking-fixture-turn")
            scope = {"threadId": provider.thread_id, "turnId": "thinking-fixture-turn"}
            provider._event("item/started", dict(scope, item={"type": "reasoning", "id": "reason-1"}))
            provider._event("item/reasoning/textDelta", dict(scope, itemId="reason-1", delta="hidden raw reasoning must not be shown"))
            provider._event("item/reasoning/summaryTextDelta", dict(scope, itemId="reason-1", delta="Public summary supplied by the runtime"))
            provider._event("item/completed", dict(scope, item={"type": "reasoning", "id": "reason-1"}))
            controller.emit("activity", {"id": "tool-1", "tool": "angelo_execute_python", "status": "approval", "arguments": {"code": "result = 1"}})
            controller.emit("activity", {"id": "tool-1", "status": "completed", "result": {"success": True}})
            controller.pump_events()
            self.assertEqual(controller.state["messages"][0]["status"], "completed")
            self.assertEqual(controller.state["messages"][0]["text"], "Public summary supplied by the runtime")
            self.assertEqual(controller.state["messages"][1]["status"], "completed")
            self.assertNotIn("hidden raw reasoning", controller.store.path.read_text())
            controller.close()
    def test_named_connections_only_store_metadata_and_preserve_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConnectionStore(directory)
            first = store.add("OPENAI", "SUBSCRIPTION", reuse_native=True)  # Added by the person, never discovered.
            second = store.add("ANTHROPIC", "API_KEY", "My work key")
            second["api_key"] = "never-write-this"
            store.activate(second["id"])
            store.save()
            self.assertNotIn("never-write-this", store.path.read_text())
            restored = ConnectionStore(directory)
            self.assertEqual(restored.active, second["id"])
            self.assertEqual(len(restored.records), 2)
            restored.remove(first["id"])
            # A removed reuse is neither re-added nor offered again.
            self.assertFalse(ConnectionStore(directory).suggests_native("codex_subscription"))
            self.assertEqual(len(ConnectionStore(directory).records), 1)
            if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
                self.assertEqual(os.stat(store.path).st_mode & 0o777, 0o600)

    def test_named_native_profiles_are_isolated_and_do_not_accept_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            record = ConnectionStore(directory).add("OPENAI", "API_KEY")
            env = profile_environment({}, "codex_api", directory, False, record["id"])
            self.assertTrue(env["CODEX_HOME"].endswith(record["id"]))
            other = profile_environment({}, "claude_subscription", directory, False, record["id"])
            self.assertTrue(other["CLAUDE_CONFIG_DIR"].endswith(record["id"]))
            with self.assertRaises(ValueError):
                profile_environment({}, "codex_api", directory, False, "../../outside")

    def test_fast_mode_uses_advertised_service_tier_not_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.models = normalize_models(MODELS)
            controller.set_options(model="native-model", effort="high", fast=True)
            self.assertEqual(codex_turn_options(controller), {"model": "native-model", "effort": "high", "serviceTierForTurn": "fast"})
            controller.set_options(fast=False)
            self.assertEqual(codex_turn_options(controller)["serviceTierForTurn"], "default")
            controller.models[0]["fast_tier"] = None
            controller.set_options(fast=True)
            with self.assertRaises(ValueError):
                codex_turn_options(controller)
            controller.close()

    def test_options_persist_per_chat_and_secret_fields_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            state = store.new()
            state["options"] = {"model": "native-model", "effort": "high", "fast": True, "approval": "READ_ONLY", "key": "never-write"}
            store.save(state)
            restored = store.load()
            self.assertEqual(restored["options"]["approval"], "READ_ONLY")
            self.assertNotIn("never-write", store.path.read_text())

    def test_reference_is_private_snapshot_not_an_arbitrary_file_read(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            references = ReferenceStore(directory, store.new()["id"])
            image = Path(directory) / "source.png"
            image.write_bytes(PNG)
            snapshots = references.import_images([image])
            path = Path(snapshots[0]["path"])
            self.assertEqual(path.read_bytes(), PNG)
            self.assertNotEqual(path, image)
            if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(references.codex_inputs(snapshots), [{"type": "localImage", "path": str(path)}])
            self.assertEqual(base64.b64decode(references.claude_inputs(snapshots)[0]["source"]["data"]), PNG)
            with self.assertRaises(ValueError):
                references.codex_inputs([dict(snapshots[0], path=str(image))])
            with self.assertRaises(ValueError):
                references.import_images([image] * 5)

    def test_codex_turn_includes_model_fast_effort_and_actual_image_input(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.models = normalize_models(MODELS)
            controller.set_options(model="native-model", fast=True, effort="high")
            source = Path(directory) / "reference.png"
            source.write_bytes(PNG)
            controller.turn_attachments = controller.references.import_images([source])
            provider = CodexProvider(controller, {})
            provider.rpc = Mock()
            def request(method, params):
                self.assertEqual(method, "turn/start")
                self.assertEqual(params["serviceTierForTurn"], "fast")
                self.assertEqual(params["effort"], "high")
                self.assertEqual(params["model"], "native-model")
                self.assertEqual(params["input"][1]["type"], "localImage")
                provider.turn_status = "completed"
                provider.done.set()
                return {"turn": {"id": "test-turn"}}
            provider.rpc.request.side_effect = request
            self.assertEqual(provider.run("test-thread", "Use the reference"), "completed")
            controller.close()

    def test_voice_requires_user_start_and_never_saves_audio_or_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            provider = Mock()
            provider.rpc.closed = threading.Event()
            voice = NativeVoiceSession(controller, provider, "test-thread")
            # Construction and captions must never open a microphone.
            with patch("angelo_agent.child_process.popen") as capture:
                voice.event("thread/realtime/transcript/done", {"threadId": "test-thread", "role": "user", "text": "Make a sphere"})
                voice.event("thread/realtime/transcript/done", {"threadId": "test-thread", "role": "user", "text": "Make a sphere"})
                capture.assert_not_called()
            controller.pump_events()
            self.assertEqual(len(controller.state["messages"]), 1)
            self.assertEqual(list(Path(directory).rglob("*.wav")), [])
            voice.stop()
            self.assertTrue(voice.stopped.is_set())
            provider.rpc.request.assert_not_called()
            controller.close()


if __name__ == "__main__":
    unittest.main()
