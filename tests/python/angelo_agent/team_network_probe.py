# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled check that a team coding agent opens no connection but its gateway: real Claude Code and Codex.

Usage, from the repository root (no account, key or network service is used; nothing leaves 127.0.0.1):

    python3 tests/python/angelo_agent/team_network_probe.py OUT [--claude PATH] [--codex PATH] [--control]

Each runtime is launched with Angelo's own team environment (``runtime_launch.child_environment``, which applies
``team_network``) and arguments, pointed at a local gateway. The fence's refusing proxy is replaced by a recorder
that refuses the same way and writes down what each connection asked for. A local gateway answers the model and
the WebFetch ticket routes as ``docs/console/gateway.md`` ("Web fetch") specifies. Claude Code is asked to fetch a
page (WebFetch must come through a ticket, its summary through the gateway route); Codex runs one turn.

A run passes when the recorder saw no connection and WebFetch's page came from a ticket. ``--control`` drops the
hook and Claude Code's quiet variable: the recorder must then see Claude Code's direct attempts, which shows it
catches a bypass. Needs ``openssl`` for the local gateway's certificate. Contract: doc/angelo/ai_providers.rst,
"Team connections reach only the gateway".
"""

import argparse
import http.server
import json
import os
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from angelo_agent import team_network  # noqa: E402

GRANT_KEY = "agk_" + "p" * 43   # A made-up key: the local gateway accepts it, nothing else ever sees it.
TICKET = "/v1/web/pages/" + "t" * 40 + "." + "s" * 43
PAGE = b"<html><head><title>Probe Page</title></head><body><h1>Fetched through the gateway</h1></body></html>"


def sse(events):
    return b"".join(b"event: %s\ndata: %s\n\n" % (e["type"].encode(), json.dumps(e).encode()) for e in events)


def message(index, decision, model):
    start = {"type": "message_start", "message": {"id": "msg_%d" % index, "type": "message", "role": "assistant",
             "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
             "usage": {"input_tokens": 10, "output_tokens": 1}}}
    if decision[0] == "tool":
        block = {"type": "tool_use", "id": "toolu_%d" % index, "name": decision[1], "input": {}}
        delta, stop = {"type": "input_json_delta", "partial_json": json.dumps(decision[2])}, "tool_use"
    else:
        block, delta, stop = {"type": "text", "text": ""}, {"type": "text_delta", "text": decision[1]}, "end_turn"
    return [start, {"type": "content_block_start", "index": 0, "content_block": block},
            {"type": "content_block_delta", "index": 0, "delta": delta}, {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
             "usage": {"output_tokens": 5}}, {"type": "message_stop"}]


class Recorder(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """A proxy that refuses every connection, as ``team_network.REFUSING_PROXY`` does, and records it."""

    daemon_threads = True

    def __init__(self):
        self.seen = []
        recorder = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                recorder.seen.append(self.rfile.readline(4096).decode("latin-1").strip())
                while self.rfile.readline(4096) not in (b"\r\n", b"\n", b""):
                    pass
                self.wfile.write(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n")

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server_address[1]


class Gateway(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """The gateway's Anthropic route and web-fetch routes, recording each request."""

    daemon_threads = True

    def __init__(self, out):
        self.calls, self.count = [], 0
        gateway = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, status, data, kind="application/json"):
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                gateway.calls.append(("GET", self.path.split("?")[0], None))
                if self.path == TICKET:
                    return self.send(200, PAGE, "text/html; charset=utf-8")
                self.send(200, b'{"data": [], "has_more": false}')

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                key = self.headers.get("x-api-key") == GRANT_KEY
                gateway.calls.append(("POST", self.path.split("?")[0], key))
                if self.path == "/v1/web/tickets":
                    return self.send(200, json.dumps({"path": TICKET}).encode()) if key else self.send(401, b"{}")
                if "count_tokens" in self.path:
                    return self.send(200, b'{"input_tokens": 5}')
                body = json.loads(raw or b"{}")
                gateway.count += 1
                (out / ("claude-request-%02d.json" % gateway.count)).write_bytes(raw)
                text = json.dumps(body.get("messages"))
                if "PROBE-FETCH" in text and "tool_result" in text:
                    decision = ("text", "Done.")
                elif "PROBE-FETCH" in text:
                    decision = ("tool", "WebFetch", {"url": "https://example.com/article", "prompt": "The title?"})
                elif "Fetched through the gateway" in text:
                    decision = ("text", "Summary: Probe Page")   # WebFetch's summary call, carrying the page.
                else:
                    decision = ("text", "OK")
                self.send(200, sse(message(gateway.count, decision, body.get("model") or "claude-opus-5-5")),
                          "text/event-stream")

        super().__init__(("127.0.0.1", 0), Handler)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(out / "gateway-cert.pem", out / "gateway-key.pem")
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def get_request(self):
        """Plain http and TLS on one port: a loopback gateway origin is http (``team_endpoints``), and WebFetch
        upgrades the ticket URL to https, as it does every URL."""
        connection, address = self.socket.accept()
        if connection.recv(1, socket.MSG_PEEK) == b"\x16":   # A TLS handshake's first byte.
            connection = self.context.wrap_socket(connection, server_side=True)
        return connection, address

    @property
    def origin(self):
        return "http://127.0.0.1:%d" % self.server_address[1]


def certificate(out):
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(out / "gateway-key.pem"),
                    "-out", str(out / "gateway-cert.pem")], check=True, capture_output=True)
    (out / "policy.json").write_text(json.dumps({"ExtraCACertificates": str(out / "gateway-cert.pem")}))


