# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a Claude Code session of Angelo's actually sends at chat start: request bytes, unbilled.

Launches the real ``claude`` CLI with Angelo's own arguments and stdin
initialization (``providers.claude_session``: the plan schema and the
appended instructions) against a local fake Anthropic Messages endpoint
(``scenario_fake_models.FakeMessages``, which answers "OK"), sends one user
message, records every request body and summarizes the first one: system
blocks, tool definitions, cache breakpoints (with each one's TTL) and where
the per-machine sections sit. Nothing is billed and no credentials are read:
the session authenticates with a dummy API key (Claude Code always uses
``ANTHROPIC_API_KEY`` in ``-p`` mode), ``HOME`` and ``CLAUDE_CONFIG_DIR`` are
fresh folders under the output directory (so its Keychain namespace is empty),
and non-essential traffic is off. Every Claude connection launches with the
same arguments and initialization (no ``--bare``), so a subscription's opening
has the same tools and Angelo-owned parts (instructions, schema).

The launch environment is Angelo's own for ``--route`` (default ``claude_api``):
the variables a person's shell may hold (``--inherit NAME=VALUE``) pass
``runtime_launch.child_environment``'s filter, the route adds its credentials
and endpoint (``claude_team``: its gateway route, with the gateway origin set to
the fake endpoint), and ``runtime_environment.session_environment`` sets the
prompt-cache TTL ``model_routing.cache_ttl`` gives the route's billing and
``--session-kind``. ``claude_subscription`` cannot sign in here: it keeps the
dummy-key transport and shows only the TTL a subscription chat's environment
sets.

Usage::

    python3.13 claude_prompt_probe.py OUT_DIR [--claude PATH] [--model opus]
        [--route claude_api|claude_team|claude_subscription] [--session-kind interactive]
        [--inherit ENABLE_PROMPT_CACHING_1H=1] [--extra-flag --exclude-dynamic-system-prompt-sections]

doc/angelo/performance.rst ("Fixed opening of a new chat") records the numbers.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import model_routing, runtime_environment  # noqa: E402
from angelo_agent.chat_settings import clean_options  # noqa: E402
from angelo_agent.cli_runtime import find_executable  # noqa: E402
from angelo_agent.provider_registry import SPECS, billing_kind  # noqa: E402
from angelo_agent.providers import child_environment, claude_session  # noqa: E402
from scenario_fake_models import FakeMessages  # noqa: E402

MARKER = "You are Angelo"
FIRST = "First message: make a 2 m cube named Eval Cube."
ROUTES = tuple(sorted(mode for mode, spec in SPECS.items() if spec["runtime"] == "claude"))
DUMMY_KEY = "probe-not-a-secret"


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _text(block):
    return block.get("text", "") if isinstance(block, dict) else str(block)


def summarize(raw, workspace):
    """Bytes of one Messages request by part, in prompt order (tools, system, messages)."""
    body = json.loads(raw)
    system = body.get("system") or []
    system = [{"type": "text", "text": system}] if isinstance(system, str) else system
    tools = body.get("tools") or []
    messages = body.get("messages") or []
    return {
        "bytes": len(raw), "model": body.get("model"),
        "tools": [{"name": tool.get("name"), "bytes": _size(tool),
                   "description": len(str(tool.get("description", "")).encode()),
                   "input_schema": _size(tool.get("input_schema", {})),
                   "cache_control": tool.get("cache_control")} for tool in tools],
        "system": [{"bytes": len(_text(block).encode()), "cache_control": block.get("cache_control"),
                    "angelo": MARKER in _text(block), "cwd": workspace in _text(block),
                    "head": _text(block)[:80]} for block in system],
        "messages": [{"role": message.get("role"), "bytes": _size(message),
                      "cwd": workspace in json.dumps(message, ensure_ascii=False),
                      "cache_control": [block.get("cache_control") for block in message.get("content") or []
                                        if isinstance(block, dict) and block.get("cache_control")]}
                     for message in messages],
        "other_keys": sorted(key for key in body if key not in {"system", "tools", "messages"}),
    }


def cache_ttls(requests):
    """Every cache breakpoint's TTL across the summarized requests: ``ttl`` or the API's 5-minute default."""
    found = set()
    for request in requests:
        controls = ([tool["cache_control"] for tool in request["tools"]]
                    + [block["cache_control"] for block in request["system"]]
                    + [control for message in request["messages"] for control in message["cache_control"]])
        found |= {control.get("ttl", "5m (default)") for control in controls if isinstance(control, dict)}
    return sorted(found)


def launch_environment(route, session_kind, inherited, endpoint, out):
    """``(environment, ttl, passed)``: Angelo's launch of a NEW native session on ``route`` at the fake ``endpoint``.

    ``passed`` names the ``inherited`` variables that came through the child-environment filter.
    """
    ttl = model_routing.cache_ttl("claude", session_kind, billing_kind(route))
    shell = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HOME": str(out / "home"),
             "CLAUDE_CONFIG_DIR": str(out / "config"), "TMPDIR": str(out / "tmp"),
             "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
             "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", **inherited,
             "ANGELO_GATEWAY_URL": endpoint}   # Read only by a team route: its gateway is the fake.
    with mock.patch.dict(os.environ, shell, clear=True):
        env = child_environment(route, DUMMY_KEY)
    passed = sorted(name for name in inherited if name in env)
    if "ANTHROPIC_BASE_URL" not in env:   # A personal route has no endpoint of its own: the probe's fake.
        env.update(ANTHROPIC_BASE_URL=endpoint, ANTHROPIC_API_KEY=DUMMY_KEY)
    env = runtime_environment.session_environment(env, SimpleNamespace(prompt_cache_ttl=ttl))
    return env, ttl, passed


