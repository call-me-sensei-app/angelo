# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local CLI commands: read-only allowlist, envelopes, projection, bounds and the client.

No sockets, Blender or provider. The allowlist tests prove that every tool
outside the phase-1 read-only set is refused before any work is queued.
"""

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
from angelo_agent.external_requests import (PHASE1_READ_TOOLS, AuditLog, RateLimiter, Work, WorkQueue,
                                             exclusion_reason, project, result_code, tools_catalog)
from angelo_agent.external_service import RequestService
from angelo_agent.files import FileWorkspace
from angelo_agent.routing import TOOLSET_VERSION
from angelo_agent.studio_spec import STUDIO_NAMES
from angelo_agent.tool_specs import COLLABORATOR_TOOLS, TOOL_MAP

# A POSIX peer credential; Windows has no uid and no transport yet, but the request logic is the same.
CLIENT = ClientInfo("tester", Peer(os.geteuid() if hasattr(os, "geteuid") else 0, os.getpid(), True))
SHORT_ROOT = "/tmp" if os.name != "nt" else None   # macOS sun_path is short; Windows uses its temp folder.
SNAPSHOT = {"document": "a" * 64, "conversations": {"tester": "11111111-1111-4111-8111-111111111111"}}


def service(**options):
    return RequestService(lambda: SNAPSHOT, main_thread_timeout=options.pop("main_thread_timeout", 0.3), **options)


def call(tool, arguments=None, **extra):
    return {"command": "call", "args": dict({"tool": tool, "arguments": {} if arguments is None else arguments,
                                             "id": "id-1", "wait": 1}, **extra)}


class AllowlistTests(unittest.TestCase):
    def test_phase_one_allowlist_is_the_designed_read_only_set(self):
        self.assertEqual(len(PHASE1_READ_TOOLS), 13)
        self.assertTrue(PHASE1_READ_TOOLS <= COLLABORATOR_TOOLS)
        for name in PHASE1_READ_TOOLS:
            self.assertTrue(TOOL_MAP[name]["annotations"]["readOnlyHint"], name)
        for excluded in ({"angelo_execute_python", "angelo_delegate", "angelo_memory_update", "angelo_read_reference",
                          "angelo_set_workspace_mode", "angelo_list_files", "angelo_read_file", "angelo_write_file",
                          "angelo_delete_file", "angelo_sculpt_view", "angelo_sculpt_review", "angelo_sculpt_target",
                          "angelo_view"}
                         | STUDIO_NAMES):
            self.assertNotIn(excluded, PHASE1_READ_TOOLS)
        # Read-only image observations wait for `angelo view`, with that reason.
        self.assertIn("angelo view", exclusion_reason("angelo_view"))

    def test_every_other_tool_is_refused_before_anything_is_queued(self):
        handler = service()
        for name in sorted(set(TOOL_MAP) - PHASE1_READ_TOOLS):
            with self.subTest(tool=name):
                envelope = handler.handle(call(name, {"anything": True}), CLIENT)
                self.assertEqual((envelope["code"], envelope["exit"]), ("refused", 4))
                self.assertEqual(envelope["result"], {"excluded": True, "reason": exclusion_reason(name)})
                self.assertEqual(len(handler.work), 0)
        self.assertIn("Python", exclusion_reason("angelo_execute_python"))
        self.assertIn("phase 2", exclusion_reason("angelo_scene_program"))

    def test_unknown_tools_bad_arguments_and_bad_guards_are_invalid_and_unqueued(self):
        handler = service()
        cases = [call("angelo_unknown"), call("angelo_scene_search", {"surprise": 1}),
                 call("angelo_inspect_scene", [], id="x"), call("angelo_inspect_scene", id="bad id!"),
                 call("angelo_inspect_scene", conversation="nope"), call("angelo_inspect_scene", expect_document="ABC"),
                 call("angelo_inspect_scene", wait=1000), call("angelo_inspect_scene", toolset=0)]
        for request in cases:
            with self.subTest(request=request):
                self.assertEqual(handler.handle(request, CLIENT)["code"], "invalid")
        self.assertEqual(len(handler.work), 0)

    def test_deferred_and_unknown_commands(self):
        handler = service()
        for command in ("watch", "stop", "observe", "view"):
            self.assertEqual(handler.handle({"command": command, "args": {}}, CLIENT)["code"], "incompatible")
        self.assertEqual(handler.handle({"command": "approve", "args": {}}, CLIENT)["code"], "invalid")
        self.assertEqual(handler.handle({"command": "session", "args": {"action": "delete"}}, CLIENT)["code"], "invalid")


class CatalogAndEnvelopeTests(unittest.TestCase):
    def test_tools_catalog_lists_allowed_schemas_and_excluded_reasons(self):
        catalog = tools_catalog(include_schema=True)
        self.assertEqual(catalog["toolset_version"], TOOLSET_VERSION)
        self.assertEqual({tool["name"] for tool in catalog["allowed"]}, PHASE1_READ_TOOLS)
        self.assertEqual(len(catalog["excluded"]), len(TOOL_MAP) - 13)
        for tool in catalog["allowed"]:
            self.assertEqual((tool["read_only"], tool["approval"]), (True, "none"))
            self.assertEqual(len(tool["schema_sha256"]), 64)
            self.assertIn("type", tool["inputSchema"])
        self.assertNotIn("inputSchema", tools_catalog()["allowed"][0])
        self.assertEqual(tools_catalog()["allowed"][0]["schema_sha256"], catalog["allowed"][0]["schema_sha256"])
        single = service().handle({"command": "tools", "args": {"tool": "angelo_execute_python"}}, CLIENT)
        self.assertEqual((single["result"]["allowed"], len(single["result"]["excluded"])), ([], 1))
        self.assertEqual(service().handle({"command": "tools", "args": {"tool": "nope"}}, CLIENT)["code"], "invalid")

    def test_result_codes_come_from_host_state_not_text(self):
        self.assertEqual(result_code({"objects": []}), "ok")
        self.assertEqual(result_code({"error": "stale handle"}), "tool_failed")
        self.assertEqual(result_code({"success": False}), "tool_failed")
        self.assertEqual(result_code({"error": "x"}, document_changed=True), "stale")
        self.assertEqual(result_code({"error": "x"}, cancelled=True), "not_applied")
        self.assertEqual(result_code({"error": "x", "expired": True}), "not_applied")
        for uncertain in ({"serialization_failed": True}, {"journal_durable": False}, {"partial_changes_possible": True},
                          {"changes": {"uncertain": True}}):
            self.assertEqual(result_code(uncertain), "uncertain")

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

    def test_full_queue_rate_limit_and_unresponsive_main_thread_report_busy(self):
        handler = service(work=WorkQueue(size=1))
        self.assertEqual(handler.handle(call("angelo_inspect_scene", wait=0.1), CLIENT)["code"], "busy")
        self.assertEqual(len(handler.work), 1)  # The abandoned item is skipped when drained.
        self.assertFalse(handler.work.take().claim())
        handler.work.submit(Work("status", CLIENT, {}))
        self.assertEqual(handler.handle({"command": "status", "args": {}}, CLIENT)["code"], "busy")
        limited = service(limiter=RateLimiter(limit=0))
        self.assertEqual(limited.handle(call("angelo_inspect_scene"), CLIENT)["code"], "busy")
        self.assertEqual(len(limited.work), 0)

    def test_claimed_call_completes_or_reports_pending_without_inventing_a_result(self):
        handler = service(main_thread_timeout=2)

        def host(response, delay):
            work = None
            while work is None:
                work = handler.work.take()
                time.sleep(0.01)
            work.claim()
            work.admitted = True
            time.sleep(delay)
            work.complete(response)
        threading.Thread(target=host, args=({"code": "ok", "message": "Completed", "result": {"path": "/tmp/x"}}, 0)).start()
        done = handler.handle(call("angelo_inspect_scene"), CLIENT)
        self.assertEqual((done["code"], done["call"]["state"]), ("ok", "finished"))
        self.assertEqual(done["result"]["path"], "<withheld>")
        self.assertIn("total_ms", done["timings"])
        threading.Thread(target=host, args=({"code": "ok", "message": "late"}, 0.5)).start()
        pending = handler.handle(call("angelo_inspect_scene", wait=0), CLIENT)
        self.assertEqual((pending["code"], pending["exit"], pending["retry"]), ("pending", 6, "poll"))
        self.assertEqual(pending["call"]["state"], "admitted")
        self.assertIsNone(pending["result"])

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
        for argv in (["--client", "bad label", "status"], ["call", "angelo_inspect_scene", "[1]"],
                     ["call", "angelo_inspect_scene", "{", "--id", "x"], ["status", "--call", "bad id"], ["nope"]):
            with self.subTest(argv=argv):
                code, stdout, _ = self.run_main(argv)
                envelope = json.loads(stdout)
                self.assertEqual((code, envelope["code"]), (2, "invalid"))
                self.assertEqual(stdout.count("\n"), 1)

    def test_missing_endpoint_is_unavailable_and_calls_get_generated_ids(self):
        root = Path(tempfile.mkdtemp(prefix="scli-", dir=SHORT_ROOT))
        self.addCleanup(shutil.rmtree, root, True)
        code, stdout, stderr = self.run_main(["--state-dir", str(root), "status"])
        self.assertEqual((code, json.loads(stdout)["code"]), (8, "unavailable"))
        self.assertIn("unavailable", stderr)
        options = cli_client.parser().parse_args(["call", "angelo_inspect_scene"])
        request = cli_client.build_request(options)
        self.assertTrue(protocol.valid_call_id(request["args"]["id"]))
        self.assertEqual(request["args"]["arguments"], {})

    def test_signal_detaches_with_a_pending_envelope_and_keeps_the_call_id(self):
        root = Path(tempfile.mkdtemp(prefix="scli-", dir=SHORT_ROOT))
        self.addCleanup(shutil.rmtree, root, True)
        def interrupted(*args):
            raise KeyboardInterrupt
        original = cli_client.run
        cli_client.run = interrupted
        try:
            code, stdout, _ = self.run_main(["--state-dir", str(root), "call", "angelo_inspect_scene", "--id", "keep-1"])
        finally:
            cli_client.run = original
        envelope = json.loads(stdout)
        self.assertEqual((code, envelope["code"], envelope["call"]["id"]), (6, "pending", "keep-1"))

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
        self.assertEqual(cli_client.default_state_directory({}, "darwin", home),
                         home / "Library/Application Support/Angelo/5.2/config/angelo-agent")
        self.assertEqual(cli_client.default_state_directory({}, "win32", home),
                         home / "AppData/Roaming/Angelo/5.2/config/angelo-agent")
        self.assertEqual(cli_client.default_state_directory({}, "linux", home),
                         home / ".config/angelo/5.2/config/angelo-agent")


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
        unsaved = FileWorkspace(state / "workspace", excluded=[state])
        with self.assertRaises(ValueError):
            unsaved.path("../cli/endpoint.json")


if __name__ == "__main__":
    unittest.main()
