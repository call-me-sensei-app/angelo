# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a Codex thread of Angelo's actually sends: request bytes, unbilled.

Drives ``codex app-server`` with Angelo's launch flags (``providers.codex_command``)
against a local fake Responses endpoint (a ``model_provider`` override to
``http://127.0.0.1``), records every request body and summarizes it. Nothing is
billed and no credentials are read: ``CODEX_HOME`` and ``HOME`` are fresh
folders under the output directory and the provider key is a dummy value.

Variants:

* ``current``: ``codex_instructions`` + ``codex_tools`` (short identifiers,
  deferred families) with the instruction trims of ``codex_command``;
* ``plan``: ``current`` as a typed-plan thread (toolset 30, ``codex_plan``):
  the plan instructions and every ``turn/start`` with the strict output schema;
* ``before``: the pre-launch ``INSTRUCTIONS`` + ``dynamic_tools`` and no trims.

``--task lookup`` (the default): the scripted model first calls one core tool
and one deferred tool from code mode's ``exec`` (as GPT-6 models do), then
replies, so the summary also records which ``tool``/``namespace`` each call
reached Angelo with. ``--task edit``: a pure edit (create and check a cube), as
the model writes it in that variant: from ``exec`` and then a closing message,
or as the plan of the turn's only message. ``turns`` counts the model requests
of each turn. Usage::

    python3.13 codex_prompt_probe.py OUT_DIR [--variant current|plan|before] [--task lookup|edit] [--model gpt-6-astra]
        [--team out|in] [--config KEY=VALUE ...] [--fork plain|before|prediction]

``--fork plain|before|prediction``: a new chat made from a pre-warmed base
thread instead of ``thread/start``. The base thread runs one warm-up turn, then
``thread/fork`` makes the chat: ``plain`` copies its history, ``before`` forks
before the warm-up turn (the base's fixed opening only) and ``prediction`` uses
``experimentalPredictionMode`` (ephemeral, the parent's loaded config, tools and
history); ``prediction-unwarmed`` forks that way from a base that never ran a
turn. ``summary.json``'s ``fork`` then says whose thread ID the chat's first
request sends as ``prompt_cache_key`` and how far that request matches the
base's warm-up request, item by item (``fork_report``).

``--team in`` declares the team-library family, as for a person signed in to an
Angelo team; the default (``out``) is what every other session and the gates
declare (doc/angelo/team_library.rst).

doc/angelo/gpt_launch.rst records the measured numbers.
"""

import argparse
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402
from angelo_agent.cli_runtime import CODEX_BUNDLE  # noqa: E402
from angelo_agent.providers import INSTRUCTIONS, codex_command, codex_instructions, find_executable  # noqa: E402
from angelo_agent.team_library_spec import TEAM_NAMES  # noqa: E402
from angelo_agent.tool_specs import CODEX_DEFERRED_NAMESPACE, TOOL_MAP, codex_tools, dynamic_tools  # noqa: E402
try:  # Toolset 30; an older tree measured with --variant current|before has no plan threads.
    from angelo_agent.codex_plan import output_schema  # noqa: E402
except ImportError:
    output_schema = None

TRIMS = ("skills.include_instructions", "include_permissions_instructions", "include_apps_instructions",
         "include_collaboration_mode_instructions", "include_environment_context_time")
DEFERRED_PROBE = "angelo_expand_recipe"
EXEC_SCRIPT = ('const a = await tools.angelo_scene_search({query: "Cube"}); text(a);\n'
               'const b = await tools.%s__%s({recipe: "probe"}); text(b);' % (CODEX_DEFERRED_NAMESPACE, DEFERRED_PROBE))
EDIT_PROGRAM = {"mode": "APPLY", "operations": [{"kind": "create_primitive", "target": None, "parameters": {
    "primitive": "CUBE", "name": "Eval Cube", "alias": "cube", "size": 2, "location": [0, 0, 0]}}],
    "expected_checks": [{"kind": "dimensions", "target": "$cube", "expected": [2, 2, 2], "tolerance": 0.0001}]}
EDIT_EXEC = "const r = await tools.angelo_scene_program(%s); text(r.split(\"\\n\")[0]);" % json.dumps(EDIT_PROGRAM)


def edit_plan():
    """The edit as a plan thread's model writes it: strict form, optional fields null."""
    from angelo_agent.codex_plan import ordinary_schema
    from angelo_agent.strict_schema import encode
    return json.dumps(encode({"reply": "", "operations": [{"tool": "angelo_scene_program", "arguments": EDIT_PROGRAM}]},
                             ordinary_schema()), separators=(",", ":"))


def script(variant, task):
    """The fake model's responses: (first turn's steps, second turn's reply)."""
    if task == "lookup":
        first = [("exec", EXEC_SCRIPT), ("message", '{"reply":"","operations":[]}' if variant == "plan" else "")]
    elif variant == "plan":
        first = [("message", edit_plan())]
    else:
        first = [("exec", EDIT_EXEC), ("message", "")]
    return first + [("message", '{"reply":"Thanks.","operations":[]}' if variant == "plan" else "Thanks.")]


def _sse(events):
    return "".join("event: %s\ndata: %s\n\n" % (event["type"], json.dumps(event)) for event in events).encode()


# Request headers kept with their values: those a backend may derive cache or session affinity from
# (``session-id``, ``conversation-id``...). Every other header, credentials included, is kept by name only.
_AFFINITY_WORDS = ("session", "conversation", "thread")
_SECRET_WORDS = ("auth", "token", "key", "cookie", "secret", "account")


def affinity_headers(headers):
    """``{"names": [...], "values": {...}}``: every header's name, and only the affinity headers' values."""
    names = sorted(name.lower() for name in headers.keys())
    values = {name.lower(): value for name, value in headers.items()
              if any(word in name.lower() for word in _AFFINITY_WORDS)
              and not any(word in name.lower() for word in _SECRET_WORDS)}
    return {"names": names, "values": values}


class FakeResponses:
    """A local Responses endpoint that replays a script and keeps every request body and its affinity headers."""

    def __init__(self, out, script):
        self.out, self.script, self.bodies, self.headers = out, list(script), [], []
        probe = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = b'{"models":[]}'
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                body = _sse(probe.reply(raw, affinity_headers(self.headers)))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.server.server_address[1]

    def reply(self, raw, headers=None):
        index = len(self.bodies)
        self.bodies.append(raw)
        self.headers.append(headers or {"names": [], "values": {}})
        (self.out / ("request-%02d.json" % index)).write_bytes(raw)
        (self.out / ("request-%02d.headers.json" % index)).write_text(json.dumps(self.headers[-1], indent=1))
        step = self.script.pop(0) if self.script else ("message", "OK")
        if step[0] == "exec":
            item = {"type": "custom_tool_call", "id": "ctc_%d" % index, "call_id": "call_%d" % index,
                    "name": "exec", "input": step[1]}
        else:
            item = {"type": "message", "role": "assistant", "id": "msg_%d" % index,
                    "content": [{"type": "output_text", "text": step[1], "annotations": []}]}
        identity = "resp_%d" % index
        return [{"type": "response.created", "response": {"id": identity}},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": {"id": identity, "usage": {
                    "input_tokens": 100, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 5,
                    "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 105}}}]


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def summarize(raw, thread=None):
    """Bytes of one Responses request by part, and what the model can see of the program shapes.

    codex-cli 0.160 sends no top-level ``instructions`` or ``tools``: the tools
    are an ``additional_tools`` input item (namespaces; code mode's ``exec``
    declares the dynamic tools as TypeScript in its description) and the
    instructions are developer messages. ``input_items`` lists the input in
    prompt order (type, role, bytes), so two runs' first requests show where
    per-thread content (the environment context's cwd, the user message)
    starts; ``prompt_cache_key_is_thread`` says whether the cache key is this
    thread's ID, so that a new chat's first request carries a key no earlier
    request used.
    """
    body = json.loads(raw)
    items = body.get("input") or []
    namespaces = [tool for item in items if item.get("type") == "additional_tools" for tool in item.get("tools") or []]
    tools = {"%s/%s" % (space.get("name"), tool.get("name")): tool
             for space in namespaces for tool in space.get("tools") or [space]}
    exec_description = next((str(tool.get("description", "")) for key, tool in tools.items()
                             if key.endswith("/exec")), "")
    program = exec_description.partition("angelo_scene_program(")[2].partition("Promise<")[0]
    verify = exec_description.partition("angelo_scene_verify(")[2].partition("Promise<")[0]
    developer = [item for item in items if item.get("type") == "message" and item.get("role") == "developer"]
    instructions = next((json.dumps(item.get("content")) for item in developer
                         if "You are Angelo" in json.dumps(item.get("content"))), "")
    text_format = (body.get("text") or {}).get("format")
    # The model writes a strict output's properties in this order (codex_plan, strict_schema).
    order = list(((text_format or {}).get("schema") or {}).get("properties") or {})
    return {"bytes": len(raw), "text_format": _size(text_format) if text_format else 0, "schema_root_order": order,
            "additional_tools": sum(_size(space) for space in namespaces),
            "tool_bytes": {key: _size(tool) for key, tool in tools.items()},
            "exec_description": len(exec_description.encode()),
            "developer_messages": [_size(item) for item in developer],
            "messages": len(items), "unknown_in_exec": exec_description.count("unknown"),
            "program_declaration": program[:400], "verify_declaration": verify[:300],
            "reference_in_instructions": "type Op=" in instructions and "type Check=" in instructions,
            "prompt_cache_key": bool(body.get("prompt_cache_key")),
            "prompt_cache_key_is_thread": (body.get("prompt_cache_key") == thread) if thread else None,
            "input_items": [[item.get("type"), item.get("role"), _size(item)] for item in items]}


# What any Windows process needs from its environment: the system folder (Winsock and the C runtime read
# SystemRoot; without it codex.exe starts, but its model requests never complete and the turn waits forever),
# the shell and executable extensions, and the temporary folder. None of them locates a person's data.
WINDOWS_SYSTEM = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "PROGRAMDATA", "TEMP", "TMP")


def fake_command(executable, out, port, overrides=()):
    """Angelo's app-server command and environment, pointed at the fake endpoint on ``port``.

    ``CODEX_HOME`` and ``HOME`` are fresh folders under ``out`` and the provider
    key is a dummy value, so nothing is billed and no sign-in is read. On
    Windows the system variables stay (``WINDOWS_SYSTEM``) and the per-user
    folders (``USERPROFILE``, ``APPDATA``, ``LOCALAPPDATA``) are fresh ones under
    ``out`` too.
    """
    command, env = codex_command(executable, "codex_subscription", str(out / "state"), reuse_login=False)
    kept = ("PATH", "USER", "LANG", "CODEX_HOME", "TMPDIR") + (WINDOWS_SYSTEM if os.name == "nt" else ())
    env = {key: value for key, value in env.items() if key.upper() in kept}
    env.update(HOME=str(out / "home"), ANGELO_PROBE_KEY="probe-not-a-secret")
    if os.name == "nt":
        profile = {"USERPROFILE": out / "home", "APPDATA": out / "home" / "AppData" / "Roaming",
                   "LOCALAPPDATA": out / "home" / "AppData" / "Local"}
        for key, folder in profile.items():
            folder.mkdir(parents=True, exist_ok=True)
            env[key] = str(folder)
    for key, value in {"model_provider": '"angelo_probe"', "model_providers.angelo_probe.name": '"probe"',
                       "model_providers.angelo_probe.base_url": '"http://127.0.0.1:%d/v1"' % port,
                       "model_providers.angelo_probe.env_key": '"ANGELO_PROBE_KEY"',
                       "model_providers.angelo_probe.wire_api": '"responses"',
                       "model_providers.angelo_probe.requires_openai_auth": "false",
                       "model_providers.angelo_probe.supports_websockets": "false"}.items():
        command += ["-c", "%s=%s" % (key, value)]
    for override in overrides:
        command += ["-c", override]
    return command, env


FORK_MODES = ("plain", "before", "prediction", "prediction-unwarmed")
WARM_UP = "Warm-up message."


def fork_report(base_raw, fork_raw, base_thread, fork_thread):
    """The forked chat's first request against the base thread's warm-up request.

    ``items`` compares the input in prompt order, whole and without the
    per-item ``id`` field; ``matching_prefix_bytes`` sums the leading items
    equal apart from their IDs. Thread IDs are not secrets.
    """
    base, fork = json.loads(base_raw), json.loads(fork_raw)
    strip = lambda item: {key: value for key, value in item.items() if key != "id"}  # noqa: E731
    items, matching, diverged = [], 0, None
    for index, (left, right) in enumerate(zip(base.get("input") or [], fork.get("input") or [])):
        same = strip(left) == strip(right)
        items.append({"index": index, "type": left.get("type"), "role": left.get("role"), "equal": left == right,
                      "equal_without_id": same, "bytes": _size(left)})
        if diverged is None and not same:
            diverged = index
        if diverged is None:
            matching += _size(left)
    key = fork.get("prompt_cache_key")
    return {"base_thread": base_thread, "fork_thread": fork_thread,
            "prompt_cache_key": {"is_base_thread": key == base_thread, "is_fork_thread": key == fork_thread,
                                 "base_request_is_base_thread": base.get("prompt_cache_key") == base_thread},
            "first_different_item": diverged, "matching_prefix_bytes": matching, "items": items,
            "other_fields_equal": {name: base.get(name) == fork.get(name)
                                   for name in sorted(set(base) | set(fork)) if name not in ("input", "prompt_cache_key")}}


def resume_status(command, env, cwd, identity):
    """Whether a fresh app-server (a restarted app) can resume thread ``identity``: "ok" or its error."""
    rpc = JsonRpcProcess(command, env=env, cwd=cwd, on_event=lambda *_: None, on_request=lambda *_: None)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        rpc.request("thread/resume", {"threadId": identity})
        return "ok"
    except Exception as error:  # The probe reports what the runtime answered.
        return str(error)[:300]
    finally:
        rpc.process.terminate()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--variant", choices=("current", "plan", "before"), default="current")
    parser.add_argument("--task", choices=("lookup", "edit"), default="lookup")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--codex", default="")
    parser.add_argument("--team", choices=("out", "in"), default="out")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE",
                        help="an extra -c override, to measure what a Codex setting removes")
    parser.add_argument("--fork", choices=FORK_MODES, default="",
                        help="make the chat with thread/fork from a pre-warmed base thread")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    # The ChatGPT app's bundled codex (macOS) before one on PATH; elsewhere the product's own search.
    executable = args.codex or find_executable("codex", CODEX_BUNDLE if Path(CODEX_BUNDLE).exists() else "")
    if args.variant == "plan" and output_schema is None:
        parser.error("this tree has no typed-plan threads (toolset 30)")
    fake = FakeResponses(out, script(args.variant, args.task))

    command, env = fake_command(executable, out, fake.port, args.config)
    if args.variant == "before":
        # Drop each "-c <trim>=..." pair: the pre-launch prefix had no trims.
        pairs, index = [], 0
        while index < len(command):
            if command[index] == "-c" and command[index + 1].split("=", 1)[0] in TRIMS:
                index += 2
                continue
            pairs.append(command[index])
            index += 1
        command = pairs
    (out / "command.json").write_text(json.dumps(command, indent=1))

    events, calls = [], []
    rpc = thread = None

    def on_request(identity, method, params):
        if method == "item/tool/call":
            calls.append({key: params.get(key) for key in ("tool", "namespace", "arguments")})
            rpc.respond(identity, {"success": True, "contentItems": [{"type": "inputText", "text": '{"probe":true}'}]})
        else:
            rpc.write({"id": identity, "error": {"code": -32601, "message": "not handled by the probe"}})

    rpc = JsonRpcProcess(command, env=env, cwd=str(out / "workspace"),
                         on_event=lambda method, params: events.append(method), on_request=on_request)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        current, plan = args.variant in ("current", "plan"), args.variant == "plan"
        allowed = None if args.team == "in" else frozenset(TOOL_MAP) - TEAM_NAMES
        instructions = (codex_instructions(short_identifiers=True, deferred=True, team=args.team == "in",
                                           **({"plan": True} if plan else {})) if current else INSTRUCTIONS)
        parameters = {"cwd": str(out / "workspace"), "modelProvider": "angelo_probe", "sandbox": "read-only",
                      "approvalPolicy": "on-request", "serviceTier": "default", "model": args.model,
                      "baseInstructions": instructions,
                      "dynamicTools": codex_tools(allowed, short_identifiers=True) if current else dynamic_tools(allowed)}
        thread = rpc.request("thread/start", parameters)["thread"]["id"]
        first = ("First message: make a 2 m cube named Eval Cube." if args.task == "edit"
                 else "First message: what is in the scene?")
        turns, forked = [], None

        def run_turn(identity, text):
            seen, requests = len(events), len(fake.bodies)
            turn = {"threadId": identity, "input": [{"type": "text", "text": text}], "effort": "low", "model": args.model}
            if plan:
                turn["outputSchema"] = output_schema(allowed)  # Every turn of a plan thread, byte-identical.
            started = rpc.request("turn/start", turn)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and "turn/completed" not in events[seen:]:
                time.sleep(.05)
            turns.append(len(fake.bodies) - requests)
            return started["turn"]["id"], requests

        if args.fork:
            warm_up, base_request = run_turn(thread, WARM_UP) if args.fork != "prediction-unwarmed" else (None, None)
            fork = {"threadId": thread}
            if args.fork == "before":
                fork["beforeTurnId"] = warm_up
            elif args.fork.startswith("prediction"):
                fork.update(experimentalPredictionMode=True, ephemeral=True, excludeTurns=True)  # Paginated threads need it.
            forked = rpc.request("thread/fork", fork)["thread"]["id"]
            _turn, fork_request = run_turn(forked, first)
            run_turn(forked, "Second message: thanks.")
        else:
            for text in (first, "Second message: thanks."):
                run_turn(thread, text)
    finally:
        rpc.process.terminate()
    summary = {"variant": args.variant, "task": args.task, "model": args.model, "team": args.team, "turns": turns,
               "requests": [dict(summarize(raw, thread), affinity_headers=headers["values"])
                            for raw, headers in zip(fake.bodies, fake.headers)], "tool_calls": calls,
               "stderr_tail": rpc.stderr_tail[-5:]}
    if forked is not None and fork_request < len(fake.bodies):
        base_raw = fake.bodies[base_request] if base_request is not None else b'{"input":[]}'
        base_headers = fake.headers[base_request]["values"] if base_request is not None else {}
        fork_headers = fake.headers[fork_request]["values"]
        summary["fork"] = dict(fork_report(base_raw, fake.bodies[fork_request], thread, forked),
                               affinity_headers={name: {"base_request_is_base_thread": base_headers.get(name) == thread,
                                                        "fork_request_is_base_thread": value == thread,
                                                        "fork_request_is_fork_thread": value == forked}
                                                 for name, value in fork_headers.items()},
                               resume_after_restart=resume_status(command, env, str(out / "workspace"), forked),
                               mode=args.fork, later_keys=[
                                   {"base": json.loads(raw).get("prompt_cache_key") == thread,
                                    "fork": json.loads(raw).get("prompt_cache_key") == forked}
                                   for raw in fake.bodies[fork_request + 1:]])
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
