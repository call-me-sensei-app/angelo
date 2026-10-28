# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Path metrics of a one-shot test: how each side got to its result, from what the run recorded.

A one-shot test grades the result with closed checks; these numbers say how
the side got there. Each is read from a record, never from the model's own
account: Angelo's request receipt and chat rows, Claude Code's stream or
Codex's notifications. A number no record gives is ``None`` and printed as
"unknown", never guessed (``UNKNOWN`` lists them with what would be needed).

* ``usage_from_claude`` / ``usage_from_codex``: the model usage so far in a
  running turn, for the cost cap.
* ``list_cost``: tokens priced at the list prices of ``model_profiles``
  (API-equivalent dollars; a subscription is not billed this way), each cache
  write at the TTL the provider reported for it (``cache_writes``), a price
  step per model request, and ``list_price_basis`` says how they were priced.
  These live in ``list_prices`` and are re-exported here.
* ``summarize_rows``: Angelo's chat rows of the message as compact facts
  (kept in the trial record by ``oneshot_session``).
* ``angelo_path`` / ``mcp_path``: one side's metrics (``oneshot_tables``
  prints them: the sides next to each other, and several trials at a glance).
* ``path_facts`` / ``script_reasons``: what an add-on's own records add (the
  receipt's ``path`` section, the job records' ``shown`` marks); every one
  stays unknown on an add-on that predates them.

