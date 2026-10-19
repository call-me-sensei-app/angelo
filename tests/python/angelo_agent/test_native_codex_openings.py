# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's opening readied when its connection is ready, and the bounded wait for it (codex_bases, codex_connect)."""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import codex_bases, codex_connect, codex_opening, codex_runtime, identifiers, providers  # noqa: E402
from angelo_agent.codex_runtime import Runtime  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from angelo_agent.telemetry import TelemetryStore, clean_receipt  # noqa: E402
from test_native_codex_runtime import (FakeProcess, PoolCase, Provider, Recorder, app_server,  # noqa: E402
                                       prediction_fork, threads_counter, total, wait_for, warm_up_reply)


def methods(process):
    return process.methods()


class OpeningCase(PoolCase):
    """CodexProvider and Controller against a fake app-server, as ``test_native_codex_runtime.ProviderTests``."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settle_before_cleanup(self.temp.name)
        self.processes = []
        self.resumed = threading.Event()
        self.resumed.set()
        self.answers = {"initialize": {"userAgent": "angelo_blender/0.162.0 (Mac OS; arm64)"},
                        "account/read": {"account": {"type": "chatgpt"}}, "config/read": {"config": {}},
                        "model/list": {"data": []}, "thread/start": threads_counter("chat"),
                        "thread/fork": prediction_fork, "turn/start": {"turn": {"id": "warm-turn"}},
                        "thread/resume": self.resume}
        processes = self.processes

        def process(command, *, env=None, cwd=None, spawn=None, on_event=None, on_request=None):
            processes.append(FakeProcess(on_event, on_request, self.answers))
            return processes[-1]
        for value in (patch.object(providers, "find_executable", return_value="/fixture/codex"),
                      patch.object(providers, "codex_command", return_value=(["/fixture/codex", "app-server"], {})),
                      patch.object(providers, "JsonRpcProcess", process)):
            value.start()
            self.addCleanup(value.stop)

    def resume(self, params):
        self.assertTrue(self.resumed.wait(5))
        return {"thread": {"id": params["threadId"]}}

    def controller(self):
        controller = Controller(self.temp.name)
        self.addCleanup(controller.close)
        controller.set_document(("doc", 1))
        controller.state.update(provider="codex_subscription", native_id=None, toolset_version=TOOLSET_VERSION)
        controller.settings = {"connection_id": "connection", "model": "gpt-6.1-sol"}
        return controller

    def chat(self, controller=None):
        """A new chat's provider, its request context as ``Controller._run`` makes it (with its identifier table)."""
        controller = controller or self.controller()
        controller._identifiers = identifiers.IdentifierTable()  # Controller._context_blocks, for a new context.
        controller._begin_metrics()
        context = controller.runtime_context()
        provider = providers.CodexProvider(context, context.settings)
        self.addCleanup(provider.close)
        return provider

    def prepare(self):
        """The connection is ready: ``codex_opening.prepare``; ``(process, runtime)`` once it was handed over."""
        thread = codex_opening.prepare(self.controller())
        thread.join(30)  # A loaded machine starts processes slowly.
        self.assertFalse(thread.is_alive())
        runtime = next(iter(codex_runtime._POOL.values()))
        return self.processes[-1], runtime

    def base(self, runtime):
        self.assertEqual(len(runtime.bases), 1)
        return next(iter(runtime.bases.values()))

    def complete_warm_up(self, process, runtime, tokens=24199):
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: record["thread"] in runtime.warming))
        thread = record["thread"]
        process.on_event("thread/tokenUsage/updated", {"threadId": thread, "tokenUsage": {
            "total": total(tokens), "last": total(tokens)}})
        warm_up_reply(process, thread)
        process.on_event("turn/completed", {"threadId": thread, "turn": {"id": "warm-turn", "status": "completed"}})
        self.assertTrue(wait_for(lambda: record["state"] == "ready"))
        return thread

    def remembered(self):
        """A base an earlier launch warmed and remembered, then the app restarts: its thread ID."""
        process, runtime = self.prepare()
        base = self.complete_warm_up(process, runtime)
        self.assertTrue(wait_for(lambda: codex_bases.BaseStore(self.temp.name).get(runtime.scope, next(
            iter(runtime.bases))) is not None))
        store = TelemetryStore.shared(self.temp.name)
        self.assertTrue(wait_for(lambda: any(r.get("kind") == "warm_up" for r in store.receipts)))
        codex_runtime.close_all()
        return base

    def bounds(self, then=None, elapsed=None):
        """``wait_bound``'s answers while the chats connect, recorded; ``then()`` runs after each.

        ``elapsed``: the base has been readied that long when the chat weighs it (a test clock).
        """
        answers, original = [], codex_bases.wait_bound

        def recorded(record, now):
            if elapsed is not None:
                record["since"] = now - elapsed
            answers.append(original(record, now))
            if then is not None:
                then()
            return answers[-1]
        stub = patch.object(codex_bases, "wait_bound", recorded)
        stub.start()
        self.addCleanup(stub.stop)
        return answers


