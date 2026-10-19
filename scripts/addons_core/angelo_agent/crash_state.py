# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's crash-report choice and which crash logs have been dealt with (``crash-reports.json``).

``consent`` is explicit and starts as ``ask``: the dialog appears for every crash.
Ticking "Remember my choice" in it changes it to ``always`` (reports are sent
without asking) or ``never`` (no dialog and no report); Preferences > System >
Updates and Reports changes it back. ``handled`` maps a crash log's fingerprint
(name, modification time and size) to what happened, so one crash is asked about
once and a log is never offered again after it was sent, declined, saved or
replaced by a newer crash.

Non-secret, small, atomic writes. A damaged file reads as ``ask`` with nothing
handled. Standard library only; no bpy.
"""

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from .studio_state import atomic_json, read_json

STATE_FILE = "crash-reports.json"
ASK, ALWAYS, NEVER = "ask", "always", "never"
CONSENTS = (ASK, ALWAYS, NEVER)
SENT, SAVED, DECLINED, DISMISSED, SUPERSEDED, FAILED, GAVE_UP = (
    "sent", "saved", "declined", "dismissed", "superseded", "failed", "gave_up")
STATUSES = (SENT, SAVED, DECLINED, DISMISSED, SUPERSEDED, FAILED, GAVE_UP)
FINAL = frozenset(STATUSES) - {FAILED}   # A failed send is asked again on the next launch, up to MAX_ATTEMPTS.
MAX_ATTEMPTS = 3
MAX_HANDLED = 50


@dataclass
class CrashState:
    consent: str = ASK
    handled: dict = field(default_factory=dict)   # fingerprint -> {"status", "at", "report_id", "attempts", "not_before"}

    def status(self, fingerprint):
        return self.handled.get(fingerprint, {}).get("status", "")

    def is_handled(self, fingerprint):
        return self.status(fingerprint) in FINAL

    def mark(self, fingerprint, status, report_id="", now=None, not_before=0.0):
        """Record what happened; ``not_before`` (epoch seconds) holds a failed send back until the server's
        Retry-After has passed, so a busy service is not asked again too soon."""
        entry = self.handled.get(fingerprint, {})
        attempts = entry.get("attempts", 0) + (1 if status in {SENT, FAILED} else 0)
        self.handled[fingerprint] = {"status": status, "at": time.time() if now is None else now,
                                     "report_id": report_id or entry.get("report_id", ""), "attempts": attempts,
                                     "not_before": float(not_before) if status == FAILED else 0.0}
        for old in sorted(self.handled, key=lambda key: self.handled[key]["at"])[:max(0, len(self.handled) - MAX_HANDLED)]:
            del self.handled[old]

    def attempts(self, fingerprint):
        return self.handled.get(fingerprint, {}).get("attempts", 0)

    def held_back(self, fingerprint, now=None):
        """True while a failed send waits for the server's Retry-After: no dialog and no send this launch."""
        now = time.time() if now is None else now
        return self.status(fingerprint) == FAILED and now < self.handled[fingerprint].get("not_before", 0.0)


def fingerprint(name, mtime_ns, size):
    """Identifies one crash log across launches without keeping its path."""
    return hashlib.sha256(("%s|%d|%d" % (name, mtime_ns, size)).encode("utf-8")).hexdigest()[:16]


def load(directory):
    document = read_json(Path(directory) / STATE_FILE, {})
    document = document if isinstance(document, dict) else {}
    consent = document.get("consent")
    handled = document.get("handled")
    clean = {}
    for key, entry in (handled.items() if isinstance(handled, dict) else ()):
        if (isinstance(key, str) and len(key) == 16 and isinstance(entry, dict) and entry.get("status") in STATUSES
                and isinstance(entry.get("at"), (int, float)) and isinstance(entry.get("attempts", 0), int)):
            clean[key] = {"status": entry["status"], "at": float(entry["at"]),
                          "report_id": entry["report_id"] if isinstance(entry.get("report_id"), str) else "",
                          "attempts": max(0, entry.get("attempts", 0)),
                          "not_before": float(entry["not_before"]) if isinstance(entry.get("not_before"), (int, float))
                          else 0.0}
    return CrashState(consent if consent in CONSENTS else ASK, clean)


def save(directory, state):
    """Persist; False when the folder can't be written (the choice then applies for this session only)."""
    try:
        atomic_json(Path(directory) / STATE_FILE, {"consent": state.consent, "handled": state.handled})
    except OSError:
        return False
    return True
