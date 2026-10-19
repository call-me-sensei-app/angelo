# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.sessions import SessionCollection, SessionStore
from angelo_agent.authentication import metadata_status, native_auth_command


class SessionsAndAuthenticationTests(unittest.TestCase):
    def test_separate_conversations_preserve_history_and_native_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            collection = SessionCollection(directory)
            first = collection.active
            state = SessionStore(directory, first).load()
            state["native_id"] = "native-thread-a"
            state["messages"] = [{"role": "user", "text": "Make a cube"}]
            SessionStore(directory, first).save(state)
            collection.update(state)
            second = collection.create("claude_subscription")
            other = SessionStore(directory, second).load()
            other["native_id"] = "native-session-b"
            other["messages"] = [{"role": "user", "text": "Make a rig"}]
            SessionStore(directory, second).save(other)
            collection.update(other)
            collection.activate(first)
            restored = SessionCollection(directory)
            self.assertEqual(restored.active, first)
            self.assertEqual(len(restored.records), 2)
            self.assertEqual(restored.records[first]["title"], "Make a cube")
            self.assertEqual(SessionStore(directory, first).load()["native_id"], "native-thread-a")
            self.assertEqual(SessionStore(directory, second).load()["native_id"], "native-session-b")
            if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
                self.assertEqual(os.stat(restored.path).st_mode & 0o777, 0o600)

    def test_old_single_session_migrates_without_losing_native_history(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = SessionStore(directory)
            state = legacy.new()
            state["native_id"] = "existing-thread"
            state["messages"] = [{"role": "assistant", "text": "Existing response"}]
            legacy.save(state)
            collection = SessionCollection(directory)
            self.assertEqual(collection.active, state["id"])
            self.assertEqual(SessionStore(directory, collection.active).load()["messages"], state["messages"])

    def test_session_ids_cannot_escape_the_private_store(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                SessionStore(directory, "../../outside")
            collection = SessionCollection(directory)
            with self.assertRaises(ValueError):
                collection.activate("missing")

    def test_subscriptions_launch_native_login_without_api_billing(self):
        with tempfile.TemporaryDirectory() as directory, patch("angelo_agent.authentication.find_executable", side_effect=lambda name, configured: "/native/" + name):
            command, env = native_auth_command("codex_subscription", {"reuse_codex_login": False}, directory, login=True)
            self.assertEqual(command, ["/native/codex", "login"])
            self.assertEqual(Path(env["CODEX_HOME"]).parts[-2:], ("codex-profile", "codex_subscription"))
            self.assertNotIn("OPENAI_API_KEY", env)
            command, env = native_auth_command("claude_subscription", {}, directory, login=True)
            self.assertEqual(command, ["/native/claude", "auth", "login", "--claudeai"])
            self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_status_never_exposes_tokens_urls_or_emails(self):
        sensitive = "Logged in using ChatGPT email@example.test https://auth.example.test/?token=private"
        self.assertEqual(metadata_status("codex_subscription", 0, sensitive), "Connected to ChatGPT")
        output = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "private@example.test", "accessToken": "private"})
        self.assertEqual(metadata_status("claude_subscription", 0, output), "Connected to Claude")
        self.assertEqual(metadata_status("codex_subscription", 0, "Logged in using an API key"), "Different native billing mode; sign in with ChatGPT")


if __name__ == "__main__":
    unittest.main()
