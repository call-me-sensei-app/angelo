# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker-hosted chat (design A′) is never colder, emptier or less visible than a chat of Angelo's own, no Blender.

Pinned here:

* **prewarm**: the window's prewarm treats a worker chat's mirror as any chat (draft, idle bound, launch key and
  ``MAX_PREWARMED`` across chats) but starts nothing in Angelo: the worker's own chat starts it
  (``chat.prewarm``), the person's Send hands it to the worker's request, and a release goes to the worker. A worker
  starts one as its chat opens, only through Angelo's launch service, and releases an unadopted one after the idle
  bound. A ChatGPT plan chat's shared app-server is started by Angelo's runtime service as it opens, with the very
  launch request the worker's first launch makes (so that launch adopts it);
* **models**: a new worker chat carries the catalog a chat of Angelo's loaded for its connection, starts no listing in
  Angelo, and a worker whose open carried none lists its own through Angelo, which reaches the mirror as ``models``;
* **live usage**: a worker's request in progress reaches the mirror's request metrics, so the meter and the live
  token count read a worker turn as they read a lead turn, and a finished turn leaves them.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_worker_chat_host as host_base  # noqa: E402
import test_native_worker_mirror as mirror_base  # noqa: E402
from angelo_agent import (chat_usage, runtime_prewarm, runtime_relay, usage_meter, worker_chat_events,  # noqa: E402
                          worker_chat_lifecycle, worker_chat_wiring, worker_mirror_prewarm)
from angelo_agent.chat_settings import normalize_models  # noqa: E402

MODELS = normalize_models([{"model": "gpt-a", "isDefault": True}, {"model": "gpt-b"}])


def until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class NoLocalProvider:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("A worker chat's runtime started in Angelo")


