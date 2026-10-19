# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent usage reports from the team account to the in-process fake console.

The real ``TeamAccount`` signs in through its loopback listener, counts tasks
from real receipts (also through ``Controller._finish_metrics``), saves them in
``team-usage.json`` and reports them to ``fake_usage_reports``, which checks
every body against the console's schema: batching, a lost answer resent with
the same ``batchId`` and counted once, 400/403/404 dropping a batch, 429 and
``Retry-After``, a refused access token renewed once, quit within its second
(and a slow console leaving the batch for the next start), signed out sending
nothing, and four real processes sharing one profile. No real console,
network or key store is used.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import team_lock, team_usage_reports  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.team_account import TeamAccount  # noqa: E402
from angelo_agent.team_usage_buffer import FILE, UsageBuffer, day_of  # noqa: E402
from angelo_agent.telemetry import RequestMetrics  # noqa: E402
from fake_team_console import ORG_ID, USER_ID  # noqa: E402
from test_native_team_account import AccountCase, FileVault, wait_for  # noqa: E402
from test_native_team_usage_reports import receipt  # noqa: E402

OPUS = ("anthropic", "claude-opus-5-5")


class FlushCase(AccountCase):
    def setUp(self):
        super().setUp()
        self.console.reports.clock = self.clock.time  # The console's "today" is the test clock's.
        self.owner = (self.console.url, ORG_ID, USER_ID)

    def usage_account(self, vault=None, clock=None, monotonic=None):
        account = TeamAccount(self.console.url, vault=self.vault if vault is None else vault,
                              process_lock=team_lock.factory(self.folder.name, 5), policy=lambda: self.policy,
                              clock=clock or self.clock.time, monotonic=monotonic or self.clock.monotonic,
                              usage_buffer=UsageBuffer(self.folder.name))
        self.addCleanup(self.stop, account)
        return account

    def count(self, account, times=1, **fields):
        for _ in range(times):
            self.assertTrue(account.count_agent_task(receipt(**fields)))

    def report_requests(self):
        return [r for r in self.console.requests if r[1].endswith("/usage/app-reports")]

    def pending(self, now=None):
        return UsageBuffer(self.folder.name).pending(self.owner, self.clock.time() if now is None else now)

    def today(self, *key):
        return (day_of(self.clock.time()),) + key

    def assert_quiet(self, account):
        """Nothing about reports reaches what the person sees."""
        view = account.view()
        self.assertTrue(view.signed_in)
        self.assertEqual((view.message, view.providers_error, view.grant_error), ("", "", ""))


