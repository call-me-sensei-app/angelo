# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The relay's bindings from the A′ security re-review, round 2 (findings 1-3, S4 churn, S5 tool names).

Each class is one finding's regression and failed while the finding stood. The fake children never signal a real
process or group: their pids are negative, ``RuntimeChild`` is patched with ``group=False`` and their ``kill`` sends
no signal.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import (codex_bases, references, runtime_bindings, runtime_child, runtime_filter,  # noqa: E402
                          runtime_service, runtime_threads)
from angelo_agent.protocol import RpcError  # noqa: E402

WS = "/tmp/angelo-chat/workspace"
A, B = str(uuid.uuid4()), str(uuid.uuid4())


class FakeProcess:
    _next = [-2000]

    def __init__(self):
        FakeProcess._next[0] -= 1
        self.pid = FakeProcess._next[0]   # Never a real pid.
        self.stdin = SimpleNamespace(write=lambda s: None, flush=lambda: None, close=lambda: None)
        self.done = threading.Event()
        self.stdout, self.stderr = self._lines(), iter(())
        self.alive = True

    def _lines(self):
        self.done.wait(30)
        return
        yield

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False   # No signal is sent.
        self.done.set()


def chat(mode="codex_subscription", connection="conn-a", key="key-of-connection-a-01", folder=None, roots=()):
    return runtime_service.LaunchChat(mode=mode, workspace=WS, store_directory="/tmp", api_key=key,
                                      connection_id=connection, references=tuple(roots),
                                      settings={"auth_directory": folder} if folder else {})


class Case(unittest.TestCase):
    def setUp(self):
        grouped = patch("angelo_agent.runtime_service.RuntimeChild", partial(runtime_child.RuntimeChild, group=False))
        grouped.start()
        self.addCleanup(grouped.stop)
        self.folder = tempfile.mkdtemp()
        self.hosted = {}
        self.record = runtime_threads.ThreadRecord(self.folder)
        self.service = self.make()

    def make(self, **keywords):
        service = runtime_service.RuntimeService(lambda s: self.hosted.get(s), spawn=lambda c, e, w: FakeProcess(),
                                                 platform="linux", secrets=lambda: (), hosted=lambda: list(self.hosted),
                                                 threads=lambda found: self.record, **keywords)
        service.plan = lambda request, found: {
            "kind": "codex", "command": ["codex"], "env": {}, "cwd": WS, "secrets": [found.api_key],
            "key": (request["session"], len(service.children())), "model_provider": "openai", "login": False}
        service._relay = lambda child: ("fd:passed", None)
        self.addCleanup(service.close)
        return service

    def launch(self, session, service=None):
        service = service or self.service
        response = service.launch({"op": "runtime.launch", "session": session}, self.hosted[session])
        return next(child for child in service.children() if child.pid == response["pid"])

    @staticmethod
    def send(child, message):
        return child.filter.inbound(json.dumps(message))