Standard library only (the add-on's own dependency-free modules are imported
lazily); no ``bpy``.
"""

import json
import re

# Usage and list prices live in list_prices; re-exported so every caller and test keeps importing them from here.
from list_prices import (  # noqa: F401
    CODEX_UNSPLIT, WRITE_TTLS, _claude_responses, _split_recorded, _stream_message, _ttl, cache_writes, cap_cost,
    claude_requests, claude_write_split, codex_requests, list_cost, list_price_basis, price_step, usage_from_claude,
    usage_from_codex, with_write_split)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".webp", ".bmp")
ANGELO_VIEW_TOOLS = ("angelo_view", "angelo_sculpt_view", "angelo_sculpt_review")
MCP_VIEW_TOOLS = ("get_screenshot_of_area_as_image", "get_screenshot_of_window_as_image")
MCP_RENDER_TOOLS = ("render_viewport_to_path", "render_thumbnail_to_path")
PYTHON_TOOL = "angelo_execute_python"
RENDER_CALLS = ("render.render(", "render.opengl(")
SHADING_SET = re.compile(r"shading\.type\s*=\s*['\"]([A-Z_]+)['\"]")
STOP_REASONS = ("finished", "time_cap", "cost_cap", "round_cap", "plan_rejected", "refusal", "approval_declined",
                "error", "provider_limit", "runtime_exit", "setup_error", "unknown")
# What the host says when it ends an Angelo turn itself (the texts of providers.py and controller.py).
DECLINED_TEXT = "The user declined this operation"
# What Angelo's tester declines in a one-shot test (oneshot_session.approval_decision), stated wherever a
# decline is reported so that it is never read as a fault of the product.
DECLINE_POLICY = ("file writes and deletes, generation and Generation Flows, delegation, memory notes, workspace "
                  "mode and the team library are declined; scene edits, reviewed Python and the render job are "
                  "approved")
CAPABILITY_LOOKUP, API_LOOKUP = "angelo_agent_capabilities", "angelo_blender_api"
MCP_MUTATIONS = ("execute_blender_code", "execute_blender_code_for_cli")
ROUND_CAP_TEXT, PLAN_REJECTED_TEXT = "iteration limit reached", "rejected the plan"
# What a report prints as "unknown", and what records it. An add-on whose receipts have a ``path`` section and
# whose job records have ``shown`` marks records the first, third and fourth; an older one leaves them unknown.
UNKNOWN = {
    "angelo.image_delivered_to_model": "whether a view's pixels were in a model request: recorded by add-ons whose "
                                       "receipt has a path section (pictures per provider turn, by kind); the "
                                       "add-on of this run predates it",
    "angelo.image_delivery_unconfirmed": "whether the agent's own pictures reached the model: they were sent to a "
                                         "runtime whose protocol does not confirm delivery (Codex code-mode "
                                         "results), so they count as sent with delivery unknown, never as none; "
                                         "whether it looked after its last edit or saw its render is unknown too",
    "angelo.codex_rounds": "model rounds inside a Codex turn: Codex does not report them to the host (its model "
                           "requests are counted)",
    "angelo.final_render_seen": "whether the agent saw the still it rendered: recorded by add-ons whose job "
                                "records carry a shown mark; without it only a job-rendered still is known (not "
                                "seen inside the turn)",
    "angelo.fallback_reason": "why a script was used instead of a typed tool: derived from the receipt's path "
                              "steps (a script after a refused typed call, or straight to a script); the add-on "
                              "of this run predates them",
    "mcp.view_mode": "the viewport shading of a screenshot: the MCP screenshot tools do not report it",
}


def unknown(value):
    return "unknown" if value is None else value


# ---------------------------------------------------------------- Angelo: chat rows

def _row_facts(row, typed_mutations, categories):
    tool, arguments = row.get("tool") or "", row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    facts = {"tool": tool, "status": row.get("status")}
    if result.get("error"):
        facts["error"] = str(result["error"])[:240]
        if DECLINED_TEXT in facts["error"]:   # The tester's decline, not a fault of the call.
            facts["declined"] = True
    if tool == PYTHON_TOOL:
        code = str(arguments.get("code") or "")
        facts.update(script=True, purpose=str(arguments.get("description") or "")[:240], code_chars=len(code),
                     categories=list(categories(code)), renders=any(call in code for call in RENDER_CALLS))
    elif tool in typed_mutations:
        program = arguments.get("program") if isinstance(arguments.get("program"), dict) else arguments
        facts.update(typed_mutation=True, operations=len(program.get("operations") or ()) or None)
    elif tool in ANGELO_VIEW_TOOLS:
        facts.update(view=True, shading=arguments.get("shading", "SOLID" if tool == "angelo_view" else None),
                     views=len(arguments.get("views") or ()) or None)
    elif tool == "angelo_job_start":
        facts.update(job=arguments.get("kind"), job_id=result.get("job"))
    return facts


def _sequence(steps):
    """Each mutation in order as text: ``typed <tool> (n ops)`` or ``script (categories)``."""
    output = []
    for step in steps:
        if step.get("script"):
            output.append("script (%s)" % (", ".join(step.get("categories") or ()) or "uncategorised"))
        elif step.get("typed_mutation"):
            ops = " (%d ops)" % step["operations"] if step.get("operations") else ""
            output.append("typed %s%s" % (step["tool"], ops))
    return output


def _before_first_mutation(calls, is_mutation, is_lookup):
    """Lookup calls made before the first change to the scene, by tool."""
    names = []
    for call in calls:
        if is_mutation(call):
            break
        if is_lookup(call):
            names.append(call["tool"])
    return {"calls": len(names), "by_tool": _count(names)}


def summarize_rows(rows):
    """Angelo's chat rows of one message as compact facts, in order (tool, status, a few arguments; no code)."""
    from angelo_agent import fallback_categories, flow_tool_spec, tool_specs
    typed = tool_specs.BOUNDED_MUTATIONS - flow_tool_spec.FLOW_EDITS
    read_only = {tool["name"] for tool in tool_specs.TOOLS if tool["annotations"]["readOnlyHint"]}
    output = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        if role == "activity":
            output.append(_row_facts(row, typed, fallback_categories.fallback_categories))
            if row.get("tool") in read_only:
                output[-1]["read_only"] = True
        elif role in ("error", "system"):
            output.append({"role": role, "text": str(row.get("text") or "")[:300]})
        elif role in ("user", "assistant"):
            output.append({"role": role, "chars": len(str(row.get("text") or ""))})
    return output


def _errors(steps):
    """Errors in order, each marked recovered when the side did something that succeeded afterwards."""
    found = []
    for index, step in enumerate(steps):
        if not step.get("failed"):
            continue
        later = any(other.get("succeeded") for other in steps[index + 1:])
        found.append({"what": step.get("what"), "text": step.get("text"), "recovered_in_turn": later})
    return {"total": len(found), "recovered_in_turn": sum(e["recovered_in_turn"] for e in found),
            "ended_turn": sum(not e["recovered_in_turn"] for e in found), "items": found[:20]}


