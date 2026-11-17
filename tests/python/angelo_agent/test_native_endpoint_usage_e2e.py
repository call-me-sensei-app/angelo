# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Custom endpoint usage, from each runtime's own usage events to the console's app-reports body.

Nothing here is a hand-built receipt. The Codex provider receives the app-server's
``thread/tokenUsage/updated`` and ``turn/completed`` notifications, and the Claude Code
provider the process's ``result`` (its ``modelUsage`` running totals), exactly as the
runtimes send them. Their own derivation (``provider_usage``) and the controller's real
hooks fill the request's metrics. The controller's finish hands the receipt to the team
account's counting (``team_usage_reports.count_task``), and the saved counts form a batch
that the fake console's strict app-reports handler (``fake_usage_reports``) accepts. The
batch must carry exactly the runtime's input, cached input, output and reasoning tokens,
per endpoint, format, model and profile.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import ClaudeAdapterFixture  # noqa: E402
from fake_usage_reports import FakeUsageReports  # noqa: E402
from test_native_provider_usage import RecordingRPC, claude_result, codex_breakdown, token_update  # noqa: E402
from angelo_agent import custom_endpoints, endpoint_connections, team_usage_buffer as buffer  # noqa: E402
from angelo_agent import team_usage_reports  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.endpoint_connections import mode_for  # noqa: E402
from angelo_agent.providers import CodexProvider  # noqa: E402
from angelo_agent.team_usage_reports import UsageReportTasks  # noqa: E402
from angelo_agent.team_view import AccountView  # noqa: E402

NOW = 1_791_633_600.0          # 2026-10-10T12:00:00Z
TODAY = "2026-10-10"
CONSOLE, ORG, USER = "https://console.angelo.studio", "org-1", "user-1"
IDENTITY = "7b1c2d3e-4f50-4a61-8b72-9c83d94ea5f6"
HEX = IDENTITY.replace("-", "")
DESCRIPTION = {"id": IDENTITY, "name": "Example Proxy", "baseUrl": "https://llm.example.test",
               "formats": {"openai-responses": {"path": "/v1"}, "gemini": {"path": "/gemini/v1beta"},
                           "anthropic-messages": {"path": ""}},
               "models": [{"id": "team-gpt-sol", "format": "openai-responses", "profile": "gpt-6.1-sol"},
                          {"id": "house-model", "format": "openai-responses"},
                          {"id": "gemini-4-carbon", "format": "gemini", "profile": "gemini-4-carbon"},
                          {"id": "team-claude", "format": "anthropic-messages", "profile": "claude-sonnet-5-5"}]}


class Reporting:
    """The signed-in member's usage counting and the console's app-reports route, both real; no network."""

    def __init__(self, case):
        folder = tempfile.TemporaryDirectory()
        case.addCleanup(folder.cleanup)
        self.console = FakeUsageReports()
        self.console.clock = lambda: NOW
        self.tasks = UsageReportTasks()

        def transport(method, url, headers, body):   # The route's own handler: strict body, 400 on any problem.
            status, value, _headers = self.console.handle(ORG, USER, body)
            return status, {}, value
        self.tasks.__dict__.update(
            _lock=threading.Lock(), console_url=CONSOLE, _clock=lambda: NOW, _transport=transport,
            _view=AccountView(status="signed_in", org_id=ORG, user_id=USER, seat=True,
                              scopes=frozenset({"api", "generate"})),
            _usage_pending={}, _usage_pending_endpoints={}, _usage_saving=threading.Lock(),
            _usage_buffer=buffer.UsageBuffer(folder.name), _enqueue=lambda name: None)
        team_usage_reports.attach(self.tasks)
        case.addCleanup(team_usage_reports.attach, None)
        endpoint_connections.set_current(custom_endpoints.endpoints([DESCRIPTION])[0], True)
        case.addCleanup(endpoint_connections.set_current, ())

    def send(self):
        """Save the counted receipts and send their batch as the scheduled report does; the accepted body."""
        self.tasks._save_usage()
        owner = (CONSOLE, ORG, USER)
        batch = self.tasks._usage_buffer.take(owner, NOW)
        delivered, _more = self.tasks._send_usage(owner, batch, token="fixture-token")
        assert delivered, self.console.received[-1]
        return self.console.reports()[-1]