class ClaimStaysWithinItsBinding(Case):
    """Finding 1: a closed chat's child served any chat, whatever its connection."""

    def test_the_review_poc_a_closed_chats_child_never_serves_another_connection(self):
        self.hosted[A] = chat()
        child = self.launch(A)
        del self.hosted[A]   # Chat A closes; the worker's pool keeps its app-server.
        self.hosted[B] = chat(mode="claude_subscription", connection="conn-b", key="")
        self.assertFalse(self.send(child, {"id": 1, "method": "thread/start", "params": {}}).passed)
        self.service.handle({"op": "runtime.kill", "session": B, "pid": 1})   # Any request for B.
        self.assertNotEqual(child.session, B)
        self.assertFalse(child.alive())   # It can serve no hosted chat: it ends.

    def test_the_pool_reuses_a_closed_chats_child_for_the_next_chat_of_its_connection(self):
        self.hosted[A] = chat()
        child = self.launch(A)
        del self.hosted[A]
        self.hosted[B] = chat()   # The same connection and key: codex_runtime.pool_key's reuse.
        self.assertTrue(self.send(child, {"id": 1, "method": "thread/start", "params": {}}).passed)
        self.assertEqual(child.session, B)
        self.assertEqual(self.service.kill_session(B), 1)   # Stop reaches it.

    def test_the_same_connection_with_another_key_is_another_binding(self):
        self.hosted[A] = chat()
        child = self.launch(A)
        self.hosted[A] = chat(key="key-of-connection-a-02")   # The key was replaced.
        self.assertFalse(self.send(child, {"id": 1, "method": "turn/start", "params": {"threadId": "t"}}).passed)
        self.service.handle({"op": "runtime.kill", "session": A, "pid": 1})
        self.assertFalse(child.alive())

    def test_an_unclaimed_child_still_takes_an_interrupt(self):
        self.hosted[A] = chat()
        child = self.launch(A)
        del self.hosted[A]
        self.assertTrue(self.send(child, {"id": 2, "method": "turn/interrupt",
                                          "params": {"threadId": "t", "turnId": "u"}}).passed)
        self.assertFalse(self.send(child, {"id": 3, "method": "turn/start", "params": {"threadId": "t"}}).passed)

    def test_removing_or_rekeying_a_connection_ends_its_children_in_every_worker(self):
        self.hosted[A], self.hosted[B] = chat(), chat(connection="conn-b", key="key-of-connection-b-01")
        mine, other = self.launch(A), self.launch(B)
        del self.hosted[A]   # A closed chat's child is ended too.
        self.assertEqual(runtime_bindings.end_connection("conn-a"), 1)
        self.assertFalse(mine.alive())
        self.assertTrue(other.alive())

    def test_a_team_sign_out_ends_every_team_connections_child(self):
        self.hosted[A], self.hosted[B] = chat(mode="team_mode_x", key=""), chat(connection="conn-b")
        team, personal = self.launch(A), self.launch(B)
        runtime_bindings.end_modes({"team_mode_x"})
        self.assertFalse(team.alive())
        self.assertTrue(personal.alive())

    def test_the_windows_hooks_reach_the_workers(self):
        with patch("angelo_agent.codex_runtime.retire_connection") as retire, \
                patch.object(runtime_bindings, "end_connection") as end:
            runtime_bindings.retire_connection("conn-a")
        retire.assert_called_once_with("conn-a")
        end.assert_called_once_with("conn-a")
        root = Path(runtime_bindings.__file__).parent
        self.assertIn("from .runtime_bindings import retire_connection as retire_codex_runtimes",
                      (root / "controller.py").read_text(encoding="utf-8"))
        sign_out = (root / "team_account_ui.py").read_text(encoding="utf-8")
        self.assertIn("end_modes(TEAM_MODES)", sign_out[sign_out.index("_account.sign_out()"):])

    def test_the_fingerprint_is_never_the_key(self):
        bound = runtime_bindings.binding(chat())
        self.assertNotIn("key-of-connection-a-01", json.dumps(bound))
        self.assertEqual(bound, runtime_bindings.binding(chat()))