def _count(names):
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _angelo_stop(record, facts, errors):
    receipt = record.get("receipt") or {}
    texts = [row.get("text", "") for row in facts.get("rows") or () if row.get("role") in ("error", "system")]
    detail = (record.get("reason") or (texts[-1] if texts else "") or "")[:300]
    lowered = " ".join(texts).lower()
    if (facts.get("stop") or {}).get("reason"):
        return {"reason": facts["stop"]["reason"], "detail": json.dumps(facts["stop"])}
    status = record.get("status")
    if status == "timeout":
        return {"reason": "time_cap", "detail": detail}
    if status == "invalid":
        return {"reason": "provider_limit", "detail": detail}
    if status in ("error", "skipped"):
        return {"reason": "setup_error", "detail": detail}
    if receipt.get("status") == "completed":
        note = "a render job was still running at the job cap" if record.get("job_wait_timed_out") else ""
        return {"reason": "finished", "detail": note}
    if ROUND_CAP_TEXT in lowered:
        return {"reason": "round_cap", "detail": detail}
    if PLAN_REJECTED_TEXT in lowered:
        return {"reason": "plan_rejected", "detail": detail}
    if "refus" in lowered:
        return {"reason": "refusal", "detail": detail}
    if record.get("declined") and not errors["total"]:
        return {"reason": "approval_declined", "detail": ", ".join(sorted(set(record["declined"])))}
    if receipt.get("status") in ("failed", "interrupted", "cancelled", "escalated"):
        return {"reason": "error", "detail": "%s: %s" % (receipt["status"], detail)}
    return {"reason": "unknown", "detail": detail}


def _own(groups):
    """The groups holding the agent's own pictures (any kind but ``attachment``) and how many those are."""
    own = [entry for entry in groups if any(kind != "attachment" for kind in entry[2])]
    return own, sum(count for entry in own for kind, count in entry[2].items() if kind != "attachment")


def path_facts(receipt, jobs):
    """What a receipt's ``path`` section and the job records' ``shown`` marks say (content-free host facts).

    Every value is None on an add-on that predates them, so a report prints "unknown" and never a guess.
    ``path["images"]`` is ``[[provider turn, edits done by then, {kind: count}], ...]``: a picture whose kind
    is not ``attachment`` is the agent's own look at the scene, and one whose ``edits`` equals the request's
    ``edits`` was seen after the last edit.

    Delivery has three states (``request_path``): ``images`` reached the model, ``unconfirmed_images`` were sent
    where the protocol cannot prove it (Codex code-mode results), never counted as none. ``delivery`` says
    "confirmed", "unknown" or "none" for the agent's own pictures (a proven attachment says nothing of them);
    ``after_last_edit`` (also for a picture of unknown capture age) and ``render_unconfirmed`` mark unknowns.
    """
    path = receipt.get("path") if isinstance(receipt.get("path"), dict) else None
    marks = [job["shown"] for job in jobs if str(job.get("kind", "")).startswith("RENDER")
             and isinstance(job.get("shown"), dict)]
    seen = any(mark.get("image_at") for mark in marks) if marks else None
    # A job record's weaker, older claim is kept apart from proof (job_store._shown): unknown, never "no".
    seen_unconfirmed = any(isinstance(job.get("shown_unconfirmed"), dict) and job["shown_unconfirmed"].get("image_at")
                           for job in jobs if str(job.get("kind", "")).startswith("RENDER"))
    if path is None:
        return {"recorded": False, "own_images": None, "own_unconfirmed": None, "all_images": None,
                "delivery": None, "per_turn": None, "after_last_edit": None, "steps": None, "steps_dropped": 0,
                "render_seen": seen, "render_unconfirmed": seen_unconfirmed and seen is not True}
    turns, uncertain = ([entry for entry in path.get(key) or () if isinstance(entry, list) and len(entry) == 3
                         and isinstance(entry[2], dict)] for key in ("images", "unconfirmed_images"))
    (own, own_count), (own_uncertain, uncertain_count) = _own(turns), _own(uncertain)
    # A count with no groups (capped or malformed) may hold the agent's own pictures: unknown, not none.
    ungrouped = bool(path.get("images_unconfirmed")) and not uncertain
    edits = path.get("edits")
    if any(entry[1] == edits for entry in own):
        after = True
    elif ungrouped or any(entry[1] is None for entry in own) or any(
            entry[1] == edits or entry[1] is None for entry in own_uncertain):
        after = None
    else:
        after = False
    job_unconfirmed = ungrouped or any(kind.startswith("job:") for entry in uncertain for kind in entry[2])
    return {"recorded": True, "own_images": own_count, "own_unconfirmed": uncertain_count,
            "all_images": path.get("images_delivered", 0), "per_turn": turns,
            "delivery": "confirmed" if own_count else "unknown" if uncertain_count or ungrouped else "none",
            "after_last_edit": after,
            "steps": [step for step in path.get("steps") or () if isinstance(step, list) and len(step) == 5],
            "steps_dropped": path.get("steps_dropped", 0), "render_seen": seen,
            "render_unconfirmed": (job_unconfirmed or seen_unconfirmed) and seen is not True}


