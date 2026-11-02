# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``unsent_loss`` at the transports: what a write, a connect and the shared pool say when the runtime ended, and the
break-it review's findings on the change's first version (``test_native_runtime_unsent_loss`` has the lead and the
Controller). Each case injects the dead runtime deterministically."""

import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from runtime_unsent_fakes import ClosingProcess, ended_process  # noqa: E402,F401
from angelo_agent import codex_runtime, protocol  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess, RpcError  # noqa: E402


class TransportTests(unittest.TestCase):
    def test_a_write_to_an_ended_process_is_transport_closed_and_nothing_is_written(self):
        rpc = ended_process()
        with self.assertRaises(RpcError) as raised:
            rpc.request("initialize", {})
        self.assertIsInstance(raised.exception, getattr(protocol, "TransportClosed", ()))
        self.assertEqual(str(raised.exception), "Agent process has disconnected")

    def test_a_broken_pipe_under_a_write_is_transport_closed(self):
        rpc = JsonRpcProcess([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(rpc.close)
        rpc.process.stdin.close()   # The pipe ends under the next write (as a worker's closed relay does).
        with self.assertRaises(RpcError) as raised:
            rpc.write({"method": "x"})
        self.assertIsInstance(raised.exception, getattr(protocol, "TransportClosed", ()))

    def test_a_claude_session_whose_process_ended_says_so_at_the_write(self):
        from angelo_agent.providers import ClaudeProvider

        class Ended:
            def write(self, text):
                raise BrokenPipeError("its process ended")

            def flush(self):
                pass
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider._lifecycle_lock = threading.RLock()
        transport = type("T", (), {})()
        transport.lock, transport.retired = threading.Lock(), threading.Event()
        transport.owner = type("O", (), {"same_request": lambda self, other: True})()
        transport.process = type("P", (), {"stdin": Ended(), "poll": lambda self: None})()
        with patch.object(ClaudeProvider, "_current", return_value=True):
            with self.assertRaises(RpcError) as raised:
                provider._write({"type": "user"}, transport, owner=object())
        self.assertIsInstance(raised.exception, getattr(protocol, "TransportClosed", ()))


class ReviewFindingTests(unittest.TestCase):
    """The break-it review's findings on the first version of this change, each pinned."""

    def test_text_the_pipe_cannot_encode_is_the_callers_error_and_the_runtime_stays(self):
        rpc = JsonRpcProcess([sys.executable, "-c", "import sys\nfor line in sys.stdin: pass"])
        self.addCleanup(rpc.close)
        with self.assertRaises(UnicodeError):
            rpc.write({"method": "x", "params": {"text": "\udcff"}})   # A lone surrogate (a Blender path's bytes).
        rpc.write({"method": "x"})   # Still alive and writable.
        self.assertFalse(rpc.closed.is_set())

    def test_a_claude_session_whose_reader_saw_the_end_first_is_a_lost_runtime(self):
        from angelo_agent.providers import ClaudeProvider
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider._lifecycle_lock = threading.RLock()
        transport = type("T", (), {})()
        transport.lock, transport.retired = threading.Lock(), threading.Event()
        transport.retired.set()
        transport.reader_ended = True   # The reader retired it before this write.
        transport.owner = type("O", (), {"same_request": lambda self, other: True})()
        transport.process = type("P", (), {"stdin": None, "poll": lambda self: None})()
        with patch.object(ClaudeProvider, "_current", return_value=True):
            with self.assertRaises(RpcError) as raised:
                provider._write({"type": "user"}, transport, owner=object())
        self.assertIsInstance(raised.exception, protocol.TransportClosed)

    def test_a_codex_connect_whose_runtime_ends_while_it_opens_its_thread_is_a_lost_runtime(self):
        from types import SimpleNamespace
        from angelo_agent import codex_connect, providers
        folder = Path(tempfile.mkdtemp())
        controller = SimpleNamespace(state={"provider": "codex_api", "native_id": None, "toolset_version": 10 ** 6,
                                            "options": {}}, store=SimpleNamespace(directory=folder), workspace=folder,
                                     api_key="", models=[], voice=None, cancelled=False,
                                     emit=lambda *args, **kwargs: None)
        dying = ClosingProcess(None, None)

        def open_thread(provider, parameters, tools, fork=True):
            dying.closed.set()   # The process ends after thread/start was written, before its answer.
            raise RpcError("Agent process exited")
        provider = providers.CodexProvider(controller, {})
        with patch.object(providers, "find_executable", return_value="codex"), \
                patch.object(providers.agent_runtime, "kind", return_value="own"), \
                patch.object(providers.CodexProvider, "_command", return_value=(["codex", "app-server"], {})), \
                patch.object(providers.codex_runtime, "transport", return_value=(dying, [])), \
                patch.object(providers.CodexProvider, "_thread_parameters", return_value=({}, "m")), \
                patch.object(codex_connect, "open_thread", side_effect=open_thread), \
                patch.object(providers, "plan_mode", return_value=False):
            with self.assertRaises(RpcError) as raised:
                provider.connect()
        self.assertIsInstance(raised.exception, protocol.TransportClosed)   # Nothing of the message was sent.

    def test_a_codex_connect_answer_of_its_own_is_never_a_lost_runtime_even_if_it_then_ended(self):
        from types import SimpleNamespace
        from angelo_agent import codex_connect, providers
        folder = Path(tempfile.mkdtemp())
        controller = SimpleNamespace(state={"provider": "codex_api", "native_id": None, "toolset_version": 10 ** 6,
                                            "options": {}}, store=SimpleNamespace(directory=folder), workspace=folder,
                                     api_key="", models=[], voice=None, cancelled=False,
                                     emit=lambda *args, **kwargs: None)
        dying = ClosingProcess(None, None)

        def open_thread(provider, parameters, tools, fork=True):
            dying.closed.set()
            raise RpcError("thread/start refused: model not available")   # Its own answer, then it ended.
        provider = providers.CodexProvider(controller, {})
        with patch.object(providers, "find_executable", return_value="codex"), \
                patch.object(providers.agent_runtime, "kind", return_value="own"), \
                patch.object(providers.CodexProvider, "_command", return_value=(["codex", "app-server"], {})), \
                patch.object(providers.codex_runtime, "transport", return_value=(dying, [])), \
                patch.object(providers.CodexProvider, "_thread_parameters", return_value=({}, "m")), \
                patch.object(codex_connect, "open_thread", side_effect=open_thread), \
                patch.object(providers, "plan_mode", return_value=False):
            with self.assertRaises(RpcError) as raised:
                provider.connect()
        self.assertNotIsInstance(raised.exception, protocol.TransportClosed)

    def test_a_codex_connect_refused_for_another_reason_is_never_a_lost_runtime(self):
        from types import SimpleNamespace
        from angelo_agent import providers
        folder = Path(tempfile.mkdtemp())
        controller = SimpleNamespace(state={"provider": "codex_api", "native_id": None, "options": {}},
                                     store=SimpleNamespace(directory=folder), workspace=folder, api_key="",
                                     models=[], voice=None, cancelled=False, emit=lambda *args, **kwargs: None)
        provider = providers.CodexProvider(controller, {})
        provider.rpc = ClosingProcess(None, None)
        provider.rpc.closed.set()   # A previous connect's process, already let go.
        with patch.object(providers, "find_executable", return_value="codex"), \
                patch.object(providers.agent_runtime, "kind", return_value="own"), \
                patch.object(providers.CodexProvider, "_command", return_value=(["codex", "app-server"], {})), \
                patch.object(providers.codex_runtime, "transport", side_effect=RpcError("Codex billing mode does not "
                                                                                       "match")), \
                patch.object(providers, "plan_mode", return_value=False):
            with self.assertRaises(RpcError) as raised:
                provider.connect()
        self.assertNotIsInstance(raised.exception, protocol.TransportClosed)

    def test_a_shared_runtime_ending_inside_its_spawn_is_logged_without_raising(self):
        runtime = codex_runtime.Runtime.__new__(codex_runtime.Runtime)
        runtime.lock, runtime.dead, runtime.leases, runtime.warming = threading.RLock(), False, [], {}
        runtime.key, runtime.opened_by, runtime.initialized = ("k",), "test", False
        runtime._died(own=True)   # ``self.rpc`` is not assigned yet: the reader saw the end first.
        self.assertTrue(runtime.dead)

    def test_a_kill_after_the_child_exited_leaves_its_end_its_own(self):
        from angelo_agent.runtime_child import RuntimeChild, ended
        process = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(5)"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process.wait()
        child = RuntimeChild(process, protocol, "chat-1234", group=False)
        child.kill("it ended before its relay opened")
        self.assertIsNone(child.ended_by)
        self.assertIn("ended on its own", ended(child, 5))

    def test_an_unexpected_end_keeps_its_error_output_whatever_its_code(self):
        import time
        from types import SimpleNamespace
        from angelo_agent.runtime_child import ended
        child = SimpleNamespace(pid=7, session="chat-1234", ended_by=None, input_closed=False, relay=None, filter=None,
                                stderr_tail=["panicked at thread main"], started=time.monotonic())
        self.assertIn("panicked at thread main", ended(child, 0))   # Code 0, yet nobody asked it to end.
        child.input_closed = True
        self.assertNotIn("panicked", ended(child, 0))   # Its worker closed its input: an ordinary end.

    def test_a_scrub_keeps_what_explains_an_end(self):
        from angelo_agent.runtime_child import scrub
        kept = "rollout-2026-11-02T01-37-01-0f3a9c1e2b4d5e6f7a8b9c0d1e2f3a4b.jsonl at 8e4fd6fd6772b10c9dfd3fd362d32c9a0683a02b"
        self.assertEqual(scrub(None, kept), kept)
        self.assertEqual(scrub(None, 'Authorization: Bearer rt_AbCd/IjKl+MnOp== refresh_token="ab/c+d=efgh12"'),
                         'Authorization: Bearer [redacted] refresh_token="[redacted]"')

    def test_a_claude_session_that_answered_its_start_with_its_own_error_is_not_a_lost_runtime(self):
        from angelo_agent import providers
        transport = type("T", (), {})()
        transport.reader_ended, transport.process = True, type("P", (), {"poll": lambda self: 1})()
        for said, lost in ((None, True), (providers.CLAUDE_DISCONNECTED, True), ("Invalid API key", False)):
            transport.initialization_error = said
            self.assertEqual(providers.claude_ended(transport) and said in (None, providers.CLAUDE_DISCONNECTED),
                             lost)
        self.assertTrue(protocol.lost_runtime(RpcError("Agent process exited")))
        self.assertFalse(protocol.lost_runtime(RpcError("Codex billing mode does not match")))

    def test_error_output_is_scrubbed_of_token_shapes_angelo_never_held(self):
        from angelo_agent.runtime_child import scrub
        text = scrub(None, "auth failed: refresh eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc and sk-proj-abcdefghij")
        self.assertNotIn("eyJhbGci", text)
        self.assertNotIn("sk-proj-abcdefghij", text)
        self.assertIn("auth failed", text)


