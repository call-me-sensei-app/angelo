# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local CLI commands: no tool for any client, envelopes, projection, bounds and the client.

No sockets, Blender or provider. Since 2.0.1 every tool call and the tool catalogue are refused for every client
before any work is queued (doc/angelo/external_cli.rst; break-it review S5).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import cli_client
from angelo_agent import external_cli_protocol as protocol
from angelo_agent.external_cli import ClientInfo, Peer
from angelo_agent.external_requests import AuditLog, RateLimiter, Work, WorkQueue, project
from angelo_agent.external_service import NO_TOOL_CALLS, RequestService
from angelo_agent.files import FileWorkspace
from angelo_agent.tool_specs import TOOL_MAP

# A POSIX peer credential; Windows has no uid and no transport yet, but the request logic is the same.
CLIENT = ClientInfo("tester", Peer(os.geteuid() if hasattr(os, "geteuid") else 0, os.getpid(), True))
SHORT_ROOT = "/tmp" if os.name != "nt" else None   # macOS sun_path is short; Windows uses its temp folder.
SNAPSHOT = {"document": "a" * 64, "conversations": {"tester": "11111111-1111-4111-8111-111111111111"}}


def service(**options):
    return RequestService(lambda: SNAPSHOT, main_thread_timeout=options.pop("main_thread_timeout", 0.3), **options)


def call(tool, arguments=None, **extra):
    return {"command": "call", "args": dict({"tool": tool, "arguments": {} if arguments is None else arguments,
                                             "id": "id-1", "wait": 1}, **extra)}


class NoToolTests(unittest.TestCase):
    def test_every_tool_call_and_the_catalogue_are_refused_for_every_client_before_anything_is_queued(self):
        handler = service()
        app = ClientInfo("claude-code", CLIENT.peer, True)
        for client in (CLIENT, app):
            for name in sorted(TOOL_MAP):
                with self.subTest(client=client.label, tool=name):
                    envelope = handler.handle(call(name), client)
                    self.assertEqual((envelope["code"], envelope["exit"]), ("refused", 4))
                    self.assertEqual(envelope["message"], NO_TOOL_CALLS)
            self.assertEqual(handler.handle({"command": "tools", "args": {}}, client)["code"], "refused")
        self.assertEqual(handler.handle({"command": "tools", "args": {"presentation": "mcp"}}, CLIENT)["code"],
                         "refused")
        self.assertEqual(len(handler.work), 0)
        self.assertIn("through Angelo's own agent", NO_TOOL_CALLS)

    def test_unknown_commands(self):
        handler = service()
        for command in ("watch", "stop", "observe", "view", "approve"):
            self.assertEqual(handler.handle({"command": command, "args": {}}, CLIENT)["code"], "invalid")
        self.assertEqual(handler.handle({"command": "session", "args": {"action": "delete"}}, CLIENT)["code"], "invalid")
        self.assertEqual(handler.handle({"command": "agent", "args": {"action": "send"}}, CLIENT)["code"], "refused",
                         "a label without an app key never reaches Angelo's agent")


class ProjectionTests(unittest.TestCase):
    def test_projection_withholds_private_paths(self):
        roots = [("/Users/alice/Library/state", "<angelo-state>"), ("/Users/alice", "<home>"), ("/", "<root>")]
        value = {"views": [{"path": "/Users/alice/Library/state/refs/v.png", "view": "FRONT"}],
                 "note": "saved in /Users/alice/Library/state/x and /Users/alicea/y",
                 "filepath": "C:\\Users\\alice\\scene.blend", "name": "relative/path", "count": 3}
        projected = project(value, roots)
        self.assertEqual(projected["views"][0]["path"], "<withheld>")
        self.assertEqual(len(projected["views"][0]["view_id"]), 32)
        self.assertEqual(projected["note"], "saved in <angelo-state>/x and /Users/alicea/y")
        self.assertEqual(projected["filepath"], "<withheld>")
        self.assertEqual((projected["name"], projected["count"]), ("relative/path", 3))


class BoundsTests(unittest.TestCase):
    def test_rate_limit_queue_bound_and_abandoned_work(self):
        clock = [0.0]
        limiter = RateLimiter(limit=2, clock=lambda: clock[0])
        self.assertTrue(limiter.allow("a") and limiter.allow("a"))
        self.assertFalse(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))
        clock[0] = 1.5
        self.assertTrue(limiter.allow("a"))
        queue = WorkQueue(size=1)
        first, second = Work("call", CLIENT, {}), Work("call", CLIENT, {})
        self.assertTrue(queue.submit(first))
        self.assertFalse(queue.submit(second))
        self.assertTrue(first.abandon())
        self.assertFalse(first.claim())  # Abandoned work never runs.

    def test_full_queue_and_unresponsive_main_thread_report_busy(self):
        handler = service(work=WorkQueue(size=1))
        self.assertEqual(handler.handle({"command": "status", "args": {}}, CLIENT)["code"], "busy")
        self.assertEqual(len(handler.work), 1)  # The abandoned item is skipped when drained.
        self.assertFalse(handler.work.take().claim())
        handler.work.submit(Work("status", CLIENT, {}))
        self.assertEqual(handler.handle({"command": "status", "args": {}}, CLIENT)["code"], "busy")

    def test_a_claimed_status_completes_with_its_result_projected(self):
        handler = service(main_thread_timeout=2)

        def host():
            work = None
            while work is None:
                work = handler.work.take()
                time.sleep(0.01)
            work.claim()
            work.complete({"code": "ok", "message": "Status", "result": {"path": "/tmp/x"}})
        threading.Thread(target=host).start()
        done = handler.handle({"command": "status", "args": {}}, CLIENT)
        self.assertEqual(done["code"], "ok")
        self.assertEqual(done["result"]["path"], "<withheld>")

    def test_audit_keeps_digests_not_arguments_and_stays_bounded(self):
        root = Path(tempfile.mkdtemp(prefix="scli-", dir=SHORT_ROOT))
        self.addCleanup(shutil.rmtree, root, True)
        audit = AuditLog(root / "audit.jsonl", limit=2048)
        handler = service(audit=audit)
        handler.handle(call("angelo_execute_python", {"description": "secret", "code": "SECRET_CODE"}), CLIENT)
        text = (root / "audit.jsonl").read_text()
        self.assertNotIn("SECRET_CODE", text)
        self.assertEqual(json.loads(text)["code"], "refused")
        if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
            self.assertEqual(os.stat(root / "audit.jsonl").st_mode & 0o777, 0o600)
        for _ in range(100):
            audit.record(label="x", command="call", code="ok")
        self.assertLessEqual((root / "audit.jsonl").stat().st_size, 2048)