class EagerResumeTests(OpeningCase):
    def test_the_first_chat_after_a_relaunch_forks_the_remembered_base(self):
        base = self.remembered()
        process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "ready"))
        self.assertEqual(methods(process), ["initialize", "account/read", "config/read", "model/list",
                                            "thread/fork", "thread/resume"],
                         "The opening's checks, the capability check and the resume: no model request")
        self.assertEqual(self.base(runtime)["attempts"], 0, "A resume is not a warm-up")
        chat = self.chat()
        self.assertEqual(chat.connect(), "fork-1")
        self.assertEqual(len(self.processes), 2, "The chat leased the process its connection started")
        self.assertEqual(process.calls[-1], ("thread/fork", dict(codex_bases.FORK, threadId=base)))
        self.assertEqual(chat._usage.total["input_tokens"], 24199, "The remembered total is its baseline")
        self.assertIsNone(getattr(chat, "_warm_after_turn", None))
        codex_connect.after_turn(chat, "completed")
        time.sleep(.05)
        self.assertNotIn("turn/start", methods(process), "Nothing is warmed when a base exists")
        self.assertEqual(methods(process).count("thread/resume"), 1)

    def test_a_second_opening_on_a_ready_base_sends_nothing_more(self):
        self.remembered()
        process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "ready"))
        sent = methods(process)
        self.prepare()
        self.assertEqual(methods(process)[len(sent):], ["account/read", "config/read", "model/list"],
                         "Only the connection checks; no resume, warm-up or second process")

    def test_a_resume_expected_within_the_saving_is_waited_for(self):
        base = self.remembered()
        self.resumed.clear()
        process, runtime = self.prepare()
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: "thread/resume" in methods(process)))
        self.assertEqual(record["phase"], "resume")
        bounds = self.bounds(then=lambda: threading.Timer(.1, self.resumed.set).start(),
                             elapsed=codex_bases.RESUME_SECONDS)  # It has run its expected time.
        self.assertEqual(self.chat().connect(), "fork-1")
        self.assertEqual(bounds, [codex_bases.PREFILL_SAVING_SECONDS])
        self.assertEqual(process.calls[-1], ("thread/fork", dict(codex_bases.FORK, threadId=base)))

    def test_a_resume_not_ready_within_the_bound_leaves_the_chat_its_own_thread(self):
        self.remembered()
        self.resumed.clear()
        process, runtime = self.prepare()
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: "thread/resume" in methods(process)))
        bounds = self.bounds(elapsed=codex_bases.RESUME_SECONDS)
        began = time.monotonic()
        thread = self.chat().connect()
        self.assertTrue(thread.startswith("chat-"), thread)
        self.assertEqual(bounds, [codex_bases.PREFILL_SAVING_SECONDS], "It waited its bound, and no longer")
        self.assertGreaterEqual(time.monotonic() - began, codex_bases.PREFILL_SAVING_SECONDS - .05)
        self.resumed.set()
        self.assertTrue(wait_for(lambda: record["state"] == "ready"), "The base still serves the next chat")
        self.assertTrue(self.chat().connect().startswith("fork-"))

    def test_a_resume_expected_later_than_the_saving_is_not_waited_for(self):
        self.remembered()
        self.resumed.clear()
        self.addCleanup(self.resumed.set)
        process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: "thread/resume" in methods(process)))
        bounds = self.bounds(elapsed=0)
        with patch.object(codex_bases, "RESUME_SECONDS", 30.0):
            thread = self.chat().connect()
        self.assertEqual(bounds, [0], "No wait at all")
        self.assertTrue(thread.startswith("chat-"))

    def test_an_overdue_resume_is_not_waited_for(self):
        self.remembered()
        self.resumed.clear()
        self.addCleanup(self.resumed.set)
        process, _runtime = self.prepare()
        self.assertTrue(wait_for(lambda: "thread/resume" in methods(process)))
        bounds = self.bounds(elapsed=codex_bases.RESUME_SECONDS + 2 * codex_bases.PREFILL_SAVING_SECONDS)
        self.assertTrue(self.chat().connect().startswith("chat-"))
        self.assertEqual(bounds, [0], "In the slow tail: its remaining time is unknown")


