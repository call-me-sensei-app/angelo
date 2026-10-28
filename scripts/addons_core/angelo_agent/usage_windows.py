# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A subscription's usage windows, as the agent runtime reported them with its replies (the usage meter).

Contract: doc/angelo/agent_chat.rst, "Usage meter". Angelo never asks for these numbers: it keeps the
latest ones a runtime sends on its own, in memory, per connection.

* **Claude Code** (2.1.292 and later; checked in the 2.1.301 and 2.1.306 binaries) sends a stream-json
  ``rate_limit_event`` whose ``rate_limit_info`` has ``status`` (allowed, allowed_warning, rejected),
  ``rateLimitType`` and ``utilization`` (a fraction, which may pass 1) for the limiting window,
  ``resetsAt`` (epoch seconds), ``isUsingOverage`` and, when the responses carry them, ``unifiedWindows``
  with the 5-hour and 7-day windows' ``utilization`` and ``resetsAt``. API-key sessions never receive one.
* **Codex** sends ``account/rateLimits/updated`` with ``rateLimits``: ``primary`` and ``secondary``
  windows (``usedPercent``, ``windowDurationMins``, ``resetsAt``). An update is sparse: a missing window
  keeps the one seen before.

A window whose reset time has passed is dropped (it has reset; the next reply reports it again), and
everything older than ``MAX_AGE`` is forgotten. Standard library only; no bpy.
"""

import math
import threading
import time

MAX_AGE = 8 * 24 * 3600
MAX_CONNECTIONS = 32
CLAUDE_WINDOWS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet")
_lock = threading.Lock()
_seen = {}  # connection key -> {"windows": {kind: window}, "overage": bool, "limited": bool, "at": epoch}
# A worker Blender sends each report to Angelo, which keeps the windows (worker_usage, design A'); None in Angelo.
forward = None


def _fraction(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1000 else None


MAX_EPOCH = 32536850399  # 3000-12-31 23:59:59 UTC: Windows' localtime raises above it (usage_view.clock).


def _epoch(value):
    return int(value) if type(value) in (int, float) and math.isfinite(value) and 0 < value <= MAX_EPOCH else None


def _store(key, windows, *, overage=None, limited=None, now=None):
    if not key:
        return
    now = time.time() if now is None else now
    with _lock:
        record = _seen.setdefault(key, {"windows": {}, "overage": False, "limited": False, "at": now})
        record["windows"].update(windows)
        if overage is not None:
            record["overage"] = overage
        if limited is not None:
            record["limited"] = limited
        record["at"] = now
        if len(_seen) > MAX_CONNECTIONS:
            del _seen[min(_seen, key=lambda name: _seen[name]["at"])]


def observe_claude(key, info, now=None):
    """A Claude Code ``rate_limit_event``'s ``rate_limit_info`` for one connection."""
    if forward is not None:
        return forward("claude", key, info)
    if not isinstance(info, dict):
        return
    windows = {}
    unified = info.get("unifiedWindows") if isinstance(info.get("unifiedWindows"), dict) else {}
    for kind in CLAUDE_WINDOWS:
        value = unified.get(kind)
        used = _fraction(value.get("utilization")) if isinstance(value, dict) else None
        if used is not None:
            windows[kind] = {"used": used, "resets_at": _epoch(value.get("resetsAt"))}
    kind, used = info.get("rateLimitType"), _fraction(info.get("utilization"))
    if kind in CLAUDE_WINDOWS and used is not None and kind not in windows:
        windows[kind] = {"used": used, "resets_at": _epoch(info.get("resetsAt"))}
    status = info.get("status")
    _store(key, windows, overage=info.get("isUsingOverage") is True,
           limited=status == "rejected" if status in ("allowed", "allowed_warning", "rejected") else None, now=now)


def observe_codex(key, snapshot, now=None):
    """A Codex ``account/rateLimits/updated`` notification's ``rateLimits`` for one connection."""
    if forward is not None:
        return forward("codex", key, snapshot)
    if not isinstance(snapshot, dict):
        return
    windows = {}
    for kind in ("primary", "secondary"):
        value = snapshot.get(kind)
        used = _fraction(value.get("usedPercent")) if isinstance(value, dict) else None
        if used is not None:
            minutes = value.get("windowDurationMins")
            windows[kind] = {"used": used / 100, "resets_at": _epoch(value.get("resetsAt")),
                             "minutes": minutes if type(minutes) is int and 0 < minutes <= 10 ** 6 else None}
    reached = snapshot.get("rateLimitReachedType")
    _store(key, windows, limited=bool(reached) if "rateLimitReachedType" in snapshot else None, now=now)


def current(key, now=None):
    """``{"windows": [...], "overage", "limited", "at"}`` for a connection, or None when nothing is known.

    Windows are in a fixed order (shortest first), each ``{"kind", "used", "resets_at", "minutes"}``.
    """
    now = time.time() if now is None else now
    with _lock:
        record = _seen.get(key)
        if record is None or now - record["at"] > MAX_AGE:
            return None
        windows = []
        for kind, value in record["windows"].items():
            if value.get("resets_at") is not None and value["resets_at"] <= now:
                continue  # Reset since it was reported.
            windows.append(dict(value, kind=kind))
        order = {"five_hour": 0, "primary": 1, "secondary": 2, "seven_day": 3, "seven_day_opus": 4,
                 "seven_day_sonnet": 5}
        windows.sort(key=lambda window: order.get(window["kind"], 9))
        return {"windows": windows, "overage": record["overage"], "limited": record["limited"], "at": record["at"]}


def window_minutes(window):
    """A window's length in minutes, when it is known."""
    return {"five_hour": 300, "seven_day": 10080, "seven_day_opus": 10080,
            "seven_day_sonnet": 10080}.get(window["kind"], window.get("minutes"))


def clear():
    """Forget every connection's windows (sign-out; tests)."""
    with _lock:
        _seen.clear()


def observe_codex_event(context, method, params):
    """The plan's windows from Codex's ``account/rateLimits/updated`` notice; True when ``method`` was that notice."""
    if method != "account/rateLimits/updated":
        return False
    observe_codex(connection_key(context), (params or {}).get("rateLimits"))
    return True


def observe_claude_event(controller, event):
    """The plan's windows from Claude Code's ``rate_limit_event``; every other event is left alone."""
    if event.get("type") == "rate_limit_event":
        observe_claude(connection_key(controller), event.get("rate_limit_info"))

def connection_key(context):
    """The connection a chat's runtime context (or controller) speaks for: its id, else its mode."""
    host = getattr(context, "host", context)
    state = getattr(host, "state", None)
    if not isinstance(state, dict):
        return None
    key = state.get("connection_id") or state.get("provider")
    return key if isinstance(key, str) and key else None
