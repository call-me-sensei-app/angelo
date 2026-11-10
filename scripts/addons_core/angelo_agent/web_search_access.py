# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude's native tools for a chat, without WebSearch once the organization has turned web search off.

Contract: doc/angelo/ai_providers.rst, "Web search turned off by the organization".
An Anthropic organization's Console can turn web search off for its keys,
including the key a team's gateway uses. Claude Code still offers WebSearch
(its ``system/init`` lists it) and each search fails with the API's answer, so
the model, told by Angelo's instructions to search, keeps trying. When a search
fails that way (``ORG_OFF``), the connection is remembered for ``REMEMBER_SECONDS``
in this process: the session's native tools change (``native_tools``), the next
turn relaunches it without WebSearch, and its instructions say web search is
unavailable (``claude_text.NO_WEB_SEARCH``). After that time one search is tried
again, so an organization that turns it back on gets it back.

Standard library only; no bpy.
"""

import re
import threading
import time

DEFAULT = ("WebSearch", "WebFetch")
# The API's refusal as Claude Code reports it in a failed WebSearch call's result ("API Error: 400 web search is not
# enabled for this organization"; 2.1.292-2.1.316), in the wording Claude Code itself classifies
# (feature_not_enabled_for_org). Only an error result of a WebSearch call is read (``turned_off``): a page or a search
# hit that quotes this sentence never counts.
ORG_OFF = re.compile(r"API Error: 4\d\d\b.{0,80}?web search is not enabled for (?:this|your) organi[sz]ation",
                     re.IGNORECASE | re.DOTALL)
REMEMBER_SECONDS = 6 * 3600
MAX_SEARCHES = 64   # WebSearch calls one round tracks (Searches).

_lock = threading.Lock()
_off = {}   # connection key -> monotonic time it was seen off


def _key(context):
    state = getattr(context, "state", None) or {}
    provider = state.get("provider")
    return (provider, state.get("connection_id")) if provider else None


def turned_off(content, is_error=True):
    """Whether a failed WebSearch call's result (its text, or its content blocks) says the organization turned it off."""
    if isinstance(content, list):
        content = "".join(block.get("text", "") for block in content[:8] if isinstance(block, dict))
    return is_error is True and isinstance(content, str) and bool(ORG_OFF.match(content.lstrip()[:400]))


def record_off(context, now=None):
    key = _key(context)
    if key:
        with _lock:
            _off[key] = time.monotonic() if now is None else now


def is_off(context, now=None):
    key = _key(context)
    with _lock:
        seen = _off.get(key) if key else None
        if seen is None:
            return False
        if (time.monotonic() if now is None else now) - seen < REMEMBER_SECONDS:
            return True
        del _off[key]
        return False


def native_tools(context, now=None):
    """The chat's native Claude tools (its own ``native_tools``, else ``DEFAULT``), less WebSearch while it is off."""
    tools = tuple(getattr(context, "native_tools", DEFAULT))
    return tuple(tool for tool in tools if tool != "WebSearch") if "WebSearch" in tools and is_off(context, now) else tools


class Searches:
    """One round's WebSearch calls, from Claude Code's stream events: ``on_off()`` once one of them failed because
    the organization turned web search off. Only those calls' error results are read."""

    def __init__(self, on_off=None):
        self.on_off, self.ids = on_off, set()

    def observe(self, event):
        kind = event.get("type") if isinstance(event, dict) else None
        content = (event.get("message") or {}).get("content") if kind in {"assistant", "user"} else None
        for item in content if isinstance(content, list) else ():
            if not isinstance(item, dict):
                continue
            if (kind == "assistant" and item.get("type") == "tool_use" and item.get("name") == "WebSearch"
                    and len(self.ids) < MAX_SEARCHES):
                self.ids.add(item.get("id"))
            elif (kind == "user" and item.get("type") == "tool_result" and item.get("tool_use_id") in self.ids
                  and callable(self.on_off) and turned_off(item.get("content"), item.get("is_error"))):
                self.on_off()
