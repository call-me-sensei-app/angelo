# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a parallel trial's lead and workers communicated, read afterwards from its Codex rollouts (BENCH-PREREG
Amendment B3, draft: reported only, never in the verdict).

A trial copies every Codex thread it wrote (``angelo-trial-NN-rollouts``: the lead's and each worker chat's, all in
the lead's Codex profile). Nothing here runs during a trial, so measuring costs the timed run nothing. Each tool call
is a ``DynamicToolCall`` item with its start and end, each request a ``token_usage_record``, each turn end a
``task_complete``. A worker thread is one whose brief has the ``Part: <name>.`` line (``worker_sessions.BRIEF``), or
one the trial record names as a worker chat's; every other thread that started, asked after or merged workers is the
lead's. A worker gets its number from the record, else from the start answer that started its part, in start order
(``trial``), and every thread it used is read as its own.

Per worker: its brief's bytes (the whole first message, and the part text the lead wrote), its first request's input
and cached tokens (the opening, tool definitions and brief it reads before it does anything: what it re-reads that
the lead already had), its requests, the messages it received, seconds from its brief to its first call that changed
its scene (a result saying ``scene_changed``), from its brief to its last turn's end, and from that end to the lead's
merge call that took its part (its idle time). Per lead: its start, status (and waiting status) and merge calls, the
seconds it spent in waiting status calls, and the model requests it sent while a waiting status call was outstanding
(``while_waiting``: each one's seconds and input tokens; a request is sent at the rollout's last input before its
usage record, a tool's output or a person's message, and ends at that record; it is the wait's when that input came
after the call started and stands before the call's answer in the rollout's own order of entries). Codex yields a long tool call's exec
cell back to the model, which then polls it with ``wait`` requests; a held exec cell sends none. A run on another
runtime (Claude Code) has no rollouts: ``measured`` False. Standard library only.
"""

import json
import re
from datetime import datetime
from pathlib import Path
import eval_send_record as send_record  # The trial's record: an end write, never one written at a send.

START, STATUS, MERGE = "angelo_workers_start", "angelo_workers_status", "angelo_workers_merge"
PART = re.compile(r"Part: (.+?)\.\n")


def _parsed(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _result(item):
    """The tool's answer as a dict: the JSON text of its first content item, its ``result`` merged in."""
    for part in item.get("content_items") or ():
        found = _parsed(part.get("text")) if isinstance(part, dict) else None
        if isinstance(found, dict):
            inner = found.get("result")
            return dict(found, **inner) if isinstance(inner, dict) else found
    return {}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _at(entry):
    """An entry's ``timestamp`` in ms since the epoch, or None."""
    try:
        return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp() * 1000
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def thread(path):
    """One rollout's user messages, tool calls, requests (input and cached tokens, sent and done ms) and turn ends,
    each with its time in ms. A turn ends at its last item's end (ms), never at ``task_complete``'s whole second
    (break-it E2E-16 L-3). Whatever is not in the expected shape is skipped, never raised (a model-written argument can
    be anything, E2E-16 M-1)."""
    found = {"path": Path(path).name, "id": Path(path).stem[-36:], "messages": [], "calls": [], "requests": [],
             "turn_ends": []}
    last, given = None, (None, None)   # given: the model's last input (a tool's output, a person's message): when, where.
    for index, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()):
        try:
            entry = _dict(json.loads(line))
        except ValueError:
            continue
        payload = _dict(entry.get("payload"))
        kind = payload.get("type") if isinstance(payload.get("type"), str) else ""
        if entry.get("type") == "response_item" and (kind.endswith("_output") or kind == "message"
                                                     and payload.get("role") == "user"):
            given = (_at(entry), index)
        if entry.get("type") == "session_meta" and isinstance(payload.get("id"), str):
            found["id"] = payload["id"]   # The thread's own id (its file name ends with it too).
        elif entry.get("type") == "token_usage_record":
            usage = _dict(payload.get("usage"))
            if _number(usage.get("input_tokens")):
                found["requests"].append((usage["input_tokens"], _number(usage.get("cached_input_tokens")) or 0,
                                          given[0], _at(entry), given[1]))
        elif payload.get("type") == "task_complete":
            done = last if last is not None else _number(payload.get("completed_at"))
            if done is not None:
                found["turn_ends"].append(done if last is not None else done * 1000)
        elif payload.get("type") == "item_completed":
            item, at, end = _dict(payload.get("item")), _number(payload.get("started_at_ms")), _number(
                payload.get("completed_at_ms"))
            last = end if end is not None else last
            if item.get("type") == "UserMessage":
                text = "".join(part.get("text") or "" for part in item.get("content") or ()
                               if isinstance(part, dict) and isinstance(part.get("text"), str))
                found["messages"].append({"at": at, "text": text})
            elif item.get("type") == "DynamicToolCall":
                found["calls"].append({"tool": item.get("tool"), "arguments": _dict(_parsed(item.get("arguments"))),
                                       "result": _result(item), "start": at, "end": end, "index": index})
    return found


