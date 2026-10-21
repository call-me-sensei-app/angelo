# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a connected MCP app is offered: its tools, the task tools and the server's instructions.

Angelo's tools carry the argument schemas Angelo's own direct-tools Claude
session reads (``claude_direct_schema.tool_list``: short identifiers, core
schemas exact, deferred tools named with a pointer to
``angelo_agent_capabilities``, an object at every root), so a call is validated
and run exactly as that route's are (``external_work``). An outside app has no
Angelo instructions of its own, so each tool also carries its description
(``tool_specs``), and the server's ``instructions`` state the host's contract
as facts: who approves, what a result proves, how identifiers work. Nothing here
tells a model when to batch, iterate or delegate; it decides.

Three task tools (``angelo_task``, ``angelo_task_status``, ``angelo_task_stop``)
exist only for apps the person allowed to run tasks; the launcher serves them
through the ``task`` command, never through a tool call.
"""

import functools
import json

from .agent_guidance import BLENDER_API_FACTS
from .claude_direct_schema import tool_list
from .claude_direct_text import CLAUDE_TOOLS_INSTRUCTIONS
from .external_clients import TASKS, tools_for
from .routing import TOOLSET_VERSION
from .tool_specs import TOOL_MAP

MAX_TASK_WAIT = 50   # external_service.MAX_TASK_WAIT: one status call never outlasts an MCP client's tool timeout.

HEADER = """Angelo (Hyperbond Studio) is a 3D app built on Blender 5.2. This is Angelo's own MCP server, not Blender MCP:
every call runs in the open Angelo window through the host checks Angelo's own agent uses.
The person approves in Angelo, never you. Under Approve for me, bounded typed edits run at once; Python scripts,
background jobs and other effects wait for the person's decision in Angelo, and the call waits with them. A call the
person declined or stopped says so: then make no further calls and tell the user.
Each committed edit is one Undo step in Angelo, and Angelo snapshots the file before the first edit of a run of calls.
Results carry the host's verified read-back of what changed; a failed or inconclusive check never proves completion."""
TASKS_FACT = """
angelo_task hands Angelo's own agent a whole task in plain language; it works in this app's External conversation in
Angelo on the person's connected account and reports progress (angelo_task_status) and its result. Tools and tasks
are alternatives: while a task runs, tool calls wait."""
# The direct-tools text's facts about identifiers, results, schemas and views, without what only Angelo's own chat
# has: its tool prefix, and the host showing the person a verified result in place of the model's reply.
_LEFT_OUT = (
    "Angelo's tools reach you as mcp__angelo__<name> (named here without the prefix); they are the host's own tools, not an\n"
    "external MCP server. ",
    "When every edit commits (or a PREVIEW stages) and all its checks pass, the host shows the person its verified result:\n"
    "end the turn with an empty reply, or one short question/caveat/choice sentence; never restate the result or claim\n"
    "completion beyond it.\n",
)


def _facts():
    text = CLAUDE_TOOLS_INSTRUCTIONS
    for sentence in _LEFT_OUT:
        if sentence not in text:   # The direct-tools text changed: this module must be revisited (tested).
            raise RuntimeError("claude_direct_text changed; update external_mcp_tools._LEFT_OUT")
        text = text.replace(sentence, "")
    return text.strip()


def instructions(levels):
    # The Blender API facts every route whose model writes scripts reads, assembled once (agent_guidance): an app's
    # model runs Python through Angelo too (the python level), and typed edits name the same enums.
    return HEADER + (TASKS_FACT if TASKS in levels else "") + "\n" + BLENDER_API_FACTS + "\n" + _facts()


TASK_TOOLS = [
    {"name": "angelo_task", "title": "Run a task with Angelo's agent",
     "description": ("Hand Angelo's own agent a whole task in plain language (for example \"model a stone bridge over "
                     "the river and light it at dusk\"). It runs in this app's External conversation in Angelo, on the "
                     "person's connected account, with Angelo's approvals, snapshots and Undo. Returns its state; "
                     "running tasks continue after this call returns."),
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "The task, as the person would type it in Angelo."},
         "wait_seconds": {"type": "number", "minimum": 0, "maximum": MAX_TASK_WAIT,
                          "description": "Seconds to wait for it to finish before returning (default 45)."}},
         "required": ["task"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}},
    {"name": "angelo_task_status", "title": "Angelo task status",
     "description": ("A task's state (running, waiting_for_approval_in_angelo, done, failed, stopped), its latest "
                     "progress rows and, when it ends, the agent's reply. Waits up to wait_seconds for it to end."),
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "wait_seconds": {"type": "number", "minimum": 0, "maximum": MAX_TASK_WAIT}},
         "required": ["task_id"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_task_stop", "title": "Stop an Angelo task",
     "description": "Stop a running task, as the person's Stop does. Committed edits stay (Undo is the person's).",
     "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}},
                     "required": ["task_id"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]
TASK_NAMES = frozenset(tool["name"] for tool in TASK_TOOLS)


@functools.lru_cache(maxsize=16)
def _catalog(levels):
    allowed = tools_for(levels)
    tools = []
    for tool in tool_list(allowed):
        spec = TOOL_MAP[tool["name"]]
        described = dict(tool)
        if "description" not in described:
            described["description"] = spec["description"]
        described["annotations"] = dict(spec["annotations"])
        tools.append(described)
    if TASKS in levels:
        tools.extend(TASK_TOOLS)
    return json.dumps({"tools": tools, "instructions": instructions(levels), "toolset_version": TOOLSET_VERSION},
                      ensure_ascii=False, separators=(",", ":"))


def app_tools(levels):
    """The ``tools`` answer for an app: its MCP tool list, the server instructions and the toolset version."""
    return json.loads(_catalog(frozenset(levels)))
