# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A long Codex chat on a forked thread is rebased in time, so it never stops at the context window.

codex-cli 0.163 never compacts a prediction fork (openai/codex#53678): at the window its turns fail
with ``contextWindowExceeded`` and go on failing. A new chat on stock Codex is such a fork of a warm
base (``codex_bases``), so the host rebases it between turns (``context_rebase``): at ``FORK_SHARE``
of the window Codex reports, or after a turn the window refused, into a plain thread that Codex
compacts itself.

``ForkedCodex`` plays a 0.163 app-server behind the real Controller: unbilled, no process, account or
inference. The provider-level classes drive the real CodexProvider with ``ScriptedRPC``'s 0.163
notifications.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from angelo_agent import context_rebase, codex_retry  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from test_native_cache_diet import DOC, LEGACY_OBSERVATION  # noqa: E402
from test_native_codex_turn_errors import THREAD, ProviderFixture, answered, failed  # noqa: E402
from test_native_routing import FakeProvider  # noqa: E402

WINDOW = 100_000  # modelContextWindow: Codex's usable window
OPENING = 24_000  # A fork starts from its base's opening
CONTEXT_FULL = {"message": "Codex ran out of room in the model's context window. Start a new thread or clear earlier "
                           "history before retrying.", "codexErrorInfo": "contextWindowExceeded",
                "additionalDetails": None, "misalignment": None}


class ForkedCodex(FakeProvider):
    """codex-cli 0.163 as a chat's provider.

    A new chat forks the warm base unless it opens a rebased chat's context (``context_rebase.pending``). Every
    turn adds ``growth`` tokens (or the next of ``jumps``). A fork that would pass the window fails
    with ``contextWindowExceeded`` and keeps its history; a plain thread compacts near 95%, as Codex does.
    """

    window, growth, jumps = WINDOW, 12_000, []
    threads, opened, outcomes = {}, [], []

    def connect(self):
        native, forked = self.controller.state.get("native_id"), False
        if not native:
            forked = not context_rebase.pending(self.controller)
            native = "%s-%d" % ("fork" if forked else "plain", len(self.threads) + 1)
            self.threads[native] = {"forked": forked, "size": OPENING}
            self.opened.append("thread/fork" if forked else "thread/start")
        self.native_id = native
        # As open_thread: only the connect that forked says so (a still-loaded fork is found, not forked).
        self.controller.emit("native_session", {"id": native, "forked": forked})
        return native

    def run(self, native_id, text, observation=None):
        self.native_id, self.prompt = native_id, text
        thread = self.threads[native_id]
        self.controller.record_provider_turn()
        self.controller.record_provider_accepted()
        size = thread["size"] + (self.jumps.pop(0) if self.jumps else self.growth)
        if thread["forked"] and size > self.window:
            # 0.163: no compaction for a prediction fork; the turn fails and the history stays as it was.
            self.outcomes.append((native_id, "contextWindowExceeded"))
            self.controller.record_context_fact("context_full", True)
            raise RpcError(CONTEXT_FULL["message"])
        if not thread["forked"] and size > self.window * .95:
            self.controller.record_context_compaction()  # A plain thread: Codex summarizes it and goes on.
            size = 30_000
            self.outcomes.append((native_id, "compacted"))
        else:
            self.outcomes.append((native_id, "completed"))
        thread["size"] = size
        self.controller.record_context_tokens(size)
        self.controller.record_context_fact("context_window", self.window)
        self.controller.emit("message_complete", {"item": "reply", "text": "Done."})
        return "completed"


class ForkRebaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        ForkedCodex.instances, ForkedCodex.threads, ForkedCodex.opened, ForkedCodex.outcomes = [], {}, [], []
        ForkedCodex.growth, ForkedCodex.jumps = 12_000, []
        self.c = Controller(self.directory.name)
        self.addCleanup(self.c.close)
        self.c.set_document(DOC)
        self.c.choose_connection("codex_subscription", {"model": ""}, "", None, [], "")
        stub = patch("angelo_agent.controller.CodexProvider", ForkedCodex)
        stub.start()
        self.addCleanup(stub.stop)

    def turn(self, text):
        self.c.send(text, LEGACY_OBSERVATION)
        deadline = time.monotonic() + 5
        while (self.c.busy or (self.c._worker and self.c._worker.is_alive())) and time.monotonic() < deadline:
            self.c.pump_events()
            time.sleep(.002)
        self.c.pump_events()
        return ForkedCodex.instances[-1].prompt

    def entry(self):
        return self.c.state["runtime_sessions"][self.c.context_key()]

    def rebases(self):
        return [m["context_rebase"] for m in self.c.state["messages"] if m.get("context_rebase")]

    def errors(self):
        return [m["text"] for m in self.c.state["messages"] if m["role"] == "error"]

    def test_a_long_forked_chat_rebases_below_its_window_and_keeps_working(self):
        prompts = [self.turn("Step %d: add a brick to the wall." % number) for number in range(1, 15)]
        self.assertEqual(self.errors(), [], "No turn ever reached the window")
        self.assertEqual(self.rebases(), [{"previous_tokens": 84_000}], "Once, at 80% of the window")
        self.assertEqual(ForkedCodex.opened, ["thread/fork", "thread/start"], "The fresh context is a plain thread")
        rebased = next(index for index, prompt in enumerate(prompts) if context_rebase.HEADER in prompt)
        self.assertEqual(rebased, 5, "The sixth message, after the fifth turn reached 84,000 of 100,000")
        self.assertIn("Step 5: add a brick", prompts[rebased].split(context_rebase.HEADER, 1)[1])
        self.assertIn(("plain-2", "compacted"), ForkedCodex.outcomes, "Codex compacts the plain thread itself")
        self.assertEqual([outcome for _, outcome in ForkedCodex.outcomes].count("contextWindowExceeded"), 0)
        self.assertNotIn("forked", self.entry())
        self.assertEqual(self.entry()["context_window"], WINDOW)
        self.assertEqual(self.c.status, "Completed")

    def test_without_the_rebase_the_same_chat_stops_at_its_window(self):
        """The failure this fixes: 0.163 refuses every later turn of a fork that reached its window."""
        with mock.patch.object(context_rebase, "due", return_value=False):
            for number in range(1, 10):
                self.turn("Step %d: add a brick to the wall." % number)
        self.assertEqual(len(self.errors()), 3, "Steps 7, 8 and 9 each failed")
        self.assertTrue(all("ran out of room" in text for text in self.errors()))

    def test_a_turn_the_window_refuses_rebases_the_next_message(self):
        ForkedCodex.jumps = [12_000, 12_000, 70_000]  # The third turn alone would pass the window.
        for number in range(1, 4):
            self.turn("Step %d." % number)
        self.assertEqual(len(self.errors()), 1)
        self.assertTrue(self.entry()["context_full"])
        seeded = self.turn("Step 3 again, smaller.")
        self.assertIn(context_rebase.HEADER, seeded)
        self.assertEqual(self.rebases(), [{"previous_tokens": 48_000}])
        self.assertEqual(ForkedCodex.opened, ["thread/fork", "thread/start"])
        for number in range(4, 8):
            self.turn("Step %d." % number)
        self.assertEqual(len(self.errors()), 1, "Working again, with no stop at the window")
        self.assertNotIn("context_full", self.entry())

    def test_a_plain_thread_is_left_to_codex(self):
        """A keyed thread on Angelo's runtime (or any thread/start) compacts itself: the host never rebases it."""
        with patch.object(ForkedCodex, "connect", autospec=True, side_effect=lambda provider: self._plain(provider)):
            for number in range(1, 13):
                self.turn("Step %d." % number)
        self.assertEqual(self.rebases(), [])
        self.assertEqual(self.errors(), [])
        self.assertIn(("plain-1", "compacted"), ForkedCodex.outcomes)

    def _plain(self, provider):
        native = provider.controller.state.get("native_id")
        if not native:
            native = "plain-%d" % (len(ForkedCodex.threads) + 1)
            ForkedCodex.threads[native] = {"forked": False, "size": OPENING}
            ForkedCodex.opened.append("thread/start")
        provider.native_id = native
        provider.controller.emit("native_session", {"id": native})
        return native

    def test_the_fork_mark_survives_a_reconnect_to_the_same_thread(self):
        self.turn("First.")
        self.assertTrue(self.entry()["forked"])
        self.c.provider = None  # A new provider finds the same, still loaded thread: it reports no fork.
        self.turn("Second.")
        self.assertTrue(self.entry()["forked"])
        self.assertEqual(ForkedCodex.opened, ["thread/fork"])


class DecisionTests(unittest.TestCase):
    def test_only_a_forked_codex_thread_is_rebased_by_its_window(self):
        due = context_rebase.due
        self.assertTrue(due(0, 80_000, native_id="t", runtime="codex", forked=True, context_window=WINDOW))
        self.assertFalse(due(0, 79_999, native_id="t", runtime="codex", forked=True, context_window=WINDOW))
        self.assertFalse(due(0, 99_000, native_id="t", runtime="codex", forked=False, context_window=WINDOW))
        self.assertFalse(due(0, 99_000, native_id="t", runtime="codex", forked=True, context_window=None),
                         "No window reported: Codex would not compact either")
        self.assertFalse(due(0, 99_000, native_id=None, runtime="codex", forked=True, context_window=WINDOW))
        self.assertTrue(due(0, None, native_id="t", runtime="codex", forked=True, context_window=WINDOW, context_full=True))
        self.assertFalse(due(0, None, native_id="t", runtime="codex", forked=False, context_window=WINDOW, context_full=True))
        self.assertFalse(due(1000, 99_000, native_id="t", runtime="codex"), "The Claude setting is Claude's")
        self.assertFalse(due(0, 10 ** 6, native_id="t", runtime="claude", forked=True, context_window=WINDOW))
        self.assertEqual(context_rebase.fork_limit(1_000_000), 800_000)
        for window in (None, 0, -5, "100000", 1.5):
            self.assertIsNone(context_rebase.fork_limit(window))


class ProviderSignalTests(ProviderFixture):
    """The real CodexProvider passes on what the rebase needs: the window and a refused turn."""

    def usage(self, turn, tokens, window=WINDOW):
        last = {"totalTokens": tokens, "inputTokens": tokens, "cachedInputTokens": 0, "outputTokens": 0,
                "reasoningOutputTokens": 0}
        return ("thread/tokenUsage/updated", {"threadId": THREAD, "turnId": turn, "tokenUsage": {
            "total": last, "last": last, "modelContextWindow": window}})

    def run_through_controller(self, *outcomes):
        self.c.provider = self.p
        self.c.remember_runtime()
        self.script(*outcomes)

        def connect(context):
            self.p.bind_request(context)
            return THREAD

        with mock.patch.object(self.c, "_connect_runtime", side_effect=connect):
            self.c._run("Add a brick.")
        self.c.pump_events()

    def test_the_reported_window_reaches_the_chat(self):
        self.run_through_controller(lambda turn: [self.usage(turn, 61_000)] + answered(turn))
        entry = self.c.state["runtime_sessions"][self.c.context_key()]
        self.assertEqual((entry["context_tokens"], entry["context_window"]), (61_000, WINDOW))
        self.assertNotIn("context_full", entry)

    def test_a_turn_the_window_refused_is_marked_and_never_resent(self):
        rpc = self.script(lambda turn: failed(turn, CONTEXT_FULL), answered)
        self.c.provider = self.p
        self.c.remember_runtime()
        with mock.patch.object(self.c, "_connect_runtime", side_effect=lambda context: (self.p.bind_request(context), THREAD)[1]):
            self.c._run("Add a brick.")
        self.c.pump_events()
        self.assertEqual(len(rpc.turn_starts()), 1, "contextWindowExceeded is never resent")
        self.assertTrue(self.c.state["runtime_sessions"][self.c.context_key()]["context_full"])
        self.assertEqual([m["text"] for m in self.c.state["messages"] if m["role"] == "error"], [CONTEXT_FULL["message"]])
        self.assertIsNone(codex_retry.delay(CONTEXT_FULL))


if __name__ == "__main__":
    unittest.main()