def _seconds(later, earlier):
    return round((later - earlier) / 1000, 1) if later is not None and earlier is not None else None


def _parts(call):
    """The parts a start call asked for, as {name: brief}, only well-formed ones."""
    parts = call["arguments"].get("parts")
    return {part["name"]: part.get("brief") if isinstance(part.get("brief"), str) else ""
            for part in (parts if isinstance(parts, list) else ()) if isinstance(part, dict)
            and isinstance(part.get("name"), str)}


def thread_cache(rollouts):
    """Per Codex thread of a trial (its rollouts, oldest first: the lead starts first), the first call's cache read
    (``cached_input_tokens / input_tokens`` of its first model request) and the calls-2+ share (token-weighted over
    its later requests), reported only. A thread that recorded no such usage is left out."""
    threads = []
    for path in sorted(Path(rollouts).glob("rollout-*.jsonl")) if Path(rollouts).is_dir() else []:
        calls = [request[:2] for request in thread(path)["requests"]]   # (input, cached), numbers only.
        if calls:
            later = [(cached, given) for given, cached in calls[1:]]
            threads.append({"thread": path.stem[-36:], "calls": len(calls),
                            "first_call_cache_read": round(calls[0][1] / calls[0][0], 3),
                            "calls_2plus_share": round(sum(c for c, _ in later) / sum(t for _, t in later), 3)
                            if later else None})
    return threads


def recorded_task(trial_folder):
    """The trial record's task (``eval_live_agent_session``: ``eval_parallel.receipts``' workers and threads, and the
    send's wall clock), or {}."""
    try:
        report = send_record.read(Path(trial_folder) / send_record.NAME)
        task = ((report.get("trials") or [{}])[0].get("tasks") or [{}])[0]
    except Exception:   # noqa: BLE001  Any shape (``tasks`` an object, a number): nothing recorded to read.
        return {}
    return task if isinstance(task, dict) else {}


def recorded_workers(trial_folder):
    """The trial record's workers (each one's number and every runtime thread its chat used), or []."""
    workers = recorded_task(trial_folder).get("workers")
    return [entry for entry in workers if isinstance(entry, dict)] if isinstance(workers, list) else []


