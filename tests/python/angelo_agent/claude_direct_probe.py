# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The direct-tools route (claude_direct.py) end to end with the real ``claude`` CLI, unbilled.

Launches the real Claude Code with Angelo's provider (``providers.ClaudeProvider``) on both routes against a
local fake Messages endpoint that plays a model: on the direct route it calls ``mcp__angelo__angelo_scene_search``
and then replies; on the plan route it answers with one StructuredOutput plan holding the same lookup. The host's
tool dispatch is a stand-in that answers at once (no Blender). It records every request body and prints, per
route: the opening's parts (tools, system, first message) in bytes, whether two launches open byte-identically,
which tools the model was offered (and any tool Angelo offered that Claude Code did not send on), and what the
model received back. Nothing is billed, no credentials are read:
a dummy key, a fresh ``HOME``/``CLAUDE_CONFIG_DIR`` and non-essential traffic off, as ``claude_prompt_probe``.

Usage::

    python3.13 claude_direct_probe.py OUT_DIR [--claude PATH]
"""

import argparse
import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent.chat_settings import clean_options  # noqa: E402
from angelo_agent.cli_runtime import find_executable  # noqa: E402
from angelo_agent import claude_direct  # noqa: E402
from angelo_agent.providers import ClaudeProvider  # noqa: E402

TOOL = "mcp__angelo__angelo_scene_search"
ROWS = {"success": True, "objects": [], "total": 0, "coverage": "SCENE"}


class Endpoint:
    def __init__(self, out):
        self.out, self.bodies, self.lock = out, [], threading.Lock()
        probe = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self._send("application/json", json.dumps({"data": [], "has_more": False}).encode())

            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                if "count_tokens" in self.path:
                    return self._send("application/json", b'{"input_tokens": 10}')
                events = probe.reply(raw)
                self._send("text/event-stream", b"".join(b"event: %s\ndata: %s\n\n" % (
                    event["type"].encode(), json.dumps(event).encode()) for event in events))

            def _send(self, kind, data):
                self.send_response(200)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reply(self, raw):
        with self.lock:
            index = len(self.bodies)
            self.bodies.append(raw)
        (self.out / ("request-%03d.json" % index)).write_bytes(raw)
        body = json.loads(raw)
        tools = {tool.get("name") for tool in body.get("tools") or []}
        users = [message for message in body.get("messages") or [] if message.get("role") == "user"]
        content = users[-1].get("content") if users else ""
        answered = isinstance(content, list) and any(block.get("type") == "tool_result" for block in content)
        if "Find the bridge" not in json.dumps(body.get("messages")):
            return self._message(index, "text", "OK")   # The CLI's own requests (titles, probes).
        if TOOL in tools and not answered:
            return self._message(index, "tool", (TOOL, {"query": "Bridge"}))
        if "StructuredOutput" in tools and not answered:
            return self._message(index, "tool", ("StructuredOutput", {"operations": [
                {"tool": "angelo_scene_search", "arguments": {"query": "Bridge"}}], "verify": True}))
        if "StructuredOutput" in tools:
            return self._message(index, "tool", ("StructuredOutput", {"reply": "No bridge yet.", "operations": []}))
        return self._message(index, "text", "No bridge yet.")

    @staticmethod
    def _message(index, kind, value):
        start = {"type": "message_start", "message": {
            "id": "msg_probe_%03d" % index, "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}
        if kind == "tool":
            block = {"type": "tool_use", "id": "toolu_probe_%03d" % index, "name": value[0], "input": {}}
            delta, stop = {"type": "input_json_delta", "partial_json": json.dumps(value[1])}, "tool_use"
        else:
            block, delta, stop = {"type": "text", "text": ""}, {"type": "text_delta", "text": value}, "end_turn"
        return [start, {"type": "content_block_start", "index": 0, "content_block": block},
                {"type": "content_block_delta", "index": 0, "delta": delta}, {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 5}},
                {"type": "message_stop"}]


def host(workspace, direct):
    context = SimpleNamespace(cancelled=False, turn_options=clean_options({}), turn_attachments=[],
                              state={"provider": "claude_subscription", "options": {}, "native_id": None},
                              workspace=workspace, instructions="", allowed_tools=None,
                              native_tools=("WebSearch", "WebFetch"), _epoch=1, _request_generation=1,
                              references=SimpleNamespace(claude_inputs=Mock(return_value=[]),
                                                         tool_images=Mock(return_value=[])),
                              events=[], calls=[], claude_direct_tools=direct)

    def emit(kind, data):
        context.events.append((kind, data))
        if kind == "native_session":
            context.state["native_id"] = data["id"]

    def request_tool(call_id, name, arguments, receive):
        context.calls.append((name, arguments))
        receive(dict(ROWS))
    context.emit, context.request_tool = emit, request_tool
    return context


def opening(raw):
    body = json.loads(raw)
    system = body.get("system") or []
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())  # noqa: E731
    return {"tools_bytes": size(body.get("tools") or []), "system_bytes": size(system),
            "first_message_bytes": size((body.get("messages") or [None])[0]),
            "tools": [tool.get("name") for tool in body.get("tools") or []]}


def run(claude, out, direct, attempt):
    folder = out / ("%s-%d" % ("direct" if direct else "plan", attempt))
    (folder / "home").mkdir(parents=True, exist_ok=True)
    endpoint = Endpoint(folder)
    context = host(folder / "home", direct)
    provider = ClaudeProvider(context, {})
    provider.executable = claude
    provider.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(folder / "home"),
                    "CLAUDE_CONFIG_DIR": str(folder / "home/.claude"), "ANTHROPIC_API_KEY": "probe-not-a-secret",
                    "ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % endpoint.server.server_address[1],
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    try:
        status = provider.run_request(context, None, "Find the bridge.", observation={})
    finally:
        provider.close()
        endpoint.server.shutdown()
    first = next(raw for raw in endpoint.bodies if b"Find the bridge" in raw)
    last = [raw for raw in endpoint.bodies if b"Find the bridge" in raw][-1]
    result = [block for message in json.loads(last)["messages"] if isinstance(message.get("content"), list)
              for block in message["content"] if block.get("type") == "tool_result"]
    sent = {name for name in opening(first)["tools"] if name.startswith(claude_direct.PREFIX)}
    offered = {claude_direct.PREFIX + tool["name"] for tool in claude_direct.tool_list()} if direct else set()
    return first, {"status": status, "host_calls": context.calls, "opening": opening(first),
                   "offered_not_sent": sorted(offered - sent),
                   "requests": sum(b"Find the bridge" in raw for raw in endpoint.bodies),
                   "model_received": result[-1] if result else None,
                   "reply": [data for kind, data in context.events if kind == "message_complete"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--claude", default="")
    args = parser.parse_args(argv)
    claude = find_executable("claude", args.claude)
    report = {}
    for direct in (False, True):
        firsts = []
        for attempt in (1, 2):
            first, summary = run(claude, args.out, direct, attempt)
            firsts.append(first)
        body = [json.loads(raw) for raw in firsts]
        summary["opening_identical_across_launches"] = all(
            json.dumps(item.get(key), sort_keys=True) == json.dumps(body[0].get(key), sort_keys=True)
            for item in body for key in ("tools", "system"))
        report["direct" if direct else "plan"] = summary
    print(json.dumps(report, indent=1)[:20000])
    missing = report["direct"]["offered_not_sent"]
    if missing or not all(summary["opening_identical_across_launches"] for summary in report.values()):
        sys.exit("Claude Code did not send every offered tool (%s), or an opening changed between launches"
                 % ", ".join(missing))


if __name__ == "__main__":
    main()