def item(format_id, model, profile, tasks, tokens):
    return dict(zip(buffer.ENDPOINT_KEYS, (TODAY, HEX, format_id, model, profile, tasks) + tokens))


class CodexEndpointUsageTests(unittest.TestCase):
    """Codex formats (Responses and Gemini): the app-server's running totals, differenced per turn."""

    def request(self, format_id, model, turns):
        reporting = Reporting(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        controller = Controller(temp.name)
        self.addCleanup(controller.close)
        controller.set_document(("factory-runtime", 17))
        controller.state["native_id"] = "native-thread"
        controller._begin_metrics()
        controller.busy, controller.cancelled = True, False
        context = controller.runtime_context()
        controller._request_metrics.set_route(mode=mode_for(HEX, format_id), requested_model=model)
        provider = CodexProvider(context, {})
        self.addCleanup(provider.close)
        provider.rpc = RecordingRPC()
        provider.bind_request(context)
        provider._usage.thread_started("native-thread")
        for turn, totals in turns:
            def start(method, parameters, turn=turn, totals=totals, **kwargs):
                for total in totals:   # One update after each model response, as Codex sends them.
                    provider._event("thread/tokenUsage/updated", token_update("native-thread", turn, total))
                provider._event("turn/completed", {"threadId": "native-thread", "turn": {
                    "id": turn, "status": "completed", "durationMs": 1500}})
                return {"turn": {"id": turn}}
            provider.rpc.request = start
            self.assertEqual(provider.run("native-thread", "Offline fixture"), "completed")
        controller._finish_metrics("completed")   # The receipt reaches the account's counting here.
        return reporting.send()

    def test_responses_and_gemini_report_the_runtimes_own_tokens(self):
        for format_id, model, profile in (("openai-responses", "team-gpt-sol", "gpt-6.1-sol"),
                                          ("gemini", "gemini-4-carbon", "gemini-4-carbon")):
            with self.subTest(format=format_id):
                # Two model responses in one turn (a tool call, then the answer): the thread total after each.
                body = self.request(format_id, model, [("turn-a", [codex_breakdown(1201, 1000, 81, 31),
                                                                    codex_breakdown(2402, 2000, 162, 62)])])
                self.assertEqual((body["entries"], body["customEndpoints"]),
                                 ([], [item(format_id, model, profile, 1, (2402, 2000, 162, 62))]))

    def test_an_unmapped_model_is_reported_untested_and_two_turns_add_up(self):
        body = self.request("openai-responses", "house-model", [
            ("turn-a", [codex_breakdown(500, 0, 40, 10)]),
            ("turn-b", [codex_breakdown(1700, 400, 100, 30)])])   # The thread total: turn b used 1200/400/60/20.
        self.assertEqual(body["customEndpoints"], [item("openai-responses", "house-model", None, 1, (1700, 400, 100, 30))])
        self.assertNotIn("Offline fixture", json.dumps(body))


class ClaudeEndpointUsageTests(ClaudeAdapterFixture, unittest.TestCase):
    """Claude Code (Messages): the result's ``modelUsage`` running totals; input counts cache reads and writes."""

    def test_messages_report_the_runtimes_own_tokens(self):
        reporting = Reporting(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        controller = Controller(temp.name)
        self.addCleanup(controller.close)
        controller.set_document(("factory-runtime", 17))
        controller._begin_metrics()
        controller._request_metrics.set_route(mode=mode_for(HEX, "anthropic-messages"), requested_model="team-claude")
        # The provider reports to the controller's own hooks (routing.ChatContext forwards them the same way).
        self.host.record_provider_turn = controller.record_provider_turn
        self.host.record_provider_usage = controller.record_provider_usage
        self.process.on_user = lambda process, _: process.stdout.emit(
            claude_result(403, 3000, 25, 64, api_ms=900, cost=.01, thinking=12))
        self.turn(None, "Offline fixture")
        controller._finish_metrics("completed")
        body = reporting.send()
        self.assertEqual(body["customEndpoints"], [
            item("anthropic-messages", "team-claude", "claude-sonnet-5-5", 1, (403 + 3000 + 25, 3000, 64, 12))])

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main()