class ReportTests(FlushCase):
    def test_tasks_reach_the_console_in_one_batch_and_nothing_secret_is_kept(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 2, seconds=95)
        self.count(account, 1, mode="codex_team", requested="gpt-6.1-sol", seconds=20)
        self.assertFalse(account.count_agent_task(receipt("claude_api")), "a personal connection is not team usage")
        wait_for(lambda: Path(self.folder.name, FILE).exists())
        self.assertEqual(self.report_requests(), [], "counting a task does no network work")
        account._task_usage_report()
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (2, 95000),
                                                         self.today("openai", "gpt-6.1-sol"): (1, 20000)})
        request, = self.report_requests()
        self.assertEqual(request[1], "/api/orgs/%s/usage/app-reports" % ORG_ID)
        self.assertEqual(request[3]["Content-Type"], "application/json")
        self.assertEqual(self.pending(), ({}, None))
        access = account.access_token()
        for path in Path(self.folder.name).rglob("*"):
            data = path.read_bytes()
            self.assertNotIn(access.encode(), data)
            self.assertNotIn(self.vault.token.encode(), data)
        self.assert_quiet(account)

    def test_reports_follow_the_15_minute_schedule(self):
        account = self.usage_account()
        self.sign_in(account)
        account.tick(idle=True)  # The first tick signed in reports at once (a batch an offline session left).
        wait_for(lambda: "usage_report" in account._last)
        self.count(account, 1)
        account.tick(idle=True)
        time.sleep(0.2)
        self.assertEqual(self.console.reports.totals(), {})
        self.clock.advance(team_usage_reports.REPORT_EVERY)
        account.tick(idle=True)
        wait_for(lambda: self.console.reports.totals() == {self.today(*OPUS): (1, 95000)})

    def test_a_lost_answer_is_sent_again_with_the_same_batch_id_and_counted_once(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 3)
        self.console.reports.fail_next = "drop"  # Applied, then the connection closes without an answer.
        account._task_usage_report()
        sent = self.pending()[1]
        self.assertIsNotNone(sent, "the batch is kept until a 2xx")
        account._task_usage_report()
        self.assertEqual([b["batchId"] for b in self.console.reports.reports()], [sent["batchId"]] * 2)
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (3, 95000)})
        self.assertEqual(self.pending(), ({}, None))
        self.assert_quiet(account)

    def test_a_server_error_keeps_the_batch_and_a_refusal_drops_it(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 1)
        self.console.reports.fail_next = 500
        account._task_usage_report()
        kept = self.pending()[1]
        self.assertIsNotNone(kept)
        log = io.StringIO()
        self.console.reports.fail_next = 400
        with contextlib.redirect_stderr(log):
            account._task_usage_report()
        self.assertEqual(json.loads(self.console.reports.received[-1][1])["batchId"], kept["batchId"])
        self.assertEqual(self.pending(), ({}, None), "a malformed batch can never land: dropped")
        self.assertEqual(log.getvalue(), "Angelo: the console refused a usage report as malformed (HTTP 400); "
                                         "it was dropped\n")
        for status in (403, 404):
            with self.subTest(status=status):
                self.count(account, 1)
                self.console.reports.fail_next = status
                account._task_usage_report()
                self.assertEqual(self.pending(), ({}, None))
        self.assertEqual(self.console.reports.totals(), {})
        self.count(account, 1)
        account._task_usage_report()
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (1, 95000)})
        self.assert_quiet(account)

    def test_429_waits_for_retry_after_in_every_process(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 1)
        self.console.reports.fail_next = 429  # Retry-After: 120
        account._task_usage_report()
        refused = json.loads(self.console.reports.received[-1][1])["batchId"]
        requests = len(self.report_requests())
        self.clock.advance(119)
        account._task_usage_report()
        self.usage_account()._report_at_exit()  # Another window quitting waits too.
        self.assertEqual(len(self.report_requests()), requests)
        self.clock.advance(2)
        account._task_usage_report()
        self.assertEqual(self.console.reports.reports()[-1]["batchId"], refused)
        # The console's own limit (60 an hour per person, here 1): its Retry-After holds the next report.
        self.console.reports.limit = 1
        self.count(account, 1)
        self.clock.advance(15 * 60)
        account._task_usage_report()
        self.assertEqual(self.console.reports.received[-1][0], 429)
        requests = len(self.report_requests())
        self.console.reports.limit = 60
        self.clock.advance(30 * 60)
        account._task_usage_report()
        self.assertEqual(len(self.report_requests()), requests, "not before the console's Retry-After")
        self.assertIsNotNone(self.pending()[1])
        self.clock.advance(31 * 60)
        account._task_usage_report()
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (2, 95000)})
        self.assert_quiet(account)

    def test_an_unexpected_failure_is_logged_by_kind_and_never_shown(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 1)
        log = io.StringIO()
        with contextlib.redirect_stderr(log), unittest.mock.patch.object(
                account._usage_buffer, "take", side_effect=ValueError("secret detail")):
            account._enqueue("usage_report")
            wait_for(lambda: "usage_report" in account._last)
        self.assertEqual(log.getvalue(), "Angelo: a usage report step failed (ValueError); it is tried again later\n")
        self.assert_quiet(account)
        account._task_usage_report()
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (1, 95000)})

    def test_a_refused_access_token_is_renewed_once(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 1)
        refreshes = len(self.console.refresh_requests())
        with self.console.lock:
            self.console.access.clear()  # The console refuses the access token the account holds.
        account._task_usage_report()
        self.assertEqual(len(self.console.refresh_requests()), refreshes + 1)
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (1, 95000)})
        self.assert_quiet(account)


class SignedOutTests(FlushCase):
    def test_signed_out_nothing_is_counted_or_sent(self):
        account = self.usage_account()
        self.assertFalse(account.count_agent_task(receipt()))
        account.tick(idle=True)
        account._task_usage_report()
        account.close()
        self.assertEqual(self.report_requests(), [])
        self.assertFalse(Path(self.folder.name, FILE).exists())

    def test_after_sign_out_counts_wait_for_the_same_person(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 2)
        account.sign_out()
        account._task_usage_report()
        account.close()
        self.assertEqual(self.report_requests(), [])
        self.assertEqual(self.pending()[0], {self.today(*OPUS): [2, 95000]})
        again = self.usage_account()
        self.sign_in(again)
        again._task_usage_report()
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (2, 95000)})


class QuitTests(FlushCase):
    def test_quit_reports_what_is_left(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 2)
        started = time.monotonic()
        account.close()
        self.assertLess(time.monotonic() - started, team_usage_reports.EXIT_SECONDS + 0.5)
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (2, 95000)})
        self.assertEqual(self.pending(), ({}, None))

    def test_a_slow_console_never_holds_quit_and_the_batch_goes_at_the_next_start(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 3)
        self.console.reports.delay = 3.0
        started = time.monotonic()
        account.close()
        self.assertLess(time.monotonic() - started, team_usage_reports.EXIT_SECONDS + 0.5)
        left = self.pending()[1]
        self.assertIsNotNone(left, "formed once at quit, kept with its id")
        self.console.reports.delay = 0.0
        restarted = self.usage_account()
        restarted.restore()
        wait_for(lambda: restarted.view().signed_in)
        restarted._task_usage_report()
        wait_for(lambda: len(self.console.reports.reports()) == 2, timeout=10)  # The slow answer lands too.
        self.assertEqual({b["batchId"] for b in self.console.reports.reports()}, {left["batchId"]})
        self.assertEqual(self.console.reports.totals(), {self.today(*OPUS): (3, 95000)})
        wait_for(lambda: not any(t.name == "AngeloUsageReport" for t in threading.enumerate()), timeout=10)

    def test_quit_never_renews_a_token(self):
        account = self.usage_account()
        self.sign_in(account)
        self.count(account, 1)
        self.clock.mono += 3600  # The access token has expired.
        refreshes = len(self.console.refresh_requests())
        account.close()
        self.assertEqual((self.report_requests(), len(self.console.refresh_requests())), ([], refreshes))
        self.assertEqual(self.pending()[0], {self.today(*OPUS): [1, 95000]})