def script_reasons(steps):
    """Why each script was written, from the request's own steps: one entry per script, in order.

    ``steps`` is the receipt's ``path["steps"]``: ``[kind, outcome, name, [names], repeats]`` with kinds
    ``lookup``, ``api``, ``typed`` and ``script``. A script after a typed call that was refused or failed is a
    fallback from it; one with nothing typed before it went straight to a script. No reason is the model's
    own account. None when the add-on recorded no steps.
    """
    if steps is None:
        return None
    reasons, before = [], []
    for kind, outcome, name, names, repeats in steps:
        if kind == "script":
            refused = [step for step in before if step[0] == "typed" and step[1] != "ok"]
            if refused:
                reason = "after a refused typed call (%s: %s)" % ("+".join(refused[-1][3]) or refused[-1][2],
                                                                  refused[-1][1])
            elif any(step[0] == "typed" for step in before):
                reason = "after typed work"
            else:
                reason = "after a lookup" if before else "straight to a script"
            reasons += [reason] * (repeats if type(repeats) is int and repeats > 0 else 1)
        before.append((kind, outcome, name, names))
    return reasons


def angelo_path(record, model="", cache_ttl="1h"):
    """One Angelo side's path metrics from its trial record (``record["oneshot"]`` is ``oneshot_session``'s).

    ``cache_ttl`` prices cache writes when the receipt's route does not name the session's own.
    """
    facts = record.get("oneshot") or {}
    rows = facts.get("rows") or []
    receipt = record.get("receipt") or {}
    cache_ttl = (receipt.get("route") or {}).get("cache_ttl") or cache_ttl
    phases, counts = receipt.get("phases") or {}, receipt.get("counts") or {}
    calls = [row for row in rows if row.get("tool")]
    done = [row for row in calls if row.get("status") == "completed"]
    scripts = [row for row in done if row.get("script")]
    typed = [row for row in done if row.get("typed_mutation")]
    views = [row for row in done if row.get("view")]
    jobs = [row for row in done if row.get("job")]
    steps = [{"failed": (row.get("status") == "failed" and not row.get("declined")) or row.get("role") == "error",
              "succeeded": row.get("status") == "completed", "what": row.get("tool") or row.get("role"),
              "text": row.get("error") or row.get("text")}
             for row in rows if row.get("tool") or row.get("role") == "error"]
    errors = _errors(steps)
    approved, declined = record.get("approved") or [], record.get("declined") or []
    usage = record.get("provider_usage") or (facts.get("watch") or {}).get("usage")
    rendered = ["job %s" % row["job"] for row in jobs if str(row.get("job", "")).startswith("RENDER")]
    rendered += ["script"] * sum(bool(row.get("renders")) for row in scripts)
    first = [phases[key] for key in ("first_text_ms", "first_native_action_ms")
             if type(phases.get(key)) in (int, float)]
    # The live row naming the work (a plan's provisional steps or names, a reasoning title) and the first tool
    # call's row, as the session's own polls saw them (eval_receipts.ProgressWatch); older records have neither.
    progress = record.get("first_progress_ms") if type(record.get("first_progress_ms")) in (int, float) else None
    shown = first + [record[key] for key in ("first_progress_ms", "first_step_ms")
                     if type(record.get(key)) in (int, float)]
    mutations = len(typed) + len(scripts)
    categories = _count(name for row in scripts for name in row.get("categories") or ())
    claude = receipt.get("runtime") == "claude"
    way = path_facts(receipt, record.get("jobs") or ())
    activity = record.get("activity") if isinstance(record.get("activity"), dict) else {}
    lookups = _before_first_mutation(calls, lambda row: row.get("script") or row.get("typed_mutation"),
                                     lambda row: row.get("read_only") and not row.get("view"))
    lookups.update(capability_lookups=sum(row["tool"] == CAPABILITY_LOOKUP for row in calls),
                   api_lookups=sum(row["tool"] == API_LOOKUP for row in calls),
                   lookup_rounds=activity.get("lookup_rounds"), plan_rejections=activity.get("plan_rejections"),
                   # Plans that ran with operations refused alone: rejected plans before plan_validation.
                   plans_refused_in_part=activity.get("plans_refused_in_part"))
    return {
        "messages_typed": facts.get("user_messages"),
        "model_requests": record.get("api_calls") or (receipt.get("usage") or {}).get("requests") or (
            counts.get("model_rounds") if claude else (facts.get("watch") or {}).get("requests")),
        "rounds": counts.get("model_rounds") if claude else None,
        "tool_calls": {"total": len(calls), "by_tool": _count(row["tool"] for row in calls)},
        "mutations": {"typed_calls": len(typed), "typed_operations": sum(row.get("operations") or 0 for row in typed),
                      "script_calls": len(scripts),
                      "typed_share": round(len(typed) / mutations, 3) if mutations else None,
                      "script_categories": categories, "sequence": _sequence(done)[:60],
                      "script_purposes": [row.get("purpose") for row in scripts][:40],
                      "steps": way["steps"], "steps_dropped": way["steps_dropped"],
                      "script_reasons": script_reasons(way["steps"]),
                      "scripts_raised": counts.get("failure_script", 0 if way["recorded"] else None)},
        "lookups": lookups,
        "approvals": {"asked": len(approved) + len(declined), "approved": len(approved), "declined": len(declined),
                      "declined_tools": sorted(set(declined)), "declined_by_policy": _count(declined),
                      "wait_s": round(phases["approval_wait_ms"] / 1000, 2) if "approval_wait_ms" in phases else None},
        "errors": errors,
        "looked": {"images": sum(row.get("views") or 1 for row in views), "calls": len(views),
                   "view_modes": _count(str(row.get("shading")) for row in views),
                   "delivered_to_model": way["own_images"], "delivered_unconfirmed": way["own_unconfirmed"],
                   "delivery": way["delivery"], "delivered_all": way["all_images"],
                   "delivered_per_turn": way["per_turn"], "after_last_edit": way["after_last_edit"],
                   "rendered": rendered,
                   # A shown mark says it; without one, a still rendered only as a job was not seen in the turn,
                   # unless a picture of a job went to the model unconfirmed: then nobody knows.
                   "final_render_seen": None if way["render_unconfirmed"] else way["render_seen"]
                   if way["render_seen"] is not None else (
                       False if rendered and all(item.startswith("job") for item in rendered) else None)},
        "first_activity_s": round(min(shown) / 1000, 2) if shown else None,
        "first_progress_s": round(progress / 1000, 2) if progress is not None else None,
        "wall_s": round((record.get("total_ms") or record.get("send_to_idle_ms") or 0) / 1000, 2) or None,
        "usage": usage, "cost_list_usd": list_cost(usage, model, cache_ttl),
        "cost_list_basis": list_price_basis([usage], model, cache_ttl, *(
            (CODEX_UNSPLIT,) if receipt.get("runtime") == "codex" else ())) if usage else None,
        "cost_reported_usd": (usage or {}).get("cost_usd"),
        "stop": _angelo_stop(record, facts, errors),
    }