def recorded(env, recorder):
    """The child's environment with the recorder in place of the refusing proxy (same refusal, plus a record)."""
    return {name: recorder.url if value == team_network.REFUSING_PROXY else value for name, value in env.items()}


def team_environment(mode, gateway_origin, out):
    """Angelo's own team child environment for ``mode`` on ``gateway_origin``, with a fresh profile."""
    from angelo_agent import managed_policy
    from angelo_agent.runtime_launch import child_environment
    saved = dict(os.environ)
    try:
        for name in [n for n in os.environ if "PROXY" in n.upper() or n.upper().startswith(("ANTHROPIC", "CLAUDE"))]:
            del os.environ[name]
        os.environ.update(ANGELO_GATEWAY_URL=gateway_origin, ANGELO_POLICY_FILE=str(out / "policy.json"))
        managed_policy.reset()   # Read the probe's policy file (its gateway certificate) now.
        env = child_environment(mode, GRANT_KEY)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    home = out / ("profile-" + mode)
    home.mkdir(exist_ok=True)
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(home), ANGELO_POLICY_FILE=str(out / "policy.json"))
    return env


def run_claude(executable, out, control):
    from angelo_agent.claude_transport import claude_arguments, initialization_request
    recorder, gateway = Recorder(), Gateway(out)
    env = recorded(team_environment("claude_team", gateway.origin, out), recorder)
    values = {"tools": "WebSearch,WebFetch", "direct": False, "fast": False, "model": "claude-opus-5-5",
              "effort": "low", "thinking": False, "resume": "", "gateway": not control}
    if control:
        env.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
    command = claude_arguments(executable, values)
    schema = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}
    lines = [initialization_request(schema, "You are a probe."),
             {"type": "user", "message": {"role": "user", "content": "PROBE-FETCH: fetch the article."}}]
    process = subprocess.Popen(command, env=env, cwd=env["HOME"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    process.stdin.write("".join(json.dumps(line) + "\n" for line in lines))
    process.stdin.flush()
    results, deadline = [], time.monotonic() + 180
    for line in process.stdout:
        event = json.loads(line) if line.startswith("{") else {}
        if event.get("type") == "user":
            results += [str(b.get("content"))[:200] for b in event["message"].get("content") or []
                        if isinstance(b, dict) and b.get("type") == "tool_result"]
        if event.get("type") == "result" or time.monotonic() > deadline:
            break
    process.stdin.close()
    process.wait(timeout=30)
    time.sleep(2)   # Late optional requests (telemetry flushes at exit) still reach the recorder.
    ticket = ("POST", "/v1/web/tickets", True) in gateway.calls and ("GET", TICKET, None) in gateway.calls
    report = {"runtime": "claude", "version": subprocess.run([executable, "--version"], capture_output=True,
                                                             text=True).stdout.strip(),
              "control": control, "proxy_saw": recorder.seen, "gateway_calls": gateway.calls,
              "tool_results": results, "webfetch_through_ticket": ticket}
    report["passed"] = (bool(recorder.seen) and not ticket) if control else (not recorder.seen and ticket and
                                                                            any("Probe Page" in r for r in results))
    return report


def run_codex(executable, out):
    from angelo_agent.protocol import JsonRpcProcess
    from angelo_agent.runtime_launch import codex_command
    from codex_prompt_probe import FakeResponses, script
    recorder, fake = Recorder(), FakeResponses(out, script("current", "lookup"))
    origin = "http://127.0.0.1:%d" % fake.port   # Plain http is allowed to a loopback gateway.
    env = team_environment("codex_team", origin, out)
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({k: v for k, v in env.items()})
        command, env = codex_command(executable, "codex_team", str(out / "codex-state"), False, None, GRANT_KEY)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    env = recorded(env, recorder)
    events = []
    rpc = JsonRpcProcess(command, env=env, cwd=str(out), on_event=lambda method, params: events.append(method),
                         on_request=lambda i, m, p: rpc.write({"id": i, "error": {"code": -32601, "message": "no"}}))
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        thread = rpc.request("thread/start", {"cwd": str(out), "modelProvider": "codex_team", "sandbox": "read-only",
                                              "approvalPolicy": "on-request", "baseInstructions": "Probe."})
        rpc.request("turn/start", {"threadId": thread["thread"]["id"], "effort": "low",
                                   "input": [{"type": "text", "text": "Hello"}]})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and "turn/completed" not in events:
            time.sleep(.05)
        time.sleep(3)
    finally:
        rpc.close()   # Waits for the exit: Windows keeps an exiting process's files locked (its state database).
    report = {"runtime": "codex", "proxy_saw": recorder.seen, "gateway_requests": len(fake.bodies),
              "completed": "turn/completed" in events}
    report["passed"] = not recorder.seen and report["completed"] and report["gateway_requests"] > 0
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--claude", default="")
    parser.add_argument("--codex", default="")
    parser.add_argument("--control", action="store_true", help="drop the hook: the recorder must see the bypass")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    certificate(out)
    reports = []
    if args.claude:
        reports.append(run_claude(args.claude, out, args.control))
    if args.codex and not args.control:
        reports.append(run_codex(args.codex, out))
    (out / "report.json").write_text(json.dumps(reports, indent=1))
    print(json.dumps(reports, indent=1))
    return 0 if reports and all(report["passed"] for report in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