class ControllerHookTests(FlushCase):
    def finish(self, controller, mode, status):
        controller._request_metrics = RequestMetrics("claude")
        controller._request_metrics.set_route(mode=mode, requested_model="claude-opus-5-5")
        controller._finish_metrics(status)

    def test_the_chat_controller_counts_each_completed_team_request(self):
        account = self.usage_account()
        self.sign_in(account)
        team_usage_reports.attach(account)
        self.addCleanup(team_usage_reports.attach, None)
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            for mode, status in (("claude_team", "completed"), ("claude_team", "failed"),
                                 ("claude_subscription", "completed"), ("claude_team", "completed")):
                self.finish(controller, mode, status)
            controller.telemetry.flush()
        account._task_usage_report()
        (key, (tasks, _longest)), = self.console.reports.totals().items()
        self.assertEqual((key, tasks), (self.today(*OPUS), 2))

    def test_a_failing_account_never_disturbs_the_chat(self):
        class Broken:
            def count_agent_task(self, receipt):
                raise RuntimeError("boom")
        team_usage_reports.attach(Broken())
        self.addCleanup(team_usage_reports.attach, None)
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            self.finish(controller, "claude_team", "completed")
            controller.telemetry.flush()
        self.assertIsNone(team_usage_reports.count_task(None))


_CHILD = """
import json, os, sys, time
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from angelo_agent import team_lock
from angelo_agent.managed_policy import Policy
from angelo_agent.team_account import TeamAccount
from angelo_agent.team_usage_buffer import UsageBuffer
from test_native_team_account import FileVault
from test_native_team_usage_reports import receipt
folder, console, tasks = sys.argv[3], sys.argv[4], int(sys.argv[5])
account = TeamAccount(console, vault=FileVault(os.path.join(folder, "vault")), policy=Policy,
                      process_lock=team_lock.factory(folder, 30), usage_buffer=UsageBuffer(folder))
while not os.path.exists(os.path.join(folder, "go")):
    time.sleep(0.005)
account.restore()
deadline = time.monotonic() + 20
while account.view().status != "signed_in" and time.monotonic() < deadline:
    time.sleep(0.01)
for index in range(tasks):
    account.count_agent_task(receipt(seconds=1 + index))
    if index % 5 == 4:
        account._task_usage_report()  # A scheduled report while the others count.
account.close()  # Quit: the report at exit.
print(json.dumps({"status": account.view().status}))
"""


class MultiProcessTests(FlushCase):
    def test_processes_sharing_a_profile_report_every_task_exactly_once(self):
        self.console.reports.clock = time.time
        vault = FileVault(os.path.join(self.folder.name, "vault"))
        first = self.usage_account(vault=vault, clock=time.time, monotonic=time.monotonic)
        self.sign_in(first)
        first.close()
        here, addons = str(Path(__file__).resolve().parent), str(ROOT / "scripts/addons_core")
        children = [subprocess.Popen([sys.executable, "-c", _CHILD, addons, here, self.folder.name, self.console.url,
                                      "20"], stdout=subprocess.PIPE, text=True) for _ in range(4)]
        time.sleep(0.5)
        Path(self.folder.name, "go").touch()
        statuses = [json.loads(child.communicate(timeout=90)[0].strip().splitlines()[-1])["status"]
                    for child in children]
        self.assertEqual(statuses, ["signed_in"] * 4)
        # The next start sends whatever a quitting process left behind (another one's batch was in flight).
        last = self.usage_account(vault=vault, clock=time.time, monotonic=time.monotonic)
        last.restore()
        wait_for(lambda: last.view().signed_in, timeout=10)
        for _ in range(5):
            if self.pending(time.time()) == ({}, None):
                break
            last._report_at_exit()
        self.assertEqual(self.pending(time.time()), ({}, None))
        self.assertEqual(self.console.reports.totals(), {(day_of(time.time()),) + OPUS: (80, 20000)})
        self.assertEqual(self.console.revoked, {}, "the processes shared one sign-in without reusing a token")


if __name__ == "__main__":
    unittest.main()