# ---------------------------------------------------------------- Blender MCP: the client's own stream

def _tool_inputs(lines, runtime):
    """``[{name, input, image}]`` of every tool call in a raw stream, in order, and how many results carried an
    image. ``image``: the call's own result put a picture in the model's context (a call is no look without it)."""
    calls, images, seen, by_id = [], 0, set(), {}
    for line in lines:
        message = line.get("message") if "message" in line and "t_s" in line else line
        if runtime == "codex":
            item = (message.get("params") or {}).get("item") if isinstance(message.get("params"), dict) else None
            if not isinstance(item, dict) or item.get("id") in seen:
                continue
            if message.get("method") == "item/completed" and item.get("type") in ("mcpToolCall", "commandExecution",
                                                                                   "imageView"):
                seen.add(item.get("id"))
                name = ("mcp__%s__%s" % (item.get("server"), item.get("tool")) if item.get("type") == "mcpToolCall"
                        else item["type"])
                image = '"type": "image"' in json.dumps(item.get("result")) or item.get("type") == "imageView"
                calls.append({"name": name, "input": item.get("arguments") or {"command": item.get("command"),
                                                                             "path": item.get("path")}, "image": image})
                images += image
            continue
        body = message.get("message") if isinstance(message.get("message"), dict) else {}
        for block in body.get("content") if isinstance(body.get("content"), list) else ():
            if not isinstance(block, dict):
                continue
            if message.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("id") not in seen:
                seen.add(block.get("id"))
                calls.append({"name": block.get("name"), "input": block.get("input") or {}, "image": False})
                by_id[block.get("id")] = calls[-1]
            elif message.get("type") == "user" and block.get("type") == "tool_result":
                content = block.get("content")
                image = any(isinstance(part, dict) and part.get("type") == "image"
                            for part in (content if isinstance(content, list) else ()))
                images += image
                if image and block.get("tool_use_id") in by_id:
                    by_id[block["tool_use_id"]]["image"] = True
    return calls, images


