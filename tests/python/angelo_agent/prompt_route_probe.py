# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Record what the routes no trial covers send, unbilled, for the prompt audit (doc/angelo/prompt_audit.rst).

Every trial so far ran Claude Code (plan) or Codex on an OpenAI connection, so no recording shows what these
routes send: the full Codex text (a third-party provider through Codex), a voice thread, Studio's prompt editor on
either runtime, and Claude Code or Codex as clients of Angelo's MCP server. It also records Claude's plan and
direct-tools routes and a delegated collaborator, so a release can audit every route of its own build without
waiting for a trial of each (``release_checks.py prompt-audit`` requires one recording per release route). Each case runs Angelo's own launch code
(``CodexProvider``, ``studio_enhance``, ``claude_session``, ``angelo_mcp.py``) against a local fake endpoint that
answers "OK", and keeps the first request's body as sent plus the runtime's own transcript or rollout. Nothing is
billed; no credential is read (dummy keys, fresh ``HOME``, ``CLAUDE_CONFIG_DIR`` and ``CODEX_HOME``).

    <Blender>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/prompt_route_probe.py OUT \\
        --codex <Angelo.app>/Contents/Helpers/codex [--claude PATH] [--client-codex PATH] [--only CASE ...]

Then ``python3 scripts/angelo/prompt_audit.py check OUT``. Limits: a voice thread's realtime session prompt
(``voice.py``) goes to the realtime API and is not recorded, only the thread's text requests; ChatGPT sign-in can't
be faked, so its route stays covered by real trials (it is the deferred text, as an OpenAI API key's).
"""

import argparse
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(ROOT / "scripts/angelo"), str(HERE)]
from claude_prompt_probe import DUMMY_KEY, launch_environment  # noqa: E402
from scenario_fake_models import FakeMessages  # noqa: E402
from team_gateway_probe import openai_stream  # noqa: E402

PROMPT = "Reply with OK."
CASES = ("claude-plan", "claude-direct", "collaborator-claude", "codex-full", "voice", "studio-codex", "studio-claude",
         "mcp-claude-code", "mcp-codex")
EXTERNAL = "grok_api"   # Any third-party Codex mode: they share the full text (codex_plan.plan_mode, team_runtime).


# What the fake model runs in an MCP client's first turn: Codex in code mode lists MCP tools only at run time
# (exec's ALL_TOOLS), so the route's received text is what this returns (doc/angelo/prompt_audit.rst).
# Codex prefixes a server's instructions to each of its tools' descriptions (about 5 KB each), and caps exec
# output: the listing names Angelo's tools, states their shared prefix once and each one's own remainder, and
# closes with an end marker the reader requires, so a cut listing fails as truncated, never as a missing fact.
LISTING_END = "prompt-audit-listing-end"
LIST_TOOLS = (
    "const own = ALL_TOOLS.filter((tool) => tool.name.includes('angelo'));\n"
    "let shared = own.length ? own[0].description || '' : '';\n"
    "for (const tool of own) { while (!(tool.description || '').startsWith(shared)) shared = shared.slice(0, -1); }\n"
    "text(JSON.stringify({shared: shared, tools: own.map((tool) => ({name: tool.name, "
    "description: (tool.description || '').slice(shared.length)})), end: %s}));" % json.dumps(LISTING_END))


def tool_call_stream(index, code):
    """A Responses stream whose one output item calls Codex's exec tool with ``code``."""
    from team_gateway_probe import _sse
    item = {"type": "custom_tool_call", "id": "ctc_%d" % index, "call_id": "call_%d" % index, "name": "exec",
            "input": code, "status": "completed"}
    return _sse([{"type": "response.created", "response": {"id": "resp_%d" % index}},
                 {"type": "response.output_item.done", "output_index": 0, "item": item},
                 {"type": "response.completed", "response": {"id": "resp_%d" % index, "usage": {
                     "input_tokens": 100, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 2,
                     "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 102}}}])


class Recorder:
    """A local OpenAI Responses endpoint: keeps every request body, answers each turn "OK".

    With ``list_tools`` set, a first request without a tool result is answered with an exec call that lists
    ``ALL_TOOLS``; the request that carries its output is answered "OK".
    """

    def __init__(self):
        self.bodies, self.list_tools, recorder = [], False, self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _reply(self, body, content_type="application/json"):
                self.send_response(200)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802  (model lists and connection checks)
                self._reply(b'{"object": "list", "data": [{"id": "grok-4.6", "object": "model"}, '
                            b'{"id": "gpt-6.1-sol", "object": "model"}]}')

            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                if self.path.split("?", 1)[0].endswith("/responses"):
                    body = json.loads(raw)
                    recorder.bodies.append(body)
                    answered = any(item.get("type") == "custom_tool_call_output" for item in body.get("input") or [])
                    if recorder.list_tools and not answered:
                        return self._reply(tool_call_stream(len(recorder.bodies), LIST_TOOLS), "text/event-stream")
                    return self._reply(openai_stream(len(recorder.bodies)), "text/event-stream")
                self._reply(b"{}")

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.2)
    return predicate()


def source_build():
    """The checkout the probe's Angelo code came from: its commit, ``+dirty`` with uncommitted add-on changes."""
    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain", "--", "scripts/addons_core/angelo_agent", "scripts/angelo")
    return git("rev-parse", "HEAD") + ("+dirty" if dirty else "")


def save(folder, api, runtime, version, bodies):
    """``requests.jsonl``: a header naming what sent it, then each request body (prompt_recordings reads it)."""
    folder.mkdir(parents=True, exist_ok=True)
    header = {"probe": folder.name, "api": api, "runtime": runtime, "runtime_version": version, "build": source_build()}
    with open(folder / "requests.jsonl", "w") as handle:
        for line in [header] + [{"body": body} for body in bodies]:
            handle.write(json.dumps(line) + "\n")


def keep_logs(source, folder, pattern):
    """Copy the runtime's own transcripts or rollouts (``pattern`` under ``source``) beside the request bodies."""
    for path in sorted(Path(source).rglob(pattern)):
        target = folder / "angelo-trial-01-rollouts" if path.name.startswith("rollout-") else folder
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / (path.name if path.name.startswith("rollout-") else "angelo-trial-01-transcript.jsonl"))


def codex_cases(out, codex, cases, recorder):
    """codex-full, voice and studio-codex through Angelo's CodexProvider on a third-party mode at the recorder."""
    from angelo_agent import provider_registry
    from angelo_agent.controller import Controller
    from angelo_agent.providers import CodexProvider
    from angelo_agent.routing import TOOLSET_VERSION
    from angelo_agent.studio_enhance import enhance_native
    spec = provider_registry.SPECS[EXTERNAL]
    environ = {"ANGELO_AGENT_RUNTIME": codex, spec["env_key"]: DUMMY_KEY}
    version = subprocess.run([codex, "--version"], capture_output=True, text=True).stdout.split()[-1]
    with mock.patch.dict(spec, base_url=recorder.url + "/v1"), mock.patch.dict(os.environ, environ):
        for case in [c for c in ("codex-full", "voice") if c in cases]:
            folder, seen = out / case, len(recorder.bodies)
            controller = Controller(str(folder / "state"))
            try:
                controller.set_document(("probe", 1))
                controller.state.update(provider=EXTERNAL, native_id=None, toolset_version=TOOLSET_VERSION)
                controller.settings, controller.api_key = {"model": "grok-4.6"}, DUMMY_KEY
                controller._begin_metrics()
                context = controller.runtime_context()
                provider = CodexProvider(context, context.settings)
                try:
                    provider.connect()
                    if case == "voice":
                        provider.start_voice_thread()
                    provider.rpc.request("turn/start", {"threadId": provider.thread_id, "effort": "low",
                                                        "input": [{"type": "text", "text": PROMPT}]})
                    wait_for(lambda: len(recorder.bodies) > seen, 60)
                finally:
                    provider.close()
            finally:
                controller.close()
            save(folder, "openai-responses", "codex", version, recorder.bodies[seen:])
            keep_logs(folder / "state", folder, "rollout-*.jsonl")
        if "studio-codex" in cases:
            folder, seen = out / "studio-codex", len(recorder.bodies)
            (folder / "state").mkdir(parents=True, exist_ok=True)
            try:
                enhance_native(str(folder / "state"), {"mode": EXTERNAL}, {"model": "grok-4.6"}, DUMMY_KEY,
                               "grok-4.6", [], "a red chair", "IMAGE", "probe-model", threading.Event())
            except ValueError:
                pass   # The fake's "OK" is a fine answer; only the request matters here.
            save(folder, "openai-responses", "codex", version, recorder.bodies[seen:])
            keep_logs(folder / "state", folder, "rollout-*.jsonl")


class ClaudeSession:
    """Claude Code's stdout, read on a thread; answers the in-process MCP server's requests as the host does.

    The direct-tools route serves Angelo's tools from the host over the control channel (``claude_direct.answer``);
    unanswered, Claude Code would send no Angelo tool, so the recording would not be that route's.
    """

    def __init__(self, process, allowed):
        self.process, self.allowed, self.events, self.lock = process, allowed, [], threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def write(self, payload):
        with self.lock:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()

    def _pump(self):
        from angelo_agent import claude_direct
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            request = event.get("request") or {}
            if event.get("type") == "control_request" and request.get("subtype") == "mcp_message":
                message = request.get("message") or {}
                if message.get("id") is None:   # a notification: acknowledged, as claude_direct.answer does
                    value = {}
                elif message.get("method") == "initialize":
                    value = {"protocolVersion": claude_direct.PROTOCOL, "capabilities": {"tools": {}},
                             "serverInfo": {"name": claude_direct.SERVER, "version": "1"}}
                elif message.get("method") == "tools/list":
                    value = {"tools": claude_direct.tool_list(self.allowed)}
                else:
                    value = {"content": [{"type": "text", "text": "{}"}]}
                # Every mcp_message gets its control response, or Claude Code keeps waiting for it.
                self.write({"type": "control_response", "response": {
                    "subtype": "success", "request_id": event.get("request_id"),
                    "response": {"mcp_response": {"jsonrpc": "2.0", "id": message.get("id"), "result": value}}}})
            self.events.append(event)

    def wait_for(self, predicate, seconds):
        return wait_for(lambda: any(predicate(event) for event in list(self.events)), seconds)


def claude_launch(out, folder, claude, command, initialize=None, prompt=None, allowed=None):
    """One Claude Code session at a fresh FakeMessages; its request bodies and its own transcript."""
    fake = FakeMessages(folder / "fake", "probe", [], model="claude-probe")
    env, _ttl, _passed = launch_environment("claude_api", "interactive", {}, fake.url, folder)
    for name in ("home", "config", "tmp", "workspace"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(command, env=env, cwd=str(folder / "workspace"), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    session = ClaudeSession(process, allowed)
    try:
        if initialize:
            session.write(initialize)
            session.wait_for(lambda event: event.get("type") == "control_response", 60)
        session.write({"type": "user", "session_id": "", "parent_tool_use_id": None,
                       "message": {"role": "user", "content": prompt or PROMPT}})
        session.wait_for(lambda event: event.get("type") == "result", 120)
    finally:
        process.stdin.close()
        try:
            process.wait(60)
        except subprocess.TimeoutExpired:
            process.terminate()   # The probe's own child only.
        fake.close()
    version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.split()[0]
    save(folder, "anthropic-messages", "claude", version, [json.loads(raw) for raw in fake.bodies])
    keep_logs(folder / "config" / "projects", folder, "*.jsonl")


def mcp_state(folder):
    """A private Angelo state folder in which both apps are connected at the default levels (no Angelo runs)."""
    from angelo_agent.external_clients import AppRegistry, DEFAULT_LEVELS
    state = folder / "angelo-state"
    state.mkdir(parents=True, exist_ok=True)
    for app in ("claude_code", "codex"):
        AppRegistry(str(state)).connect(app, levels=DEFAULT_LEVELS)
    return state


def mcp_launcher(state, client, tree=ROOT):
    """Angelo's MCP server as a client launches it; tree: the source whose server to run (default this one)."""
    return [sys.executable, "-I", str(Path(tree) / "scripts/angelo/angelo_mcp.py"), "--client", client,
            "--state-dir", str(state)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--codex", required=True, help="Angelo's agent runtime (Angelo.app/Contents/Helpers/codex)")
    parser.add_argument("--claude", default=shutil.which("claude") or "claude")
    parser.add_argument("--client-codex", default=shutil.which("codex") or "", help="Codex as an MCP client")
    parser.add_argument("--only", nargs="*", default=list(CASES), choices=CASES)
    parser.add_argument("--mcp-tree", default=str(ROOT), help="the source tree whose MCP server the clients run")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    recorder = Recorder()
    codex_cases(out, args.codex, args.only, recorder)
    for case in ("claude-plan", "claude-direct", "collaborator-claude"):
        if case not in args.only:
            continue
        from angelo_agent.chat_settings import clean_options
        from angelo_agent.claude_transport import claude_session
        from angelo_agent.tool_specs import COLLABORATOR_TOOLS
        import prompt_sources
        context = SimpleNamespace(turn_options=clean_options({"model": "opus", "effort": "low"}),
                                  state={"provider": "claude_api", "options": {}}, native_tools=("WebSearch", "WebFetch"),
                                  claude_direct_tools=case == "claude-direct")
        if case == "collaborator-claude":
            context.allowed_tools = COLLABORATOR_TOOLS
            context.instructions = prompt_sources._collaborator_instructions(ROOT)
        command, initialize = claude_session(args.claude, context)
        claude_launch(out, out / case, args.claude, command, initialize, allowed=getattr(context, "allowed_tools", None))
    if "studio-claude" in args.only:
        from angelo_agent.claude_transport import claude_session
        from angelo_agent.studio_enhance import EnhancementContext
        folder = out / "studio-claude"
        context = EnhancementContext(str(folder / "state"), {"mode": "claude_api"}, {}, DUMMY_KEY, "opus", [],
                                     threading.Event())
        command, initialize = claude_session(args.claude, context)
        claude_launch(out, folder, args.claude, command, initialize, "Improve this IMAGE prompt: a red chair")
    if "mcp-claude-code" in args.only:
        folder = out / "mcp-claude-code"
        folder.mkdir(parents=True, exist_ok=True)
        config = folder / "mcp.json"
        command = mcp_launcher(mcp_state(folder), "claude-code", args.mcp_tree)
        config.write_text(json.dumps({"mcpServers": {"angelo": {"command": command[0], "args": command[1:]}}}))
        claude_launch(out, folder, args.claude, [args.claude, "-p", "--input-format", "stream-json",
                                                 "--output-format", "stream-json", "--verbose", "--model", "opus",
                                                 "--mcp-config", str(config), "--strict-mcp-config",
                                                 "--allowedTools", "mcp__angelo"])
    if "mcp-codex" in args.only and args.client_codex:
        folder, seen = out / "mcp-codex", len(recorder.bodies)
        home = folder / "codex-home"
        home.mkdir(parents=True, exist_ok=True)
        command = mcp_launcher(mcp_state(folder), "codex", args.mcp_tree)
        recorder.list_tools = True
        env = {"PATH": "/usr/bin:/bin", "HOME": str(folder), "CODEX_HOME": str(home), "PROBE_KEY": DUMMY_KEY}
        subprocess.run([args.client_codex, "exec", "--skip-git-repo-check", "--json", "--model", "gpt-6.1-sol",
                        "-c", "model_provider=probe", "-c", "model_providers.probe.name=probe",
                        "-c", "model_providers.probe.base_url=" + json.dumps(recorder.url + "/v1"),
                        "-c", "model_providers.probe.env_key=PROBE_KEY", "-c", "model_providers.probe.wire_api=responses",
                        "-c", "mcp_servers.angelo.command=" + json.dumps(command[0]),
                        "-c", "mcp_servers.angelo.args=" + json.dumps(command[1:]), PROMPT],
                       env=env, cwd=str(folder), stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
        recorder.list_tools = False
        version = subprocess.run([args.client_codex, "--version"], capture_output=True, text=True).stdout.split()[-1]
        save(folder, "openai-responses", "codex", version, recorder.bodies[seen:])
        keep_logs(home, folder, "rollout-*.jsonl")
    print(json.dumps({case: len(list((out / case).glob("requests.jsonl"))) and
                      sum(1 for _ in open(out / case / "requests.jsonl")) - 1
                      for case in args.only if (out / case).is_dir()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
