# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a goal round says: the next request's words to the model, and the person's notices (goal_mode.rst, "Rounds",
"Stopping").

The model's words (``continuation``) are English, never translated, and facts only: the round, the measurement and
its details, the budget used, how the goal ends. They never say what to do next. The objective is the person's text
and the agent's stop message the agent's: both are quoted as data (``quoted``: JSON-quoted, invisible characters
escaped), so neither reads as Angelo's own words. The person's notices are ``ui_text.notice`` templates, translated
where drawn. No bpy.
"""

import json

from . import goal_state as G
from .ui_text import n_, notice
from .worker_resume import quoted

DETAIL_KEYS = ("iou", "mean_px", "p95_px", "max_px", "mean_world", "p95_world", "max_world", "centre_offset_px",
               "size_ratio", "measured_every_px", "reference", "drawn", "objects", "notes", "gaps", "passed_count",
               "total", "failing")
STOP_LINE = ("angelo_goal STOP ends the goal with DONE or BLOCKED, or pauses it with NEEDS_INPUT; its message is shown "
             "to the person.")

__all__ = ("continuation", "end_notice", "held_elsewhere_notice", "pause_notice", "round_facts", "set_notice",
           "quoted")


def _percent(value):
    return "%.1f%%" % (value * 100)


def _tokens(count):
    """``940``, ``8.4k``, ``212k``, ``1.8M``: English, for the model (``usage_view.tokens`` is the person's)."""
    if count < 1000:
        return str(count)
    if count < 10_000:
        return "%.1fk" % (count / 1000)
    if count < 1_000_000:
        return "%dk" % round(count / 1000)
    return "%.1fM" % (count / 1_000_000)


def _minutes(milliseconds):
    return int(round(milliseconds / 60_000))


def _measured_line(goal, measurement):
    if G.judged(goal):
        if goal["check"] == G.REFERENCE:
            return ("No measurement: no camera is set for its reference comparison yet (angelo_goal SET with measure "
                    "sets one).")
        changed = goal["rounds"] and goal["rounds"][-1]["verdict"] == G.PROGRESS
        return "No measurement: the goal is judged by you; the last round %s the scene." % (
            "changed" if changed else "did not change")
    if not isinstance(measurement, dict) or not measurement.get("measured"):
        return "Not measured this round: %s." % quoted(str((measurement or {}).get("reason") or "unknown")[:200])
    scored = [entry["score"] for entry in goal["rounds"] if entry.get("score") is not None]
    previous = scored[-2] if len(scored) >= 2 else None
    if goal["check"] == G.REFERENCE:
        line = ("Measured (outline, with the camera and targets set for this goal): overlap %s (passes at %d%%; best "
                "%s") % (
            _percent(measurement["iou"]), round(goal["pass_at"] * 100), _percent(goal["best"] or 0.0))
        line += (", last round %s)" % _percent(previous)) if previous is not None else ")"
        if "mean_px" in measurement:
            line += "; edge distance mean %.1f px" % measurement["mean_px"]
            if "mean_world" in measurement:
                line += " (%g world units)" % measurement["mean_world"]
            if "p95_px" in measurement:
                line += ", p95 %.1f px" % measurement["p95_px"]
        return line + "."
    return "Measured (scene checks): %d of %d pass (best %d)." % (
        measurement.get("passed_count", 0), measurement.get("total", 0), goal["best"] or 0)


def continuation(goal, measurement):
    """The words of the next round's request (model-facing, English)."""
    lines = ["From Angelo, not the person: round %d of the goal held in this chat: %s." % (
        len(goal["rounds"]) + 1, quoted(goal["objective"])), _measured_line(goal, measurement)]
    if isinstance(measurement, dict) and measurement.get("measured") and not G.judged(goal):
        details = {key: measurement[key] for key in DETAIL_KEYS if key in measurement}
        lines.append("Details: " + json.dumps(details, ensure_ascii=True, separators=(",", ":")))
    budget, spent = goal["budget"], goal["spent"]
    used = "Budget: %d of %d minutes working time, %s tokens" % (
        _minutes(spent["working_ms"]), budget["minutes"], _tokens(spent["tokens"]))
    if budget.get("tokens") is not None:
        used += " of %s" % _tokens(budget["tokens"])
    if budget.get("usd") is not None:
        used += ", $%.2f of $%.2f" % (spent["usd"], budget["usd"])
    used += "; %d of %d rounds without progress; at most %d rounds." % (
        goal["stall"], budget["stall_rounds"], budget["max_rounds"])
    lines += [used, STOP_LINE]
    return "\n".join(lines)


def _score_percent(goal):
    value = goal.get("ended_at_score")
    return None if value is None else int(round(value * 100))


def end_notice(goal):
    """The person's notice for an ended goal (stored English, translated where drawn)."""
    status, reason = goal["status"], goal.get("reason") or ""
    rounds, minutes = len(goal["rounds"]), _minutes(goal["spent"]["working_ms"])
    score = _score_percent(goal)
    if status == G.PASSED:
        if goal["check"] == G.REFERENCE:
            return notice(
                n_("Goal passed: outline matches {score}% from one camera · {rounds} rounds · {minutes} min"),
                          score=score, rounds=rounds, minutes=minutes)
        return notice(n_("Goal passed: every check passes · {rounds} rounds · {minutes} min"),
                      rounds=rounds, minutes=minutes)
    if status == G.STALLED:
        if G.judged(goal):
            return notice(n_("Goal stopped: the scene did not change in {count} rounds"),
                          count=goal["budget"]["stall_rounds"])
        return notice(n_("Goal stopped: no measured progress in {count} rounds"), count=goal["budget"]["stall_rounds"])
    if status == G.BUDGET:
        budget, spent = goal["budget"], goal["spent"]
        if reason == "usd":
            return notice(n_("Goal stopped: its ${limit} spend limit is reached (${spent})"),
                          limit="%.2f" % (budget.get("usd") or 0.0), spent="%.2f" % spent["usd"])
        if reason == "tokens":
            return notice(n_("Goal stopped: its {limit} token limit is reached"),
                          limit=_tokens(budget.get("tokens") or 0))
        if reason == "rounds":
            return notice(n_("Goal stopped after {count} rounds"), count=budget["max_rounds"])
        return notice(n_("Goal stopped: its {limit} minutes of working time are used ({rounds} rounds)"),
                      limit=budget["minutes"], rounds=rounds)
    if status == G.UNMEASURED:
        # The reason is the host's technical detail (it may name the person's objects): shown quoted, as data.
        return notice(n_("Goal stopped: it could not be measured ({reason})"), reason=quoted(reason))
    if status == G.DONE:
        if score is not None and not G.judged(goal):
            return notice(n_("The agent ended the goal at {score}% of {pass_at}%: {message}"), score=score,
                          pass_at=round(goal["pass_at"] * 100), message=reason)
        return notice(n_("The agent ended the goal: {message}"), message=reason)
    if status == G.BLOCKED:
        return notice(n_("The agent stopped the goal as blocked: {message}"), message=reason)
    if status == G.FAILED:
        if reason == G.SEND_FAILED:
            return notice(n_("Goal stopped: Angelo could not send the next round ({error})"),
                          error=quoted(goal.get("error") or ""))
        if reason == G.GOAL_DEFECT:
            return notice(n_("Goal stopped: a problem in Angelo ended it ({error})"),
                          error=quoted(goal.get("error") or ""))
        if reason == G.NOT_SENT:
            return notice(n_("Goal stopped: the request was not sent"))
        return notice(n_("Goal stopped: the round ended with an error"))
    worded = {G.NOT_STARTED: n_("The proposed goal was not started"),
              G.INTERRUPTED: n_("Goal stopped: Angelo interrupted the round"),
              G.CONNECTION_CHANGED: n_("Goal stopped: the chat's connection changed"),
              G.READ_ONLY: n_("Goal stopped: the chat is read only"),
              G.DOCUMENT_CHANGED: n_("Goal stopped: another file is open")}
    return notice(worded.get(reason, n_("Goal stopped")))


def set_notice(goal):
    """The person's notice when the agent holds a goal from their words: what it is and how it ends."""
    if goal["status"] == G.PROPOSED:
        return notice(n_("The agent proposes a goal: keep going until it's done. Start it on the goal chip"))
    return notice(n_("Angelo will keep going until the goal is met, for up to {minutes} minutes of work. "
                     "Stop goal on its chip ends it"), minutes=goal["budget"]["minutes"])


def pause_notice(goal):
    """The person's notice for a paused goal."""
    if goal.get("paused_by") == G.PAUSED_BY_AGENT:
        return notice(n_("Goal paused: the agent asks: {message}"), message=goal.get("reason") or "")
    if goal.get("paused_by") == G.PAUSED_BY_RESTART:
        return notice(n_("Goal paused because Angelo restarted: Resume continues it"))
    if goal.get("paused_by") == G.PAUSED_BY_IDLE:
        return notice(n_("Goal paused: nothing was continuing it. Resume continues it"))
    return notice(n_("Goal paused for your message: Resume continues it"))


def held_elsewhere_notice():
    """No goal was added: another chat holds this document's goal (one goal per document)."""
    return notice(n_("No goal was added: another chat holds this file's goal. Stop it there first"))


def round_facts(goal, measurement):
    """The facts a round's row keeps for drawing (``request_rows.kept_fields``): numbers only, never words."""
    last = goal["rounds"][-1] if goal["rounds"] else {}
    value = G.score(goal, measurement)
    if value is not None and goal["check"] == G.REFERENCE:
        value = int(round(value * 100))
    return {"round": len(goal["rounds"]) + 1, "score": value, "pass_at": int(round(goal["pass_at"] * 100)),
            "check": goal["check"], "minutes": _minutes(goal["spent"]["working_ms"]),
            "limit_minutes": goal["budget"]["minutes"], "last_seconds": int(last.get("working_ms", 0) // 1000),
            "last_tokens": last.get("tokens"), "last_usd": last.get("usd") if goal["budget"].get("usd") is not None
            else None}


def row_display(facts):
    """The round row as drawn (``console.display_text``), in the interface language, from its kept numbers."""
    from .ui_text import iface_
    from . import usage_view
    seconds = int(facts.get("last_seconds") or 0)
    duration = iface_("{minutes}m {seconds}s", minutes=seconds // 60, seconds=seconds % 60) if seconds >= 60 else \
        iface_("{seconds}s", seconds=seconds)
    spend = usage_view.tokens(int(facts.get("last_tokens") or 0))
    if facts.get("last_usd") is not None:
        spend = iface_("{tokens} tokens, {money}", tokens=spend, money=usage_view.money(float(facts["last_usd"])))
    else:
        spend = iface_("{tokens} tokens", tokens=spend)
    if facts.get("check") == G.REFERENCE and facts.get("score") is not None:
        return iface_("Angelo continued toward the goal · round {round} · outline {score}% of {pass_at}% · last round "
                      "{duration}, {spend}", round=facts.get("round", 0), score=facts["score"],
                      pass_at=facts.get("pass_at", 0), duration=duration, spend=spend)
    if facts.get("check") == G.SCENE and facts.get("score") is not None:
        return iface_("Angelo continued toward the goal · round {round} · {score} checks pass · last round {duration}, "
                      "{spend}", round=facts.get("round", 0), score=facts["score"], duration=duration, spend=spend)
    return iface_("Angelo continued toward the goal · round {round} · last round {duration}, {spend}",
                  round=facts.get("round", 0), duration=duration, spend=spend)