def _mcp_stop(record, state):
    result = record.get("result") or {}
    detail = str(record.get("abort") or result.get("error") or result.get("reply") or "")[:300]
    if (state.get("stop") or {}).get("reason"):
        return {"reason": state["stop"]["reason"], "detail": json.dumps(state["stop"])}
    status = record.get("status")
    if status == "timeout":
        return {"reason": "time_cap", "detail": detail}
    if status == "invalid":
        return {"reason": "provider_limit", "detail": detail}
    if record.get("abort"):
        return {"reason": "runtime_exit", "detail": detail}
    if result.get("stop_reason") == "refusal" or "refus" in str(result.get("subtype") or "").lower():
        return {"reason": "refusal", "detail": detail}
    if result.get("subtype") == "error_max_turns":
        return {"reason": "round_cap", "detail": detail}
    if status == "completed":
        return {"reason": "finished", "detail": ""}
    return {"reason": "error" if result else "unknown", "detail": "%s: %s" % (result.get("subtype"), detail)}


def mcp_path(record, lines, state=None, model="", final_name=""):
    """One Blender MCP side's path metrics from its task record and raw stream lines (Claude Code or Codex)."""
    from angelo_agent import fallback_categories
    state = state or {}
    runtime = record.get("runtime") or "claude"
    inputs, image_results = _tool_inputs(lines, runtime)
    calls = record.get("tool_calls") or []
    names = [str(call.get("name") or "") for call in calls]
    short = [name.rsplit("__", 1)[-1] for name in names]
    codes = [str(call["input"].get("code") or "") for call in inputs
             if str(call["name"]).endswith("execute_blender_code")]
    scripts = sum(name == "execute_blender_code" and not call.get("is_error") for name, call in zip(short, calls))
    reads = [str(call["input"].get("file_path") or call["input"].get("path") or "") for call in inputs
             if call["name"] in ("Read", "imageView", "view_image")]
    image_reads = [path for path in reads if path.lower().endswith(IMAGE_SUFFIXES)]
    looks = sum(name in MCP_VIEW_TOOLS for name in short) + len(image_reads)
    rendered = ["script"] * sum(any(call in code for call in RENDER_CALLS) for code in codes)
    rendered += [name for name in short if name in MCP_RENDER_TOOLS]
    steps = [{"failed": bool(call.get("is_error")), "succeeded": not call.get("is_error") and "returned_ms" in call,
              "what": name, "text": None} for name, call in zip(short, calls)]
    result = record.get("result") or {}
    first = [record[key] for key in ("first_text_ms", "first_tool_call_ms") if type(record.get(key)) in (int, float)]
    usage = record.get("provider_usage")
    if runtime == "claude":   # A run recorded before its usage kept the TTL split gets it from the raw stream.
        usage = with_write_split(usage, lines)
    # Each model request's own usage from the raw stream: a model with a price step is priced per request.
    requests = codex_requests(lines) if runtime == "codex" else claude_requests(lines)
    denials = result.get("permission_denials") or []
    ordered = [{"tool": name} for name in short]
    lookups = _before_first_mutation(ordered, lambda call: call["tool"] in MCP_MUTATIONS,
                                     lambda call: call["tool"] not in MCP_VIEW_TOOLS + MCP_RENDER_TOOLS)
    lookups.update(capability_lookups=None, api_lookups=sum("api_docs" in name or "manual_docs" in name
                                                           for name in short),
                   lookup_rounds=None, plan_rejections=None, plans_refused_in_part=None)
    # In the order of the client's own calls: did a picture come back after the last script? A call whose result
    # held no picture (a missing file, a failed screenshot) was no look, whatever it asked for.
    edits = [index for index, call in enumerate(inputs) if str(call["name"]).endswith("execute_blender_code")]
    views = [index for index, call in enumerate(inputs) if call.get("image") and (
             str(call["name"]).rsplit("__", 1)[-1] in MCP_VIEW_TOOLS or call["name"] in ("Read", "imageView", "view_image"))]
    seen = [str(call["input"].get("file_path") or call["input"].get("path") or "") for call in inputs
            if call.get("image") and call["name"] in ("Read", "imageView", "view_image")]
    return {
        "messages_typed": state.get("messages"),
        "model_requests": record.get("api_calls"),
        "rounds": result.get("num_turns"),
        "tool_calls": {"total": len(calls), "by_tool": _count(short)},
        "mutations": {"typed_calls": 0, "typed_operations": 0, "script_calls": scripts,
                      "typed_share": 0.0 if scripts else None,
                      "script_categories": _count(name for code in codes
                                                  for name in fallback_categories.fallback_categories(code)),
                      "sequence": ["script (%s)" % (", ".join(fallback_categories.fallback_categories(code))
                                                    or "uncategorised") for code in codes][:60],
                      "script_purposes": []},
        "lookups": lookups,
        "approvals": {"asked": 0, "approved": 0, "declined": 0, "declined_tools": [], "wait_s": None,
                      "declined_by_policy": {},
                      "note": "no approval gate", "denied_by_client": len(denials),
                      "denied_tools": sorted({str(d.get("tool_name")) for d in denials if isinstance(d, dict)})},
        "errors": _errors(steps),
        "looked": {"images": looks, "calls": looks, "view_modes": {},
                   "shading_set_in_scripts": _count(m for code in codes for m in SHADING_SET.findall(code)),
                   "delivered_to_model": image_results, "rendered": rendered,
                   "after_last_edit": (bool(views) and views[-1] > max(edits, default=-1)) if inputs else None,
                   "final_render_seen": (True if final_name and any(path.lower().endswith(final_name.lower())
                                                                    for path in seen) else None)},
        "first_activity_s": round(min(first) / 1000, 2) if first else None,
        "first_progress_s": "–",  # Claude Code's own rows: its first text or tool call is the first activity.
        "wall_s": round((record.get("wall_ms") or 0) / 1000, 2) or None,
        "usage": usage, "cost_list_usd": list_cost(usage, model, "5m", requests),
        "cost_list_basis": list_price_basis([usage], model, "5m", *(
            (CODEX_UNSPLIT,) if runtime == "codex" else ()), per_request=[requests]) if usage else None,
        "cost_reported_usd": (usage or {}).get("cost_usd"),
        "stop": _mcp_stop(record, state),
    }
