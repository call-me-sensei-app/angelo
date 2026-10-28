# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The relay's guards from the A′ security, concurrency and parity reviews (S1, S4-S7, C10, U13).

Each class is one finding's regression: it failed while the finding stood. ``runtime_requests`` lists the child's
own requests; the Codex list here is its app-server protocol's ``ServerRequest`` union, the Claude list the control
requests Claude Code sends its SDK client.
"""

import json
import sys
import threading
import unittest
from pathlib import Path
from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import claude_direct, claude_transport, runtime_child, runtime_filter, runtime_service  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.tool_specs import native_plan_schema  # noqa: E402

WS = "/tmp/angelo-chat/workspace"
CODEX_APPROVALS = ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                   "item/permissions/requestApproval", "mcpServer/elicitation/request", "execCommandApproval",
                   "applyPatchApproval")
CODEX_OTHERS = ("item/tool/requestUserInput", "account/chatgptAuthTokens/refresh", "attestation/generate",
                "currentTime/read", "some/futureRequest")
CLAUDE_OTHERS = ("hook_callback", "oauth_token_refresh", "host_auth_token_refresh", "request_user_dialog",
                 "remote_control_work_secret", "register_device_hooks", "upload_device_hook_template", "future")


def line(value):
    return json.dumps(value)


def codex(**keywords):
    return runtime_filter.Filter("codex", WS, "openai", **keywords)


class S1ApprovalsAreAngelosToDecline(unittest.TestCase):
    def test_every_codex_request_but_a_tool_call_is_answered_by_angelo_and_never_relayed(self):
        flt = codex()
        for number, method in enumerate(CODEX_APPROVALS + CODEX_OTHERS):
            with self.subTest(method=method):
                routed = flt.outbound(line({"id": number, "method": method, "params": {}}))
                self.assertIsNone(routed.line)   # The worker never sees it.
                answer = json.loads(routed.answer)
                self.assertEqual(answer["id"], number)
                if "result" in answer:   # A decline where the method has one, else the providers' -32601.
                    self.assertIn(answer["result"].get("decision", answer["result"].get("action")), ("decline",))
                else:
                    self.assertEqual(answer["error"]["code"], -32601)
                # And the worker can not answer it in Angelo's place.
                self.assertFalse(flt.inbound(line({"id": number, "result": {"decision": "accept"}})).passed)

    def test_the_review_poc_accept_answer_never_reaches_the_child(self):
        flt = codex()
        flt.outbound(line({"id": 7, "method": "item/fileChange/requestApproval", "params": {}}))
        self.assertFalse(flt.inbound(line({"id": 7, "result": {"decision": "accept"}})).passed)

    def test_a_tool_call_answer_must_have_its_methods_shape(self):
        flt = codex()
        good = {"success": True, "contentItems": [{"type": "inputText", "text": "{}"},
                                                  {"type": "inputImage", "imageUrl": "data:image/png;base64,AA"}]}
        for number, (answer, fits) in enumerate((
                ({"result": good}, True), ({"error": {"code": -32601, "message": "no"}}, True),
                ({"result": {"decision": "accept"}}, False), ({"result": dict(good, extra=1)}, False),
                ({"result": {"success": "yes", "contentItems": []}}, False),
                ({"result": {"success": True, "contentItems": [{"type": "shell", "command": "x"}]}}, False),
                ({"error": {"code": "x", "message": 1}}, False))):
            with self.subTest(answer=answer):
                flt.outbound(line({"id": number, "method": "item/tool/call", "params": {}}))
                verdict = flt.inbound(line(dict(answer, id=number)))
                self.assertEqual(verdict.passed, fits)
                if not fits:   # The child is answered with the method's refusal instead: nothing waits forever.
                    refusal = json.loads(verdict.child)
                    self.assertEqual((refusal["id"], refusal["result"]["success"]), (number, False))

    def test_claude_permission_prompts_elicitations_and_the_rest_are_answered_by_angelo(self):
        flt = runtime_filter.Filter("claude", WS)
        for subtype in ("can_use_tool", "elicitation") + CLAUDE_OTHERS:
            with self.subTest(subtype=subtype):
                routed = flt.outbound(line({"type": "control_request", "request_id": subtype,
                                            "request": {"subtype": subtype, "tool_name": "Bash"}}))
                self.assertIsNone(routed.line)
                response = json.loads(routed.answer)["response"]
                self.assertEqual(response["request_id"], subtype)
                if subtype == "can_use_tool":
                    self.assertEqual(response["response"]["behavior"], "deny")
                elif subtype == "elicitation":
                    self.assertEqual(response["response"]["action"], "decline")
                else:
                    self.assertEqual(response["subtype"], "error")
                allow = {"type": "control_response", "response": {"subtype": "success", "request_id": subtype,
                                                                   "response": {"behavior": "allow"}}}
                self.assertFalse(flt.inbound(line(allow)).passed)

    def test_a_claude_mcp_answer_must_answer_the_same_message(self):
        flt = runtime_filter.Filter("claude", WS)

        def ask(request_id, message_id=5):
            routed = flt.outbound(line({"type": "control_request", "request_id": request_id, "request": {
                "subtype": "mcp_message", "server_name": "angelo",
                "message": {"jsonrpc": "2.0", "id": message_id, "method": "tools/call"}}}))
            self.assertIsNotNone(routed.line)   # Angelo's own tools: the worker answers.

        def answer(request_id, reply):
            return line({"type": "control_response", "response": {"subtype": "success", "request_id": request_id,
                                                                   "response": {"mcp_response": reply}}})
        ask("a")
        self.assertTrue(flt.inbound(answer("a", {"jsonrpc": "2.0", "id": 5, "result": {"content": []}})).passed)
        ask("b")
        verdict = flt.inbound(answer("b", {"jsonrpc": "2.0", "id": 6, "result": {}}))   # Another message's id.
        self.assertFalse(verdict.passed)
        self.assertEqual(json.loads(verdict.child)["response"]["response"]["mcp_response"]["id"], 5)
        ask("c")
        self.assertFalse(flt.inbound(line({"type": "control_response", "response": {
            "subtype": "success", "request_id": "c", "response": {"behavior": "allow"}}})).passed)
        ask("d")
        self.assertTrue(flt.inbound(line({"type": "control_response", "response": {
            "subtype": "error", "request_id": "d", "error": "gone"}})).passed)


class S5ClaudeInitializeIsOneAngeloBuilds(unittest.TestCase):
    def setUp(self):
        self.flt = runtime_filter.Filter("claude", WS)

    def passes(self, request):
        return self.flt.inbound(line({"type": "control_request", "request_id": "i", "request": request})).passed

    def test_the_builders_initializes_pass_for_any_tool_set(self):
        for allowed in (None, frozenset(), frozenset({"angelo_execute_python", "angelo_view"}),
                        frozenset({"angelo_apply_batch", "angelo_list_files", "WebSearch"})):
            with self.subTest(allowed=allowed):
                schema = native_plan_schema(allowed)
                self.assertTrue(self.passes(claude_transport.initialization_request(schema, "x")["request"]))
        self.assertTrue(self.passes(claude_direct.initialization_request("y")["request"]))

    def test_other_server_configs_or_schemas_are_refused(self):
        direct = claude_direct.initialization_request("y")["request"]
        plan = claude_transport.initialization_request(native_plan_schema(None), "x")["request"]
        changed_schema = json.loads(json.dumps(plan))
        changed_schema["jsonSchema"]["properties"]["reply"] = {"type": "object"}
        for request in (dict(direct, sdkMcpServerConfigs={"other": {"anything": {"nested": [1, 2, 3]}}}),
                        dict(direct, sdkMcpServers=["angelo", "other"]),
                        dict(direct, sdkMcpServerConfigs={"angelo": {"command": "sh"}}),
                        {"subtype": "initialize", "sdkMcpServers": ["angelo"],
                         "sdkMcpServerConfigs": {"other": {}}},
                        dict(plan, jsonSchema={}), changed_schema, dict(plan, systemPrompt="x"),
                        dict(plan, hooks={"Stop": []}), dict(direct, jsonSchema=plan["jsonSchema"])):
            with self.subTest(request=str(request)[:120]):
                self.assertFalse(self.passes(request))


class S6ThreadsAreTheChatsOwn(unittest.TestCase):
    def forwarded(self, flt, method, thread, **params):
        self.sent = getattr(self, "sent", 100) + 1   # Its own id: one id is in flight once (external audit 3 #1).
        verdict = flt.inbound(line({"id": self.sent, "method": method, "params": dict(params, threadId=thread)}))
        self.assertTrue(verdict.passed)
        return json.loads(verdict.line)["params"]["threadId"]

    def test_another_thread_reaches_the_child_as_one_that_does_not_exist(self):
        flt = codex()
        for method in ("thread/fork", "thread/resume"):
            with self.subTest(method=method):
                self.assertNotEqual(self.forwarded(flt, method, "some-other-thread"), "some-other-thread")

    def test_threads_this_child_opened_and_the_chats_recorded_ones_pass(self):
        flt = codex(recorded=lambda: ("recorded-native-id",))
        self.assertEqual(self.forwarded(flt, "thread/resume", "recorded-native-id"), "recorded-native-id")
        flt.inbound(line({"id": 10, "method": "thread/start", "params": {}}))
        flt.outbound(line({"id": 10, "result": {"thread": {"id": "started-here"}}}))
        self.assertEqual(self.forwarded(flt, "thread/fork", "started-here"), "started-here")

    def test_the_fork_capability_probe_still_gets_the_childs_own_check(self):
        flt = codex()   # codex_bases.supports_forks: a random id the child refuses before any lookup.
        sent = flt.inbound(line({"id": 4, "method": "thread/fork", "params": {
            "threadId": "probe", "experimentalPredictionMode": True, "excludeTurns": True}}))
        params = json.loads(sent.line)["params"]
        self.assertEqual((params["experimentalPredictionMode"], params["excludeTurns"]), (True, True))


def fake_spawn(started):
    class FakeProcess:
        def __init__(self):
            self.pid = 10000 + len(started)
            self.killed = False
            self.stdin = SimpleNamespace(write=lambda s: None, flush=lambda: None, close=lambda: None)
            self.stdout, self.stderr = iter(threading.Event().wait, True), iter(())

        def poll(self):
            return -9 if self.killed else None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    def spawn(command, env, cwd):
        process = FakeProcess()
        started.append(process)
        return process
    return spawn


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.started, self.hosted = [], {"s"}
        chat = runtime_service.LaunchChat(mode="codex_subscription", workspace=WS, store_directory="/tmp",
                                          api_key="sk-chat-key-0001")
        self.chat = chat
        self.service = runtime_service.RuntimeService(lambda session: chat if session in self.hosted else None,
                                                      spawn=fake_spawn(self.started), platform="linux",
                                                      secrets=lambda: ["sk-angelo-other-key-02"])
        self.service.plan = lambda request, chat: {
            "kind": "codex", "command": ["codex"], "env": {}, "cwd": WS, "secrets": [], "model_provider": "openai",
            "key": (request["session"], len(self.started)), "login": False}
        self.service._relay = lambda child: ("fd:passed", None)
        # The fakes' pids are not real: their kill must never signal a process group of that number.
        grouped = patch("angelo_agent.runtime_service.RuntimeChild", partial(runtime_child.RuntimeChild, group=False))
        grouped.start()
        self.addCleanup(grouped.stop)
        self.addCleanup(setattr, self.service, "closed", True)

    def launch(self, session="s"):
        response = self.service.launch({"op": "runtime.launch", "session": session}, self.chat)
        return self.service._children[response["pid"]]

    def live(self):
        return [child for child in self.service.children() if child.alive()]


class S4ChildrenAreBounded(ServiceCase):
    def test_the_review_poc_leaves_no_more_than_a_chats_bound(self):
        for _ in range(25):
            self.launch()
        self.assertLessEqual(len(self.live()), runtime_service.SESSION_CHILDREN)

    def test_a_new_launch_beyond_the_bound_ends_the_oldest(self):
        children = [self.launch() for _ in range(runtime_service.SESSION_CHILDREN)]
        newest = self.launch()
        self.assertFalse(children[0].alive())
        self.assertTrue(all(child.alive() for child in children[1:] + [newest]))

    def test_a_worker_never_runs_more_than_its_bound(self):
        self.hosted.add("t")
        for _ in range(runtime_service.SESSION_CHILDREN):
            self.launch("s")
        for _ in range(runtime_service.WORKER_CHILDREN - runtime_service.SESSION_CHILDREN):
            self.launch("t")
        with self.assertRaises(RpcError):
            self.launch("t")
        self.assertEqual(len(self.live()), runtime_service.WORKER_CHILDREN)


class S7AffinityIdentityIsAngelos(ServiceCase):
    def probe(self, identity="whatever-the-worker-says"):
        with patch("angelo_agent.cache_affinity_secret.secret", return_value=b"s" * 32):
            return self.service.probe({"session": "s", "what": "cache_affinity", "scope": "personal",
                                       "identity": identity}, self.chat)["account"]

    def name_account(self, child, account, number=1):
        child.filter.inbound(line({"id": number, "method": "account/read", "params": {}}))
        child.filter.outbound(line({"id": number, "result": {"account": account}}))

    def test_the_worker_names_no_identity(self):
        child = self.launch()
        with self.assertRaises(RpcError):
            self.probe()   # The child named no account yet: no digest.
        self.name_account(child, {"type": "chatgpt", "email": "a@example.com"})
        self.assertEqual(self.probe("one"), self.probe("another"))

    def test_two_accounts_key_nothing(self):
        child = self.launch()
        self.name_account(child, {"type": "chatgpt", "email": "a@example.com"})
        self.name_account(child, {"type": "chatgpt", "email": "b@example.com"}, 2)
        with self.assertRaises(RpcError):
            self.probe()

    def test_the_identity_is_the_one_the_pool_checks(self):
        from angelo_agent import codex_runtime
        runtime = SimpleNamespace(lock=threading.Lock(), account=None, retire=lambda: None)
        codex_runtime.Runtime.note_account(runtime, {"type": "chatgpt", "email": "a@example.com"})
        self.assertEqual(runtime.account,
                         runtime_filter.account_identity({"type": "chatgpt", "email": "a@example.com"}))


class C10StopReachesAReusedAppServer(ServiceCase):
    def test_a_closed_chats_child_serving_the_next_chat_is_killed_by_its_stop(self):
        pooled = self.launch("s")
        self.hosted.discard("s")   # Its chat closed; the worker's pool keeps the app-server for the next chat.
        self.hosted.add("next")
        self.assertEqual(self.service.kill_session("next"), 1)
        self.assertFalse(pooled.alive())

    def test_the_workers_own_kill_reaches_it_too(self):
        pooled = self.launch("s")
        self.hosted.discard("s")
        self.hosted.add("next")
        self.assertEqual(self.service.kill({"session": "next", "pid": pooled.pid}), {"ok": True, "found": True})

    def test_a_hosted_chats_child_is_never_another_chats(self):
        self.hosted.add("t")
        other = self.launch("t")
        self.launch("s")
        self.assertEqual(self.service.kill({"session": "s", "pid": other.pid})["found"], False)
        self.assertTrue(other.alive())


class U13AngelosKeysNeverReachTheWorker(ServiceCase):
    def test_a_key_angelo_holds_now_is_redacted_though_the_launch_had_none(self):
        child = self.launch()
        routed = child.filter.outbound(line({"id": 1, "result": {"a": "sk-chat-key-0001",
                                                                 "b": "sk-angelo-other-key-02"}}))
        self.assertNotIn("sk-chat-key-0001", routed.line)
        self.assertNotIn("sk-angelo-other-key-02", routed.line)

    def test_errors_to_the_worker_carry_no_key(self):
        def fail(request, chat):
            raise RuntimeError("bad key sk-angelo-other-key-02")
        self.service.plan = fail
        with self.assertRaises(RpcError) as raised:
            self.service.handle({"op": "runtime.launch", "session": "s"})
        self.assertNotIn("sk-angelo-other-key-02", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