class ResumableThreadsAreAngelosRecord(Case):
    """Finding 2: the recorded thread ids came from data the worker writes."""

    def resumed(self, child, thread):
        verdict = self.send(child, {"id": 5, "method": "thread/resume", "params": {"threadId": thread}})
        return json.loads(verdict.line)["params"]["threadId"]

    def test_the_journals_native_id_and_a_written_base_store_are_not_a_record(self):
        store = tempfile.mkdtemp()
        # A worker-writable folder: a remembered base it holds names nothing here.
        codex_bases.BaseStore(store).path.write_text(json.dumps({"bases": {"x": {"thread": "planted", "at": 1}}}))
        self.hosted[A] = runtime_service.LaunchChat(mode="codex_subscription", workspace=WS, store_directory=store,
                                                    connection_id="conn-a")
        child = self.launch(A)
        self.assertNotEqual(self.resumed(child, "planted"), "planted")
        self.assertNotIn("threads", runtime_service.LaunchChat.__dataclass_fields__)

    def test_a_thread_the_child_opened_resumes_after_angelo_restarts(self):
        self.hosted[A] = chat()
        child = self.launch(A)
        self.send(child, {"id": 7, "method": "thread/start", "params": {}})
        child.filter.outbound(json.dumps({"id": 7, "result": {"thread": {"id": "the-chats-thread"}}}))
        self.record = runtime_threads.ThreadRecord(self.folder)   # A new process reads the file again.
        restarted = self.launch(A, self.make())
        self.assertEqual(self.resumed(restarted, "the-chats-thread"), "the-chats-thread")
        self.hosted[B] = chat()   # Another chat of the same connection: not its thread.
        self.assertNotEqual(self.resumed(self.launch(B, self.make()), "the-chats-thread"), "the-chats-thread")

    def test_a_warm_up_base_serves_every_chat_of_its_connection_only(self):
        self.hosted[A] = chat()
        child = self.launch(A)
        self.send(child, {"id": 8, "method": "thread/start", "params": {}})
        child.filter.outbound(json.dumps({"id": 8, "result": {"thread": {"id": "base-thread"}}}))
        self.assertTrue(self.send(child, {"id": 9, "method": "turn/start", "params": {
            "threadId": "base-thread", "input": [{"type": "text", "text": codex_bases.WARM_UP}]}}).passed)
        self.hosted[B] = chat()
        self.assertEqual(self.resumed(self.launch(B, self.make()), "base-thread"), "base-thread")
        self.hosted[B] = chat(connection="conn-b")
        self.assertNotEqual(self.resumed(self.launch(B, self.make()), "base-thread"), "base-thread")

    def test_a_claude_resume_names_only_a_session_angelo_saw(self):
        from angelo_agent import claude_transport
        from angelo_agent.chat_settings import CLAUDE_EFFORTS
        self.hosted[A] = chat(mode="claude_subscription")
        flt = runtime_filter.Filter("claude", WS, opened=lambda thread: self.record.opened(A, thread))
        flt.outbound(json.dumps({"type": "system", "subtype": "init", "session_id": "seen-session"}))

        def command(resume):
            values = {"tools": "", "direct": False, "fast": False, "model": "", "effort": sorted(CLAUDE_EFFORTS)[0],
                      "thinking": False, "resume": resume}
            arguments = claude_transport.claude_arguments("", values)[1:]
            with patch("angelo_agent.runtime_launch.child_environment", return_value={}), \
                    patch("angelo_agent.runtime_launch.profile_environment", side_effect=lambda env, *a: env), \
                    patch("angelo_agent.cli_runtime.with_runtime_path", side_effect=lambda env, e: env), \
                    patch("angelo_agent.runtime_environment.session_environment"), \
                    patch("angelo_agent.runtime_environment.require_angelo_profile"), \
                    patch("angelo_agent.claude_transport.check_command_budget"):
                built, _env = self.service._claude({"session": A, "arguments": arguments}, self.hosted[A], {},
                                                   "claude")
            return next(part[len("--resume="):] for part in built if part.startswith("--resume="))
        self.assertEqual(command("seen-session"), "seen-session")
        self.assertNotEqual(command("another-session"), "another-session")


