# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether codex app-server waits for an ``item/tool/call`` reply: unbilled, explicit only.

Drives the installed ``codex app-server`` with Angelo's launch flags against
``codex_prompt_probe.FakeResponses`` (a local endpoint, a dummy key, fresh
``CODEX_HOME``/``HOME`` under OUT_DIR). The scripted model makes one dynamic
tool call from code mode's ``exec``. One app-server per case:

* ``noanswer``: the call is never answered and the turn is not interrupted;
* ``noanswer_interrupt``: never answered, then ``turn/interrupt``;
* ``refuse``: answered at once with ``codex_delivery.STALE_CALL``;
* ``interrupt_late_refuse``: ``turn/interrupt``, then that refusal after the turn ended;
* ``product``: what the host does with a stale call: the refusal, then
  ``codex_turns.OpenTurns.interrupt`` of its turn.

Each case then starts a second turn on the same thread through the host's
guard (``OpenTurns.start``: interrupt any open turn and wait for it to end) and
records whether the first turn completed, whether the guard had to interrupt,
which turn ID ``turn/start`` returned and how many model requests were made.
``--raw`` sends the second ``turn/start`` without the guard, which shows the
unanswered turn absorbing it (the same turn ID). Usage::

    python3.13 codex_stale_call_probe.py OUT_DIR [--raw] [CASE ...]

doc/angelo/agent_execution.rst ("pending delivery") records the result.
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import codex_prompt_probe as probe  # noqa: E402
from angelo_agent.cli_runtime import CODEX_BUNDLE  # noqa: E402
from angelo_agent.codex_delivery import STALE_CALL  # noqa: E402
from angelo_agent.codex_turns import OpenTurns  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402
from angelo_agent.providers import codex_command, codex_instructions, find_executable  # noqa: E402
from angelo_agent.team_library_spec import TEAM_NAMES  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, codex_tools  # noqa: E402

CASES = ("noanswer", "noanswer_interrupt", "refuse", "interrupt_late_refuse", "product")
ONE_CALL = 'const a = await tools.angelo_scene_search({query: "Cube"}); text(a);'
REFUSAL = {"success": False, "contentItems": [{"type": "inputText", "text": STALE_CALL}]}
MODEL = "gpt-6-astra"


def launch(out, fake):
    executable = find_executable("codex", CODEX_BUNDLE if Path(CODEX_BUNDLE).exists() else "")
    command, env = codex_command(executable, "codex_subscription", str(out / "state"), reuse_login=False)
    env = {key: value for key, value in env.items() if key in ("PATH", "USER", "LANG", "CODEX_HOME", "TMPDIR")}
    env.update(HOME=str(out / "home"), ANGELO_PROBE_KEY="probe-not-a-secret")
    for key, value in {"model_provider": '"angelo_probe"', "model_providers.angelo_probe.name": '"probe"',
                       "model_providers.angelo_probe.base_url": '"http://127.0.0.1:%d/v1"' % fake.port,
                       "model_providers.angelo_probe.env_key": '"ANGELO_PROBE_KEY"',
                       "model_providers.angelo_probe.wire_api": '"responses"',
                       "model_providers.angelo_probe.requires_openai_auth": "false",
                       "model_providers.angelo_probe.supports_websockets": "false"}.items():
        command += ["-c", "%s=%s" % (key, value)]
    return command, env


class _Closing:
    """The guard's provider: the probe only records that it would have closed the connection."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def run_case(case, root, raw=False):
    out = root / (case + ("-raw" if raw else ""))
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    fake = probe.FakeResponses(out, [("exec", ONE_CALL)] + [("message", "done %d" % index) for index in range(3)])
    command, env = launch(out, fake)
    log, calls, started, turns = [], [], time.monotonic(), OpenTurns()
    rpc = None

    def stamp(kind, detail):
        log.append([round(time.monotonic() - started, 2), kind, detail])

    def on_event(method, params):
        turns.observe(method, params)  # As the provider does, whoever owns the turn.
        if method in ("turn/started", "turn/completed"):
            stamp(method, [params["turn"].get("id"), params["turn"].get("status")])

    def on_request(identity, method, params):
        stamp(method, [identity, params.get("turnId"), params.get("tool")])
        if method != "item/tool/call":
            rpc.write({"id": identity, "error": {"code": -32601, "message": "not handled by the probe"}})
            return
        turns.observe(method, params)
        calls.append(identity)
        if case in ("refuse", "product"):
            rpc.respond(identity, REFUSAL)
        if case == "product":
            turns.interrupt(rpc, params["threadId"], params["turnId"])  # codex_delivery.close_orphaned_turn

    def wait(predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not predicate():
            time.sleep(.05)
        return predicate()

    def completed(turn):
        return any(kind == "turn/completed" and detail[0] == turn for _, kind, detail in log)

    rpc = JsonRpcProcess(command, env=env, cwd=str(out / "workspace"), on_event=on_event, on_request=on_request)
    result = {"case": case}
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        allowed = frozenset(TOOL_MAP) - TEAM_NAMES
        thread = rpc.request("thread/start", {
            "cwd": str(out / "workspace"), "modelProvider": "angelo_probe", "sandbox": "read-only",
            "approvalPolicy": "on-request", "serviceTier": "default", "model": MODEL,
            "baseInstructions": codex_instructions(short_identifiers=True, deferred=True, team=False),
            "dynamicTools": codex_tools(allowed, short_identifiers=True)})["thread"]["id"]

        def turn(text):
            return rpc.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}],
                                              "effort": "low", "model": MODEL}, timeout=20)["turn"]["id"]

        first = turn("First: what is in the scene?")
        result["call_arrived"] = wait(lambda: bool(calls), 20)
        if "interrupt" in case:
            rpc.request("turn/interrupt", {"threadId": thread, "turnId": first}, timeout=15)
        result["first_completed_within_20s"] = wait(lambda: completed(first), 20)
        if case == "interrupt_late_refuse" and calls:
            rpc.respond(calls[0], REFUSAL)
            time.sleep(2)
        result["model_requests_before_second_turn"] = len(fake.bodies)
        result["open_before_second_turn"] = turns.open_on(thread)
        if raw:
            second = turn("Second: thanks.")
        else:
            guard = _Closing()
            second = turns.start(guard, rpc, {"threadId": thread, "input": [{"type": "text", "text": "Second: thanks."}],
                                              "effort": "low", "model": MODEL})["turn"]["id"]
            result["guard_closed_connection"] = guard.closed
        result["second_turn_is_a_new_turn"] = second != first
        result["second_completed_within_45s"] = wait(lambda: completed(second), 45)
        result["model_requests_total"] = len(fake.bodies)
        result["process_alive"] = rpc.process.poll() is None
    finally:
        rpc.process.terminate()
    result.update(log=log, stderr_tail=rpc.stderr_tail[-3:])
    (out / "result.json").write_text(json.dumps(result, indent=1))
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        raise SystemExit(__doc__)
    raw = "--raw" in argv
    argv = [item for item in argv if item != "--raw"]
    root, cases = Path(argv[0]).resolve(), argv[1:] or CASES
    unknown = sorted(set(cases) - set(CASES))
    if unknown:
        raise SystemExit("Unknown case(s): %s; choose from %s" % (", ".join(unknown), ", ".join(CASES)))
    summary = [run_case(case, root, raw) for case in cases]
    print(json.dumps([{key: value for key, value in row.items() if key not in ("log", "stderr_tail")}
                      for row in summary], indent=1))


if __name__ == "__main__":
    main()
