# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Record the tool layout the runtimes really send to a custom endpoint, for Test connection's drift guard.

A release step (doc/angelo/releasing.rst, "Agent runtime"): run it whenever the
agent runtime pin (``agent_runtime.json``) moves, and commit the fixture it
writes. It launches the runtimes exactly as Angelo launches an organization
endpoint's chat (``runtime_launch.codex_command``, ``child_environment``) against
a local recorder on 127.0.0.1 that answers each request with a short finished
reply, and keeps the first request that offers tools:

* ``responses_code_mode``: Codex with a model mapped to a code-mode profile
  (gpt-6.1-sol: its freeform ``exec`` custom tool, Responses Lite);
* ``responses_plain``: Codex with an unmapped model (Codex's generic metadata);
* ``gemini``: Codex on the Gemini wire with a mapped Gemini model;
* ``messages``: Claude Code with a mapped Claude model.

Each request is reduced to its tool layout (``endpoint_tool_layout``), never its
text, and written to ``fixtures/endpoint_tool_layouts.json`` stamped with the fork
commit the given ``codex`` was built from (``--built-from``), never one it wasn't.
When that is not the pinned commit, ``--fork`` (a checkout of the fork) records the
files that differ between the two; ``test_native_endpoint_tool_layouts.py`` accepts
such a capture only while every one of them is a file that can't change a request. ``test_native_endpoint_tool_layouts.py`` then fails until the
fixture matches the current pin, and holds Test connection's requests to it.
Nothing leaves 127.0.0.1; no key or model is involved.

Usage: python capture_endpoint_tool_layouts.py --codex PATH --built-from SHA [--fork CHECKOUT] [--claude PATH] [--write]
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import suite_isolation  # noqa: E402,F401  No real provider login (provider_isolation.py).
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import endpoint_tool_layout  # noqa: E402
import host_waits  # noqa: E402

FIXTURE = HERE / "fixtures" / "endpoint_tool_layouts.json"
WAIT = 90.0
ENDPOINT_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
recorded = []


class Recorder(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802 - model lists: empty.
        self._send("application/json", json.dumps({"data": [], "models": []}).encode())

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        recorded.append((self.path, body, {k.lower(): v for k, v in self.headers.items()}))
        if self.path.endswith("/responses"):
            done = {"id": "r1", "object": "response", "status": "completed", "model": body.get("model"), "output": [
                {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "done", "annotations": []}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
            events = [("response.created", dict(done, status="in_progress", output=[])), ("response.completed", done)]
            data = "".join("event: %s\ndata: %s\n\n" % (name, json.dumps({"type": name, "response": value}))
                           for name, value in events)
        elif "enerateContent" in self.path:
            data = "data: %s\n\n" % json.dumps({"candidates": [{"content": {"role": "model", "parts": [
                {"text": "done"}]}, "finishReason": "STOP", "index": 0}], "usageMetadata": {
                "promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2}})
        else:
            frames = [{"type": "message_start", "message": {"id": "m1", "type": "message", "role": "assistant",
                                                            "model": body.get("model"), "content": [],
                                                            "usage": {"input_tokens": 1, "output_tokens": 1}}},
                      {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                      {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "done"}},
                      {"type": "content_block_stop", "index": 0},
                      {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
                      {"type": "message_stop"}]
            data = "".join("event: %s\ndata: %s\n\n" % (f["type"], json.dumps(f)) for f in frames)
        self._send("text/event-stream", data.encode())

    def _send(self, kind, data):
        self.send_response(200)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def first_with_tools(since, format_id):
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        for path, body, headers in recorded[since:]:
            if body.get("tools") or any(isinstance(i, dict) and i.get("tools") for i in body.get("input") or ()):
                return endpoint_tool_layout.layout(format_id, body, headers)
        time.sleep(0.1)
    raise SystemExit("no request with tools for %s within %.0f s" % (format_id, WAIT))


def drain(process, send):
    """Read the app-server's messages until it ends, declining any request it makes of the client."""
    for line in process.stdout:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" in message and message.get("method"):
            try:
                send({"id": message["id"], "error": {"code": -32601, "message": "not answered by the capture"}})
            except (OSError, ValueError):
                return


def codex_case(codex, base, format_id, model, profile, work):
    from angelo_agent import custom_endpoints, endpoint_connections
    from angelo_agent.runtime_launch import codex_command
    path = {"openai-responses": "/v1", "gemini": "/gemini/v1beta"}[format_id]
    entry = {"id": ENDPOINT_ID, "name": "Layout capture", "baseUrl": base, "formats": {format_id: {"path": path}},
             "credential": "none", "models": [dict({"id": model, "format": format_id},
                                                  **({"profile": profile} if profile else {}))]}
    endpoints, _ = custom_endpoints.endpoints([entry], "policy")
    endpoint_connections.set_current(endpoints, True)
    mode = endpoint_connections.mode_for(endpoints[0].id, format_id)
    command, env = codex_command(codex, mode, tempfile.mkdtemp(dir=work), reuse_login=False,
                                 connection_id=endpoint_connections.connection_id("capture", "org", "user", mode))
    since = len(recorded)
    process = subprocess.Popen(command, env=env, cwd=str(work), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def send(value):
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()

    def answer(identity):
        while True:
            message = json.loads(process.stdout.readline() or "{}")
            if not message:
                raise SystemExit("the app-server ended before answering")
            if message.get("id") == identity and "method" not in message:
                return message.get("result") or {}
            if "id" in message and message.get("method"):
                send({"id": message["id"], "error": {"code": -32601, "message": "not answered by the capture"}})
    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "angelo_blender", "title": "Angelo",
                                                                        "version": "0.1.0"},
                                                         "capabilities": {"experimentalApi": True}}})
        answer(1)
        send({"method": "initialized"})
        send({"id": 2, "method": "thread/start", "params": {
            "cwd": str(work), "modelProvider": mode, "model": model, "sandbox": "read-only",
            "approvalPolicy": "never", "dynamicTools": [{"name": "angelo_probe", "description": "A probe.",
                                                         "inputSchema": {"type": "object", "properties": {}}}]}})
        thread = answer(2)["thread"]["id"]
        send({"id": 3, "method": "turn/start", "params": {"threadId": thread, "input": [{"type": "text",
                                                                                          "text": "Say done."}]}})
        threading.Thread(target=drain, args=(process, send), daemon=True).start()   # Until it ends.
        return first_with_tools(since, format_id)
    finally:
        process.kill()
        process.wait(host_waits.CAP_S)   # Killed: it ends at once; the cap only bounds a hang.


def claude_case(claude, base, work):
    from angelo_agent import custom_endpoints, endpoint_connections
    from angelo_agent.runtime_launch import child_environment
    entry = {"id": ENDPOINT_ID, "name": "Layout capture", "baseUrl": base,
             "formats": {"anthropic-messages": {"path": ""}}, "credential": "none",
             "models": [{"id": "team-claude", "format": "anthropic-messages", "profile": "claude-sonnet-5-5"}]}
    endpoints, _ = custom_endpoints.endpoints([entry], "policy")
    endpoint_connections.set_current(endpoints, True)
    mode = endpoint_connections.mode_for(endpoints[0].id, "anthropic-messages")
    since = len(recorded)
    env = dict(child_environment(mode, ""), PATH=os.environ.get("PATH", "/usr/bin:/bin"), HOME=str(work))
    settings = json.dumps({"modelOverrides": endpoint_connections.claude_model_overrides(mode)})
    subprocess.run([claude, "-p", "Read the file probe.txt.", "--model", "team-claude", "--settings", settings,
                    "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--tools=Read"], env=env, cwd=str(work), stdin=subprocess.DEVNULL, capture_output=True,
                   timeout=WAIT)
    return first_with_tools(since, "anthropic-messages")


def full_commit(fork, commit):
    """The full hash of ``commit`` (from ``fork`` when given), so the stamp is never a prefix."""
    if not fork:
        if len(commit) != 40:
            raise SystemExit("--built-from must be a full 40-hex commit without --fork")
        return commit
    return subprocess.run(["git", "-C", fork, "rev-parse", commit + "^{commit}"], check=True, capture_output=True,
                          text=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codex", required=True, help="the agent runtime's codex (the pinned build)")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--write", action="store_true", help="replace the fixture")
    parser.add_argument("--built-from", required=True, help="the fork commit the given codex was built from")
    parser.add_argument("--fork", help="a checkout of the fork, to record how --built-from differs from the pin")
    args = parser.parse_args()
    os.environ["ANGELO_AGENT_RUNTIME"] = args.codex
    from angelo_agent import agent_runtime
    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    work = Path(tempfile.mkdtemp(prefix="endpoint-layouts-"))
    layouts = {"responses_code_mode": codex_case(args.codex, base, "openai-responses", "team-gpt-sol", "gpt-6.1-sol",
                                                 work),
               "responses_plain": codex_case(args.codex, base, "openai-responses", "house-model", None, work),
               "gemini": codex_case(args.codex, base, "gemini", "gemini-4-carbon", "gemini-4-carbon", work),
               "messages": claude_case(args.claude, base, work)}
    version = subprocess.run([args.claude, "--version"], capture_output=True, text=True).stdout.split(" ")[0]
    built, pinned = full_commit(args.fork, args.built_from), agent_runtime.PIN["fork"]["commit"]
    runtime = {"codex_version": agent_runtime.PIN["version"], "codex_commit": built, "claude_code": version}
    if built != pinned:
        if not args.fork:
            raise SystemExit("--built-from is not the pinned commit: give --fork to record how the two differ")
        changed = subprocess.run(["git", "-C", args.fork, "diff", "--name-only", built, pinned], check=True,
                                 capture_output=True, text=True).stdout.split()
        runtime["equivalent_to"] = {"commit": pinned, "changed_files": sorted(changed)}
    fixture = {"runtime": runtime,
               "captured": datetime.date.today().isoformat(), "layouts": layouts}
    print(json.dumps(fixture, indent=1))
    if args.write:
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n")
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
