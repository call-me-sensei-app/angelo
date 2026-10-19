# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent tasks on team connections, counted here and reported to the Angelo console in batches.

Contract: doc/angelo/ai_providers.rst, "Agent usage reports"; the console's
``POST /api/orgs/{org}/usage/app-reports``. The gateway meters tokens and cost;
only the app knows what a task is, so it reports, per UTC day, gateway
provider and model, how many agent tasks finished and the longest one.

* **A task** is one completed agent request (``Controller._finish_metrics``
  with status ``completed``) on a team coding connection (``claude_team``,
  ``codex_team`` and the other ``TEAM`` coding modes): its length is the
  receipt's ``total_ms``, from send to idle. Personal connections, failed,
  stopped and escalated requests, and local preview acceptances (no route)
  are not counted. ``task_entry`` reads only the receipt's allowlisted route
  and phases; no prompt, path or token is involved.
* **Counting** (``count_task``, the controller's hook) does no I/O: the count
  is kept in memory, attributed to the signed-in person, and the account's
  worker saves it to ``team_usage_buffer`` soon after.
* **Reporting** runs on the account's worker about every 15 minutes while
  signed in (``REPORT_EVERY``; the first at sign-in or at start, which sends a
  batch left by an offline session), and at quit (``report_at_exit``: at most
  ``EXIT_SECONDS``, without a token refresh; what is left goes at the next
  start). A 2xx removes the batch (``applied: false`` means it already landed);
  429 waits for ``Retry-After``; 400, 403 and 404 drop the batch, since it can
  never land, and a 400 is logged; other failures keep it for the next try.
  An expired token is renewed once as for every console call; nothing here is
  ever shown to the person.

Standard library only; no bpy. ``TeamAccount`` provides ``_lock``, ``_view``,
``_clock``, ``_monotonic``, ``_enqueue``, ``_authorized``, ``_transport``,
``_access``, ``_access_expiry``, ``_closed`` and ``console_url``.
"""

import json
import sys
import threading
from urllib.parse import quote

from . import model_profiles
from .provider_registry import CODING, SPECS, TEAM
from .team_errors import SignInRequired, TeamError, retry_after_seconds
from .team_lock import LockTimeout
from .team_usage_buffer import MAX_TASK_MS, day_of, merge
from .team_view import OFFLINE, SIGNED_IN

REPORT_EVERY = 15 * 60          # seconds between scheduled reports of one process
EXIT_SECONDS = 1.0              # the most a quit waits for its report
MAX_BATCHES = 3                 # reports one scheduled run sends back to back (over 200 entries waiting)
LIMITED_WAIT = 15 * 60          # seconds to wait after a 429 without a usable Retry-After
TEAM_CODING = {mode: spec["team_provider"] for mode, spec in SPECS.items()
               if spec["method"] == TEAM and spec["group"] == CODING}
_TIERS = {"strong": 0, "balanced": 1, "fast": 2}

_account = None  # The team account that counts this session's tasks (team_account_ui.start), or None.


def attach(account):
    """``account`` counts the tasks every chat finishes from now on; None stops counting."""
    global _account
    _account = account


def count_task(receipt):
    """The controller's hook: a finished request's receipt (``telemetry.clean_receipt``). Never raises."""
    account = _account
    if account is not None:
        try:
            account.count_agent_task(receipt)
        except Exception:  # noqa: BLE001 - usage reporting must never disturb the chat that finished.
            pass


def task_model(route):
    """The model a task is reported under: the one the runtime served (as the gateway records it).

    The requested model when the runtime served it (or reported nothing), else the strongest known served model
    (Claude Code may also call a fast model for small chores), else ``unknown`` like the gateway.
    """
    requested = route.get("requested_model") or ""
    requested = model_profiles.resolve(requested) or requested
    served = [m for m in route.get("served_models", ()) if isinstance(m, str) and m]
    match = [m for m in served if m == requested or requested and m.startswith((requested + "-", requested + "["))]
    if requested and (match or not served):
        return match[0] if match else requested
    if not served:
        return "unknown"

    def rank(model):
        facts = model_profiles.MODELS.get(model_profiles.resolve(model) or "")
        return _TIERS.get(facts["tier"] if facts else "", 1), model
    return min(served, key=rank)


def task_entry(receipt):
    """``(provider, model, ms)`` of a receipt that counts as a team agent task, else None."""
    if not isinstance(receipt, dict) or receipt.get("status") != "completed":
        return None
    route, phases = receipt.get("route") or {}, receipt.get("phases") or {}
    provider = TEAM_CODING.get(route.get("mode"))
    milliseconds = phases.get("total_ms")
    if provider is None or type(milliseconds) not in {int, float} or not 0 <= milliseconds < float("inf"):
        return None
    return provider, task_model(route)[:200], min(MAX_TASK_MS, int(round(milliseconds)))


def owner_of(console_url, view):
    """``(console, organization, user)`` whose tasks these are, or None when no team sign-in is in use."""
    usable = view.status in {SIGNED_IN, OFFLINE} and view.org_id and view.user_id
    return (console_url, view.org_id, view.user_id) if usable else None


def report_path(org_id):
    return "/api/orgs/%s/usage/app-reports" % quote(org_id, safe="")


def outcome(status):
    """What to do with a batch after an answer with HTTP ``status`` (None: no answer)."""
    if status is not None and 200 <= status < 300:
        return "sent"
    return "drop" if status in {400, 403, 404} else "retry"


def _log(message):
    """A fixed line for the console log: never a token, a body or a batch."""
    sys.stderr.write("Angelo: %s\n" % message)


def _quietly(work):
    """Run report work on the account's worker. A failure is logged by its kind only and never reaches the
    worker, which would show it as the account's error: reports are never shown to the person."""
    try:
        work()
    except Exception as error:  # noqa: BLE001
        _log("a usage report step failed (%s); it is tried again later" % type(error).__name__)


class UsageReportTasks:
    def count_agent_task(self, receipt):
        """Any thread, no I/O: count a finished request for the signed-in person and queue the save."""
        found = task_entry(receipt)
        if found is None or self._usage_buffer is None:
            return False
        provider, model, milliseconds = found
        with self._lock:
            owner = owner_of(self.console_url, self._view)
            if owner is None:
                return False  # Not signed in to a team: nothing is counted or sent.
            key = (day_of(self._clock()), provider, model)
            merge(self._usage_pending.setdefault(owner, {}), {key: (1, milliseconds)})
        self._enqueue("usage_save")
        return True

    def _save_usage(self):
        """Move the counts kept in memory into the buffer; on a failure they stay for the next try.

        One save at a time: a quit's save waits for a worker's that already took counts out of memory.
        """
        with self._usage_saving:
            with self._lock:
                pending, self._usage_pending = self._usage_pending, {}
            try:
                for owner in list(pending):
                    self._usage_buffer.add(owner, pending[owner], self._clock())
                    del pending[owner]
            except (LockTimeout, OSError):
                with self._lock:
                    for owner, counts in pending.items():
                        merge(self._usage_pending.setdefault(owner, {}), counts)
                return False
            return True

    def _task_usage_save(self):
        if self._usage_buffer is not None:
            _quietly(self._save_usage)

    def _task_usage_report(self):
        if self._usage_buffer is not None:
            _quietly(self._report_usage)

    def _report_usage(self):
        """A scheduled report: save, then send the person's pending batches (at most ``MAX_BATCHES``)."""
        if not self._save_usage():
            return
        with self._lock:
            view = self._view
            owner = owner_of(self.console_url, view) if view.signed_in and "api" in view.scopes else None
        try:
            for index in range(MAX_BATCHES if owner else 0):
                # Only the first waits for the shared schedule; the rest follow a delivered batch at once.
                batch = self._usage_buffer.take(owner, self._clock(), scheduled=index == 0)
                if batch is None:
                    return
                delivered, more = self._send_usage(owner, batch)
                if not (delivered and more):
                    return
        except (LockTimeout, OSError):
            return  # The next scheduled report tries again.

    def _send_usage(self, owner, batch, token=None):
        """Send one batch and record the outcome: ``(delivered, more waiting)``. ``token``: the exit path's."""
        body = json.dumps(batch, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        status, hold = None, 0
        try:
            if token is None:
                self._authorized("POST", report_path(owner[1]), body, headers)
                status = 200
            else:
                headers.update(Authorization="Bearer " + token, Accept="application/json")
                status, answer, _data = self._transport("POST", self.console_url + report_path(owner[1]), headers, body)
                hold = retry_after_seconds(answer.get("retry-after")) if status == 429 else 0
        except SignInRequired:
            pass  # The sign-in ended (the account says so where it shows itself); the batch waits for it.
        except TeamError as error:
            status = error.status
            hold = error.retry_after if status == 429 else 0
        except OSError:
            pass  # NotSent or Unanswered on the exit path: the batch is sent again at the next start.
        if status == 429:
            hold = hold or LIMITED_WAIT
        result = outcome(status)
        if status == 400:
            _log("the console refused a usage report as malformed (HTTP 400); it was dropped")
        more = self._usage_buffer.settle(owner, batch["batchId"], result, self._clock(), hold)
        return result == "sent", more

    def report_at_exit(self, timeout=EXIT_SECONDS):
        """At quit: save the counts and send one report, waiting at most ``timeout`` seconds in all."""
        if self._usage_buffer is None or self._closed:
            return
        thread = threading.Thread(target=self._report_at_exit, daemon=True, name="AngeloUsageReport")
        thread.start()
        thread.join(timeout)  # A daemon left sending is ended with the process; its batch stays for the next start.

    def _report_at_exit(self):
        try:
            if not self._save_usage():
                return
            with self._lock:
                view = self._view
                usable = self._access and self._access_expiry - self._monotonic() > 5  # No refresh at quit.
                token = self._access if usable and view.signed_in and "api" in view.scopes else ""
                owner = owner_of(self.console_url, view)
            batch = self._usage_buffer.take(owner, self._clock(), scheduled=False, lease=0) if token and owner else None
            if batch is not None:
                self._send_usage(owner, batch, token)
        except Exception:  # noqa: BLE001 - quitting must never fail for a usage report.
            pass
