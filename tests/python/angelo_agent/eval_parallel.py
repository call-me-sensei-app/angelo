# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parallel-agents arm of a live trial (``eval_live_agent.py --parallel-agents``), in-app.

The trial's Angelo runs with ``ANGELO_ENABLE_PARALLEL_AGENTS=1``; everything else is the single-chat arm's, so the
two arms differ only in that switch. This module adds what a lead and its workers need from the driver
(``eval_live_agent_session.py``), as a person would give it:

* ``decide``: the tester approves the lead's ``angelo_workers_start`` card (it starts processes and model sessions,
  so it always asks) and, in an Ask chat, its merge; every other decision is the arm's ordinary rule;
* ``settled``: a turn is over only when the lead's turn ended **and** every worker it started finished its own (or
  ended), so a trial is never graded while a worker still builds;
* ``receipts``: the lead's own receipt (receipts are shared by every chat of the profile, so the latest one may be
  a worker's), each worker's, and ``usage_all_chats``, every request of the lead and its workers since the send,
  summed under ``bench_totals``' rules (a field one of them did not record is unknown, never a partial sum);
* ``observe`` (every tick of a running task, from ``cards``): every runtime thread the lead and each worker chat has
  used (a context rebase or a model switch replaces a chat's thread, break-it E2E-18 L-2) and each worker chat's
  process, which ``receipts`` records: a merged worker's process has left the add-on's records by the turn's end;
* ``begin`` (the trial's boot): the tasks it sends (``split_request``) and, with parallel edit on, the ready clock:
  the wall-clock moment each worker Blender told Angelo it was ready, on each part it took, whatever path made it
  ready (launched for it, launched ahead and then taken, or a warm worker switched to it), stamped on the thread that
  heard it (BENCH-PREREG Amendment E's baseline; break-it E2E-19B M-4);
* ``totals``: the task's ``provider_usage`` becomes that sum, so pricing and reports count every chat's tokens;
  the lead's own is kept as ``provider_usage_lead``;
* ``stop``: a timed-out or stopped trial stops every worker chat too.

Off (the single-chat arm) every function is a no-op that returns what the driver did before.
"""

import os
import time

ENABLED = os.environ.get("ANGELO_ENABLE_PARALLEL_AGENTS") == "1"   # The bench's parallel arm (set by the launcher).
# The person's request in the parallel arm's message (maintainer, 2026-10-25: a comparison where parallel edit is
# certainly in use), appended byte-identical to every case's brief; the single arm's message never has it. BENCH-PREREG
# Amendment D ("Freeze this text") replaced Amendment A's sentence: at a cap of 1 worker, a clear speed-up needs the
# lead to build its own share while the worker builds. FROZEN_SHA256 is its registered digest (UTF-8, no newline).
PARALLEL_REQUEST = ("Split this work between yourself and the workers: build your own pieces while they build theirs, "
                    "and hand out more as they finish.")
FROZEN_SHA256 = "5ecc54171d8a9b199eaa8cd9ce0dd59005fdce27101740f512252994491e0270"


def with_request(prompt, request):
    """The text sent: ``prompt`` with ``request`` as its own paragraph (``split_request``; the one-shot report quotes
    and checks the same text)."""
    return prompt + "\n\n" + request


def split_request(tasks):
    """The tasks a trial sends: in the forced parallel arm (``ENABLED``, never a run as shipped) each brief ends with
    ``PARALLEL_REQUEST`` as its own paragraph, so the record's prompt SHA-256 is of the text sent."""
    if not ENABLED:
        return tasks
    return [dict(task, prompt=with_request(task["prompt"], PARALLEL_REQUEST), parallel_request=PARALLEL_REQUEST)
            for task in tasks]


def begin(tasks):
    """The trial's parallel side at boot: the tasks it sends, and the ready clock when parallel edit is on."""
    if on():
        install_ready_clock()
    return split_request(tasks)


_ready = {}   # worker pid -> [{"part", "how", "at"}]: each moment it told Angelo it was ready, in order


def install_ready_clock():
    """Stamp the wall-clock moment ``WorkerProcess._launch`` returns (the worker said ready on the part it was launched
    on, cold or ahead of a start) and ``_switch`` returns (a running worker said ready on another part: a warm worker
    reused, or one launched ahead on a scratch file and then taken). Restarting a worker on the part it already serves
    adds no moment. Recording only: the product's call, result and errors are unchanged, and a moment that cannot be
    stamped is left out."""
    from angelo_agent.worker_process import WorkerProcess
    for name, how in (("_launch", "launched"), ("_switch", "switched")):
        original = getattr(WorkerProcess, name)
        if getattr(original, "ready_clock", False):
            continue

        def stamped(self, *args, original=original, how=how, **kwargs):
            part = str(self.part)   # The part this call makes it ready on (a take may rename it meanwhile).
            result = original(self, *args, **kwargs)
            try:
                _ready.setdefault(self.process.pid, []).append({"part": part, "how": how, "at": round(time.time(), 3)})
            except Exception:   # noqa: BLE001
                pass
            return result
        stamped.ready_clock, stamped.__wrapped__ = True, original
        setattr(WorkerProcess, name, stamped)


def ready_on(pid, part):
    """The moment worker ``pid`` said ready on ``part`` (the first, if it took that part file twice), or None."""
    return next((stamp for stamp in _ready.get(pid, ()) if stamp["part"] == str(part)), None)


def on():
    """Parallel edit runs in this trial's Angelo: the bench's parallel arm, or a run as shipped (``--parallel-as-
    shipped``, the gates of record) where the app's own capability check decides. Its workers are then waited for,
    priced and checked for limits like the lead, whoever started them."""
    try:
        from angelo_agent import parallel_access
    except ImportError:
        return ENABLED
    return parallel_access.enabled()


# The launcher's side (eval_live_agent.py) --------------------------------------------------------------------------

def add_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--parallel-agents", action="store_true",
                       help="the parallel-agents arm: the trial's Angelo offers its lead chat worker agents "
                            "(ANGELO_ENABLE_PARALLEL_AGENTS=1); without it the switch is explicitly off")
    group.add_argument("--parallel-as-shipped", action="store_true",
                       help="the gates of record: the switch is left unset, so the trial's Angelo decides by its own "
                            "capability check, as for a person (a worker-start card is still declined)")
    parser.add_argument("--worker-cap", type=int, default=0,
                        help="with --parallel-agents: at most this many workers at once (ANGELO_PARALLEL_WORKER_CAP)")


def launch_line(path):
    """Angelo's own line on parallel edit, as its launch printed it ("Angelo: parallel edit on: ..."; "(forced at
    launch)" when a switch was set), or None (a build before the line, or no log)."""
    from pathlib import Path
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return None
    return next((line.strip() for line in lines if line.startswith("Angelo: parallel edit ")), None)


def environment(args):
    """Explicit every way, so an outer runner's value never decides a run: ``1`` the parallel arm, ``0`` (the default)
    off, and empty as shipped (``parallel_access`` then measures the machine)."""
    if getattr(args, "parallel_as_shipped", False) is True:
        return {"ANGELO_ENABLE_PARALLEL_AGENTS": "", "ANGELO_PARALLEL_WORKER_CAP": ""}
    forced = getattr(args, "parallel_agents", False)
    cap = getattr(args, "worker_cap", 0) if forced else 0
    return {"ANGELO_ENABLE_PARALLEL_AGENTS": "1" if forced else "0",
            "ANGELO_PARALLEL_WORKER_CAP": str(cap) if isinstance(cap, int) and cap > 0 else ""}


# The trial's side (eval_live_agent_session.py) ---------------------------------------------------------------------
APPROVED = frozenset({"angelo_workers_start", "angelo_workers_merge"})


def decide(name):
    """``"approve"`` for the lead's worker cards in this arm, else None (the arm's ordinary rule decides)."""
    return "approve" if ENABLED and name in APPROVED else None


def workers(ui, lead):
    from angelo_agent import worker_sessions
    return [ui._controllers[s] for s in worker_sessions.workers_of(lead.state["id"]) if s in ui._controllers]


def cards(ui, lead):
    """(chat, call) for every pending call of ``lead`` and, in this arm, of each of its workers: a worker's card is
    answered by the same rule as the single arm's identical edit (break-it E2E-1 M-6: an Ask arm's worker cards
    expired unanswered while the trial waited)."""
    chats = [lead] + (workers(ui, lead) if on() else [])
    if on():
        observe(chats)
    return [(chat, call) for chat in chats for call in list(chat.pending.values())]


_seen = {}   # chat session id -> {"threads": every native id it had, "pid": its worker's process}


def threads(chat):
    """The runtime threads ``chat`` names now (a Codex thread id names its rollout)."""
    named = {value.get("native_id") for value in (chat.state.get("runtime_sessions") or {}).values()
             if isinstance(value, dict)} | {chat.state.get("native_id")}
    return {str(native) for native in named if native}


def observe(chats):
    """Keep every thread each chat has used so far and the process of the worker it is bound to. Recording only: what
    cannot be read is left out, never raised into the trial's tick."""
    from angelo_agent import worker_routing
    for chat in chats:
        seen = _seen.setdefault(chat.state["id"], {"threads": set()})
        try:
            seen["threads"] |= threads(chat)
            worker = worker_routing._workers.get(chat.state["id"])
            if worker is not None and "pid" not in seen and worker.process is not None:
                seen["pid"] = worker.process.pid
        except Exception as error:   # noqa: BLE001
            print("ANGELO_LIVE_EVAL_PARALLEL_OBSERVE", type(error).__name__, error, flush=True)


# BENCH-PREREG Amendment G, the maintainer: "Settle rule: yes. A pending worker-done event counts as not settled, in
# both arms, so a trial includes the lead's resumed merge." (``worker_resume``; a single-arm trial never has one.)
PENDING_EVENT_UNSETTLED = True


def settled(ui, lead):
    """Every worker of ``lead`` finished its turn or ended, and none is still starting (with
    ``PENDING_EVENT_UNSETTLED``, and the lead's worker-done event is not pending)."""
    if not on():
        return True
    from angelo_agent import worker_resume, worker_sessions
    if any(item["lead"] is lead for item in worker_sessions._launching):
        return False
    if PENDING_EVENT_UNSETTLED and worker_resume.pending(lead.state["id"]):
        return False
    return all(worker_sessions.finished(s) for s in worker_sessions.workers_of(lead.state["id"]))


def events(lead, first, record, stopped=None):
    """Reported only (Amendment G): how many worker-done events Angelo sent the lead in this task (its rows from
    ``first``, the task's message), and, for a task the harness stopped at its timeout, how many of them came after
    the stop (row ``stopped``): the maintainer kept the stop as it is, "the event may still fire within the stop
    grace" (BENCH-PREREG, Clarification of G)."""
    if on():
        sent = lambda start: sum(1 for row in lead.state["messages"][start:]   # noqa: E731
                                 if isinstance(row, dict) and row.get("request_kind") == "angelo_event")
        record["worker_done_events"] = sent(first)
        if stopped is not None:
            record["worker_done_events_after_stop"] = sent(stopped)


def receipts(ui, lead, marker, record):
    """The lead's own receipt, each worker's and ``usage_all_chats`` into ``record`` (module docstring)."""
    if not on():
        return
    import bench_totals
    from eval_receipts import receipts_since
    lead_id = lead.state["id"]
    sessions = {lead_id} | {chat.state["id"] for chat in workers(ui, lead)}
    every = receipts_since(lead, marker, sessions)   # Every chat's rows: one store per state folder (E2E-3 H-1).
    own = [r for r in every if r.get("session_id") == lead_id]
    record["receipt"] = own[-1] if own else None
    names = {}
    from angelo_agent import parallel_access, parallel_sessions
    observe([lead] + workers(ui, lead))   # Every thread each chat used, and each worker's ready moment.
    for chat in workers(ui, lead):
        names[chat.state["id"]] = parallel_sessions.names.get(chat.state["id"], "")
    from angelo_agent import worker_sessions as started
    record["lead_threads"] = sorted(_seen[lead_id]["threads"])
    # Amendment E1: each worker process's baseline is its first sample after it first said ready.
    record["worker_processes"] = [{"pid": pid, "ready_at_unix": stamps[0]["at"], "ready": stamps}
                                  for pid, stamps in sorted(_ready.items()) if stamps]
    record["workers"] = [{"name": names[session], "worker": (started._parts.get(session) or (None,))[0],
                          # Every runtime thread it used (a Codex thread id names its rollout): the record joins by them.
                          "threads": sorted(_seen[session]["threads"]), "pid": _seen[session].get("pid"),
                          # When its worker said ready on this chat's part, and how (launched, switched).
                          "ready": ready_on(_seen[session].get("pid"), (started._parts.get(session) or (0, 0, ""))[2]),
                          "requests": len(rows),
                          "total_ms": [(r.get("phases") or {}).get("total_ms") for r in rows],
                          "usage": [r.get("usage") for r in rows]}
                         for session in names for rows in [[r for r in every if r.get("session_id") == session]]]
    sent = [r for r in every if r.get("session_id") in sessions
            and ((r.get("counts") or {}).get("provider_turn_requests") or r.get("usage"))]
    fields = sorted({key for r in sent for key, value in (r.get("usage") or {}).items() if bench_totals.is_number(value)})
    record["usage_all_chats"] = {key: bench_totals.known_sum(sent, lambda r, key=key: (r.get("usage") or {}).get(key))
                                 for key in fields} or None
    # "working": workers that sent the provider a request (a receipt that counts one) or are answering now (a reply,
    # a thought or an activity in their chat: a first turn still running at the cap has no receipt yet); one whose
    # brief failed did neither (break-it E2E-7 L-6, E2E-8 L-5).
    # Only this task's work: a receipt since the send, or a turn running now that has answered (E2E-9 L-6).
    worked = {session for session in names if any(r.get("session_id") == session and r in sent for r in every)}
    worked |= {chat.state["id"] for chat in workers(ui, lead) if chat.busy and any(
        row.get("role") in ("assistant", "thinking", "activity") for row in chat.state.get("messages") or ())}
    record["parallel"] = {"workers": len(names), "working": len(worked & set(names)), "requests": len(sent),
                          "worker_limit": parallel_access.worker_limit()}   # What applied (the machine's, or the cap).
    from angelo_agent import worker_sessions   # A chat still ending its turn has no receipt yet: said, never hidden.
    import eval_abnormal_end
    open_turns = ([] if eval_abnormal_end.turn_ended(lead) else ["the lead"]) + [   # Its own too (E2E-6 L-5).
        names[session] for session in names if not worker_sessions.finished(session)]
    if open_turns or any(item["lead"] is lead for item in worker_sessions._launching):
        record["accounting_partial"] = open_turns or ["a worker still starting"]


def limit(limits, ui, lead, marker, record, first_row):
    """A provider limit in the lead's request or, in this arm, in any of its workers' requests: exclusion rule 1
    applies to every chat of a trial, as the single arm's one chat (break-it E2E-3 M-5)."""
    receipt = record.get("receipt") or {}
    found = limits.angelo_limit(lead.state["messages"][first_row:], receipt.get("usage"), receipt.get("counts"))
    if found or not on():
        return found
    from eval_receipts import receipts_since
    from angelo_agent import parallel_sessions
    for chat in workers(ui, lead):
        for row in receipts_since(lead, marker, {chat.state["id"]}) or [{}]:
            found = limits.angelo_limit(chat.state.get("messages") or [], row.get("usage"), row.get("counts"))
            if found:
                return "worker %s: %s" % (parallel_sessions.names.get(chat.state["id"], "?"), found)
    return None


def totals(summary, record):
    """``summarize_receipt``'s fields with every chat's tokens as the task's (the lead's own kept apart)."""
    if on() and (summary or record.get("usage_all_chats")):   # The workers' usage even without the lead's receipt.
        summary = dict(summary or {}, provider_usage_lead=(summary or {}).get("provider_usage"),
                       provider_usage=record.get("usage_all_chats"))
    return summary


def stop(lead):
    """Stop every worker chat of ``lead`` (a timeout or a harness stop of the lead's turn)."""
    if not on():
        return
    from angelo_agent import ui, worker_sessions
    worker_sessions.cancel_starts(lead)   # A worker still launching is never briefed after the stop (E2E-6 L-6).
    for chat in workers(ui, lead):
        try:
            chat.stop()
        except Exception as error:   # noqa: BLE001 - one chat that cannot stop never keeps the others running.
            print("ANGELO_LIVE_EVAL_PARALLEL_STOP", type(error).__name__, error, flush=True)
