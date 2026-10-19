# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Does a prediction-mode fork's first request reuse the prompt cache on real servers? A small A/B.

Unbilled by default: the same flow runs against ``codex_prompt_probe``'s local
fake endpoint, to check the script itself. ``--authorize-billed-run`` sends it
to OpenAI through one ChatGPT subscription connection's dedicated Codex
profile (``--codex-home .../codex-profile/<connection id>``, never
``~/.codex``; its sign-in file is only checked to exist, never read). Each
thread uses Angelo's own typed-plan parameters (instructions, dynamic tools and
strict response format), so the opening is the one a new chat sends.

* Arm A: one base thread runs a warm-up turn; each chat is then an ephemeral
  ``thread/fork`` with ``experimentalPredictionMode``, which sends the base's
  thread ID as ``prompt_cache_key`` (``codex_prompt_probe.py --fork``).
* Arm B: each chat is a fresh ``thread/start`` (its own key).

Every chat sends the same first message. Chats alternate A B B A A B B A A B so
drift within the run affects both arms alike. Per turn it records the input,
cached input and output tokens of each model request (``thread/tokenUsage/updated``),
the time to the first streamed event and to completion, the thread IDs and the
``session-id`` header each chat sends. The real requests are not intercepted:
that header is what the same binary sends against the fake endpoint (a
prediction fork sends its base's thread ID, every other chat its own), and a
dry run records it as observed (``fake_affinity_headers``).
Rate-limit and account events are not kept. A failed turn stops the run unless
it looks like a transport error (at most two retries); a usage limit or a
sign-in problem always stops it. Usage::

    python3.13 codex_cache_fork_check.py OUT_DIR [--chats 5] [--model gpt-6.1-sol]
        [--authorize-billed-run --connection-id ID --codex-home .../codex-profile/ID]
"""

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent.cli_runtime import CODEX_BUNDLE  # noqa: E402
from angelo_agent.codex_plan import output_schema  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402
from angelo_agent.providers import codex_command, codex_instructions, find_executable  # noqa: E402
from angelo_agent.storage import valid_profile_directory  # noqa: E402
from angelo_agent.team_library_spec import TEAM_NAMES  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, codex_tools  # noqa: E402
from codex_prompt_probe import FakeResponses  # noqa: E402

ALLOWED = frozenset(TOOL_MAP) - TEAM_NAMES  # As the gates and benchmarks run (not signed in to a team).
ORDER = "ABBAABBAAB"
WARM_UP = 'Warm-up message. Make no change: answer with reply "OK" and no operations.'
FIRST = 'Connection check. Make no change: answer with reply "OK" and no operations.'
EMPTY_PLAN = '{"reply":"OK","operations":[]}'
TURN_SECONDS = 240
MAX_RETRIES = 2
STOP_WORDS = ("usage limit", "rate limit", "limit reached", "sign in", "log in", "login", "unauthorized",
              "401", "403", "quota", "credits")
DROPPED_EVENTS = ("account/",)  # Rate limits and credit balances are not this check's data.


class Stop(RuntimeError):
    """The run must end: a usage limit, a sign-in problem, or no retries left."""


def dedicated_profile(codex_home, connection_id):
    """A validated dedicated Codex profile, never ``~/.codex``; its sign-in file must exist (never read)."""
    path = Path(codex_home).expanduser().resolve()
    if path == (Path.home() / ".codex").resolve():
        raise SystemExit("--codex-home must be a dedicated profile, never ~/.codex")
    valid_profile_directory(str(path), connection_id, "codex_subscription")
    if not (path / "auth.json").is_file():
        raise Stop("The Codex profile is not signed in; sign-in is the person's step, not this check's")
    return str(path)


class Session:
    """One app-server process: Angelo's thread parameters, turns and the events each turn produced."""

    def __init__(self, command, env, workspace, model, provider):
        self.events, self.lock = [], threading.Lock()
        self.model, self.workspace, self.provider = model, workspace, provider
        self.rpc = JsonRpcProcess(command, env=env, cwd=workspace, on_event=self._event, on_request=self._request)
        self.rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                        "capabilities": {"experimentalApi": True}})
        self.rpc.notify("initialized")

    def _event(self, method, params):
        if not method.startswith(DROPPED_EVENTS):
            with self.lock:
                self.events.append((time.monotonic(), method, params))

    def _request(self, identity, method, params):
        if method == "item/tool/call":  # This check answers no tool: the model is asked for an empty plan.
            self.rpc.respond(identity, {"success": False, "contentItems": [
                {"type": "inputText", "text": "Not available in this check: answer with an empty plan."}]})
        else:
            self.rpc.write({"id": identity, "error": {"code": -32601, "message": "not handled by this check"}})

    def start_thread(self):
        return self.rpc.request("thread/start", {
            "cwd": self.workspace, "modelProvider": self.provider, "sandbox": "read-only",
            "approvalPolicy": "on-request", "serviceTier": "default", "model": self.model,
            "baseInstructions": codex_instructions(short_identifiers=True, deferred=True, plan=True, team=False),
            "dynamicTools": codex_tools(ALLOWED, short_identifiers=True, defer=True)})["thread"]["id"]

    def prediction_fork(self, base):
        return self.rpc.request("thread/fork", {"threadId": base, "experimentalPredictionMode": True,
                                                "ephemeral": True, "excludeTurns": True})["thread"]["id"]

    def turn(self, thread, text):
        """One turn's measurements: model requests (usage), first streamed event and completion times."""
        with self.lock:
            seen = len(self.events)
        sent = time.monotonic()
        turn = self.rpc.request("turn/start", {
            "threadId": thread, "input": [{"type": "text", "text": text}], "summary": "auto", "effort": "low",
            "model": self.model, "serviceTierForTurn": "default", "outputSchema": output_schema(ALLOWED)})["turn"]["id"]
        deadline, done = sent + TURN_SECONDS, None
        while time.monotonic() < deadline and done is None:
            time.sleep(.05)
            with self.lock:
                done = next((event for event in self.events[seen:] if event[1] == "turn/completed"
                             and event[2].get("turn", {}).get("id") == turn), None)
        with self.lock:
            mine = [event for event in self.events[seen:] if _turn_of(event[2]) in (turn, None)]
        usage = [event[2]["tokenUsage"]["last"] for event in mine if event[1] == "thread/tokenUsage/updated"]
        # The first streamed model output: a delta, else the first agent message or reasoning item that starts.
        streamed = next((at for at, method, _ in mine if "delta" in method.lower()), None) or next(
            (at for at, method, params in mine if method == "item/started"
             and (params.get("item") or {}).get("type") in ("agentMessage", "reasoning")), None)
        status = done[2]["turn"].get("status") if done else "timeout"
        error = (done[2]["turn"].get("error") if done else None) or next(
            (params for _, method, params in mine if method == "error"), None)
        return {"thread": thread, "turn": turn, "status": status, "error": error,
                "requests": [{"input_tokens": u.get("inputTokens"), "cached_input_tokens": u.get("cachedInputTokens"),
                              "output_tokens": u.get("outputTokens"),
                              "reasoning_output_tokens": u.get("reasoningOutputTokens")} for u in usage],
                "first_event_s": round(streamed - sent, 3) if streamed else None,
                "completed_s": round(done[0] - sent, 3) if done else None,
                "events": [[round(at - sent, 3), method] for at, method, _ in mine]}

    def close(self):
        self.rpc.stdin_close = getattr(self.rpc.process.stdin, "close", lambda: None)()
        try:
            self.rpc.process.wait(30)
        except Exception:
            self.rpc.process.terminate()  # This check's own child only.