class SharedPoolTests(unittest.TestCase):
    def setUp(self):
        codex_runtime.close_all()
        self.addCleanup(codex_runtime.close_all)
        self.made = []

    def connect(self):
        def spawn(on_event, on_request):
            self.made.append(ClosingProcess(on_event, on_request))
            return self.made[-1]
        return codex_runtime.transport(spawn, lambda *a: None, lambda *a: None,
                                       lambda rpc: rpc.request("initialize"),
                                       lambda rpc: rpc.request("account/read"),
                                       key=("conn", "codex_subscription", None, ("codex",)), connection_id="conn",
                                       mode="codex_subscription")

    def test_an_ended_shared_process_is_never_handed_to_the_next_connect(self):
        lease, _models = self.connect()
        lease.close()
        self.made[0].closed.set()   # It ended; its reader has not told the runtime yet.
        lease, _models = self.connect()
        self.assertEqual(len(self.made), 2)   # A fresh app-server for the next chat.
        self.assertIn("account/read", self.made[1].methods)

    def test_a_connect_whose_runtime_ended_under_it_is_reported_as_nothing_sent(self):
        def spawn(on_event, on_request):
            process = ClosingProcess(on_event, on_request)
            process.closed.set()   # Dead before Angelo's first write.
            return process
        with self.assertRaises(RpcError) as raised:
            codex_runtime.transport(spawn, lambda *a: None, lambda *a: None, lambda rpc: rpc.request("initialize"),
                                    lambda rpc: rpc.request("account/read"))
        self.assertIsInstance(raised.exception, getattr(protocol, "TransportClosed", ()))


if __name__ == "__main__":
    unittest.main()
