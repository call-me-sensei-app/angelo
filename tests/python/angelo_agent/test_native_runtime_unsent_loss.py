# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A request whose agent runtime ended before it sent anything starts once more on a fresh runtime (``unsent_loss``).

1.0.8 evidence: under heavy load a worker chat's first message failed "Agent process has disconnected" (the
transport was found closed at Angelo's first write), with no retry although nothing was sent. Each case here injects
that dead runtime deterministically (the transports and the shared pool: ``test_native_runtime_unsent_loss_paths``):

* the lead hands a worker a prewarmed child that ended between its adoption and its relay opening (real relay);
* the lead logs every launch's path and every child's end: who ended it, its code, and for a failure of its own its
  redacted error output;
* the Controller starts such a request once more, never one a model round of which may have gone, never after Stop,
  closes a failed retry's provider, keeps a new context's message across the retry, counts the retry in the receipt,
  and a second loss gives the message back unsent with the reason.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import stat
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from runtime_unsent_fakes import ended_process, wait_for  # noqa: E402
from angelo_agent import cli_runtime, provider_usage, runtime_relay  # noqa: E402
from angelo_agent import runtime_service, worker_channel  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess, RpcError  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

POSIX_ONLY = unittest.skipIf(sys.platform == "win32", environment_skip(
    "The relay's descriptor passing is POSIX; Windows uses named pipes (test_native_runtime_filter.WindowsRelayTests, "
    "test_native_parallel_workers_transport)"))
KEY = "sk-unsent-test-" + "k" * 40
SESSION = "chat-1"