class EagerWarmUpTests(OpeningCase):
    def test_the_warm_up_starts_at_connect_and_the_first_chat_forks_it(self):
        process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: "turn/start" in methods(process)))
        self.assertEqual(methods(process), ["initialize", "account/read", "config/read", "model/list",
                                            "thread/fork", "thread/start", "turn/start"],
                         "Nothing remembered: the warm-up is the opening's only model request")
        base = self.complete_warm_up(process, runtime)
        self.assertEqual(self.base(runtime)["attempts"], 1)
        chat = self.chat()
        self.assertEqual(chat.connect(), "fork-1", "The first chat's message came after the warm-up")
        self.assertEqual(process.calls[-1], ("thread/fork", dict(codex_bases.FORK, threadId=base)))
        codex_connect.after_turn(chat, "completed")
        time.sleep(.05)
        self.assertEqual(methods(process).count("turn/start"), 1, "One warm-up for the variant, as before")

    def test_a_message_during_the_warm_up_starts_its_own_thread_and_warms_nothing_more(self):
        process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: "turn/start" in methods(process)))
        bounds = self.bounds()
        chat = self.chat()
        self.assertEqual(chat.connect(), "chat-2")
        self.assertEqual(bounds, [0], "A warm-up far from done is not waited for")
        codex_connect.after_turn(chat, "completed")
        time.sleep(.05)
        self.complete_warm_up(process, runtime)
        self.assertEqual(methods(process).count("turn/start"), 1, "The chat's turn warms no second base")
        self.assertTrue(self.chat().connect().startswith("fork-"), "The next chat forks it")

    def test_a_warm_up_whose_reply_arrived_is_waited_for(self):
        process, runtime = self.prepare()
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: record["thread"] in runtime.warming))
        thread = record["thread"]
        warm_up_reply(process, thread)  # Its turn is about to complete.

        def complete():
            process.on_event("turn/completed", {"threadId": thread, "turn": {"id": "warm-turn",
                                                                            "status": "completed"}})
        threading.Timer(.1, complete).start()
        self.assertTrue(self.chat().connect().startswith("fork-"))

    def test_a_misbehaving_warm_up_is_no_base_even_for_a_waiting_chat(self):
        process, runtime = self.prepare()
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: record["thread"] in runtime.warming))
        warm_up_reply(process, record["thread"], '{"reply":"Checked first.","operations":[]}')

        def complete():
            process.on_event("turn/completed", {"threadId": record["thread"], "turn": {"id": "warm-turn",
                                                                                      "status": "completed"}})
        threading.Timer(.1, complete).start()
        self.assertTrue(self.chat().connect().startswith("chat-"))
        self.assertEqual(record["state"], "failed")


class WarmUpAccountingTests(OpeningCase):
    def test_a_warm_up_leaves_a_receipt_with_its_usage(self):
        process, runtime = self.prepare()
        self.complete_warm_up(process, runtime, tokens=24199)
        store = TelemetryStore.shared(self.temp.name)
        self.assertTrue(wait_for(lambda: any(r.get("kind") == "warm_up" for r in store.receipts)))
        receipt = next(r for r in store.receipts if r.get("kind") == "warm_up")
        self.assertEqual(receipt["status"], "completed")
        self.assertNotIn("session_id", receipt, "No chat made it")
        self.assertEqual(receipt["usage"]["input_tokens"], 24199)
        self.assertEqual(receipt["usage"]["cached_input_tokens"], 0)
        self.assertEqual((receipt["usage"]["turns"], receipt["usage"]["requests"]), (1, 1))
        self.assertEqual(receipt["route"], {"mode": "codex_subscription", "requested_model": "gpt-6.1-sol",
                                            "effort": "low", "fast": False})
        self.assertEqual(store.report()["retained_requests"], 0, "A chat's statistics are unchanged")
        self.assertTrue(store.flush())
        self.assertIn('"kind": "warm_up"', (Path(self.temp.name) / "performance.json").read_text())

    def test_a_failed_warm_up_is_counted_too_and_unknown_usage_is_never_zero(self):
        process, runtime = self.prepare()
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: record["thread"] in runtime.warming))
        process.on_event("turn/completed", {"threadId": record["thread"], "turn": {"id": "warm-turn",
                                                                                  "status": "failed"}})
        store = TelemetryStore.shared(self.temp.name)
        self.assertTrue(wait_for(lambda: any(r.get("kind") == "warm_up" for r in store.receipts)))
        receipt = next(r for r in store.receipts if r.get("kind") == "warm_up")
        self.assertEqual(receipt["status"], "failed")
        self.assertNotIn("usage", receipt)

    def test_a_resume_leaves_no_receipt(self):
        self.remembered()
        store = TelemetryStore.shared(self.temp.name)
        count = len(store.receipts)
        _process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "ready"))
        self.assertEqual(len(store.receipts), count)

    def test_the_receipt_kind_is_allowlisted(self):
        receipt = {"status": "completed", "runtime": "codex", "phases": {"total_ms": 1}}
        self.assertEqual(clean_receipt(dict(receipt, kind="warm_up"))["kind"], "warm_up")
        self.assertNotIn("kind", clean_receipt(dict(receipt, kind="anything")))


