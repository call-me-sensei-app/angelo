# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's model runtime runs in Angelo and is relayed to the worker (design A', contract C5).

Real channels (a Unix socket pair, the relay's descriptor passed with Angelo's answer), the real worker transport
(``protocol.JsonRpcProcess`` through ``cli_runtime.launch``) and a stand-in runtime (``runtime_relay_fake_child``)
that echoes what it sees, its key included: what reaches the worker proves the key never does.
"""

import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import (cache_affinity, claude_direct, cli_runtime, runtime_filter, runtime_relay,  # noqa: E402
                          runtime_service, worker_channel)
from angelo_agent.claude_transport import claude_arguments  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess, RpcError  # noqa: E402

POSIX_ONLY = unittest.skipIf(sys.platform == "win32", "The relay's descriptor passing is POSIX; Windows uses pipes")
KEY = "sk-relay-test-" + "k" * 40
SESSION = "chat-1"
CLAUDE_VALUES = {"tools": "WebSearch,WebFetch", "direct": False, "fast": False, "model": "opus", "effort": "low",
                 "thinking": False, "resume": ""}


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:   # A zombie no one reaped yet has ended too.
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


@POSIX_ONLY
class RelayTests(unittest.TestCase):
    mode = "codex_api"

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.workspace = self.folder / "workspace"
        self.workspace.mkdir()
        fake = self.folder / "fake-runtime"
        fake.write_text("#!%s\n" % sys.executable + (Path(__file__).parent / "runtime_relay_fake_child.py").read_text())
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.chat = runtime_service.LaunchChat(
            mode=self.mode, workspace=str(self.workspace), store_directory=str(self.folder), api_key=KEY,
            settings={"auth_directory": str(self.folder), "connection_id": str(uuid.uuid4()),
                      "reuse_codex_login": False})
        self.logged = []
        self.service = runtime_service.RuntimeService(lambda session: self.chat if session == SESSION else None,
                                                      executable=lambda kind, settings: str(fake),
                                                      log=self.logged.append)
        listener = worker_channel.SocketPairListener()
        worker_end = worker_channel.connect("fd:%d" % os.dup(listener.pass_fds()[0]))
        listener.started()
        self.client = worker_channel.Client(listener.accept(None), on_reverse=self.service.handle)
        # The worker: its reader (worker_host._read) only parses; the reverse caller takes Angelo's answers.
        self.worker, self.normal, self.seen = worker_end, [], []
        self.launcher = runtime_relay.install(worker_end, lambda: [SESSION])
        threading.Thread(target=self._worker_reader, daemon=True).start()
        self.addCleanup(self._clean)

    def _worker_reader(self):
        try:
            while True:
                frame = self.worker.receive()
                self.normal.append(frame)
                self.worker.send({"seq": frame["seq"], "response": {"echo": frame["request"]}})
        except Exception:  # noqa: BLE001 - the test ended the connection.
            pass

    def _clean(self):
        runtime_relay.uninstall()
        self.service.close()
        self.client.close()
        self.worker.close()

    def codex(self, events=None, requests=None):
        events, requests = [] if events is None else events, [] if requests is None else requests
        return JsonRpcProcess(["codex", "app-server", "--listen", "stdio://"], env={}, cwd=str(self.workspace),
                              spawn=cli_runtime.launch,
                              on_event=lambda method, params: events.append((method, params)),
                              on_request=lambda identity, method, params: requests.append((identity, method)))

    def recording(self, rpc):
        """Every relay frame the worker receives, as text."""
        frames, receive = [], rpc.process.endpoint.receive

        def recorded():
            frame = receive()
            frames.append(json.dumps(frame))
            return frame
        rpc.process.endpoint.receive = recorded
        return frames

    # Launch and round trip -------------------------------------------------------------------------------------

    def test_a_codex_round_trip_runs_through_angelo_both_ways(self):
        events, requests = [], []
        rpc = self.codex(events, requests)
        self.addCleanup(rpc.close)
        self.assertEqual(rpc.request("initialize", {"clientInfo": {"name": "angelo_blender"}})["userAgent"], "fake/1.0")
        rpc.notify("initialized")
        account = rpc.request("account/read", {"refreshToken": False})
        self.assertEqual(account["account"]["type"], "apiKey")
        self.assertNotIn(KEY, json.dumps(account))            # The child said it; the worker never hears it.
        self.assertIn("[redacted]", account["note"])
        started = rpc.request("thread/start", {"cwd": str(self.workspace), "sandbox": "read-only",
                                               "approvalPolicy": "on-request", "modelProvider": "openai"})
        self.assertEqual(started["echo"], "thread/start")
        self.assertTrue(wait_for(lambda: requests == [(99, "item/tool/call")]))   # The child's own request.
        rpc.respond(99, {"success": True, "contentItems": []})
        self.assertTrue(wait_for(lambda: any(method == "got" for method, _params in events)))
        self.assertEqual(self.service.children()[0].session, SESSION)
        self.assertNotIn(KEY, " ".join(rpc.stderr_tail))   # The child's stderr stays in Angelo.

    def test_normal_requests_are_untouched_while_a_launch_is_answered(self):
        rpc = self.codex()
        self.addCleanup(rpc.close)
        self.assertEqual(self.client.request({"op": "save"}, 5), {"echo": {"op": "save"}})
        self.assertEqual(self.normal[-1]["request"], {"op": "save"})

    def test_a_line_larger_than_the_worker_channel_allows_crosses_whole(self):
        rpc = self.codex()
        self.addCleanup(rpc.close)
        size = worker_channel.RESPONSE_LIMIT + 123
        self.assertEqual(len(rpc.request("model/list", {"size": size}, timeout=30)["data"]), size)

    def test_an_api_key_codex_chat_is_signed_in_by_angelo_alone(self):
        rpc = self.codex()
        self.addCleanup(rpc.close)
        frames = self.recording(rpc)
        rpc.request("initialize", {})
        child = self.service.children()[0]
        self.assertEqual(child.filter.initialized, {"userAgent": "fake/1.0"})   # Angelo's own, before the relay.
        rpc.request("account/read", {})
        self.assertFalse(any(KEY in frame for frame in frames))

    def test_nothing_the_worker_receives_or_launches_with_holds_the_key(self):
        answers, route = [], self.launcher.caller.route
        self.launcher.caller.route = lambda frame, handles: (answers.append(json.dumps(frame)), route(frame, handles))
        rpc = self.codex()
        self.addCleanup(rpc.close)
        frames = self.recording(rpc)
        rpc.request("initialize", {})
        rpc.request("account/read", {})
        self.assertTrue(answers and frames)
        self.assertFalse(any(KEY in text for text in answers + frames + rpc.process.args))
        self.assertEqual(json.loads(answers[0])["response"]["relay"], "fd:passed")

    # The filter ------------------------------------------------------------------------------------------------

    def test_refused_requests_never_reach_the_runtime_and_are_answered_as_errors(self):
        rpc = self.codex()
        self.addCleanup(rpc.close)
        rpc.request("initialize", {})
        for method, params in (("account/login/start", {"type": "apiKey", "apiKey": "sk-other"}),
                               ("account/logout", {}), ("config/write", {"key": "model", "value": "x"}),
                               ("thread/start", {"config": {"model_providers.evil.base_url": "https://x"}}),
                               ("thread/start", {"cwd": "/"}), ("thread/start", {"sandbox": "danger-full-access"}),
                               ("thread/realtime/start", {"threadId": "t"}), ("shell/run", {}),
                               ("model/list", {"nested": [{"api_key": "x"}]})):
            with self.subTest(method=method, params=params), self.assertRaises(RpcError) as raised:
                rpc.request(method, params, timeout=5)
            self.assertIn("does not relay", str(raised.exception))
        seen = rpc.request("config/read", {})["seen"]
        self.assertEqual(seen, ["initialize", "initialized", "account/login/start", "config/read"])   # Angelo's own.
        self.assertTrue(self.logged)   # Each refusal is logged.

    def test_an_answer_to_no_request_of_the_runtime_is_dropped_with_a_notice(self):
        rpc = self.codex()
        self.addCleanup(rpc.close)
        rpc.respond(12345, {"forged": True})
        self.assertTrue(wait_for(lambda: any("does not relay" in line for line in rpc.stderr_tail)))

    def test_an_answer_that_does_not_fit_its_request_reaches_the_child_as_angelos_refusal(self):
        events, requests = [], []
        rpc = self.codex(events, requests)
        self.addCleanup(rpc.close)
        rpc.request("thread/start", {"cwd": str(self.workspace), "sandbox": "read-only",
                                     "approvalPolicy": "on-request", "modelProvider": "openai"})
        self.assertTrue(wait_for(lambda: requests == [(99, "item/tool/call")]))
        rpc.respond(99, {"decision": "accept"})   # S1: not an item/tool/call answer.
        self.assertTrue(wait_for(lambda: any(method == "got" for method, _params in events)))
        got = next(params for method, params in events if method == "got")
        self.assertEqual(got["result"]["success"], False)   # Angelo's refusal, not the worker's answer.

    # Ending ----------------------------------------------------------------------------------------------------

    def test_kill_goes_to_angelo_and_ends_the_child(self):
        rpc = self.codex()
        pid = rpc.process.pid
        self.assertTrue(alive(pid))
        rpc.process.kill()
        self.assertTrue(wait_for(lambda: not alive(pid)))
        self.assertIsNotNone(rpc.process.wait(5))
        self.assertTrue(rpc.closed.wait(5))
        rpc.close()

    def test_the_child_ending_is_the_workers_end_of_file_and_return_code(self):
        rpc = self.codex()
        with self.assertRaises(RpcError):
            rpc.request("turn/interrupt", {"threadId": "t", "turnId": "u"}, timeout=10)   # The fake exits with 3.
        self.assertTrue(rpc.closed.wait(5))
        self.assertEqual(rpc.process.wait(5), 3)
        self.assertEqual(rpc.process.poll(), 3)
        rpc.close()

    def test_close_ends_stdin_and_the_child_exits_by_itself(self):
        rpc = self.codex()
        pid = rpc.process.pid
        rpc.close()
        self.assertTrue(wait_for(lambda: not alive(pid)))
        self.assertEqual(rpc.process.returncode, 0)

    def test_closing_the_relay_ends_the_child(self):
        rpc = self.codex()
        pid = rpc.process.pid
        rpc.process.endpoint.close()
        self.assertTrue(wait_for(lambda: not alive(pid)))
        self.assertTrue(rpc.closed.wait(5))

    def test_angelo_closing_ends_every_child(self):
        first, second = self.codex(), self.codex()
        pids = [first.process.pid, second.process.pid]
        runtime_service._close_all()   # What Angelo's exit runs.
        self.assertTrue(wait_for(lambda: not any(alive(pid) for pid in pids)))
        self.assertTrue(first.closed.wait(5) and second.closed.wait(5))
        with self.assertRaises(RpcError):
            self.codex()   # A closed service starts nothing.

    # Who may launch what ---------------------------------------------------------------------------------------

    def test_only_a_hosted_chat_in_its_own_folder_launches(self):
        with patch.object(self.launcher, "sessions", lambda: ["someone-else"]), self.assertRaises(RpcError):
            self.codex()
        with patch.object(self.launcher, "sessions", lambda: [SESSION, "chat-2"]), self.assertRaises(RpcError):
            self.codex()   # Which chat is not certain: none starts.
        with self.assertRaises(RpcError):
            JsonRpcProcess(["codex", "app-server"], env={}, cwd=str(self.folder), spawn=cli_runtime.launch)
        with self.assertRaises(RpcError):
            cli_runtime.launch(["/bin/sh", "-c", "id"], env={}, cwd=str(self.workspace), text=True)
        self.assertEqual(self.service.children(), [])

    def test_a_prewarmed_child_is_handed_over_on_its_matching_launch(self):
        request = {"op": "runtime.launch", "session": SESSION, "workspace": str(self.workspace),
                   **runtime_relay.launch_request(["codex", "app-server"], {})}
        self.assertTrue(self.service.prewarm(request))
        warm_pid = self.service.children()[0].pid
        rpc = self.codex()
        self.addCleanup(rpc.close)
        self.assertEqual(rpc.process.pid, warm_pid)
        self.assertEqual(rpc.request("initialize", {})["userAgent"], "fake/1.0")
        self.assertTrue(self.service.prewarm(dict(request, text_only=True)))   # Another launch key: not adopted.
        other = self.codex()
        self.addCleanup(other.close)
        self.assertNotEqual(other.process.pid, self.service._warm and self.service._warm[1].pid)

    def test_probes_return_only_non_secret_results(self):
        rpc = self.codex()
        self.addCleanup(rpc.close)
        account = rpc.request("account/read", {})["account"]   # The child names its account to Angelo, too (S7).
        with patch("angelo_agent.cache_affinity_secret.secret", return_value=b"s" * 32):
            answer = self.launcher.probe("cache_affinity", identity="digest-of-the-account")
            expected = cache_affinity.account_digest(self.mode, runtime_filter.account_identity(account), b"s" * 32)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["account"], expected)   # Keyed by what the child said, never the worker's identity.
        self.assertNotIn(KEY, json.dumps(answer))
        with self.assertRaises(RpcError):
            self.launcher.probe("read_the_key")
        with self.assertRaises(RpcError):
            self.launcher.probe("list_models")   # A codex_api chat lists its models through its runtime.


@POSIX_ONLY
class ClaudeRelayTests(RelayTests):
    mode = "claude_api"

    def claude(self, values=None, env=None):
        command = claude_arguments("claude", dict(CLAUDE_VALUES, **(values or {})))
        return cli_runtime.launch(command, env=env or {}, cwd=str(self.workspace), stdin=-1, stdout=-1, stderr=-1,
                                  text=True, encoding="utf-8", bufsize=1)

    def test_a_claude_session_relays_stream_json_and_redacts_its_key(self):
        process = self.claude()
        self.addCleanup(process.kill)
        process.stdin.write(json.dumps(claude_direct.initialization_request("x")) + "\n")
        process.stdin.flush()
        answer = json.loads(process.stdout.readline())
        self.assertEqual(answer["response"]["subtype"], "success")
        self.assertEqual(answer["response"]["response"]["key_seen"], "[redacted]")   # The child had the key.
        # The child's permission prompt is Angelo's to answer (S1): denied there, never relayed to the worker.
        echoed = json.loads(process.stdout.readline())
        self.assertEqual(echoed["type"], "assistant")
        self.assertEqual(echoed["echo"]["response"]["request_id"], "child-ask-1")
        self.assertEqual(echoed["echo"]["response"]["response"]["behavior"], "deny")
        process.stdin.write(json.dumps({"type": "control_request", "request_id": "r2", "request": {
            "subtype": "apply_flag_settings", "settings": {"apiKeyHelper": "/bin/sh"}}}) + "\n")
        refused = json.loads(process.stdout.readline())
        self.assertEqual((refused["response"]["subtype"], refused["response"]["request_id"]), ("error", "r2"))

    def test_only_a_launch_angelo_builds_is_started(self):
        command = claude_arguments("claude", CLAUDE_VALUES)
        for changed in (command + ["--dangerously-skip-permissions"],
                        [part.replace('{"mcpServers":{}}', '{"mcpServers":{"x":{"command":"sh"}}}') for part in command],
                        [part.replace('"hooks": {}', '"hooks": {"Stop": []}') for part in command]):
            with self.subTest(changed=changed[-1]), self.assertRaises(RpcError):
                cli_runtime.launch(changed, env={}, cwd=str(self.workspace), text=True)
        with self.assertRaises(RpcError):
            self.claude({"tools": "Bash"})
        self.assertEqual(self.service.children(), [])

    def test_session_options_are_rebuilt_by_angelo(self):
        process = self.claude(env={"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "PATH": "/evil"})
        self.addCleanup(process.kill)
        env = self._launched_env()
        self.assertEqual(env.get("CLAUDE_CODE_PROMPT_CACHE_TTL"), "1h")
        self.assertEqual(env.get("ANTHROPIC_API_KEY"), KEY)   # Angelo's key, in Angelo's child only.
        self.assertNotEqual(env.get("PATH"), "/evil")
        with self.assertRaises(RpcError):
            self.claude(env={"CLAUDE_CODE_PROMPT_CACHE_TTL": "9d"})

    def _launched_env(self):
        request = {"op": "runtime.launch", "session": SESSION, "workspace": str(self.workspace),
                   **runtime_relay.launch_request(claude_arguments("claude", CLAUDE_VALUES),
                                                  {"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"})}
        return self.service.plan(request, self.chat)["env"]

    # The Codex-shaped cases of the base class do not apply to a Claude chat.
    test_a_codex_round_trip_runs_through_angelo_both_ways = None
    test_a_line_larger_than_the_worker_channel_allows_crosses_whole = None
    test_an_api_key_codex_chat_is_signed_in_by_angelo_alone = None
    test_refused_requests_never_reach_the_runtime_and_are_answered_as_errors = None
    test_an_answer_to_no_request_of_the_runtime_is_dropped_with_a_notice = None
    test_an_answer_that_does_not_fit_its_request_reaches_the_child_as_angelos_refusal = None
    test_the_child_ending_is_the_workers_end_of_file_and_return_code = None
    test_a_prewarmed_child_is_handed_over_on_its_matching_launch = None
    test_probes_return_only_non_secret_results = None
    test_normal_requests_are_untouched_while_a_launch_is_answered = None
    test_nothing_the_worker_receives_or_launches_with_holds_the_key = None   # Claude's: the stream-json case.

    def codex(self, events=None, requests=None):   # The ending cases run on a Claude session here.
        if self.service.closed:
            raise RpcError("closed")
        process = self.claude()
        return JsonRpcShim(process)


class JsonRpcShim:
    """The ending cases' view of a Claude session (``closed`` once its stdout ends)."""

    def __init__(self, process):
        self.process, self.closed = process, threading.Event()
        threading.Thread(target=lambda: (list(process.stdout), self.closed.set()), daemon=True).start()

    def close(self):
        from angelo_agent.claude_transport import close_process
        close_process(self.process)


if __name__ == "__main__":
    unittest.main()