@POSIX_ONLY
class LeadTests(unittest.TestCase):
    """The lead's runtime service over a real relay, with ``runtime_relay_fake_child`` as the model runtime."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.workspace = self.folder / "workspace"
        self.workspace.mkdir()
        fake = self.folder / "fake-runtime"
        fake.write_text("#!%s\n" % sys.executable + (Path(__file__).parent / "runtime_relay_fake_child.py").read_text())
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.chat = runtime_service.LaunchChat(
            mode="codex_api", workspace=str(self.workspace), store_directory=str(self.folder), api_key=KEY,
            settings={"auth_directory": str(self.folder), "connection_id": str(uuid.uuid4()),
                      "reuse_codex_login": False})
        self.logged = []
        self.service = runtime_service.RuntimeService(lambda session: self.chat if session == SESSION else None,
                                                      executable=lambda kind, settings: str(fake),
                                                      log=self.logged.append)
        listener = worker_channel.SocketPairListener()
        self.worker = worker_channel.connect("fd:%d" % os.dup(listener.pass_fds()[0]))
        listener.started()
        self.client = worker_channel.Client(listener.accept(None), on_reverse=self.service.handle)
        runtime_relay.install(self.worker, lambda: [SESSION])
        threading.Thread(target=self._worker_reader, daemon=True).start()   # Takes Angelo's answers in (as a worker).
        self.addCleanup(self._clean)

    def _worker_reader(self):
        try:
            while True:
                frame = self.worker.receive()
                self.worker.send({"seq": frame["seq"], "response": {}})
        except Exception:  # noqa: BLE001 - the test ended the connection.
            pass

    def _clean(self):
        runtime_relay.uninstall()
        self.service.close()
        self.client.close()
        self.worker.close()

    def codex(self):
        rpc = JsonRpcProcess(["codex", "app-server", "--listen", "stdio://"], env={}, cwd=str(self.workspace),
                             spawn=cli_runtime.launch)
        self.addCleanup(rpc.close)
        return rpc

    def test_a_prewarm_that_ends_after_its_adoption_is_never_handed_out(self):
        request = {"op": "runtime.launch", "session": SESSION, "workspace": str(self.workspace),
                   **runtime_relay.launch_request(["codex", "app-server"], {})}
        self.assertTrue(self.service.prewarm(request))
        warm = self.service.children()[0]
        adopt = self.service._adopt

        def adopt_then_end(key):   # The window: adopted alive, ended before its relay opens.
            child = adopt(key)
            if child is not None:
                os.killpg(child.pid, 9)
                assert wait_for(lambda: not child.alive()), "the adopted child did not end"
            return child
        with patch.object(self.service, "_adopt", side_effect=adopt_then_end):
            rpc = self.codex()
        self.assertNotEqual(rpc.process.pid, warm.pid)   # A fresh child, never the one that ended.
        self.assertEqual(rpc.request("initialize", {})["userAgent"], "fake/1.0")
        self.assertTrue(any("ended before its relay opened" in line for line in self.logged), self.logged)

    def test_every_child_end_is_logged_with_who_ended_it_its_code_and_redacted_output(self):
        rpc = self.codex()
        pid = rpc.process.pid
        rpc.request("initialize", {})
        rpc.close()   # The worker closes its input: the child exits by itself.
        self.assertTrue(wait_for(lambda: any("model runtime %d of chat" % pid in line for line in self.logged)),
                        self.logged)
        line = next(line for line in self.logged if "model runtime %d of chat" % pid in line)
        self.assertIn("after its worker closed its input", line)
        self.assertIn("code 0", line)
        self.assertNotIn("error output", line)   # A clean end needs no explanation.
        failing = self.codex()
        failing.request("initialize", {})
        failing.write({"id": 7, "method": "turn/interrupt", "params": {}})   # The stand-in exits 3 by itself.
        self.assertTrue(wait_for(lambda: any("model runtime %d of chat" % failing.process.pid in line
                                             for line in self.logged)), self.logged)
        line = next(line for line in self.logged if "model runtime %d of chat" % failing.process.pid in line)
        self.assertIn("ended on its own (code 3", line)
        self.assertIn("stderr line with the key", line)   # Its own error output explains it ...
        self.assertNotIn(KEY, line)                        # ... never with a key.
        killed = self.codex()
        killed.request("initialize", {})
        self.service.kill_session(SESSION)
        self.assertTrue(wait_for(lambda: any("model runtime %d of chat" % killed.process.pid in line
                                             and "Angelo ended it" in line for line in self.logged)), self.logged)

    def test_a_launch_log_names_the_path_it_took(self):
        rpc = self.codex()
        self.assertTrue(any("launch started child %d" % rpc.process.pid in line for line in self.logged), self.logged)


class Provider:
    """A chat's provider whose runtime is lost as each case says (``losses``), then works."""

    def __init__(self, losses, after_round=False, stop=None):
        self.losses, self.after_round, self.stop, self.connects, self.closed = losses, after_round, stop, 0, 0
        self.controller = None

    def bind_request(self, context):
        self.controller = context

    @property
    def needs_connect(self):
        return True

    def connect(self):
        self.connects += 1
        if self.connects <= self.losses:
            if self.stop is not None:
                self.stop()
            if self.after_round:   # A model round may have gone before the runtime was lost.
                provider_usage.record_runtime_round(self.controller)
            ended_process().request("turn/start", {})   # The ended runtime, met at Angelo's first write.
        return "thread-1"

    def run_request(self, context, native_id, text, observation=None):
        return "completed"

    def close(self):
        self.closed += 1


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("doc", 1))
        self.c.state.update(provider="codex_subscription", native_id="thread-1", toolset_version=TOOLSET_VERSION)
        self.c.state["messages"] = [{"role": "user", "text": "Make a cube."}]
        self.c._begin_metrics()
        self.c.busy = True
        self.c._turn_message = "Make a cube."

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def run_with(self, provider):
        made = []

        def factory(context, settings):
            made.append(provider)
            provider.controller = context
            return provider
        self.c.provider = None
        self.c.provider_factory = factory
        self.c._run("Make a cube.", {"objects": []})
        self.c.pump_events()
        return made

    def rows(self, role):
        return [m for m in self.c.state["messages"] if m.get("role") == role]

    def test_a_runtime_lost_before_anything_was_sent_is_retried_once_on_a_fresh_runtime(self):
        provider = Provider(losses=1)
        made = self.run_with(provider)
        self.assertEqual(provider.connects, 2)
        self.assertEqual(len(made), 2)   # The lost provider closed and let go; a new one connected.
        self.assertEqual(self.c.status, "Completed")
        self.assertEqual(self.rows("error"), [])

    def test_a_request_a_model_round_of_which_may_have_gone_is_never_retried(self):
        provider = Provider(losses=1, after_round=True)
        self.run_with(provider)
        self.assertEqual(provider.connects, 1)
        self.assertEqual(self.c.status, "Failed")
        self.assertEqual([row["text"] for row in self.rows("error")], ["Agent process has disconnected"])

    def test_a_second_loss_gives_the_message_back_unsent_and_says_why(self):
        provider = Provider(losses=2)
        self.run_with(provider)
        self.assertEqual(provider.connects, 2)   # One retry, never more.
        errors = self.rows("error")
        self.assertEqual(len(errors), 1)
        self.assertIn("nothing was sent", errors[0]["text"])
        self.assertTrue(errors[0].get("i18n"))   # Said in the person's language.
        self.assertTrue(any("wasn't sent" in row.get("text", "") for row in self.rows("system")))
        self.assertEqual(self.c.unsent_draft, "Make a cube.")

    def test_a_retry_after_a_lost_context_sends_the_message_composed_for_the_new_context(self):
        from angelo_agent.codex_connect import ContextLost
        self.c.state["messages"] = [{"role": "user", "text": "Make the cube red."},
                                    {"role": "assistant", "text": "The cube is red now."},
                                    {"role": "user", "text": "Make a cube."}]
        sent, steps = [], iter(["lost", "dead", "ok"])

        class Recorder:
            controller = None

            def bind_request(self, context):
                self.controller = context

            def run_request(self, context, native_id, text, observation=None):
                sent.append(text)
                return "completed"

            def close(self):
                pass

        def connect(context):
            step = next(steps)
            if step == "lost":
                raise ContextLost("The chat's Codex thread is no longer available")
            if step == "dead":
                ended_process().request("thread/start", {})
            self.c.provider = Recorder()
            return "new-thread"
        with patch.object(self.c, "_connect_runtime", side_effect=connect):
            self.c._run("Make a cube.", {"objects": []})
        self.assertEqual(len(sent), 1)
        self.assertIn("Make the cube red.", sent[0])   # The new context's handoff, kept across the retry.

    def test_a_context_lost_on_both_attempts_still_sends_the_message_once(self):
        from angelo_agent.codex_connect import ContextLost
        self.c.state["messages"] = [{"role": "user", "text": "Make the cube red."},
                                    {"role": "assistant", "text": "The cube is red now."},
                                    {"role": "user", "text": "Make a cube."}]
        sent, steps = [], iter(["lost", "dead", "lost", "ok"])

        class Recorder:
            controller = None

            def bind_request(self, context):
                self.controller = context

            def run_request(self, context, native_id, text, observation=None):
                sent.append(text)
                return "completed"

            def close(self):
                pass

        def connect(context):
            step = next(steps)
            if step == "lost":
                raise ContextLost("The chat's Codex thread is no longer available")
            if step == "dead":
                ended_process().request("thread/start", {})
            self.c.provider = Recorder()
            return "new-thread"
        with patch.object(self.c, "_connect_runtime", side_effect=connect):
            self.c._run("Make a cube.", {"objects": []})
        self.assertEqual(len(sent), 1, sent)
        self.assertTrue(sent[0].startswith("Make a cube."))   # The message, then the new context's blocks ...
        self.assertEqual(sent[0].count("Make a cube."), 2)   # ... whose handoff holds it once more: never a third.

    def test_a_retry_that_fails_closes_its_own_provider(self):
        class Fails(Provider):
            def run_request(self, context, native_id, text, observation=None):
                raise RpcError("Codex turn failed")
        first, fresh = Provider(losses=1), Fails(losses=0)
        made = iter([first, fresh])

        def factory(context, settings):
            provider = next(made)
            provider.controller = context
            return provider
        self.c.provider, self.c.provider_factory = None, factory
        self.c._run("Make a cube.", {"objects": []})
        self.c.pump_events()
        self.assertEqual(fresh.closed, 1)   # Never left bound to the chat with its runtime.
        self.assertIsNone(self.c.provider)

    def test_a_retry_is_counted_in_the_requests_receipt(self):
        self.run_with(Provider(losses=1))
        self.assertEqual(self.c._request_metrics.counts.get("runtime_lost_retries"), 1)

    def test_a_stopped_request_is_never_started_again(self):
        provider = Provider(losses=1, stop=lambda: setattr(self.c, "cancelled", True))
        self.run_with(provider)
        self.assertEqual(provider.connects, 1)


if __name__ == "__main__":
    unittest.main()