class WaitBoundTests(unittest.TestCase):
    def test_the_bound_is_the_saving_and_only_when_the_base_is_expected_within_it(self):
        saving, expected = codex_bases.PREFILL_SAVING_SECONDS, codex_bases.RESUME_SECONDS
        resume = {"state": "warming", "phase": "resume", "since": 100.0}
        self.assertEqual(codex_bases.wait_bound(resume, 100.01 + expected - saving), saving)
        self.assertEqual(codex_bases.wait_bound(resume, 100.0 + expected), saving)
        self.assertEqual(codex_bases.wait_bound(resume, 100.0 + expected - saving - .1), 0, "Too early")
        self.assertEqual(codex_bases.wait_bound(resume, 100.0 + expected + saving + .1), 0, "In the slow tail")
        warm = {"state": "warming", "phase": "warm-up", "since": 100.0}
        self.assertEqual(codex_bases.wait_bound(warm, 101.0), 0)
        self.assertEqual(codex_bases.wait_bound(warm, 160.0), 0, "Still waiting for the model: seconds")
        self.assertEqual(codex_bases.wait_bound(dict(warm, messages=1), 101.0), saving)
        self.assertEqual(codex_bases.wait_bound(dict(warm, usage={}), 101.0), saving)
        self.assertEqual(codex_bases.wait_bound({"state": "warming", "phase": None, "since": 1.0}, 99.0), 0)
        self.assertEqual(codex_bases.wait_bound(dict(resume, state="failed"), 1e9), 0)


class RetiredTests(PoolCase):
    def test_a_retired_or_dead_process_is_never_warmed(self):
        spawn, processes = app_server({"thread/fork": prediction_fork})
        runtime = Runtime(("key",), "connection", spawn)
        runtime.retired = True
        self.assertFalse(runtime.warm("v", {}, {}))
        self.assertEqual(processes[0].methods(), [])

    def test_a_retirement_before_the_warm_up_turn_sends_no_model_request(self):
        spawn, processes = app_server({"thread/fork": prediction_fork})
        runtime = Runtime(("key",), "connection", spawn)
        retire = lambda params: setattr(runtime, "retired", True) or {"thread": {"id": "base"}}  # noqa: E731
        processes[0].answers["thread/start"] = retire
        self.assertTrue(runtime.warm("v", {}, {}))
        self.assertTrue(wait_for(lambda: runtime.bases["v"]["state"] == "failed"))
        self.assertNotIn("turn/start", processes[0].methods())


class ControllerHookTests(OpeningCase):
    def test_a_loaded_catalog_readies_the_opening_and_a_failed_one_does_not(self):
        controller = self.controller()
        for loaded in (True, False):
            with self.subTest(loaded=loaded):
                prepared = threading.Event()
                answer = (lambda self: []) if loaded else providers.CodexProvider.list_models
                with patch.object(providers.CodexProvider, "list_models", answer), \
                        patch.object(codex_opening, "prepare", lambda controller, epoch=None: prepared.set()):
                    if not loaded:
                        self.answers["account/read"] = {"account": {"type": "apiKey"}}
                    controller.models_loading = False
                    controller.refresh_models()
                    self.assertEqual(prepared.wait(1 if loaded else .5), loaded)

    def test_an_opening_on_another_runtime_kind_or_own_process_does_nothing(self):
        controller = self.controller()
        for mode in ("claude_subscription", "codex_api", "team_codex"):
            controller.state["provider"] = mode
            self.assertIsNone(codex_opening.prepare(controller), mode)
        self.assertEqual(self.processes, [], "No process, no catalog request")
        own = Provider(FakeProcess(Recorder().event, Recorder().request))
        self.assertFalse(codex_connect.prepare_opening(own, {}, lambda: []))


if __name__ == "__main__":
    unittest.main()