class ClientTests(unittest.TestCase):
    def run_main(self, argv, environ=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        original = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout, stderr
        try:
            code = cli_client.main(argv, environ=environ or {})
        finally:
            sys.stdout, sys.stderr = original
        return code, stdout.getvalue(), stderr.getvalue()

    def test_usage_errors_are_invalid_envelopes(self):
        for argv in (["--client", "bad label", "status"], ["call", "angelo_inspect_scene"], ["tools"],
                     ["status", "--call", "x"], ["nope"]):
            with self.subTest(argv=argv):
                code, stdout, _ = self.run_main(argv)
                envelope = json.loads(stdout)
                self.assertEqual((code, envelope["code"]), (2, "invalid"))
                self.assertEqual(stdout.count("\n"), 1)

    def test_missing_endpoint_is_unavailable(self):
        root = Path(tempfile.mkdtemp(prefix="scli-", dir=SHORT_ROOT))
        self.addCleanup(shutil.rmtree, root, True)
        code, stdout, stderr = self.run_main(["--state-dir", str(root), "status"])
        self.assertEqual((code, json.loads(stdout)["code"]), (8, "unavailable"))
        self.assertIn("unavailable", stderr)
        self.assertEqual(cli_client.build_request(cli_client.parser().parse_args(["session", "new"])),
                         {"command": "session", "args": {"action": "new"}})

    def test_a_signal_before_the_answer_is_unavailable(self):
        root = Path(tempfile.mkdtemp(prefix="scli-", dir=SHORT_ROOT))
        self.addCleanup(shutil.rmtree, root, True)
        def interrupted(*args):
            raise KeyboardInterrupt
        original = cli_client.run
        cli_client.run = interrupted
        try:
            code, stdout, _ = self.run_main(["--state-dir", str(root), "status"])
        finally:
            cli_client.run = original
        envelope = json.loads(stdout)
        self.assertEqual((code, envelope["code"]), (8, "unavailable"))

    def test_output_escapes_untrusted_text_in_both_formats(self):
        value = protocol.envelope("tool_failed", "bad \u202e\x1b[2J name", result={"name": "Cube\u202e\x1b"})
        stdout, stderr = io.StringIO(), io.StringIO()
        self.assertEqual(cli_client.emit(value, "json", stdout, stderr), 1)
        self.assertTrue(stdout.getvalue().isascii())
        self.assertNotIn("\x1b", stderr.getvalue())
        stdout = io.StringIO()
        cli_client.emit(value, "text", stdout, io.StringIO())
        self.assertNotIn("\x1b", stdout.getvalue())
        self.assertNotIn("\u202e", stdout.getvalue())

    def test_state_directory_defaults_follow_the_add_on(self):
        home = Path("/home/u")
        self.assertEqual(cli_client.default_state_directory({"ANGELO_STATE_DIR": "/s"}, "linux", home), Path("/s"))
        self.assertEqual(cli_client.default_state_directory({"BLENDER_USER_CONFIG": "/c"}, "linux", home),
                         Path("/c/angelo-agent"))
        series = cli_client.BLENDER_SERIES   # This build's per-version folder (test_native_api_drift pins it).
        self.assertEqual(cli_client.default_state_directory({}, "darwin", home),
                         home / "Library/Application Support/Angelo" / series / "config/angelo-agent")
        self.assertEqual(cli_client.default_state_directory({}, "win32", home),
                         home / "AppData/Roaming/Angelo" / series / "config/angelo-agent")
        self.assertEqual(cli_client.default_state_directory({}, "linux", home),
                         home / ".config/angelo" / series / "config/angelo-agent")


class FileToolBoundaryTests(unittest.TestCase):
    def test_file_tools_cannot_reach_the_endpoint_record(self):
        root = Path(tempfile.mkdtemp(prefix="scli-", dir=SHORT_ROOT))
        self.addCleanup(shutil.rmtree, root, True)
        state = root / "project" / "state"
        (state / "cli").mkdir(parents=True)
        (state / "cli" / "endpoint.json").write_text("{}")
        (state / "workspace").mkdir()
        saved = FileWorkspace(root / "project", excluded=[state])
        with self.assertRaises(ValueError):
            saved.path("state/cli/endpoint.json")
        unsaved = FileWorkspace(state / "workspace", excluded=[state], own=state / "workspace")   # As ui.py has it.
        with self.assertRaises(ValueError):
            unsaved.path("../cli/endpoint.json")
        self.assertEqual(unsaved.path("notes.txt"), (state / "workspace" / "notes.txt").resolve())


if __name__ == "__main__":
    unittest.main()
