# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a codex launched as Angelo launches it keeps a code-mode cell open while an Angelo call waits for the
person: the model's next request, end to end and unbilled.

Every Angelo call can wait: for an approval card, for its scene, for a job. Angelo asks its runtime to hold a
code-mode cell while such a call is unanswered (``runtime_launch.HOLD_DYNAMIC_TOOLS``), because a cell handed back
as "Script running" lets the model end its turn with the call unanswered, and the turn's end cancels the call
(``turn_end``). Angelo 1.0.10 launched with ``features.instant_interrupt = false`` and its runtime held only a step
instant interrupt could preempt, so nothing was held: a script approval card was cancelled 31 s after it appeared.
Neither side's unit tests could see that; only the runtime under Angelo's own launch flags can.

This drives ``codex app-server`` with Angelo's launch flags (``codex_prompt_probe.fake_command``:
``runtime_launch.codex_command`` pointed at ``codex_prompt_probe.FakeResponses``) and Angelo's tools. The fake
model's first reply is one ``exec`` cell that asks for a short yield (``YIELD_MS``) and calls
``angelo_execute_python``; the probe answers that call only after ``ANSWER_SECONDS``, as a person reading the script
before Approve. The model's next request must carry the finished cell ("Script completed" with the answer), never
"Script running", with no ``wait`` poll before it. Nothing leaves 127.0.0.1; CODEX_HOME and HOME are fresh folders
under OUT. Usage::

    python3.13 approval_hold_probe.py OUT_DIR --codex <agent runtime's codex>

Exit status 0 when every expectation holds (``expectations`` in ``report.json``).
"""

import argparse
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import codex_prompt_probe as probe  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402
from angelo_agent.providers import codex_instructions  # noqa: E402
from angelo_agent.team_library_spec import TEAM_NAMES  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, codex_tools  # noqa: E402

YIELD_MS = 500          # The cell's own yield time: its timer fires long before the answer.
ANSWER_SECONDS = 3.0    # Six yields; any approval card takes at least this long.
ANSWER = "probe: the person answered"
TOOL = "angelo_execute_python"   # The call that waits for an Approve (the 1.0.10 case); every Angelo tool is held.
CELL = ('// @exec: {"yield_time_ms": %d}\n'
        'text(await tools.%s({description: "Probe step that waits for the person.", code: "result = 1"}));'
        % (YIELD_MS, TOOL))


def output_of(raw):
    """The first exec cell's output as the model was given it (text), and the model's ``wait`` polls, from a
    request body (bytes)."""
    items = json.loads(raw).get("input") or []
    output = next((item.get("output") for item in items if item.get("type") == "custom_tool_call_output"), None)
    text = output if isinstance(output, str) else json.dumps(output) if output is not None else ""
    polls = [item for item in items if item.get("type") == "function_call" and item.get("name") == "wait"]
    return text, polls


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    fake = probe.FakeResponses(out, [("exec", CELL), ("message", "Done.")])
    command, env = probe.fake_command(args.codex, out, fake.port)
    (out / "command.json").write_text(json.dumps(command, indent=1))
    events, calls, answered = [], [], []

    def answer(identity):
        answered.append(time.monotonic())
        rpc.respond(identity, {"success": True, "contentItems": [{"type": "inputText", "text": ANSWER}]})

    def on_request(identity, method, params):
        if method == "item/tool/call":
            # Answered from a timer, never on the reader thread: the runtime's own wait is what is measured.
            calls.append(params.get("tool"))
            threading.Timer(ANSWER_SECONDS, answer, (identity,)).start()
        else:
            rpc.write({"id": identity, "error": {"code": -32601, "message": "not handled by the probe"}})

    rpc = JsonRpcProcess(command, env=env, cwd=str(out / "workspace"),
                         on_event=lambda method, params: events.append(method), on_request=on_request)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        thread = rpc.request("thread/start", {
            "cwd": str(out / "workspace"), "modelProvider": "angelo_probe", "sandbox": "read-only",
            "approvalPolicy": "on-request", "serviceTier": "default", "model": args.model,
            "baseInstructions": codex_instructions(short_identifiers=True, deferred=True, team=False),
            "dynamicTools": codex_tools(frozenset(TOOL_MAP) - TEAM_NAMES, short_identifiers=True)})["thread"]["id"]
        rpc.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "Run the probe step."}],
                                   "effort": "low", "model": args.model})
        deadline = time.monotonic() + 60 + ANSWER_SECONDS
        while time.monotonic() < deadline and "turn/completed" not in events:
            time.sleep(.05)
    finally:
        rpc.close()   # Waits for the exit: Windows keeps an exiting process's files locked (its state database).
    text, polls = output_of(fake.bodies[1]) if len(fake.bodies) > 1 else ("", [])
    expectations = {
        "the cell called %s" % TOOL: calls == [TOOL],
        "the model's next request carries the finished cell": "Script completed" in text and ANSWER in text,
        "the model was never handed a running cell": "Script running" not in text,
        "the model never polled the cell with wait": not polls,
        "the turn completed": "turn/completed" in events,
    }
    report = {"codex": args.codex, "expectations": expectations, "passed": all(expectations.values()),
              "exec_output": text[:400], "model_requests": len(fake.bodies), "calls": calls,
              "answered": bool(answered), "yield_ms": YIELD_MS, "answer_seconds": ANSWER_SECONDS,
              "stderr_tail": rpc.stderr_tail[-5:]}
    (out / "report.json").write_text(json.dumps(report, indent=1))
    for name, held in expectations.items():
        print(("PASS " if held else "FAIL ") + name)
    print("exec output: %r" % text[:200])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
