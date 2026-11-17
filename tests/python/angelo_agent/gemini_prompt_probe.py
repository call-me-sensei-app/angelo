# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a Gemini thread of Angelo's sends through the agent runtime's Gemini wire: request bytes, unbilled.

Drives ``codex app-server`` with Angelo's launch for the Gemini API connection (``runtime_launch.codex_command``,
``team_runtime.model_provider_entry``) against a local fake Gemini endpoint (the provider's ``base_url`` pointed at
``http://127.0.0.1``), with Angelo's instructions and dynamic tools for a third-party provider. Nothing is billed and
no credential is read: ``CODEX_HOME`` and ``HOME`` are fresh folders under the output directory and the key is a
dummy. Two chats run two turns each; the fake model calls one dynamic tool (with a thought signature) in the first.

Checks written to ``summary.json`` (exit 1 if any fails):

* every request of both chats sends the same ``systemInstruction`` and ``tools`` bytes (Gemini's implicit cache is
  prefix-based), and neither holds a date, a time or a per-chat path;
* the key travels only in ``x-goog-api-key`` (no ``Authorization`` header, nothing in the URL);
* the model step after the tool call sends the model's parts back with the thought signature verbatim, and the tool's
  result as a ``functionResponse`` of that function;
* ``thread/tokenUsage/updated`` reports the fake's cached and thinking tokens.

Usage (the wire's runtime build is named by ``--codex``)::

    python3.13 gemini_prompt_probe.py OUT_DIR --codex <codex with the Gemini wire>
"""

import argparse
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import agent_runtime  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402
from angelo_agent.providers import codex_instructions  # noqa: E402
from angelo_agent.runtime_launch import codex_command  # noqa: E402
from angelo_agent.tool_specs import codex_tools  # noqa: E402

MODE, MODEL, KEY = "gemini_api", "gemini-4-carbon", "probe-key-not-real"
SIGNATURE = "c2lnbmF0dXJlLW9mLXRoZS1maXJzdC1zdGVw"   # Opaque bytes the model returns and must get back.
USAGE = {"promptTokenCount": 1200, "cachedContentTokenCount": 800, "candidatesTokenCount": 20,
         "thoughtsTokenCount": 40, "totalTokenCount": 1260}
DATE_OR_TIME = re.compile(r"\b20\d\d-\d\d-\d\d\b|\b\d\d:\d\d(:\d\d)?\b")


def chunk(parts, finish=None, usage=None):
    candidate = {"content": {"role": "model", "parts": parts}}
    if finish:
        candidate["finishReason"] = finish
    event = {"candidates": [candidate], "modelVersion": MODEL}
    if usage:
        event["usageMetadata"] = usage
    return "data: %s\r\n\r\n" % json.dumps(event)


class FakeGemini:
    """streamGenerateContent with a fixed script; keeps every request's body, path and header names."""

    def __init__(self):
        self.requests, probe = [], self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                probe.requests.append({"path": self.path, "body": body,
                                       "headers": sorted(name.lower() for name in self.headers.keys()),
                                       "key": self.headers.get("x-goog-api-key")})
                stream = probe.answer(body)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(stream.encode())

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, body):
        """The first step of a chat's first turn calls a tool; every other step answers in text."""
        contents = body.get("contents") or []
        has_tool_result = any("functionResponse" in part for c in contents for part in c.get("parts", []))
        users = [c for c in contents if c.get("role") == "user"
                 and any("text" in part for part in c.get("parts", []))]
        if len(users) == 1 and not has_tool_result:
            return (chunk([{"text": "Looking at the scene.", "thought": True}])
                    + chunk([{"functionCall": {"name": "angelo_scene_search", "args": {"query": "Cube"}},
                              "thoughtSignature": SIGNATURE}], "STOP", USAGE))
        return chunk([{"text": "Done."}], "STOP", USAGE)


def run_chat(command, env, cwd, base, tools, turns):
    events = []

    def on_request(identity, method, params):
        if method == "item/tool/call":
            rpc.respond(identity, {"success": True, "contentItems": [{"type": "inputText", "text": '{"objects":["Cube"]}'}]})
        else:
            rpc.write({"id": identity, "error": {"code": -32601, "message": "not handled by the probe"}})

    rpc = JsonRpcProcess(command, env=env, cwd=cwd, on_event=lambda method, params: events.append((method, params)),
                         on_request=on_request)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        thread = rpc.request("thread/start", {"cwd": cwd, "modelProvider": MODE, "sandbox": "read-only",
                                              "approvalPolicy": "on-request", "serviceTier": "default", "model": MODEL,
                                              "baseInstructions": base, "dynamicTools": tools})["thread"]["id"]
        for text in turns:
            seen = len(events)
            rpc.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}],
                                       "effort": "medium", "model": MODEL})
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and not any(m == "turn/completed" for m, _ in events[seen:]):
                time.sleep(0.05)
    finally:
        rpc.close()
    return events


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--codex", required=True, help="the agent runtime build with the Gemini wire")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    fake = FakeGemini()
    summary = {"codex": args.codex, "checks": {}}
    with patch.dict(agent_runtime.PIN, {"wire_apis": list(agent_runtime.PIN.get("wire_apis", ())) + ["gemini"]}), \
            patch.dict(os.environ, {"ANGELO_AGENT_RUNTIME": args.codex}):
        chats = []
        for index in range(2):
            folder = out / ("chat-%d" % index)
            for name in ("state", "home", "workspace"):
                (folder / name).mkdir(parents=True, exist_ok=True)
            command, env = codex_command(args.codex, MODE, str(folder / "state"), True, api_key=KEY)
            command += ["-c", 'model_providers.%s.base_url="http://127.0.0.1:%d/v1beta"' % (MODE, fake.port)]
            env.update(HOME=str(folder / "home"))
            base = codex_instructions(short_identifiers=True, deferred=False, team=False, plan=False)
            tools = codex_tools(None, short_identifiers=True, defer=False)
            chats.append(run_chat(command, env, str(folder / "workspace"), base, tools,
                                  ["How many cubes are in the scene?", "Thanks."]))
    requests = fake.requests
    (out / "requests.json").write_text(json.dumps(requests, indent=1))
    check = summary["checks"]
    openings = {json.dumps([r["body"].get("systemInstruction"), r["body"].get("tools")], sort_keys=True) for r in requests}
    check["requests_seen"] = len(requests) >= 6
    check["one_opening_for_every_request_of_both_chats"] = len(openings) == 1
    opening = next(iter(openings)) if openings else ""
    check["no_date_or_time_in_the_opening"] = not DATE_OR_TIME.search(opening)
    check["no_per_chat_path_in_the_opening"] = str(out) not in opening and "chat-0" not in opening
    check["key_only_in_its_header"] = all(r["key"] == KEY and "authorization" not in r["headers"]
                                          and KEY not in r["path"] for r in requests)
    check["streams_on_the_model_path"] = all(r["path"].startswith("/v1beta/models/%s:streamGenerateContent" % MODEL)
                                             for r in requests)
    after_tool = next((r["body"] for r in requests if any("functionResponse" in p for c in r["body"].get("contents", [])
                                                         for p in c.get("parts", []))), {})
    parts = [p for c in after_tool.get("contents", []) for p in c.get("parts", [])]
    check["thought_signature_sent_back_verbatim"] = any(p.get("thoughtSignature") == SIGNATURE for p in parts)
    check["tool_result_is_a_function_response"] = any(
        (p.get("functionResponse") or {}).get("name") == "angelo_scene_search" for p in parts)
    usage = [params for events in chats for method, params in events if method == "thread/tokenUsage/updated"]
    last = [(u.get("tokenUsage") or {}).get("last") or {} for u in usage]
    check["usage_reports_cached_and_thinking_tokens"] = any(
        item.get("cachedInputTokens") == 800 and item.get("reasoningOutputTokens") == 40 for item in last)
    check["every_turn_completed"] = sum(1 for events in chats for m, _ in events if m == "turn/completed") == 4
    summary["success"] = all(check.values())
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
