# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What happens to one crash from the dialog's point of view: the choice, the send, and what is remembered.

``CrashFlow`` holds a pending crash's report and moves through ``ask`` -> ``sending``
-> ``sent`` or ``failed`` (or ``review`` for a report sent earlier). Rules:

* **Nothing is sent without the person's yes**: Send in the dialog, or ``consent == "always"`` which the
  person set themselves by ticking "Remember my choice" on Send. ``never`` shows no dialog at all.
* "Remember my choice" applies to the button pressed: Send makes ``always``, Don't Send makes ``never``.
  Closing, saving or reporting a problem never changes consent.
* What is shown is ``crash_report.render(document)``; what is sent is ``crash_report.body(document)``,
  the same text, encoded. ``text`` and ``payload`` are built once, here.
* Every crash is recorded exactly once on the way out (``finish``): sent, declined, saved, dismissed. A
  failed automatic send is asked again at the next launch, up to ``crash_state.MAX_ATTEMPTS`` times.
* The last report sent is kept (``last-crash-report.json``) so the person can read exactly what left.

The upload runs on a worker thread; ``poll()`` (main thread) picks up its result.
Standard library only; no bpy.
"""

import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from . import app_version, crash_report, crash_state, crash_upload
from .studio_state import atomic_json, read_json

LAST_REPORT_FILE = "last-crash-report.json"
ASK, SENDING, SENT, FAILED, REVIEW = "ask", "sending", "sent", "failed", "review"


class CrashFlow:
    def __init__(self, directory, pending, document, endpoint, *, send=crash_upload.send, clock=time.time):
        self.directory, self.pending, self.document, self.endpoint = Path(directory), pending, document, endpoint
        self.send_function, self.clock = send, clock
        self.text = crash_report.render(document)
        self.payload = crash_report.body(document)
        assert self.payload == self.text.encode("ascii")
        self.phase, self.outcome, self.saved, self.remember = ASK, None, False, False
        self._result, self._lock = None, threading.Lock()
        self._finished = False

    @property
    def report_id(self):
        return self.document["report_id"]

    @property
    def can_send(self):
        return bool(self.endpoint)

    @classmethod
    def review(cls, directory, last):
        """A read-only flow showing a report that was sent earlier (``last_sent``)."""
        flow = cls(directory, None, json.loads(last["text"]), "")
        flow.phase = REVIEW
        return flow

    # -- the person's choices ---------------------------------------------------------------
    def start_send(self, remember=False):
        """Send now (the person pressed Send, or consent is ``always``). Returns False when it can't."""
        if not self.can_send or self.phase in {SENDING, SENT}:
            return False
        self.remember = remember
        if remember:   # The person's explicit choice, kept even if this send fails.
            self._consent(crash_state.ALWAYS)
        self.phase = SENDING
        threading.Thread(target=self._work, name="angelo-crash-upload", daemon=True).start()
        return True

    def _work(self):
        outcome = self.send_function(self.endpoint, self.payload, self.report_id)
        with self._lock:
            self._result = outcome

    def poll(self):
        """Main thread: True when a send just finished (``phase`` is then ``sent`` or ``failed``)."""
        with self._lock:
            outcome, self._result = self._result, None
        if outcome is None:
            return False
        self.outcome = outcome
        self.phase = SENT if outcome.kind == crash_upload.SENT else FAILED
        if self.phase == SENT:
            self._remember_sent()
        return True

    def decline(self, remember=False):
        """Don't Send."""
        if remember:
            self._consent(crash_state.NEVER)
        self.finish(crash_state.DECLINED)

    def mark_saved(self):
        self.saved = True

    # -- recording ----------------------------------------------------------------------------
    def finish(self, status=None):
        """Record the crash as dealt with. Idempotent; returns the status recorded (None for a review).

        A send that failed for a reason worth retrying (offline, a busy or failing service) is recorded as
        failed, so the next launch asks again, not before the server's Retry-After, until the attempts run out.
        """
        if self._finished or self.pending is None:
            return None
        self._finished = True
        state = crash_state.load(self.directory)
        if status is None:
            if self.phase == SENT:
                status = crash_state.SENT
            elif self.saved:
                status = crash_state.SAVED
            elif self.phase == SENDING or (self.phase == FAILED and self._retryable()):
                status = self._failure_status(state)
            else:
                status = crash_state.DISMISSED
        self._mark(state, status)
        return status

    def finish_automatic(self):
        """After an automatic send: sent is final; a failure is tried again at a later launch, then given up."""
        state = crash_state.load(self.directory)
        status = crash_state.SENT if self.phase == SENT else (
            self._failure_status(state) if self._retryable() else crash_state.GAVE_UP)
        self._mark(state, status)
        self._finished = True
        return status

    def _retryable(self):
        return self.outcome is not None and self.outcome.kind in {crash_upload.OFFLINE, crash_upload.RETRY}

    def _failure_status(self, state):
        last = state.attempts(self.pending.fingerprint) + 1 >= crash_state.MAX_ATTEMPTS
        return crash_state.GAVE_UP if last else crash_state.FAILED

    def _mark(self, state, status):
        wait = self.outcome.retry_after if self.outcome is not None else 0.0
        state.mark(self.pending.fingerprint, status, self.report_id if status == crash_state.SENT else "", self.clock(),
                   not_before=self.clock() + wait if wait else 0.0)
        crash_state.save(self.directory, state)

    def _consent(self, consent):
        state = crash_state.load(self.directory)
        state.consent = consent
        crash_state.save(self.directory, state)

    def _remember_sent(self):
        try:
            atomic_json(self.directory / LAST_REPORT_FILE, {"sent_at": self.clock(), "report_id": self.report_id,
                                                           "text": self.text})
        except OSError:
            pass  # The report was sent; only the local copy for "what was sent" is missing.

    # -- links and files -------------------------------------------------------------------------
    def problem_url(self):
        """The report-a-problem page with the environment already filled in (the report's own fields)."""
        system, app = self.document["system"], self.document["app"]
        query = {"os": ("%s %s" % (system["os"], system["os_version"])).strip(), "gpu": system["gpu"]["renderer"],
                 "version": "%s (%s)" % (app["version"], app["build_hash"] or "unknown build")}
        if self.phase == SENT:
            query["report"] = self.report_id
        return app_version.link("report_bug") + "?" + urlencode({k: v for k, v in query.items() if v})

    def default_file_name(self):
        return crash_report.default_file_name(self.document)

    def save_to(self, path):
        """Write the report text (exactly what would be sent) to ``path``; raises OSError."""
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_name(target.name + ".json")
        target.write_text(self.text + "\n", encoding="utf-8")
        self.mark_saved()
        return target


def last_sent(directory):
    """``{"sent_at", "report_id", "text"}`` of the last report that was sent, or None."""
    value = read_json(Path(directory) / LAST_REPORT_FILE, None)
    if isinstance(value, dict) and isinstance(value.get("text"), str) and isinstance(value.get("report_id"), str):
        try:
            json.loads(value["text"])
        except ValueError:
            return None
        return value
    return None
