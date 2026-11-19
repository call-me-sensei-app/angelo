# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A finished task's result as ``angelo_status`` returns it, beyond the summary, pictures and tools
(``external_status.result``): what changed and what it cost. Pure: no bpy, no I/O.

* **changes**: Angelo's verified account of the task's edits, the ``host_summary`` rows its turn ended with (the host
  checked each edit it names; ``codex_plan.emit_summary``, ``program_completion``), oldest first; ``verified`` says
  whether the turn had one. A turn that only read, or whose edits Angelo could not certify, has none: its tool rows and
  the agent's own reply are then the account, and the result says so.
* **cost**: the task's spend from its send to the next task's send (or now), so a later task's tokens are never this
  one's (``external_usage.tasks``).

doc/angelo/external_mcp.rst, "Status".
"""

from . import external_usage

MAX_CHANGES_TEXT = 4000   # Characters of verified account per result; ``changes_truncated`` when cut.
UNVERIFIED = "Angelo did not certify edits in this task: read its tool rows and reply for what it did"


def changes(messages, row):
    """The verified account rows after the task's own message, up to the next task's message."""
    texts, used, cut = [], 0, False
    for message in messages[row + 1:]:
        if message.get("role") == "user" and message.get("request_kind") in {None, "external_task"}:
            break
        if message.get("role") == "system" and message.get("host_summary") is True:
            text = str(message.get("text") or "")
            room = MAX_CHANGES_TEXT - used
            if room <= 0:
                cut = True
                break
            if len(text) > room:
                text, cut = text[:room], True
            texts.append(text)
            used += len(text)
    value = {"changes": texts, "verified": bool(texts)}
    if cut:
        value["changes_truncated"] = True
    if not texts:
        value["changes_note"] = UNVERIFIED
    return value


def cost(tasks, task, now):
    """``task``'s own spend (``tasks``: the app's task starts in this conversation, oldest first), or None."""
    for found in external_usage.tasks(tasks, now):
        if found["task"] == task:
            return found["usage"]
    return None


def completed(base, messages, row, tasks, task, now):
    """``external_status.result``'s ``base`` with the task's changes and cost."""
    return dict(base, **changes(messages, row), cost=cost(tasks, task, now))