def _read_until(process, predicate, seconds):
    """Lines of stream-json stdout until one satisfies ``predicate`` (or the deadline)."""
    found, lines = threading.Event(), []

    def pump():
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            lines.append(event.get("type"))
            if predicate(event):
                found.set()
                return

    threading.Thread(target=pump, daemon=True).start()
    found.wait(seconds)
    return found.is_set(), lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--claude", default="")
    parser.add_argument("--model", default="opus")
    parser.add_argument("--extra-flag", action="append", default=[])
    parser.add_argument("--route", choices=ROUTES, default="claude_api")
    parser.add_argument("--session-kind", choices=sorted(model_routing.SESSION_KINDS), default="interactive")
    parser.add_argument("--inherit", action="append", default=[], metavar="NAME=VALUE",
                        help="a variable of the shell that launched Blender (never a credential)")
    args = parser.parse_args(argv)
    inherited = dict(item.split("=", 1) for item in args.inherit)
    out = args.out.resolve()
    for name in ("home", "config", "tmp", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    executable = args.claude or find_executable("claude")
    fake = FakeMessages(out, "probe", [], model="claude-probe")
    context = SimpleNamespace(turn_options=clean_options({"model": args.model, "effort": "low"}),
                              state={"provider": args.route, "options": {}})
    command, initialize = claude_session(executable, context)
    command += args.extra_flag
    env, ttl, passed = launch_environment(args.route, args.session_kind, inherited, fake.url, out)
    (out / "command.json").write_text(json.dumps(command, indent=1))
    process = subprocess.Popen(command, env=env, cwd=str(out / "workspace"), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    status = {}
    try:
        process.stdin.write(json.dumps(initialize) + "\n")
        process.stdin.flush()
        status["initialized"], _ = _read_until(process, lambda event: event.get("type") == "control_response", 60)
        process.stdin.write(json.dumps({"type": "user", "session_id": "", "parent_tool_use_id": None,
                                        "message": {"role": "user", "content": FIRST}}) + "\n")
        process.stdin.flush()
        status["result"], status["events"] = _read_until(process, lambda event: event.get("type") == "result", 120)
    finally:
        process.stdin.close()  # End of input ends a stream-json session.
        try:
            status["exit"] = process.wait(60)
        except subprocess.TimeoutExpired:
            process.terminate()  # The probe's own child only.
            status["exit"] = "terminated"
        fake.close()
    workspace = str(out / "workspace")
    requests = [summarize(raw, workspace) for raw in fake.bodies]
    summary = {"route": args.route, "session_kind": args.session_kind, "angelo_ttl": ttl,
               "launch_ttl_variable": env.get("CLAUDE_CODE_PROMPT_CACHE_TTL"), "endpoint": env["ANTHROPIC_BASE_URL"],
               "inherited": sorted(inherited), "inherited_passed": passed,
               "cache_control_ttls": cache_ttls(requests),
               "command_flags": [flag for flag in command[1:] if flag.startswith("--")], "status": status,
               "requests": requests}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
