# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which gateway paths the coding runtimes call for a team connection: unbilled.

Launches the installed Codex (``codex app-server``) and Claude Code (``claude
-p``) exactly as a team connection does (``providers.codex_command`` for
``codex_team``; ``child_environment``, ``profile_environment`` and
``claude_session`` with its stdin initialization for ``claude_team``), but with
``ANGELO_GATEWAY_URL`` pointing at a local recorder laid out like the Angelo
gateway (``/v1/<provider>/<path>``). The recorder answers the model calls with
a minimal valid stream and every other path with the gateway's own 404
``NOT_FOUND``, records method, path and which credential header arrived (never
its value; the key is a dummy), and checks every request against the
gateway's allow-list (copied below). Codex also runs ``thread/compact/start`` to show where compaction goes.

Nothing reaches a provider or the real gateway; ``HOME``, ``CODEX_HOME`` and
``CLAUDE_CONFIG_DIR`` are fresh folders under the output directory. Usage::

    python3.13 team_gateway_probe.py OUT_DIR [--runtime codex|claude|both]

Findings are recorded in doc/angelo/ai_providers.rst ("Gateway compatibility").
"""

import argparse
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import managed_policy, runtime_environment, team_connections  # noqa: E402
from angelo_agent.chat_settings import clean_options  # noqa: E402
from angelo_agent.cli_runtime import CODEX_BUNDLE  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402
from angelo_agent.providers import (child_environment, claude_session, codex_command, codex_instructions,  # noqa: E402
                                    find_executable, profile_environment)
from angelo_agent.tool_specs import codex_tools  # noqa: E402

DUMMY_KEY = "agk_" + "p" * 43   # Shaped like a grant key; never valid anywhere.
_ID, _MODEL = "[A-Za-z0-9._:-]{1,200}", "[A-Za-z0-9._-]{1,200}"
# The gateway's allow-list for the two coding providers, as the console gateway defines it.
ALLOWED = {
    "openai": [("POST", "/v1/responses"), ("POST", "/v1/chat/completions"), ("GET", "/v1/models"),
               ("GET", "/v1/models/%s" % _ID), ("POST", "/v1/images/generations"), ("POST", "/v1/images/edits")],
    "anthropic": [("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens"), ("GET", "/v1/models"),
                  ("GET", "/v1/models/%s" % _ID)],
}
UNSAFE = re.compile(r"%|\\|//|/\.\.?(/|$)")


def allowed(method, path):
    match = re.fullmatch(r"/v1/([a-z]+)(/.*)", path.split("?", 1)[0])
    if not match or UNSAFE.search(path.split("?", 1)[0]):
        return False
    return any(method == m and re.fullmatch(p, match.group(2)) for m, p in ALLOWED.get(match.group(1), ()))


def _sse(events, named=True):
    return "".join(("event: %s\n" % event["type"] if named else "") + "data: %s\n\n" % json.dumps(event)
                   for event in events).encode()


def openai_stream(index):
    item = {"type": "message", "role": "assistant", "id": "msg_%d" % index,
            "content": [{"type": "output_text", "text": "OK", "annotations": []}]}
    return _sse([{"type": "response.created", "response": {"id": "resp_%d" % index}},
                 {"type": "response.output_item.done", "output_index": 0, "item": item},
                 {"type": "response.completed", "response": {"id": "resp_%d" % index, "usage": {
                     "input_tokens": 100, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 2,
                     "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 102}}}])


def anthropic_stream(index, model):
    message = {"id": "msg_%d" % index, "type": "message", "role": "assistant", "model": model, "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1}}
    return _sse([{"type": "message_start", "message": message},
                 {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                 {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}},
                 {"type": "content_block_stop", "index": 0},
                 {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                  "usage": {"output_tokens": 2}},
                 {"type": "message_stop"}])


class FakeGateway:
    def __init__(self):
        self.requests = []
        gateway = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _reply(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                credential = ("x-api-key" if self.headers.get("x-api-key") else
                              "authorization" if self.headers.get("authorization") else "none")
                path = self.path
                ok = allowed(method, path)
                gateway.requests.append({"method": method, "path": path, "allowed": ok, "credential": credential,
                                         "upgrade": self.headers.get("upgrade", "")})
                index = len(gateway.requests)
                bare = path.split("?", 1)[0]
                if not ok:
                    error = {"type": "error", "error": {"type": "not_found_error", "code": "NOT_FOUND",
                                                        "message": "This path is not allowed."}}
                    return self._reply(404, json.dumps(error).encode())
                if bare == "/v1/openai/v1/responses":
                    return self._reply(200, openai_stream(index), "text/event-stream")
                if bare.startswith("/v1/openai/v1/models"):
                    return self._reply(200, json.dumps({"object": "list", "data": [
                        {"id": "gpt-6.1-sol", "object": "model"}]}).encode())
                if bare == "/v1/anthropic/v1/messages":
                    body = json.loads(raw or b"{}")
                    if body.get("stream"):
                        return self._reply(200, anthropic_stream(index, body.get("model", "")), "text/event-stream")
                    return self._reply(200, json.dumps({"id": "msg_%d" % index, "type": "message", "role": "assistant",
                                                        "model": body.get("model", ""), "stop_reason": "end_turn",
                                                        "content": [{"type": "text", "text": "OK"}],
                                                        "usage": {"input_tokens": 10, "output_tokens": 1}}).encode())
                if bare == "/v1/anthropic/v1/messages/count_tokens":
                    return self._reply(200, b'{"input_tokens": 10}')
                return self._reply(200, b'{"data": [], "has_more": false}')

            def do_GET(self):  # noqa: N802
                self._handle("GET")

            def do_POST(self):  # noqa: N802
                self._handle("POST")

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def isolated(env, out, extra=()):
    keep = {key: value for key, value in env.items()
            if key in ("PATH", "USER", "LANG", "TMPDIR", "CODEX_HOME", "CLAUDE_CONFIG_DIR") + tuple(extra)
            or key.startswith(("ANTHROPIC_", "ANGELO_TEAM"))}
    return dict(keep, HOME=str(out / "home"))


def probe_codex(out, gateway):
    executable = find_executable("codex", CODEX_BUNDLE)  # The ChatGPT app's codex first where it exists (macOS).
    command, env = codex_command(executable, "codex_team", str(out / "state"), reuse_login=False, api_key=DUMMY_KEY)
    rpc, events = None, []
    rpc = JsonRpcProcess(command, env=isolated(env, out, ("ANGELO_TEAM_GRANT_KEY",)), cwd=str(out / "workspace"),
                         on_event=lambda method, params: events.append(method),
                         on_request=lambda identity, method, params: rpc.write(
                             {"id": identity, "error": {"code": -32601, "message": "not handled"}}))
    outcome = {"command_base_url": next(c for c in command if "base_url" in c)}
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        thread = rpc.request("thread/start", {
            "cwd": str(out / "workspace"), "modelProvider": "codex_team", "sandbox": "read-only",
            "approvalPolicy": "on-request", "serviceTier": "default", "model": "gpt-6.1-sol",
            "baseInstructions": codex_instructions(short_identifiers=True, deferred=True),
            "dynamicTools": codex_tools(short_identifiers=True)})["thread"]["id"]
        for text in ("What is in the scene?", "Thanks."):
            seen = len(events)
            rpc.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}],
                                       "effort": "low", "model": "gpt-6.1-sol"})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and "turn/completed" not in events[seen:]:
                time.sleep(0.05)
        seen = len(events)
        try:
            rpc.request("thread/compact/start", {"threadId": thread})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not any("compact" in e or e == "turn/completed" for e in events[seen:]):
                time.sleep(0.05)
            time.sleep(1.0)
            outcome["compact"] = [e for e in events[seen:] if "compact" in e or "turn" in e][:8]
        except Exception as error:  # noqa: BLE001 - recorded, not fatal.
            outcome["compact"] = "thread/compact/start failed: %s" % str(error)[:200]
    finally:
        rpc.process.terminate()
    outcome["stderr_tail"] = rpc.stderr_tail[-5:]
    return outcome


def probe_claude(out, gateway):
    executable = find_executable("claude")
    identity = team_connections.connection_id("https://console.angelo.studio", "org-probe", "user-probe", "claude_team")
    context = SimpleNamespace(turn_options=clean_options({"model": "claude-opus-5-5", "effort": "low"}),
                              state={"provider": "claude_team", "options": {}}, allowed_tools=None, prompt_cache_ttl="1h")
    env = profile_environment(child_environment("claude_team", DUMMY_KEY), "claude_team", str(out / "state"), False, identity)
    env = runtime_environment.session_environment(env, context)
    runtime_environment.require_angelo_profile("claude_team", env, identity)  # As ClaudeProvider._start.
    command, initialize = claude_session(executable, context)
    session = tuple(runtime_environment.SESSION_CONTEXT) + ("CLAUDE_CODE_PROMPT_CACHE_TTL",)
    process = subprocess.Popen(command, env=isolated(env, out, session), cwd=str(out / "workspace"), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    message = {"type": "user", "session_id": "", "parent_tool_use_id": None,
               "message": {"role": "user", "content": [{"type": "text", "text": "Say OK."}]}}
    try:
        stdout, stderr = process.communicate(json.dumps(initialize) + "\n" + json.dumps(message) + "\n", timeout=90)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    results = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    final = next((r for r in reversed(results) if r.get("type") == "result"), {})
    init = next((r for r in results if r.get("type") == "system" and r.get("subtype") == "init"), {})
    return {"base_url": env.get("ANTHROPIC_BASE_URL"), "bare": "--bare" in command, "tools": init.get("tools"),
            "api_key_source": init.get("apiKeySource"),
            "result": {k: final.get(k) for k in ("subtype", "is_error", "result")},
            "stderr_tail": stderr.splitlines()[-5:]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--runtime", choices=("codex", "claude", "both"), default="both")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    gateway = FakeGateway()
    os.environ["ANGELO_GATEWAY_URL"] = gateway.url  # team_runtime.base_url reads it; no managed policy is used.
    managed_policy.reset(managed_policy.Policy())
    summary = {}
    for runtime, probe in (("codex", probe_codex), ("claude", probe_claude)):
        if args.runtime in (runtime, "both"):
            start = len(gateway.requests)
            summary[runtime] = probe(out, gateway)
            summary[runtime]["requests"] = gateway.requests[start:]
            summary[runtime]["refused_by_gateway"] = sorted({"%s %s" % (r["method"], r["path"].split("?")[0])
                                                             for r in gateway.requests[start:] if not r["allowed"]})
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
