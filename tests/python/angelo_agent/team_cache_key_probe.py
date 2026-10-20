# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What two members' team chats send on the wire: the team cache key, end to end, unbilled.

Each chat is a separate "member's Angelo": its own team account, state folder, Codex profile, working directory
and app-server process. The account signs in to an in-process fake console (``fake_team_console``) that sends the
team's ``cacheAffinitySecret`` with the providers read, exactly as the console contract says; the chat then runs
the real ``CodexProvider.connect`` (``codex_connect.open_thread``, the support check, the key) on Angelo's agent
runtime (``ANGELO_AGENT_RUNTIME`` or the bundled one) with its gateway route on a local recorder laid out like the
Angelo gateway, and sends one turn. The recorder answers the model call with a minimal stream and keeps every
request body and the routing headers. Nothing reaches a provider, the real console or the gateway.

Chats: members A and B of one team (two chats for A), the same team after its secret is rotated, another team
with the same secret value, and A with no secret (an older console: per-thread keys, as Angelo 1.0.1). The report
compares, per chat, ``prompt_cache_key`` and the ``session-id`` header, and the request's static prefix
(``instructions``, ``tools``, ``text.format`` and the input items before the environment context) byte for byte.

    <Blender>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/team_cache_key_probe.py OUT_DIR

Exit status 0 when every expectation holds (``expectations`` in the report).
"""

import argparse
import hashlib
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import fake_team_console  # noqa: E402
from angelo_agent import managed_policy, team_lock  # noqa: E402
from angelo_agent.codex_plan import output_schema  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.providers import CodexProvider  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from angelo_agent.team_account import TeamAccount  # noqa: E402
from angelo_agent.team_http import http_transport  # noqa: E402
from fake_team_console import ORG_ID, OTHER_ORG_ID, FakeConsole  # noqa: E402
from team_gateway_probe import allowed, openai_stream  # noqa: E402
from test_native_team_account import browse, wait_for  # noqa: E402

CONNECTION = "0b6c1d2e-3f40-4a51-8b62-7c83d94e0a1f"  # Any UUID: the team connection's id in these chats.
GRANT_KEY = "agk_" + "p" * 43   # Shaped like a grant key; the recorder accepts anything.
PROMPT = "Reply with OK."
SECRET, ROTATED = os.urandom(32).hex(), os.urandom(32).hex()
PER_THREAD = ("id", "internal_chat_message_metadata_passthrough")


class Recorder:
    """The gateway's OpenAI route on 127.0.0.1: keeps each request's body and routing headers."""

    def __init__(self):
        self.requests, recorder = [], self

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
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                bare = self.path.split("?", 1)[0]
                if method == "POST" and bare == "/v1/openai/v1/responses":
                    def header(name):
                        return self.headers.get(name) or self.headers.get(name.replace("-", "_"))
                    recorder.requests.append({"body": json.loads(raw), "encoding": header("content-encoding"),
                                              "session_id": header("session-id"), "thread_id": header("thread-id")})
                    return self._reply(200, openai_stream(len(recorder.requests)), "text/event-stream")
                if not allowed(method, self.path):
                    return self._reply(404, b'{"error": {"code": "NOT_FOUND", "message": "Not found."}}')
                return self._reply(200, b'{"object": "list", "data": [{"id": "gpt-6.1-sol", "object": "model"}]}')

            def do_GET(self):  # noqa: N802
                self._handle("GET")

            def do_POST(self):  # noqa: N802
                self._handle("POST")

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def signed_in_member(folder, user_id, email, org=ORG_ID, secret=SECRET):
    """A member's own team account, signed in to its own fake console that sends ``secret`` (or none)."""
    fake_team_console.USER_ID = user_id   # The fake console names one person; each member signs in as their own.
    console = FakeConsole(email=email)
    console.cache_affinity_secret = secret
    account = TeamAccount(console.url, vault=None, transport=http_transport,
                          process_lock=team_lock.factory(str(folder), 5), policy=Policy)
    status, _final, _page = browse(console.callback(account.begin_sign_in(device="Probe"), org=org))
    if status != 200:
        raise RuntimeError("the fake console refused the sign-in (%s)" % status)
    wait_for(lambda: account.view().signed_in and account.view().providers is not None, 15)
    if secret:
        wait_for(lambda: account.cache_affinity_secret() is not None, 15)
    return console, account


