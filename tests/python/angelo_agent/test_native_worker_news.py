# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A lead's own tool results say which workers finished since it last read them (``worker_news``), a lead's lookup of
its worker tools says how many workers were launched ahead (``worker_pool.looked_up``), and the family's texts state
when a start runs and what a worker's merge shows. No Blender.

Evidence (LANE-overheads.md, the C1 and C2 capped early runs' Codex rollouts): in 6 of 6 parallel trials the lead spent
a request on a status call (2.8-8.2 s) after its own script before merging a part that had already finished; in 4 of
7 the lead wrote its own share's script in the same request as its start, so every worker started 40-49 s after the
message instead of 16-20 s.
"""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
import angelo_agent  # noqa: E402
from angelo_agent import parallel_sessions, worker_news, worker_pool, worker_sessions, worker_spec  # noqa: E402

FIELD = "workers_finished_since_status"


def show(context):
    """A status answer reaching the lead's model through a route: its final copy, packed and delivered."""
    copy = worker_news.noted(context, {"workers": []})
    worker_news.acknowledge(worker_news.packed())
    return copy


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.done = {"w1": False, "w2": False}
        patches = [mock.patch.object(worker_sessions, "_started", {"lead": ["w1", "w2"]}),
                   mock.patch.object(worker_sessions, "_parts", {"w1": (1, "Sofa", Path("/a")),
                                                                 "w2": (2, "Chair", Path("/b"))}),
                   mock.patch.object(worker_sessions, "finished", lambda s: self.done[s]),
                   mock.patch.object(parallel_sessions, "_merged", {}),
                   mock.patch.object(parallel_sessions, "_merging", set()),
                   *(mock.patch.object(worker_news, name, {}) for name in ("_told", "_turn", "_staged", "_told_cards")
                     if hasattr(worker_news, name))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(worker_news.withheld)
        self.context = SimpleNamespace(host=SimpleNamespace(state={"id": "lead"}))

    def deliver(self, result, raw=None):
        copy = worker_news.noted(self.context, result, raw)
        worker_news.acknowledge(worker_news.packed())
        return copy

    def test_a_delivered_result_after_a_worker_finished_names_it_exactly_once(self):
        result = {"success": True}
        self.assertIs(self.deliver(result), result)   # Nothing finished: no bytes.
        self.done["w2"] = True
        self.assertEqual(self.deliver(result), {"success": True, FIELD: ["2 Chair"]})
        self.assertIs(self.deliver(result), result)   # Told once.
        self.assertEqual(result, {"success": True})   # The raw result is never changed.

    def test_news_packed_but_never_delivered_stays_news(self):
        self.done["w1"] = True
        worker_news.noted(self.context, {})   # Packed ...
        worker_news.withheld()                 # ... the reply was withheld.
        self.assertIsNone(worker_news.packed())
        self.assertEqual(worker_news.fresh("lead"), ["w1"])
        self.assertEqual(self.deliver({})[FIELD], ["1 Sofa"])
        self.assertEqual(worker_news.fresh("lead"), [])

    def test_a_status_built_for_the_host_acknowledges_nothing(self):
        """Angelo's own turn reads status (worker_resume): nothing reached the model (break-it OVH-M-3)."""
        from angelo_agent import worker_facts
        self.done["w1"] = True
        with mock.patch.object(worker_facts, "_sessions", lambda lead_id: {}), \
                mock.patch.object(worker_facts, "_lead", lambda lead_id: None):
            worker_facts.annotate("lead", {"workers": []})
        self.assertEqual(worker_news.fresh("lead"), ["w1"])
        self.assertEqual(self.deliver({})[FIELD], ["1 Sofa"])

    def test_a_status_answer_is_told_by_its_field_never_by_its_rows(self):
        """Break-it OVH6-M-2: rows may be excerpted or re-sent; only the explicit field, written whole, tells."""
        self.done["w1"] = True
        copy = show(self.context)
        self.assertEqual(copy, {"workers": [], FIELD: ["1 Sofa"]})
        self.assertEqual(worker_news.fresh("lead"), [])

    def test_a_failed_attempt_consumes_nothing_and_a_later_payload_names_it_again(self):
        """Break-it OVH6-M-1: nothing is consumed while a payload is built."""
        self.done["w1"] = True
        worker_news.noted(self.context, {})
        worker_news.withheld()   # Its write failed.
        self.assertEqual(self.deliver({})[FIELD], ["1 Sofa"])
        self.assertEqual(worker_news.fresh("lead"), [])

    def test_a_resumed_worker_s_next_finish_is_news_without_any_status_between(self):
        """Break-it OVH-M-1: told once, messaged again, finished again: news again."""
        self.done["w1"] = True
        worker_news.began("w1")
        self.assertEqual(self.deliver({})[FIELD], ["1 Sofa"])
        worker_news.began("w1")   # A message relaunches it; its turn ends while the lead writes.
        self.assertEqual(worker_news.fresh("lead"), ["w1"])
        self.assertEqual(self.deliver({})[FIELD], ["1 Sofa"])

    def test_a_message_to_a_worker_chat_starts_a_new_turn(self):
        from angelo_agent import worker_facts
        chat = SimpleNamespace(state={"id": "w1"})
        worker_facts.turn_started(chat)
        worker_facts.turn_started(chat)
        self.assertEqual(worker_news._turn["w1"], 2)

    def test_each_thread_takes_only_what_it_packed(self):
        import threading
        self.done["w1"] = True
        worker_news.noted(self.context, {})
        taken = []
        other = threading.Thread(target=lambda: taken.append(worker_news.packed()))
        other.start()
        other.join()
        self.assertEqual(taken, [None])   # Another thread packed nothing.
        self.assertEqual(worker_news.fresh("lead"), ["w1"])
        self.assertIsNotNone(worker_news.packed())

    def test_a_part_whose_start_or_brief_failed_is_never_news(self):
        """Its chat stays behind once its brief failed: status says failed_to_start, the merge refuses it (OVH2-M-3)."""
        self.done["w1"] = True
        with mock.patch.dict(worker_sessions.failures, {"lead/1": "the brief could not be sent"}):
            self.assertEqual(self.deliver({}), {})

    def test_a_merged_or_merging_part_is_never_news(self):
        self.done.update(w1=True, w2=True)
        parallel_sessions._merged["w1"] = "/a"
        parallel_sessions._merging.add("w2")
        self.assertEqual(self.deliver({}), {})

    def test_a_chat_with_no_workers_and_a_non_object_result_are_unchanged(self):
        self.done["w1"] = True
        other = SimpleNamespace(state={"id": "another chat"})
        self.assertEqual(worker_news.noted(other, {"a": 1}), {"a": 1})
        self.assertEqual(worker_news.noted(self.context, "text"), "text")
        self.assertEqual(worker_news.fresh("lead"), ["w1"])

    def test_a_failure_to_read_is_no_news_never_a_failed_result(self):
        with mock.patch.object(worker_sessions, "finished", mock.Mock(side_effect=RuntimeError("gone"))):
            self.assertEqual(worker_news.noted(self.context, {"a": 1}), {"a": 1})

    def test_old_rows_with_a_delivery_token_never_show_it(self):
        from angelo_agent import model_results
        self.assertNotIn(model_results.WORKER_FINISHES, model_results.model_result(
            "angelo_workers_status", {"workers": [], model_results.WORKER_FINISHES: "7"}))


class ClaudeBoundaryTests(unittest.TestCase):
    """The Claude route's real MCP answer (``claude_direct.answer``): what a packed answer showed is told only once its
    reply was written, never sent to the runtime, and never told when the write fails (OVH2-M-1). The transport write
    is the test's ``_write`` (no Claude Code process here)."""

    def setUp(self):
        patches = [mock.patch.object(worker_news, name, {}) for name in ("_told", "_told_cards")
                   if hasattr(worker_news, name)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def answer(self, write):
        import threading
        from unittest.mock import Mock
        from angelo_agent import claude_direct
        state = claude_direct.DirectRequest(SimpleNamespace(_current=lambda owner: True),
                                            SimpleNamespace(references=Mock(), allowed_tools=None), Mock(), Mock())
        shown = ("lead", frozenset({("w1", 1)}), frozenset())
        state.call = Mock(return_value={"content": [], "isError": False, worker_news.CARRY: shown})
        owner = SimpleNamespace(controller=SimpleNamespace(allowed_tools=None), same_request=lambda other: True)
        transport = SimpleNamespace(lock=threading.RLock(), turn=SimpleNamespace(owner=owner), owner=owner)
        state.owner = owner
        provider = SimpleNamespace(_direct=state, _write=write, _current=lambda *a: True)
        event = {"type": "control_request", "request_id": "r-1", "request": {
            "subtype": "mcp_message", "server_name": claude_direct.SERVER,
            "message": {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": "angelo_scene_search", "arguments": {}}}}}
        claude_direct.answer(provider, transport, event)
        for thread in threading.enumerate():
            if thread.name == "AngeloClaudeCall":
                thread.join(5)

    def test_a_written_reply_tells_what_it_showed_and_never_carries_the_bookkeeping(self):
        written = []
        self.answer(lambda message, *a, **k: written.append(message))
        self.assertEqual(worker_news._told, {"lead": {("w1", 1)}})
        self.assertNotIn(worker_news.CARRY, json.dumps(written))

    def test_a_reply_whose_write_fails_tells_nothing(self):
        from angelo_agent.protocol import RpcError

        def refuse(*_a, **_k):
            raise RpcError("Claude runtime no longer owns this request")
        self.answer(refuse)
        self.assertEqual(worker_news._told, {})


class LookupTests(unittest.TestCase):
    def test_a_lookup_that_launched_workers_ahead_says_how_many(self):
        lead = SimpleNamespace(state={"id": "lead"})
        entries = [{"lead": "lead", "error": None}, {"lead": "lead", "error": RuntimeError()},
                   {"lead": "other", "error": None}]
        names = {"names": ["angelo_workers_start"]}
        with mock.patch.object(worker_pool, "_pool", entries), mock.patch.object(worker_pool, "eligible", lambda c: True), \
                mock.patch.object(worker_pool, "prelaunch", lambda c: []):
            self.assertEqual(worker_pool.looked_up(lead, names, {"tools": []}),
                             {"tools": [], "workers_launched_ahead": 1})
            self.assertEqual(worker_pool.looked_up(lead, {"names": ["angelo_view"]}, {"tools": []}), {"tools": []})
        with mock.patch.object(worker_pool, "_pool", []), mock.patch.object(worker_pool, "eligible", lambda c: False):
            self.assertEqual(worker_pool.looked_up(lead, names, {"tools": []}), {"tools": []})   # None launched.


class TextTests(unittest.TestCase):
    """Facts the family states (no instruction: ``test_native_worker_python_consent`` screens every family text)."""

    def test_the_start_says_when_it_runs_and_what_a_worker_does_meanwhile(self):
        text = worker_spec.start_description(1, "in this run")
        self.assertIn("A call runs once the whole request that makes it is written", text)
        self.assertIn("delays every worker's start by its writing time", text)
        self.assertIn("A worker that has started runs beside your requests", text)

    def test_status_and_merge_say_a_wait_naming_parts_lets_the_same_request_merge_them(self):
        tools = {tool["name"]: tool for tool in worker_spec.WORKER_TOOLS}
        status = tools[worker_spec.PREFIX + "status"]
        self.assertIn("workers", status["inputSchema"]["properties"])
        self.assertIn("a merge after it in the same request takes them as they finish", status["description"])
        self.assertIn("while no window shows the tiles, a card not yet named in an approval_no_window_shows that "
                      "reached you ends a wait", status["description"])   # State (OVH3-L-1); no "once" (OVH8-L-1).
        self.assertNotIn("ends a wait once", status["description"])
        self.assertNotIn("if it comes while", status["description"])
        self.assertIn("Your tool results and messages list finished, unmerged workers not yet listed in one that "
                      "reached you (%s)" % FIELD, status["description"])   # Break-it OVH7-L-1: no "once".
        self.assertNotIn("none listed before", status["description"])
        self.assertIn("a status wait naming it answers when it ends", tools[worker_spec.PREFIX + "merge"]["description"])

    def test_texts_claim_only_what_the_host_establishes(self):
        """Break-it OVH-L-1: a merge places a part in the lead's scene, not beside every other part; a worker counted
        ahead is launching or ready, not ready."""
        self.assertIn("into the lead's scene, where the lead sees it with the rest of that scene", worker_spec.WORKER_BRIEFING)
        self.assertNotIn("every other part", worker_spec.WORKER_BRIEFING)
        self.assertIn("workers_launched_ahead counts workers launching or ready for you", worker_spec.WORKER_GUIDANCE)
        self.assertNotIn("ready seconds later", worker_spec.WORKER_GUIDANCE)


if __name__ == "__main__":
    unittest.main()