def _turn_of(params):
    return params.get("turnId") or (params.get("turn") or {}).get("id") if isinstance(params, dict) else None


def _checked(record, retries):
    """Raise Stop for a limit or sign-in failure; return True when a transport failure may be retried."""
    if record["status"] == "completed" and record["requests"]:
        return False
    text = json.dumps(record["error"] or record["status"]).lower()
    if any(word in text for word in STOP_WORDS):
        raise Stop("Stopped on a usage-limit or sign-in error: %s" % text[:300])
    if retries[0] >= MAX_RETRIES:
        raise Stop("Stopped: a turn failed and no retries are left: %s" % text[:300])
    retries[0] += 1
    return True


def summarize(records):
    def first(record):
        return record["requests"][0] if record["requests"] else {}

    arms = {}
    for arm in ("A", "B"):
        rows = [record for record in records if record["arm"] == arm]
        cached = [first(row).get("cached_input_tokens") or 0 for row in rows]
        inputs = [first(row).get("input_tokens") or 0 for row in rows]
        arms[arm] = {"chats": len(rows), "first_request_input": inputs, "first_request_cached": cached,
                     "cached_share_median": round(statistics.median(c / i for c, i in zip(cached, inputs) if i), 3)
                     if any(inputs) else None,
                     "first_event_s_median": statistics.median([r["first_event_s"] for r in rows if r["first_event_s"]])
                     if any(r["first_event_s"] for r in rows) else None,
                     "completed_s_median": statistics.median([r["completed_s"] for r in rows if r["completed_s"]])
                     if any(r["completed_s"] for r in rows) else None}
    return arms


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--chats", type=int, default=5, help="chats per arm (1-5)")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--authorize-billed-run", action="store_true")
    parser.add_argument("--connection-id", default="")
    parser.add_argument("--codex-home", default="")
    args = parser.parse_args(argv)
    if not 1 <= args.chats <= 5:
        parser.error("--chats must be 1-5")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("OUT_DIR must be new or empty")
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    executable = find_executable("codex", CODEX_BUNDLE if Path(CODEX_BUNDLE).exists() else "")
    fake = None
    if args.authorize_billed_run:
        if not args.connection_id or not args.codex_home:
            parser.error("a billed run needs --connection-id and --codex-home")
        profile = dedicated_profile(args.codex_home, args.connection_id)
        command, env = codex_command(executable, "codex_subscription", str(out / "state"), reuse_login=True,
                                     connection_id=args.connection_id, native_profile=profile)
        provider = "openai"
    else:
        fake = FakeResponses(out, [("message", EMPTY_PLAN)] * 64)
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
        provider = "angelo_probe"
    (out / "command.json").write_text(json.dumps([part for part in command], indent=1))
    result = {"billed": args.authorize_billed_run, "model": args.model, "connection_id": args.connection_id or None,
              "order": ORDER[:2 * args.chats], "first_message": FIRST, "warm_up_message": WARM_UP,
              "started_unix": time.time(), "records": [], "stopped": None}
    session, retries = None, [0]
    try:
        session = Session(command, env, str(out / "workspace"), args.model, provider)
        base = session.start_thread()
        while True:
            warm = dict(session.turn(base, WARM_UP), arm="warm-up", session_id_header=base)
            result["records"].append(warm)
            if not _checked(warm, retries):
                break
        for arm in ORDER[:2 * args.chats]:
            while True:
                thread = session.prediction_fork(base) if arm == "A" else session.start_thread()
                record = dict(session.turn(thread, FIRST), arm=arm, base_thread=base if arm == "A" else None,
                              # What this binary sends (codex_prompt_probe --fork prediction, unbilled): a
                              # prediction fork's session-id header and prompt_cache_key are its base's thread ID.
                              session_id_header=base if arm == "A" else thread)
                result["records"].append(record)
                if not _checked(record, retries):
                    break
    except Stop as stop:
        result["stopped"] = str(stop)
    except Exception as error:  # Recorded with whatever ran; the person decides on a rerun.
        result["stopped"] = "%s: %s" % (type(error).__name__, str(error)[:300])
    finally:
        if session is not None:
            result["stderr_tail"] = [line[-300:] for line in session.rpc.stderr_tail[-5:]]
            session.close()
        if fake is not None:
            result["fake_requests"] = len(fake.bodies)
            result["fake_affinity_headers"] = [headers["values"] for headers in fake.headers]
    completed = [record for record in result["records"] if record["arm"] in ("A", "B") and record["status"] == "completed"]
    result["summary"] = summarize(completed)
    result["retries"] = retries[0]
    (out / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({"stopped": result["stopped"], "summary": result["summary"], "retries": result["retries"],
                      "warm_up": [record["requests"] for record in result["records"] if record["arm"] == "warm-up"]},
                     indent=1))
    return 1 if result["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
