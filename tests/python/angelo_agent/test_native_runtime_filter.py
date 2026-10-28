# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The relay filter (``runtime_filter``), the worker channel's reverse requests and the Windows relay's failure path.

The end-to-end relay is ``test_native_runtime_relay``; these pin each rule alone, a fuzz of random lines, and the
channel's descriptor handling.
"""

import json
import os
import random
import socket
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import runtime_filter, runtime_relay, runtime_service, worker_channel  # noqa: E402
from angelo_agent import claude_direct, claude_transport  # noqa: E402
from angelo_agent.claude_transport import claude_arguments, launch_values  # noqa: E402
from angelo_agent.tool_specs import native_plan_schema  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402

WORKSPACE = "/tmp/angelo-chat/workspace"
POSIX_ONLY = unittest.skipIf(sys.platform == "win32", "Descriptor passing is POSIX")


def codex():
    return runtime_filter.Filter("codex", WORKSPACE, "openai", secrets=("sk-secret-value-123",))


def line(value):
    return json.dumps(value)


class CodexFilterTests(unittest.TestCase):
    def test_what_the_providers_send_passes(self):
        flt = codex()
        for message in ({"id": 1, "method": "initialize", "params": {"clientInfo": {}}},
                        {"method": "initialized", "params": {}},
                        {"id": 2, "method": "account/read", "params": {"refreshToken": False}},
                        {"id": 3, "method": "thread/start", "params": {
                            "cwd": WORKSPACE, "modelProvider": "openai", "sandbox": "read-only",
                            "approvalPolicy": "on-request", "serviceTier": "default", "baseInstructions": "x",
                            "model": "gpt", "dynamicTools": [], "cacheAffinityKey": "k"}},
                        {"id": 4, "method": "thread/fork", "params": {"threadId": "t", "experimentalPredictionMode":
                                                                      True, "ephemeral": True, "excludeTurns": True}},
                        {"id": 5, "method": "turn/start", "params": {"threadId": "t", "input": [], "summary": "auto",
                                                                     "model": "gpt", "effort": "low",
                                                                     "serviceTierForTurn": "default",
                                                                     "outputSchema": {}}},
                        {"id": 6, "method": "turn/interrupt", "params": {"threadId": "t", "turnId": "u"}}):
            with self.subTest(method=message["method"]):
                self.assertTrue(flt.inbound(line(message)).passed)

    def test_sign_in_configuration_voice_and_unknown_methods_are_refused_with_an_error(self):
        flt = codex()
        for method in ("account/login/start", "account/login/cancel", "account/logout", "config/write",
                       "config/batchWrite", "thread/realtime/start", "command/exec", "fuzzyFileSearch", ""):
            with self.subTest(method=method):
                verdict = flt.inbound(line({"id": 7, "method": method, "params": {}}))
                self.assertFalse(verdict.passed)
                self.assertEqual(json.loads(verdict.reply)["error"]["code"], -32600)

    def test_a_thread_can_not_move_its_key_folder_sandbox_or_provider(self):
        flt = codex()
        for params in ({"config": {"model_provider": "evil"}}, {"cwd": "/"}, {"sandbox": "danger-full-access"},
                       {"approvalPolicy": "never"}, {"modelProvider": "evil"}, {"sandboxPolicy": {}}):
            for method in ("thread/start", "thread/resume", "turn/start"):
                with self.subTest(method=method, params=params):
                    self.assertFalse(flt.inbound(line({"id": 8, "method": method, "params": params})).passed)

    def test_any_api_key_field_is_refused_wherever_it_is(self):
        flt = codex()
        for holder in ({"apiKey": "x"}, {"a": [{"API_KEY": "x"}]}, {"deep": {"api-key": "x"}}):
            with self.subTest(holder=holder):
                self.assertFalse(flt.inbound(line({"id": 9, "method": "model/list", "params": holder})).passed)

    def test_answers_go_only_to_the_childs_own_requests_once(self):
        flt = codex()
        answer = line({"id": 41, "result": {"success": True, "contentItems": [{"type": "inputText", "text": "{}"}]}})
        self.assertFalse(flt.inbound(answer).passed)
        flt.outbound(line({"id": 41, "method": "item/tool/call", "params": {}}))
        self.assertTrue(flt.inbound(answer).passed)
        self.assertFalse(flt.inbound(answer).passed)   # Once.
        self.assertFalse(flt.inbound(line({"id": 41, "result": {}, "error": {}})).passed)

    def test_lines_from_the_child_carry_no_secret(self):
        flt = codex()
        self.assertNotIn("sk-secret-value-123",
                         flt.outbound(line({"id": 1, "result": {"k": "sk-secret-value-123"}})).line)
        short = runtime_filter.Filter("codex", WORKSPACE, secrets=("abc",))
        self.assertEqual(short.outbound('{"a":"abc"}').line, '{"a":"abc"}')   # Not a secret Angelo set: untouched.

    def test_a_worker_initialize_after_angelos_own_is_answered_here(self):
        flt = codex()
        flt.initialized = {"userAgent": "codex/1"}
        verdict = flt.inbound(line({"id": 1, "method": "initialize", "params": {}}))
        self.assertEqual((verdict.passed, json.loads(verdict.reply)), (False, {"id": 1, "result": {"userAgent": "codex/1"}}))
        self.assertIsNone(flt.inbound(line({"method": "initialized"})).reply)

    def test_random_lines_never_pass_unless_allowed_and_never_raise(self):
        generator = random.Random(1027)
        alphabet = ['{', '}', '[', ']', '"', ':', ',', 'id', 'method', 'params', 'apiKey', '1', 'null', ' ', 'x']
        flt = codex()
        for _ in range(3000):
            text = "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 30)))
            verdict = flt.inbound(text)
            if verdict.passed:
                message = json.loads(text)
                self.assertIn(message.get("method"), runtime_filter.CODEX_METHODS)
                self.assertFalse(runtime_filter.holds_api_key(message))


class ClaudeFilterTests(unittest.TestCase):
    def setUp(self):
        self.flt = runtime_filter.Filter("claude", WORKSPACE)

    def test_user_messages_and_the_providers_control_requests_pass(self):
        for message in ({"type": "user", "session_id": "", "message": {"role": "user", "content": []}},
                        claude_transport.initialization_request(native_plan_schema(None), "x"),
                        claude_direct.initialization_request("x"),
                        {"type": "control_request", "request_id": "s", "request": {"subtype": "interrupt"}},
                        {"type": "control_request", "request_id": "e", "request": {
                            "subtype": "apply_flag_settings", "settings": {"effortLevel": "high"}}}):
            with self.subTest(message=message):
                self.assertTrue(self.flt.inbound(line(message)).passed)

    def test_settings_hooks_and_other_requests_are_refused(self):
        for request in ({"subtype": "apply_flag_settings", "settings": {"apiKeyHelper": "sh"}},
                        {"subtype": "initialize", "hooks": {"Stop": []}},
                        {"subtype": "initialize", "agents": {}}, {"subtype": "set_permission_mode"},
                        {"subtype": "mcp_set_servers"}):
            with self.subTest(request=request):
                verdict = self.flt.inbound(line({"type": "control_request", "request_id": "r", "request": request}))
                self.assertFalse(verdict.passed)
                self.assertEqual(json.loads(verdict.reply)["response"]["subtype"], "error")
        self.assertFalse(self.flt.inbound(line({"type": "keep_alive"})).passed)
        self.assertFalse(self.flt.inbound(line({"type": "control_response", "response": {"request_id": "x"}})).passed)


class ClaudeLaunchTests(unittest.TestCase):
    def test_launch_values_round_trip_every_shape_the_provider_builds(self):
        for values in ({"tools": "WebSearch,WebFetch", "direct": False, "fast": False, "model": "", "effort": "low",
                        "thinking": False, "resume": ""},
                       {"tools": "", "direct": True, "fast": True, "model": "claude-opus-5-5[1m]", "effort": "max",
                        "thinking": True, "resume": "5f0c3a52-0d47-4d7e-9a8e-111111111111"}):
            with self.subTest(values=values):
                self.assertEqual(launch_values(claude_arguments("claude", values)[1:]), values)

    def test_anything_else_is_refused(self):
        good = claude_arguments("claude", {"tools": "WebSearch", "direct": False, "fast": False, "model": "",
                                           "effort": "low", "thinking": False, "resume": ""})[1:]
        for arguments in (good + ["--add-dir", "/"], good[::-1], [p.replace("dontAsk", "bypassPermissions") for p in good],
                          [p.replace("low", "ultra") for p in good], good + ["--resume=../x"], []):
            with self.subTest(arguments=arguments[-1:]), self.assertRaises(RpcError):
                launch_values(arguments)


@POSIX_ONLY
class ReverseChannelTests(unittest.TestCase):
    def pair(self, on_reverse=None):
        listener = worker_channel.SocketPairListener()
        theirs = worker_channel.connect("fd:%d" % os.dup(listener.pass_fds()[0]))
        listener.started()
        client = worker_channel.Client(listener.accept(None), on_reverse=on_reverse)
        caller = worker_channel.ReverseCaller(theirs)
        threading.Thread(target=self._drain, args=(theirs,), daemon=True).start()
        self.addCleanup(client.close)
        self.addCleanup(theirs.close)
        return client, caller, theirs

    @staticmethod
    def _drain(endpoint):
        try:
            while True:
                endpoint.receive()
        except Exception:  # noqa: BLE001
            pass

    def test_without_a_handler_every_request_is_refused(self):
        _client, caller, _theirs = self.pair()
        response, handles = caller.request({"op": "runtime.launch"}, 5)
        self.assertEqual((response["error"]["message"], handles), ("Angelo serves no requests from this worker", []))

    def test_a_descriptor_crosses_with_its_answer_and_is_closed_by_angelo(self):
        _client, caller, _theirs = self.pair(lambda request: ({"ok": True}, handle))
        before = len(os.listdir("/dev/fd"))
        a, b = socket.socketpair()
        handle = b.detach()
        response, handles = caller.request({"op": "x"}, 5)
        self.assertEqual((response, len(handles)), ({"ok": True}, 1))
        received = socket.socket(fileno=handles[0])
        a.sendall(b"hi")
        self.assertEqual(received.recv(2), b"hi")
        received.close()
        a.close()
        self.assertTrue(wait_until(lambda: len(os.listdir("/dev/fd")) == before))   # Angelo's copy is gone too.

    def test_a_descriptor_with_any_other_frame_is_closed(self):
        listener = worker_channel.SocketPairListener()
        theirs = worker_channel.connect("fd:%d" % os.dup(listener.pass_fds()[0]))
        listener.started()
        ours = listener.accept(None)
        a, b = socket.socketpair()
        before = set(os.listdir("/dev/fd"))
        ours.send({"seq": 1, "request": {"op": "x"}}, b.fileno())
        self.assertEqual(theirs.receive(), {"seq": 1, "request": {"op": "x"}})
        self.assertLessEqual(set(os.listdir("/dev/fd")), before | {str(b.fileno())})   # Nothing new kept.
        for item in (a, b, ours, theirs):
            item.close()

    def test_a_connection_that_ends_wakes_every_waiting_request(self):
        gate = threading.Event()
        client, caller, theirs = self.pair(lambda request: gate.wait(10) and {"ok": True})
        result = []
        threading.Thread(target=lambda: result.append(caller.request({"op": "x"}, 30))).start()
        threading.Timer(0.2, client.close).start()
        self.assertTrue(wait_for_list(result))
        self.assertEqual(result[0], (None, []))
        gate.set()


def wait_until(predicate, timeout=5.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def wait_for_list(items, timeout=5.0):
    import time
    deadline = time.monotonic() + timeout
    while not items and time.monotonic() < deadline:
        time.sleep(0.02)
    return bool(items)


class WindowsRelayTests(unittest.TestCase):
    def test_a_worker_that_never_connects_to_its_relay_ends_the_child(self):
        killed, accepted = threading.Event(), []
        win32 = SimpleNamespace(
            create_server=lambda name, inbound: SimpleNamespace(name=name),
            accept=lambda server, pid, timeout, alive=None: accepted.append(pid) or (_ for _ in ()).throw(
                OSError("no connection")),
            close_handle=lambda pipe: None)
        service = runtime_service.RuntimeService(lambda session: None, worker_pid=4242, platform="win32",
                                                 win32=win32, log=lambda text: None)
        child = SimpleNamespace(kill=killed.set, attach=lambda endpoint: None)
        argument, handle = service._relay(child)
        self.assertTrue(argument.startswith("pipe:" + worker_channel.PIPE_PREFIX))
        self.assertIsNone(handle)
        self.assertTrue(killed.wait(5))
        self.assertEqual(accepted, [4242])   # Only the worker's own process is accepted.


class WorkerSideTests(unittest.TestCase):
    def test_relayed_turns_off_collaborators_in_a_worker(self):
        self.assertFalse(runtime_relay.relayed())
        source = (Path(runtime_relay.__file__).parent / "delegation.py").read_text()
        self.assertIn("if runtime_relay.relayed():", source)

    def test_only_model_runtime_launches_are_asked_for(self):
        with self.assertRaises(RpcError):
            runtime_relay.launch_request(["/bin/sh", "-c", "id"], {})
        self.assertEqual(runtime_relay.launch_request(["codex", "app-server", "-c", 'web_search="disabled"'], {}),
                         {"provider_kind": "codex", "text_only": True})


if __name__ == "__main__":
    unittest.main()