class MirrorPrewarmTests(mirror_base.Fixture):
    def setUp(self):
        super().setUp()
        self.mirror.settings = {"model": ""}
        self.mirror.state["provider"], self.mirror.state["connection_id"] = "claude_subscription", "c1"
        for patcher in (mock.patch("angelo_agent.providers.ClaudeProvider", NoLocalProvider),
                        mock.patch("angelo_agent.providers.CodexProvider", NoLocalProvider),
                        mock.patch.object(runtime_prewarm, "key", lambda controller: ("k",))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def prewarms(self):
        until(lambda: False, 0.05)   # Ops go off the main thread.
        return [fields["action"] for _session, op, fields in self.ops.calls if op == "chat.prewarm"]

    def test_writing_in_a_worker_chat_prewarms_in_its_worker_never_here(self):
        runtime_prewarm.observe(self.mirror, "", [self.mirror], 0.0)
        runtime_prewarm.observe(self.mirror, "Make the leg", [self.mirror], 1.0)   # Written: a draft that changed.
        self.assertTrue(getattr(self.mirror.prewarmed.provider, "remote", False))
        self.assertIsNone(self.mirror.provider)   # A mirror holds no provider: nothing was launched here.
        runtime_prewarm.observe(self.mirror, "Make the legs", [self.mirror], 2.0)
        self.assertIsNotNone(self.mirror.prewarmed)   # Kept: its runtime is the worker's, not ``provider``.
        self.assertEqual(self.prewarms(), ["start"])
        runtime_prewarm.observe(self.mirror, "", [self.mirror], 3.0)   # The draft went: released in the worker.
        self.assertIsNone(self.mirror.prewarmed)
        self.assertTrue(until(lambda: self.prewarms() == ["start", "release"]))

    def test_the_idle_bound_and_the_one_slot_hold_across_chats_and_send_hands_it_over(self):
        other = mirror_base.MirrorController(self.directory, self.session, self.ops)
        runtime_prewarm.observe(self.mirror, "", [self.mirror], 0.0)
        runtime_prewarm.observe(self.mirror, "a", [self.mirror], 1.0)
        runtime_prewarm.observe(self.mirror, "a", [self.mirror], 1.0 + runtime_prewarm.IDLE_SECONDS)
        self.assertIsNone(self.mirror.prewarmed)   # Idle: released.
        runtime_prewarm.observe(self.mirror, "ab", [self.mirror, other], 200.0)
        other.settings = dict(self.mirror.settings)
        other.state.update(provider="claude_subscription", connection_id="c1")
        runtime_prewarm.observe(other, "", [self.mirror, other], 201.0)
        runtime_prewarm.observe(other, "x", [self.mirror, other], 202.0)   # MAX_PREWARMED: the other one goes.
        self.assertEqual((self.mirror.prewarmed, other.prewarmed is not None), (None, True))
        self.assertTrue(until(lambda: self.prewarms().count("release") == 2))
        self.ops.calls.clear()
        other.send("Make it")
        self.assertIsNone(other.prewarmed)   # The worker's request adopts it: nothing released from here.
        self.assertNotIn("chat.prewarm", [op for _s, op, _f in self.ops.calls])

    def test_a_draft_still_written_keeps_the_workers_prewarm_and_a_closed_chat_starts_none(self):
        runtime_prewarm.observe(self.mirror, "", [self.mirror], 0.0)
        runtime_prewarm.observe(self.mirror, "a", [self.mirror], 1.0)
        runtime_prewarm.observe(self.mirror, "ab", [self.mirror], 1.0 + worker_mirror_prewarm.TOUCH_SECONDS)
        self.assertTrue(until(lambda: self.prewarms() == ["start", "start"]))
        runtime_prewarm.release(self.mirror)
        self.mirror.send_op = None   # Not open in a worker (before its open, or handed back).
        runtime_prewarm.observe(self.mirror, "abc", [self.mirror], 500.0)
        self.assertIsNone(self.mirror.prewarmed)


class WorkerPrewarmTests(host_base.Fixture):
    def setUp(self):
        super().setUp()
        self.started = []
        for patcher in (mock.patch.object(runtime_relay, "relayed", lambda: True),
                        mock.patch.object(runtime_prewarm, "key", lambda chat: ("k",)),
                        mock.patch.object(runtime_prewarm, "start", self.start)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, chat, launch_key, now):
        self.started.append(launch_key)
        chat.prewarmed = runtime_prewarm.Prewarm(SimpleNamespace(close=lambda: None),
                                                 SimpleNamespace(release=lambda: None), launch_key, now)
        return chat.prewarmed

    def test_an_open_starts_its_runtime_through_angelo_and_the_lead_keeps_or_releases_it(self):
        self.open(models=MODELS)
        chat = self.chats.hosted(self.session)
        self.assertEqual(self.started, [("k",)])
        touched = chat.prewarmed.touched
        self.assertEqual(self.ask("chat.prewarm", action="start"), {"ok": True, "warm": True})
        self.assertEqual(self.started, [("k",)])   # One runtime: the next start only keeps it.
        self.assertGreaterEqual(chat.prewarmed.touched, touched)
        with mock.patch.object(runtime_prewarm, "release", lambda chat: setattr(chat, "prewarmed", None)):
            self.assertEqual(self.ask("chat.prewarm", action="release"), {"ok": True, "warm": False})
        self.assertIsNone(chat.prewarmed)

    def test_without_angelos_launch_service_no_prewarm_starts_in_a_worker(self):
        with mock.patch.object(runtime_relay, "relayed", lambda: False):
            self.open(models=MODELS)
            self.assertEqual(self.ask("chat.prewarm", action="start"), {"ok": True, "warm": False})
        self.assertEqual(self.started, [])

    def test_an_unadopted_prewarm_ends_after_the_idle_bound(self):
        self.open(models=MODELS)
        chat = self.chats.hosted(self.session)
        chat.prewarmed.touched -= runtime_prewarm.IDLE_SECONDS + 1
        with mock.patch.object(runtime_prewarm, "release", lambda chat: setattr(chat, "prewarmed", None)):
            self.chats.pump()
        self.assertIsNone(chat.prewarmed)


class SharedPrewarmTests(unittest.TestCase):
    def test_a_plan_chats_shared_app_server_starts_with_the_launch_request_the_worker_makes(self):
        asked = []
        worker = SimpleNamespace(runtime=SimpleNamespace(prewarm=asked.append))
        for mode, expected in (("codex_subscription", 1), ("codex_api", 0), ("claude_subscription", 0)):
            asked.clear()
            mirror = SimpleNamespace(state={"id": "w1", "provider": mode}, workspace=Path("/state/workspaces/w1"))
            worker_chat_wiring._prewarm_shared(worker, mirror)
            self.assertTrue(until(lambda: len(asked) == expected, 1.0))
            if expected:   # The worker's first launch (``runtime_relay.Launcher``) of a plain app-server: same key.
                self.assertEqual(asked[0], {"session": "w1", "workspace": "/state/workspaces/w1",
                                            **runtime_relay.launch_request(["codex", "app-server"], {})})


class CatalogTests(unittest.TestCase):
    def chat(self, models=(), provider="codex_subscription", connection="c1", **extra):
        return SimpleNamespace(**dict(dict(state={"provider": provider, "connection_id": connection},
                                           models=list(models), models_loading=False, models_error="",
                                           _next_binding=None), **extra))

    def test_a_new_worker_chat_carries_the_catalog_angelo_loaded_for_its_connection_and_starts_no_listing(self):
        lead, new = self.chat(MODELS), self.chat(refresh_models=mock.Mock())
        with mock.patch.object(worker_chat_lifecycle, "enabled", lambda: True):
            worker_chat_wiring.catalog(new, lead)
        self.assertEqual(new.models, MODELS)
        new.refresh_models.assert_not_called()
        with mock.patch.object(worker_chat_lifecycle, "enabled", lambda: False):   # Off: loaded as before.
            worker_chat_wiring.catalog(new, lead)
        new.refresh_models.assert_called_once_with()

    def test_only_a_ready_catalog_of_the_same_connection_is_taken(self):
        new = self.chat()
        for other in (self.chat(MODELS, connection="c2"), self.chat(MODELS, provider="codex_api"),
                      self.chat(MODELS, models_loading=True), self.chat(MODELS, models_error="no"),
                      self.chat(MODELS, _next_binding={"models": []}), self.chat(MODELS, hosted_in_worker=True)):
            with self.subTest(other=other):
                self.assertEqual(worker_chat_wiring.lead_catalog(new, other), [])
        self.assertEqual(worker_chat_wiring.lead_catalog(new, self.chat(MODELS)), MODELS)


class WorkerCatalogTests(host_base.Fixture):
    def test_an_open_with_no_catalog_lists_its_own_through_angelo_and_the_mirror_hears_it(self):
        listed = []

        def list_models(provider):
            listed.append(provider.controller.runtime_transport)
            return MODELS
        with mock.patch.object(host_base.FakeProvider, "list_models", list_models, create=True):
            self.open(models=[])
            chat = self.chats.hosted(self.session)
            self.assertTrue(until(lambda: chat.events.qsize() or chat.models, 5.0))
            self.chats.pump()
            self.chats.outbox.flush()
        self.assertEqual((listed, chat.models), ([self.transport], MODELS))   # Through Angelo's transport only.
        forwarded = self.endpoint.events("models")
        self.assertEqual(forwarded[-1]["data"]["models"], MODELS)

    def test_an_open_that_carried_a_catalog_lists_nothing(self):
        with mock.patch.object(host_base.FakeProvider, "list_models", side_effect=AssertionError, create=True):
            self.open(models=MODELS)
        self.assertEqual(self.chats.hosted(self.session).models, MODELS)
        self.assertEqual(host_base.FakeProvider.instances, [])


class LiveUsageTests(mirror_base.Fixture):
    def test_a_worker_turn_shows_in_the_lead_meter_as_a_lead_turn_does(self):
        self.worker.busy = True
        metrics = self.worker._request_metrics = mirror_base.angelo_agent.telemetry.RequestMetrics(
            runtime="codex", session_id=self.session)
        metrics.set_route(mode="codex_api", requested_model="gpt-a")
        metrics.provider_usage({"input_tokens": 1000, "output_tokens": 40})
        metrics.count("provider_turn_requests")
        metrics.path.running = {"input_tokens": 500, "output_tokens": 10}
        data = worker_chat_events.running(self.worker)
        self.mirror.state["provider"] = "codex_api"
        self.mirror.begin_turn()
        self.frame("running_usage", worker_chat_events.plain(data))
        self.mirror.pump_events()
        live = usage_meter.in_progress(self.mirror._request_metrics, "codex_api")
        self.assertEqual(live, usage_meter.in_progress(metrics, "codex_api"))   # The lead turn's own reading.
        self.assertEqual(usage_meter.turn_tokens(self.mirror), 50)
        self.assertIsNotNone(chat_usage._inputs(self.mirror)[2])
        for _ in range(2):   # The stream's estimate since the last reported count, on top of it, as a lead's.
            self.frame("delta", dict(mirror_base.TURN, item="r", text="x" * 40))
            self.mirror.pump_events()
            counted = usage_meter.turn_tokens(self.mirror)
        self.assertEqual(counted, 60)
        self.frame("finished", dict(mirror_base.TURN, status="completed"))
        self.mirror.pump_events()
        self.assertIsNone(usage_meter.in_progress(self.mirror._request_metrics, "codex_api"))

    def test_malformed_numbers_never_reach_the_meter_and_nothing_applies_between_turns(self):
        self.frame("running_usage", {"usage": {"input_tokens": 5}})
        self.mirror.pump_events()
        self.assertIsNone(self.mirror._request_metrics)
        self.mirror.begin_turn()
        self.frame("running_usage", {"usage": {"input_tokens": -1, "output_tokens": "9", "cached": 3},
                                     "running": "x", "turns": "2", "route": {"mode": ["x"], "model": "m"},
                                     "served_models": [1, "m"]})
        self.mirror.pump_events()
        metrics = self.mirror._request_metrics
        self.assertEqual((metrics.usage, metrics.path.running, metrics.counts["provider_turn_requests"],
                          metrics.route, metrics.served_models), ({}, None, 0, {"model": "m"}, ["m"]))

    def test_nothing_is_sent_between_turns(self):
        self.assertIsNone(worker_chat_events.running(self.worker))


class WorkerRunningTests(host_base.Fixture):
    def test_a_busy_hosted_chat_forwards_its_request_usage_when_it_changes(self):
        self.open(models=MODELS)
        chat = self.chats.hosted(self.session)
        chat.busy = True
        chat._request_metrics = mirror_base.angelo_agent.telemetry.RequestMetrics(runtime="codex")
        chat._request_metrics.path.running = {"output_tokens": 3}
        self.chats._send_running(chat)
        self.chats._send_running(chat)   # Unchanged: nothing more.
        chat._request_metrics.path.running = {"output_tokens": 9}
        self.chats._send_running(chat)   # Changed within the bound: waits.
        self.chats._running[self.session] = (time.monotonic() - 1.0, self.chats._running[self.session][1])
        self.chats._send_running(chat)
        self.chats.outbox.flush()
        sent = [event["data"]["running"] for event in self.endpoint.events("running_usage")]
        self.assertEqual(sent, [{"output_tokens": 3}, {"output_tokens": 9}])
        chat.busy = False


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
