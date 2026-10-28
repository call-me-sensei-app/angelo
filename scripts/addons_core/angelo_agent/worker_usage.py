# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Usage and telemetry of a worker chat: the worker forwards the records, Angelo writes them (design A', C8).

A hosted chat's ``Controller`` runs in its worker Blender but keeps its state in Angelo's state folder. Four writers
are reachable from its request path in that process:

* ``TelemetryStore.record``, which writes ``performance.json`` (``Controller._finish_metrics``, and a Codex
  warm-up's receipt from ``codex_bases.record_warm_up``);
* ``usage_meter.record`` and ``record_background``, which write ``usage.json`` (the same two callers);
* ``team_usage_reports.count_task``, which counts a team task for the signed-in account, saved to
  ``team-usage.json``;
* ``usage_windows.observe_claude`` / ``observe_codex``, which keep a subscription's rate windows in memory (the
  providers' event readers).

A second writer of those files would lose or double totals, and Angelo's meter would never see the windows. So in a
worker (``install``, then ``cover`` for each state folder a hosted chat uses) every one of them forwards its
record as one ``usage`` or ``telemetry`` event through ``send``, and nothing is written there.
The telemetry store and the usage ledger still keep the record in the worker's memory, so reads made there (the
agent's metrics tool) see it. ``covered`` says whether a chat's stores forward; a chat that is not covered is
never hosted (fail closed). A record that cannot be sent is dropped and counted (``dropped``), never written in the
worker, as a metrics write that fails is dropped today.

In Angelo, ``apply`` writes each forwarded record through the same store the chat would have used in Angelo's own
process, in the folder Angelo names: never in a folder the worker names. Each record carries a random id and is
applied once. The stores clean what they are given as they clean their own callers' records (``clean_receipt``,
``receipt_entries``, ``task_entry``, the windows' own checks), so a forwarded record gets no more authority than a
local one.

Standard library only; no bpy.
"""

import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from . import team_usage_reports, telemetry, usage_meter, usage_windows

USAGE, TELEMETRY = "usage", "telemetry"
MAX_APPLIED = 4096   # Ids remembered by ``apply``: far more than one channel can repeat.
MAX_KEY = 256

_lock = threading.Lock()
_send = None
dropped = 0


def _forward(kind, session, data):
    """Send one record (never raises); True when it went, False when it was dropped."""
    global dropped
    with _lock:   # One record at a time, in order, from any thread (the providers' readers forward windows).
        send = _send
        if send is None:
            dropped += 1
            return False
        try:
            send({"session": session, "kind": kind, "data": dict(data, id=uuid.uuid4().hex)})
            return True
        except Exception:  # noqa: BLE001 - Angelo is gone or the frame failed: the record is lost, never written here.
            dropped += 1
            return False


class ForwardingTelemetryStore(telemetry.TelemetryStore):
    """A worker's performance receipts: kept in memory, forwarded, never written."""

    def record(self, receipt):
        value = telemetry.clean_receipt(receipt)
        if value is None:
            return False
        with self.lock:
            self.receipts.append(value)
            self._revision += 1
            self._persisted = self._revision
        _forward(TELEMETRY, value.get("session_id"), {"record": "receipt", "receipt": value})
        return True

    def _drain(self):
        return None

    def flush(self, timeout=2):
        return True   # Forwarded: Angelo's store persists it.


class ForwardingLedger(usage_meter.UsageLedger):
    """A worker's usage totals: counted in memory, forwarded, never written."""

    def add(self, chat_id, receipt, default_mode=None):
        counted = super().add(chat_id, receipt, default_mode)
        if counted:
            _forward(USAGE, chat_id, {"record": "meter", "chat": chat_id, "receipt": receipt, "mode": default_mode})
        return counted

    def _schedule(self):
        return None

    def flush(self):
        return False

    def _write(self):
        return False


class _TeamTasks:
    """Stands in for the team account in a worker: a finished task is forwarded (``team_usage_reports.attach``)."""

    @staticmethod
    def count_agent_task(receipt):
        _forward(USAGE, receipt.get("session_id") if isinstance(receipt, dict) else None,
                 {"record": "team_task", "receipt": receipt})


def _windows(runtime, key, info):
    _forward(USAGE, None, {"record": "windows", "runtime": runtime, "key": key, "info": info})


def install(send):
    """Worker start-up: every usage and telemetry record goes through ``send(event)`` from now on.

    ``send`` takes ``{"session", "kind", "data"}`` (the C3 event without its time) and must be safe to call from
    any thread. Then ``cover`` each state folder before a hosted chat's ``Controller`` is made there.
    """
    global _send
    if not callable(send):
        raise TypeError("install needs a callable that sends one event")
    with _lock:
        _send = send
    usage_windows.forward = _windows
    team_usage_reports.attach(_TeamTasks())


def uninstall():
    """Stop forwarding (the worker's exit; tests). Records made after this are dropped, never written."""
    global _send
    with _lock:
        _send = None
    if usage_windows.forward is _windows:
        usage_windows.forward = None
    if isinstance(team_usage_reports._account, _TeamTasks):
        team_usage_reports.attach(None)


def installed():
    with _lock:
        return _send is not None


def cover(directory):
    """Make the state folder's shared telemetry store and usage ledger forwarding ones, in this process."""
    if not installed():
        raise RuntimeError("Usage forwarding is not installed in this worker")
    resolved = str(Path(directory).resolve())
    with telemetry._LOCK:
        if not isinstance(telemetry._STORES.get(resolved), ForwardingTelemetryStore):
            telemetry._STORES[resolved] = ForwardingTelemetryStore(resolved)
    with usage_meter._LOCK:
        ledger = usage_meter._LEDGERS.get(resolved)
        if not isinstance(ledger, ForwardingLedger):
            ledger = ForwardingLedger(resolved)
        usage_meter._LEDGERS[resolved] = usage_meter._LEDGERS[str(directory)] = ledger


def covered(controller):
    """True when this chat's usage and telemetry records are forwarded, never written (``chat.open`` refuses else)."""
    store = getattr(controller, "telemetry", None)
    return (installed() and isinstance(store, ForwardingTelemetryStore)
            and isinstance(usage_meter.UsageLedger.shared(store.directory), ForwardingLedger)
            and usage_windows.forward is _windows and isinstance(team_usage_reports._account, _TeamTasks))


# Angelo side --------------------------------------------------------------------------------------------------------

_applied = OrderedDict()
_applied_lock = threading.Lock()


def _first_time(identity):
    if not isinstance(identity, str) or not 0 < len(identity) <= 64:
        return False
    with _applied_lock:
        if identity in _applied:
            return False
        _applied[identity] = True
        while len(_applied) > MAX_APPLIED:
            _applied.popitem(last=False)
        return True


def apply(event, directory):
    """Angelo: write one forwarded ``usage`` / ``telemetry`` event in ``directory`` (Angelo's state folder).

    Returns True when it was written; False for a repeat, an unknown record or one the store refused. Never raises:
    a usage record never disturbs the chat it came from.
    """
    if installed() or not isinstance(event, dict) or event.get("kind") not in (USAGE, TELEMETRY):
        return False   # A worker never writes one, its own or another's.
    data = event.get("data")
    if not isinstance(data, dict) or not _first_time(data.get("id")):
        return False
    try:
        return _write(event["kind"], data, directory)
    except Exception:  # noqa: BLE001 - as the stores' own callers: the record is left uncounted.
        return False


def _write(kind, data, directory):
    record = data.get("record")
    if kind == TELEMETRY:
        return record == "receipt" and telemetry.TelemetryStore.shared(directory).record(data.get("receipt")) is True
    if record == "meter":
        chat, mode = data.get("chat"), data.get("mode")
        if chat is not None and (not isinstance(chat, str) or len(chat) > 64) or \
                mode is not None and not isinstance(mode, str):
            return False
        ledger = usage_meter.UsageLedger.shared(directory)
        return ledger.add(chat, data.get("receipt"), mode) if chat else ledger.add_background(data.get("receipt"))
    if record == "team_task":
        team_usage_reports.count_task(data.get("receipt"))
        return True
    if record == "windows":
        key, runtime = data.get("key"), data.get("runtime")
        if not isinstance(key, str) or not 0 < len(key) <= MAX_KEY or runtime not in ("claude", "codex"):
            return False
        observe = usage_windows.observe_claude if runtime == "claude" else usage_windows.observe_codex
        observe(key, data.get("info"))
        return True
    return False