def chat(folder, account, recorder):
    """One new team chat in ``folder`` for ``account``: connect, one turn; the recorded request."""
    seen = len(recorder.requests)
    with patch("angelo_agent.team_library_access.account", lambda: account):
        controller = Controller(str(folder / "state"))
        try:
            controller.set_document(("probe", 1))
            controller.state.update(provider="codex_team", native_id=None, toolset_version=TOOLSET_VERSION)
            controller.settings = {"connection_id": CONNECTION, "model": "gpt-6.1-sol"}
            controller.api_key = GRANT_KEY
            controller._begin_metrics()
            context = controller.runtime_context()
            provider = CodexProvider(context, context.settings)
            try:
                provider.connect()
                provider.rpc.request("turn/start", {
                    "threadId": provider.thread_id, "input": [{"type": "text", "text": PROMPT}], "effort": "low",
                    "model": "gpt-6.1-sol", "outputSchema": output_schema(None)})
                wait_for(lambda: len(recorder.requests) > seen, 60)
                time.sleep(0.5)
                return dict(recorder.requests[seen], thread=provider.thread_id, keyed=provider._team_keyed)
            finally:
                provider.close()
        finally:
            controller.close()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def summary(request):
    """What a chat's first request carried: its routing key and the digests of its static prefix."""
    body = request["body"]
    items = body.get("input") or []
    environment = next((i for i, item in enumerate(items) if "<environment_context>" in json.dumps(item)), len(items))
    # Per-thread item ids and Codex's per-turn metadata (turn id, time) are not prompt text: left out.
    prefix = [{k: v for k, v in item.items() if k not in PER_THREAD} for item in items[:environment]]
    return {"prompt_cache_key": body.get("prompt_cache_key"), "session_id": request["session_id"],
            "thread": request["thread"], "keyed": request["keyed"],
            "prefix": {"instructions": digest(body.get("instructions")), "tools": digest(body.get("tools")),
                       "format": digest((body.get("text") or {}).get("format")),
                       "items_before_environment": digest(prefix),
                       "items_counted": environment},
            "bytes": len(json.dumps(body))}


def run(out):
    managed_policy.reset(Policy())
    recorder = Recorder()
    environ = {"ANGELO_GATEWAY_URL": recorder.url, "ANGELO_CACHE_KEY_TRACE": "1"}
    plan = [  # name, member, email, org, console secret
        ("A1", "11111111-1111-4111-8111-111111111111", "ana@northwind.example", ORG_ID, SECRET),
        ("B1", "22222222-2222-4222-8222-222222222222", "ben@northwind.example", ORG_ID, SECRET),
        ("A2", "11111111-1111-4111-8111-111111111111", "ana@northwind.example", ORG_ID, SECRET),
        ("B-rotated", "22222222-2222-4222-8222-222222222222", "ben@northwind.example", ORG_ID, ROTATED),
        ("other-team", "33333333-3333-4333-8333-333333333333", "cy@elsewhere.example", OTHER_ORG_ID, SECRET),
        ("A-no-secret-1", "11111111-1111-4111-8111-111111111111", "ana@northwind.example", ORG_ID, None),
        ("A-no-secret-2", "11111111-1111-4111-8111-111111111111", "ana@northwind.example", ORG_ID, None),
    ]
    results = {}
    with patch.dict(os.environ, environ):
        os.environ.pop("ANGELO_TEAM_CACHE_KEY_SECRET", None)
        # Each member's folders live outside any Git checkout: Codex reports an enclosing repository's remotes in
        # its client metadata, which a person's Angelo state folder never has.
        work = Path(tempfile.mkdtemp(prefix="team-cache-key-chats-"))
        for name, user, email, org, secret in plan:
            folder = work / name
            folder.mkdir(parents=True)
            console, account = signed_in_member(folder, user, email, org, secret)
            try:
                results[name] = summary(chat(folder, account, recorder))
                results[name]["member"], results[name]["org"] = user[:8], org[:8]
            finally:
                account.close()
                console.close()
    key = {name: result["prompt_cache_key"] for name, result in results.items()}
    prefix = {name: result["prefix"] for name, result in results.items()}
    expectations = {
        "A1 and B1 (two members) send one key": key["A1"] == key["B1"],
        "A2 (A's next chat) sends the same key": key["A2"] == key["A1"],
        "the session-id header follows the key":
            results["A1"]["session_id"] == results["B1"]["session_id"] == key["A1"],
        "the team key is not a thread id": key["A1"] not in {r["thread"] for r in results.values()},
        "a rotated secret gives another key": key["B-rotated"] != key["A1"],
        "another team (same secret value) gives another key": key["other-team"] != key["A1"],
        "no secret: each chat keyed by its own thread": all(
            key[n] == results[n]["thread"] for n in ("A-no-secret-1", "A-no-secret-2")) and
            key["A-no-secret-1"] != key["A-no-secret-2"],
        "every chat's static prefix is byte-identical":
            len({json.dumps(p, sort_keys=True) for p in prefix.values()}) == 1,
        "keyed chats say so on their receipt route": all(results[n]["keyed"] for n in ("A1", "B1", "A2")) and
            not results["A-no-secret-1"]["keyed"],
    }
    report = {"chats": results, "expectations": expectations, "passed": all(expectations.values()),
              "note": "Unbilled: fake console and a local gateway recorder; secrets are random per run and never "
                      "written here; keys are pseudonyms of (team, model, prefix)."}
    (out / "team-cache-key-probe.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("out", nargs="?", default="")
    args = parser.parse_args(argv)
    out = Path(args.out or tempfile.mkdtemp(prefix="team-cache-key-probe-"))
    out.mkdir(parents=True, exist_ok=True)
    report = run(out)
    for name, held in report["expectations"].items():
        print(("PASS " if held else "FAIL ") + name)
    print("Report: %s" % (out / "team-cache-key-probe.json"))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
