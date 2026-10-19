# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the MCP side's Claude Code asked the API for, per task: its effort and thinking display.

These went unrecorded on the MCP side (Angelo's side keeps every
request's parameters in its transcript's ``api-request`` lines), and the thinking display is a possible confound
of a thinking comparison. The MCP side runs with ``--no-session-persistence``, so it has no transcript; the
record is made from what the bench does have:

* the launch arguments: ``--effort`` (Claude Code sends it as ``output_config.effort``) and ``--thinking-display``
  (absent, Claude Code 2.1.301's print mode asks for ``updates``: doc/angelo/debug_mode.rst);
* the raw stream: Claude Code stamps each request's ``message_start`` line with the display it requested
  (``thinking_display``), and its ``init`` line gives its version and whether per-turn effort is active.

The unbilled dry run checks the record against the request bodies its fake endpoint received. Standard library only.
"""

from collections import Counter

DEFAULT_DISPLAY = "Claude Code default (not passed)"
BASIS = ("effort and requested display: the launch arguments; observed display: each request's message_start line "
         "(Claude Code's thinking_display)")


def _argument(command, flag):
    command = list(command) if isinstance(command, (list, tuple)) else []   # A test double's stream has none.
    if flag in command and command.index(flag) + 1 < len(command):
        return command[command.index(flag) + 1]
    prefix = flag + "="
    return next((part[len(prefix):] for part in command if isinstance(part, str) and part.startswith(prefix)), None)


def mcp(command, events):
    """``request_parameters`` of one MCP task: ``command`` is the client's launch, ``events`` the task's stream."""
    displays, init = Counter(), None
    for _, message in events:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "system" and message.get("subtype") == "init":
            init = message
        elif message.get("type") == "stream_event" and (message.get("event") or {}).get("type") == "message_start":
            displays[str(message.get("thinking_display") or "unrecorded")] += 1
    return {"effort": _argument(command, "--effort"), "model": _argument(command, "--model"),
            "thinking_display_requested": _argument(command, "--thinking-display") or DEFAULT_DISPLAY,
            "thinking_display_observed": dict(sorted(displays.items())), "requests": sum(displays.values()),
            "claude_code_version": (init or {}).get("claude_code_version"),
            "per_turn_effort_active": (init or {}).get("per_turn_effort_active"), "basis": BASIS}


def body_parameters(raw):
    """``(thinking display, effort)`` of one recorded Messages request body (the dry run's fake endpoint)."""
    import json
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        return None, None
    return (((body.get("thinking") or {}).get("display")), ((body.get("output_config") or {}).get("effort")))


def codex(config, events):
    """``request_parameters`` of one Codex MCP task: effort, model and the reasoning summary asked for (the
    ``turn/start`` the bench sent: ``bench_mcp_codex.reasoning_summary``), and per request its reasoning tokens and
    whether a summary arrived (``thread/tokenUsage/updated`` closes each request)."""
    from bench_mcp_codex import reasoning_summary
    requests, summarized = [], False
    for _, message in events:
        method, params = message.get("method"), message.get("params") or {}
        if method == "item/reasoning/summaryTextDelta" or (
                method == "item/completed" and ((params.get("item") or {}).get("type") == "reasoning")
                and (params["item"].get("summary") or [])):
            summarized = True
        elif method == "thread/tokenUsage/updated":
            last = (params.get("tokenUsage") or {}).get("last") or {}
            requests.append({"reasoning_tokens": last.get("reasoningOutputTokens"), "summary": summarized})
            summarized = False
    return {"effort": config.get("effort"), "model": config.get("model"),
            "reasoning_summary_requested": reasoning_summary(config).get("summary") or "Codex default (not passed)",
            "requests": requests, "basis": "the turn/start the bench sent; per request, the stream's reasoning items"}
