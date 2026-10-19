# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude chats adopt the current toolset; no process, credentials or inference.

The real Controller and ClaudeProvider run a request end to end. Only the Claude
process, its version probe and auth discovery are replaced by fixtures.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import NativeInitializeFixture, SUPPORTED, completed
from angelo_agent.chat_settings import normalize_models
from angelo_agent.controller import Controller
from angelo_agent.providers import ClaudeProvider
from angelo_agent.routing import TOOLSET_VERSION, route_key

CLAUDE = normalize_models([{"value": "sonnet", "displayName": "Claude Sonnet", "isDefault": True,
                            "supportedEffortLevels": ["low", "medium", "high"]}], "claude")


class OfflineClaude(ClaudeProvider):
    def connect(self):
        self.executable, self.env = "claude", {"FIXTURE": "only"}
        return self.controller.state["native_id"]


class ClaudeToolsetVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.c.choose_connection("claude_subscription", {"model": ""}, "", None, CLAUDE, "sonnet")
        self.prompts = []
        self.key = route_key(self.c.state.get("connection_id"), self.c.state["provider"])
        patches = [patch("angelo_agent.controller.ClaudeProvider", OfflineClaude),
                   patch("angelo_agent.child_process.popen",
                         side_effect=lambda *args, **kwargs: NativeInitializeFixture(on_user=self.reply)),
                   patch("angelo_agent.child_process.run",
                         return_value=SimpleNamespace(returncode=0, stdout=SUPPORTED))]
        for value in patches:
            value.start()
            self.addCleanup(value.stop)

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def reply(self, process, event):
        content = event["message"]["content"]
        self.prompts.append(content if isinstance(content, str) else content[0]["text"])
        process.stdout.emit(completed())

    def turn(self, text):
        self.c.send(text, {"objects": []})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.c.pump_events()
            if not self.c.busy and not (self.c._worker and self.c._worker.is_alive()):
                self.c.pump_events()
                return self.prompts[-1]
            time.sleep(.002)
        self.fail("Claude fixture turn did not finish")

    def test_chat_saved_under_an_older_toolset_hands_off_history_once(self):
        # A resumed chat already synced to its history, saved before this toolset.
        self.c.state["messages"] = [{"role": "user", "text": "OLD-HISTORY-MARKER request"},
                                    {"role": "assistant", "text": "Earlier answer"}]
        self.c.state["native_id"] = "claude-native-session"
        self.c.state["toolset_version"] = TOOLSET_VERSION - 1
        self.c.state["runtime_sessions"][self.key] = {"native_id": "claude-native-session",
                                                      "toolset_version": TOOLSET_VERSION - 1, "synced_length": 2}
        self.assertIn("OLD-HISTORY-MARKER", self.turn("first"))
        self.assertEqual(self.c.state["toolset_version"], TOOLSET_VERSION)
        self.assertEqual(self.c.state["runtime_sessions"][self.key]["toolset_version"], TOOLSET_VERSION)
        for text in ("second", "third"):
            self.assertNotIn("OLD-HISTORY-MARKER", self.turn(text))

    def test_current_chat_never_hands_off_already_synced_history(self):
        self.assertNotIn("Shared conversation handoff", self.turn("first"))
        self.assertNotIn('"text": "first"', self.turn("second"))
        self.assertEqual(self.c.state["toolset_version"], TOOLSET_VERSION)


if __name__ == "__main__":
    unittest.main()
