# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual unmodified Codex sidecar -> localhost Responses fixture, never billed.

Run explicitly, not in default discovery. All URLs are replaced ONLY inside
this test, keys are synthetic, and profiles/workspaces are new temporary dirs.
This proves native protocol/tool plumbing, not remote credentials or latency.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import providers
from angelo_agent.api_connections import CheckResult
from angelo_agent.chat_settings import clean_options
from angelo_agent.provider_registry import EXTERNAL_CODEX_MODES, SPECS
from angelo_agent.routing import TOOLSET_VERSION

KEY = "fixture-scoped-local-only-not-an-api-key"
CAPTURE = []


class ResponsesFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CAPTURE.append({"body": data, "path": self.path, "auth": self.headers.get("Authorization")})
        n = len(CAPTURE)
        response = {"id": f"resp_local_{n}", "object": "response", "created_at": int(time.time()),
                    "model": data["model"], "status": "in_progress", "output": []}
        # Real dynamic tool dispatch on the first request for each provider;
        # the native runtime, not a custom agent loop, submits the continuation.
        function = not any(i.get("type") == "function_call_output" for i in data.get("input", []) if isinstance(i, dict))
        item = ({"id": f"fc_local_{n}", "type": "function_call", "call_id": f"call_local_{n}",
                 "name": "angelo_inspect_scene", "arguments": "{}", "status": "completed"} if function else
                {"id": f"msg_local_{n}", "type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "Local native provider test complete.", "annotations": []}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        def event(kind, **fields):
            value = {"type": kind, **fields}
            self.wfile.write(("event: " + kind + "\ndata: " + json.dumps(value) + "\n\n").encode())
            self.wfile.flush()
        event("response.created", response=response)
        event("response.output_item.added", output_index=0, item=dict(item, status="in_progress", arguments="") if function else dict(item, content=[]))
        if function:
            event("response.function_call_arguments.delta", item_id=item["id"], output_index=0, delta="{}")
            event("response.function_call_arguments.done", item_id=item["id"], output_index=0, arguments="{}")
        else:
            event("response.content_part.added", item_id=item["id"], output_index=0, content_index=0,
                  part={"type": "output_text", "text": "", "annotations": []})
            event("response.output_text.delta", item_id=item["id"], output_index=0, content_index=0, delta=item["content"][0]["text"])
            event("response.output_text.done", item_id=item["id"], output_index=0, content_index=0, text=item["content"][0]["text"])
        event("response.output_item.done", output_index=0, item=item)
        response.update(status="completed", output=[item], usage={"input_tokens": 20, "output_tokens": 10,
            "total_tokens": 30, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}})
        event("response.completed", response=response)
        self.close_connection = True


class ExternalNativeRuntimeTests(unittest.TestCase):
    def test_native_codex_dispatches_all_three_custom_provider_routes(self):
        executable = providers.find_executable("codex")
        server = ThreadingHTTPServer(("127.0.0.1", 0), ResponsesFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        real_command = providers.codex_command
        evidence = []
        try:
            for mode in sorted(EXTERNAL_CODEX_MODES):
                with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                    model = SPECS[mode]["models"][0]
                    def local_command(*args, **kwargs):
                        command, env = real_command(*args, **kwargs)
                        command = [f'model_providers.{mode}.base_url="http://127.0.0.1:{server.server_port}/v1"'
                                   if value.startswith(f"model_providers.{mode}.base_url=") else value for value in command]
                        return command, env
                    calls, events = [], []
                    def request(call_id, name, arguments, callback, *, delivery_owner=None):
                        calls.append((name, arguments))
                        callback({"success": True, "scene": "SyntheticLocalScene", "objects": []})
                    context = SimpleNamespace(state={"provider": mode, "native_id": None, "toolset_version": TOOLSET_VERSION,
                            "options": clean_options({"model": model["id"], "effort": model["default_effort"]})},
                        store=SimpleNamespace(directory=Path(directory)), workspace=Path(directory), api_key=KEY,
                        models=[model], turn_options=clean_options({"model": model["id"], "effort": model["default_effort"]}),
                        turn_attachments=[], references=SimpleNamespace(codex_inputs=lambda attachments: []), cancelled=False,
                        voice=None, allowed_tools={"angelo_inspect_scene"}, emit=lambda kind, data=None: events.append((kind, data)), request_tool=request)
                    provider = providers.CodexProvider(context, {"codex_path": executable, "reuse_codex_login": False})
                    context.settings = provider.settings
                    begin = len(CAPTURE)
                    with patch("angelo_agent.providers.codex_command", side_effect=local_command), patch(
                            "angelo_agent.providers.check_connection", return_value=CheckResult("verified", "Fixture", True, [model])):
                        try:
                            native_id = provider.connect()
                            status = provider.run(native_id, "Inspect the scene with the supplied native tool, then summarize.")
                            self.assertEqual(status, "completed")
                            self.assertEqual(calls, [("angelo_inspect_scene", {})])
                        finally:
                            provider.close()
                    captures = CAPTURE[begin:]
                    self.assertEqual(len(captures), 2)
                    self.assertTrue(all(c["body"]["model"] == model["id"] and c["auth"] == "Bearer " + KEY for c in captures))
                    self.assertTrue(any(i.get("type") == "function_call_output" for i in captures[-1]["body"]["input"]))
                    self.assertTrue(any(kind == "message_complete" for kind, _ in events))
                    self.assertTrue(all(t["type"] in {"function", "web_search"} for t in captures[0]["body"].get("tools", [])))
                    self.assertFalse(any(t.get("name") in {"spawn_agent", "view_image", "exec_command", "create_goal"}
                                         for t in captures[0]["body"].get("tools", [])))
                    evidence.append({"mode": mode, "model": model["id"], "native_id_created": bool(native_id),
                        "requests": len(captures), "tool_dispatch": calls[0][0], "service_tier": captures[0]["body"].get("service_tier"),
                        "tools": [{"type": t.get("type"), "name": t.get("name"),
                                   "functions": [f.get("name") for f in t.get("tools", [])]}
                                  for t in captures[0]["body"].get("tools", [])]})
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(len(evidence), len(EXTERNAL_CODEX_MODES))
        if os.environ.get("ANGELO_PROVIDER_QA_REPORT"):
            Path(os.environ["ANGELO_PROVIDER_QA_REPORT"]).write_text(json.dumps({"success": True,
                "fixture_only": True, "billable_requests": 0, "evidence": evidence}, indent=2))
        print("ANGELO_EXTERNAL_CODEX_LOCAL", json.dumps(evidence))


if __name__ == "__main__":
    unittest.main()