def trial(rollouts, workers=(), lead_threads=()):
    """The trial's communication record, or ``{"measured": False, ...}``. Workers are joined by identity: the trial
    record's ``workers`` name each worker's number and every runtime thread its chat used, gathered while the trial ran
    (a context rebase or a model switch replaces a chat's thread: break-it E2E-18 L-2), ``lead_threads`` the lead's,
    and a rollout is named by its thread (E2E-17 M-3: by name and order, a failed launch or same-named parts ready out
    of order were misjoined). A brief thread the record does not name falls back to the order join, never to no
    number: a start answer's ``starting`` gives each started part its number in start order, and the thread takes its
    part's next number that no named brief thread holds, by its brief's time; a refused start never counts as one.
    Every thread of a worker is read as that worker's, and every lead thread is read (E2E-16 M-5)."""
    known = {thread: entry.get("worker") for entry in workers or () for thread in (entry.get("threads") or ())
             if isinstance(thread, str) and _number(entry.get("worker")) is not None}
    paths = sorted(Path(rollouts).glob("rollout-*.jsonl")) if Path(rollouts).is_dir() else []
    threads = [thread(path) for path in paths]
    named = {id(t): PART.search(t["messages"][0]["text"]) if t["messages"] else None for t in threads}
    ours = [t for t in threads if named[id(t)] or t["id"] in known]
    leads = [t for t in threads if t not in ours and (t["id"] in set(lead_threads or ()) or any(
        c["tool"] in (START, STATUS, MERGE) for c in t["calls"]))]
    if not leads:
        return {"measured": False, "why": "no rollouts" if not threads else "no thread started a worker"}
    calls = sorted((call for lead in leads for call in lead["calls"]), key=lambda call: call["start"] or 0)
    started, written, merges, refused = {}, {}, {}, 0   # started: name -> [numbers, in start order]
    for call in calls:
        if call["tool"] == START:
            # A part past the free places is queued (worker_queue) and starts later: it is started all the same.
            starting = [entry for key in ("starting", "queued") for entry in (call["result"].get(key) or ())
                        if isinstance(entry, dict)]
            refused += not starting
            parts = _parts(call)
            for entry in starting:
                if isinstance(entry.get("name"), str) and _number(entry.get("worker")) is not None:
                    started.setdefault(entry["name"], []).append(entry["worker"])
                    written[entry["worker"]] = len(parts.get(entry["name"], "").encode("utf-8"))
        elif call["tool"] == MERGE:
            for entry in call["result"].get("merged") or ():   # The call that took the part, not a refused one.
                if isinstance(entry, dict) and _number(entry.get("worker")) is not None:
                    merges.setdefault(entry["worker"], call["start"])
    claimed = {known[t["id"]] for t in ours if named[id(t)] and t["id"] in known}   # Brief threads the record names.
    groups, taken = {}, {}
    for other in sorted(ours, key=lambda t: (t["messages"][0]["at"] or 0) if t["messages"] else 0):
        number = known.get(other["id"])
        if number is None:   # The order join, over the numbers of its part no named brief thread holds.
            name = named[id(other)].group(1)
            free = [n for n in started.get(name, []) if n not in claimed]
            number = free[taken.get(name, 0)] if taken.get(name, 0) < len(free) else None
            taken[name] = taken.get(name, 0) + 1
        groups.setdefault(("worker", number) if number is not None else ("thread", id(other)), []).append(other)
    names = {number: name for name, numbers in started.items() for number in numbers}
    workers = [_worker(key[1] if key[0] == "worker" else None, group, named, names, written, merges)
               for key, group in groups.items()]
    status = [call for call in calls if call["tool"] == STATUS]
    waits = [call for call in status if call["arguments"].get("wait")]
    return {"measured": True, "lead": {"while_waiting": _while_waiting(leads),
        "starts": sum(call["tool"] == START for call in calls) - refused, "refused_starts": refused,
        "status_calls": len(status), "waiting_status_calls": len(waits),
        "merges": sum(call["tool"] == MERGE for call in calls),
        "wait_s": round(sum((call["end"] - call["start"]) / 1000 for call in waits
                            if call["end"] is not None and call["start"] is not None), 1),
        "requests": sum(len(lead["requests"]) for lead in leads), "tool_calls": len(calls), "threads": len(leads)},
        "workers": workers, "unmatched_threads": len(threads) - len(leads) - len(ours)}