class TurnInputsAreChecked(Case):
    """Finding 3: ``turn/start`` input items and tool-result image URLs were not checked."""

    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp()) / "references" / A
        self.root.mkdir(parents=True)
        self.image = self.root / (str(uuid.uuid4()) + ".png")
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        self.hosted[A] = chat(roots=(str(self.root),))
        self.child = self.launch(A)

    def turn(self, items):
        return self.send(self.child, {"id": 4, "method": "turn/start", "params": {"threadId": "t", "input": items}})

    def test_the_review_poc_is_refused(self):
        self.assertFalse(self.turn([{"type": "localImage", "path": "/outside/the/chat/references.png"},
                                    {"type": "some-other-item", "path": "/elsewhere"}]).passed)
        self.assertFalse(self.turn([{"type": "some-other-item", "path": "/elsewhere"}]).passed)
        self.assertFalse(self.turn([{"type": "localImage", "path": str(self.root / ".." / "x.png")}]).passed)

    def test_a_link_inside_the_references_to_a_file_outside_is_refused(self):
        outside = Path(tempfile.mkdtemp()) / "secret.png"
        outside.write_bytes(b"x")
        link = self.root / "link.png"
        os.symlink(outside, link)
        self.assertFalse(self.turn([{"type": "localImage", "path": str(link)}]).passed)

    def test_the_providers_own_inputs_pass(self):
        store = references.ReferenceStore(self.root.parent.parent, A)
        items = store.codex_inputs([{"path": self.image.name, "mime": "image/png"}])
        self.assertTrue(self.turn([{"type": "text", "text": "Make it red"}] + items).passed)

    def test_a_tool_result_carries_inline_pictures_only(self):
        store = references.ReferenceStore(self.root.parent.parent, A)
        honest = references.codex_result_images(SimpleNamespace(references=SimpleNamespace(
            tool_images=lambda result: [{"path": self.image.name, "mime": "image/png"}],
            checked_path=store.checked_path)), {})
        for number, (url, fits) in enumerate(((honest[0]["imageUrl"], True), ("file:///etc/passwd", False),
                                              ("https://example.com/a.png", False))):
            with self.subTest(url=url[:20]):
                self.child.filter.outbound(json.dumps({"id": number, "method": "item/tool/call", "params": {}}))
                verdict = self.send(self.child, {"id": number, "result": {"success": True, "contentItems": [
                    {"type": "inputImage", "imageUrl": url}]}})
                self.assertEqual(verdict.passed, fits)

    def test_a_claude_user_message_is_text_and_base64_images(self):
        flt = runtime_filter.Filter("claude", WS)
        honest = {"type": "user", "session_id": "", "parent_tool_use_id": None, "message": {"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA"}}]}}
        self.assertTrue(flt.inbound(json.dumps(honest)).passed)
        for block in ({"type": "image", "source": {"type": "url", "url": "file:///etc/passwd"}},
                      {"type": "document", "source": {"type": "file", "path": "/x"}}):
            message = dict(honest, message={"role": "user", "content": [block]})
            self.assertFalse(flt.inbound(json.dumps(message)).passed)


class LaunchChurnIsBounded(Case):
    """S4: launch-and-kill churn per chat was unbounded."""

    def test_a_chat_beyond_its_burst_is_refused_until_the_window_passes(self):
        now = [0.0]
        service = self.make(churn=runtime_bindings.Churn(clock=lambda: now[0]))
        self.hosted[A] = chat()
        for _ in range(runtime_bindings.LAUNCH_BURST):
            self.launch(A, service)
        with self.assertRaisesRegex(RpcError, "too many model runtimes"):
            self.launch(A, service)
        now[0] += runtime_bindings.LAUNCH_WINDOW
        self.launch(A, service)


class ClaudeToolNamesAreBounded(unittest.TestCase):
    """S5: the worker chose the tool-name set, and an unknown name raised a KeyError."""

    def initialize(self, names):
        from angelo_agent import claude_transport
        schema = {"properties": {"operations": {"items": {"oneOf": [
            {"properties": {"tool": {"const": name}}} for name in names]}}}}
        return dict(claude_transport.initialization_request({}, "prompt")["request"], jsonSchema=schema)

    def test_an_unknown_name_is_a_clean_refusal(self):
        self.assertFalse(runtime_filter.built_initialize(self.initialize(["not_an_angelo_tool"])))

    def test_a_huge_set_is_refused_before_any_schema_is_built(self):
        with patch("angelo_agent.tool_specs.native_plan_schema") as build:
            self.assertFalse(runtime_filter.built_initialize(self.initialize("t%d" % n for n in range(100000))))
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