def _while_waiting(leads):
    """The lead's requests sent while a waiting status call was out, in each lead thread: the input that sent one came
    after the call started (the request that made the call was sent before it) and stands before the call's own entry
    in the rollout, which Codex writes as the answer arrives (break-it E2E-19B: the answer's output, which sends the
    next request, is written a few ms after the call ends, so times alone would hang on that margin). Their count,
    seconds and input tokens, each and in all; None when a request or a waiting call has no time to place it by."""
    during, timed = [], True
    for lead in leads:
        waits = [call for call in lead["calls"] if call["tool"] == STATUS and call["arguments"].get("wait")]
        timed &= all(call["start"] is not None for call in waits) and not (waits and any(
            r[2] is None or r[3] is None for r in lead["requests"]))
        during += [r for r in lead["requests"] if r[2] is not None and r[4] is not None and any(
            call["start"] is not None and call["start"] < r[2] and r[4] < call["index"] for call in waits)]
    if not timed:
        return None
    return {"requests": len(during), "s": round(sum(r[3] - r[2] for r in during) / 1000, 1),
            "input_tokens": sum(r[0] for r in during),
            "each": [{"s": round((r[3] - r[2]) / 1000, 1), "input_tokens": r[0]} for r in during]}


def _changed(result):
    """A call's answer says it changed the scene: a typed edit's ``scene_changed``, or a script's ``changes`` (what it
    added, modified or removed; worker Python answers so, with no ``scene_changed``)."""
    changes = result.get("changes")
    return result.get("scene_changed") is True or isinstance(changes, dict) and any(changes.values())


def _worker(number, group, named, names, written, merges):
    """One worker's row from every thread its chat used (its brief thread first, then any that replaced it)."""
    brief_thread = next((t for t in group if named[id(t)]), None)
    brief = brief_thread["messages"][0] if brief_thread else None
    merged = merges.get(number)
    requests = [request for t in group for request in t["requests"]]
    calls = [call for t in group for call in t["calls"]]
    ends = sorted(end for t in group for end in t["turn_ends"] if merged is None or end <= merged)
    edit = min((call["start"] for call in calls if _changed(call["result"]) and call["start"] is not None),
               default=None)
    first = requests[0] if requests else (None, None)
    return {"worker": number, "name": named[id(brief_thread)].group(1) if brief_thread else names.get(number),
            "brief_bytes": len(brief["text"].encode("utf-8")) if brief else None,
            "lead_written_bytes": written.get(number), "threads": len(group),
            "messages_received": sum(len(t["messages"]) for t in group), "requests": len(requests),
            "first_request_input_tokens": first[0], "first_request_cached_tokens": first[1],
            "input_tokens": sum(r[0] for r in requests), "cached_tokens": sum(r[1] for r in requests),
            "tool_calls": len(calls), "brief_to_first_edit_s": _seconds(edit, brief and brief["at"]),
            "brief_to_done_s": _seconds(ends[-1] if ends else None, brief and brief["at"]),
            "done_to_merge_s": _seconds(merged, ends[-1] if ends else None), "merged": merged is not None}


def line(record):
    """One trial's record in a sentence for the report."""
    if not record.get("measured"):
        return "not measured (%s)" % record.get("why")
    lead = record["lead"]
    waiting = lead.get("while_waiting")
    text = ("lead: %d start (%d refused), %d status (%d waiting, %s s waited), %d merge, %d requests, %s; " % (
        lead["starts"], lead["refused_starts"], lead["status_calls"], lead["waiting_status_calls"], lead["wait_s"],
        lead["merges"], lead["requests"], "requests while a waiting status call was out: not measured (no times)"
        if waiting is None else "%d requests while a waiting status call was out (%s s, %d input tokens)" % (
            waiting["requests"], waiting["s"], waiting["input_tokens"])))
    text += "; ".join(
        "worker %s %s: brief %s B (lead wrote %s B), first request %s tokens (%s cached), %d requests, %d messages "
        "received, brief to first edit %s s, to done %s s, done to merge %s s%s" % (
            entry["worker"], entry["name"], entry["brief_bytes"], entry["lead_written_bytes"],
            entry["first_request_input_tokens"], entry["first_request_cached_tokens"], entry["requests"],
            entry["messages_received"], entry["brief_to_first_edit_s"], entry["brief_to_done_s"],
            entry["done_to_merge_s"] if entry["merged"] else "never merged",
            ", over %d threads" % entry["threads"] if entry.get("threads", 1) > 1 else "")
        for entry in record["workers"]) or "no worker thread"
    return text + ("; %d thread(s) matched no worker" % record["unmatched_threads"] if record["unmatched_threads"]
                   else "")
