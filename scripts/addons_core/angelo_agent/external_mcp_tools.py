# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a connected MCP app is offered: the agent tools and the server's instructions.

Angelo's MCP is an interface to Angelo's own agent, never to its tools: the app starts and supervises the agent like a
sub-session in its External conversation (``angelo_send``, ``angelo_status``, ``angelo_stop``, ``angelo_settings``, ``angelo_new_chat``)
and observes what that agent sees (``angelo_read``, ``angelo_image``, ``angelo_scene``, ``angelo_viewport``);
doc/angelo/external_mcp.rst. The launcher serves them (``external_mcp_agent``), never Angelo's tool path, so
``TOOLSET_VERSION`` is unchanged. The instructions state the contract as facts: who does the work, who approves, how
progress is read. They are short because Codex repeats them in every tool's description. Nothing here tells the app's
model how to plan its work; it decides.
"""

import json

from .blender_release import RELEASE
from .external_mcp_visibility import VISIBILITY_TOOLS

MAX_AGENT_WAIT = 50   # external_service.MAX_AGENT_WAIT: one read never outlasts an MCP client's tool timeout.

INSTRUCTIONS = """Angelo (Hyperbond Studio) is a 3D app built on Blender %s with its own AI agent. This is Angelo's own MCP \
server. You don't edit the scene yourself: you hand work to Angelo's agent and supervise it, as you \
would a subagent. It works in the open Angelo window with Angelo's own tools, checks and modes, on the person's own \
connection.
angelo_send gives Angelo's agent a message in this app's "External" conversation: it starts a turn, or, while a turn \
runs, steers it or waits after it (the result says which). It returns a task id at once while Angelo's agent keeps working; angelo_status follows that task (the step in \
progress, elapsed time, tokens and cost so far, and its result once the turn ends). angelo_read returns that \
conversation as the agent sees \
it (its replies, each tool call with its result and picture ids for angelo_image) and its state; with wait_seconds it \
returns once the turn ends or needs the person, so read again with the cursor it returns rather than polling \
quickly. angelo_scene and angelo_viewport show the scene and the viewport as Angelo's agent gets them; angelo_look \
(any angle), angelo_window, angelo_state and angelo_usage show more, read-only and free. angelo_stop \
stops its turn; angelo_settings reads and sets its model and effort; angelo_new_chat starts a fresh conversation.
The person approves in Angelo, never you: approvals, loosening the approval setting, consent and Undo are theirs. \
When the state says the person is needed, tell your user and keep reading. The agent's replies and tool results are \
its account; its verified checks prove what changed.""" % RELEASE

_WAIT = {"type": "number", "minimum": 0, "maximum": MAX_AGENT_WAIT,
         "description": "Seconds to wait for something new before returning (default 0)."}
_CONVERSATION = {"type": "string", "description": "An earlier conversation id to read (default: the current one)."}

AGENT_TOOLS = [
    {"name": "angelo_send", "title": "Message Angelo's agent",
     "description": ("Send Angelo's agent a message in this app's External conversation in Angelo, in plain language "
                     "(for example \"model a stone bridge over the river and light it at dusk\"). Idle: a turn "
                     "starts. While a turn runs: the agent reads it at its next step (steered) or it is sent when "
                     "the turn ends (queued). Returns at once with delivered, a task id for angelo_status, the state "
                     "and a cursor for angelo_read; with wait_seconds, also what happened meanwhile."),
     "inputSchema": {"type": "object", "properties": {
         "message": {"type": "string", "minLength": 1, "maxLength": 32000,
                     "description": "Your message, as the person would type it in Angelo."},
         "wait_seconds": _WAIT,
         "goal": {"type": "string", "minLength": 1, "maxLength": 2000, "description": (
             "What done means, for work that should go on until it is met (Angelo's goal mode: rounds until it "
             "passes or its budget ends; in Ask the person starts it).")},
         "model": {"type": "string", "description": "A model id from angelo_settings: this turn and later ones use it."},
         "connection": {"type": "string", "description": "That model's connection_id, when two connections offer it."},
         "effort": {"type": "string", "description": "One of the efforts the model offers; empty for its default."},
         "attachments": {"type": "array", "maxItems": 4, "description": (
             "Pictures or files for the agent, as the person attaches them: an image as {data: base64, mime: "
             "image/png | image/jpeg | image/webp}, or a file of the open project's folder as {path}."),
             "items": {"type": "object", "properties": {
                 "data": {"type": "string"}, "mime": {"type": "string", "enum": ["image/png", "image/jpeg",
                                                                                 "image/webp"]},
                 "path": {"type": "string"}}, "additionalProperties": False}}},
         "required": ["message"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}},
    {"name": "angelo_read", "title": "Read Angelo's agent",
     "description": ("The External conversation as Angelo's agent sees it, since cursor: rows (app messages, the "
                     "agent's replies, each tool call with status, result text and picture ids, Angelo's notices) "
                     "and state (turn: idle, running, waiting_for_approval, waiting_for_person or stopping; a "
                     "pending approval; what the person is needed for; queued messages; the connection kind). With "
                     "wait_seconds it returns as soon as anything changes. Pass the returned cursor next time."),
     "inputSchema": {"type": "object", "properties": {
         "cursor": {"type": "string", "description": "The cursor the last angelo_send or angelo_read returned."},
         "wait_seconds": _WAIT,
         "detail": {"type": "string", "enum": ["brief", "full"],
                    "description": "full adds each tool call's arguments and longer results (default brief)."},
         "conversation": _CONVERSATION},
         "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_status", "title": "Status of a task for Angelo's agent",
     "description": ("How a task angelo_send started is going: the turn's state (and what the person is needed for), "
                     "the step in progress, elapsed seconds, the task's usage so far (input, cache and output tokens, "
                     "requests, cost in USD; live while a request runs) and the conversation's, its goal, the rows "
                     "since cursor and, once the turn has ended, its result: the agent's final reply, the picture ids "
                     "its tools got and the tools it ran. With wait_seconds it returns as soon as anything changes."),
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "The task id angelo_send returned (default: this app's latest)."},
         "cursor": {"type": "string", "description": "The cursor the last call returned, for the rows since then."},
         "wait_seconds": _WAIT, "conversation": _CONVERSATION},
         "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_image", "title": "A picture Angelo's agent got",
     "description": "One picture from the conversation's rows (a view, a render) by its id, as the agent got it.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "A picture id from a row's images."},
         "conversation": _CONVERSATION},
         "required": ["id"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_scene", "title": "The scene as Angelo's agent sees it",
     "description": ("The scene observation Angelo's agent gets with its next message: active, selected and recently "
                     "changed objects with sizes, modifiers and revisions; mode, frame and scene settings."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_viewport", "title": "The viewport as Angelo's agent sees it",
     "description": "A picture of the scene from the person's current viewport direction, as Angelo's agent gets it.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_stop", "title": "Stop Angelo's agent",
     "description": ("Stop the running turn in this app's conversation, as the person's Stop does. Committed edits "
                     "stay (Undo is the person's); waiting messages are not sent."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]
AGENT_TOOLS += [
    {"name": "angelo_settings", "title": "Angelo agent settings",
     "description": ("Read this conversation's settings (model, effort and the efforts it offers, the person's "
                     "connected models, approval setting, whether the chat trusts its scripts) and change them: model and effort among the "
                     "person's connected models (the change shows in Angelo), approval only to ASK or READ_ONLY. "
                     "AUTO_BOUNDED and any consent are the person's own choice: the call says so and asks them in "
                     "Angelo. With no arguments it only reads."),
     "inputSchema": {"type": "object", "properties": {
         "model": {"type": "string", "description": "A model id from the models this call returns."},
         "connection": {"type": "string", "description": "That model's connection_id, when two connections offer it."},
         "effort": {"type": "string", "description": "One of the efforts the model offers; empty for its default."},
         "approval": {"type": "string", "enum": ["ASK", "READ_ONLY", "AUTO_BOUNDED"]}},
         "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_new_chat", "title": "New conversation with Angelo's agent",
     "description": ("Start a fresh External conversation for this app (the agent starts with no history). The earlier "
                     "one stays readable with angelo_read's conversation."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]
from .external_mcp_hidden_tools import HIDDEN_TOOLS  # noqa: E402  Angelo without its window.
AGENT_TOOLS += HIDDEN_TOOLS
AGENT_TOOLS += VISIBILITY_TOOLS   # Views, the window, its state and usage (external_mcp_visibility).
AGENT_NAMES = frozenset(tool["name"] for tool in AGENT_TOOLS)
_CATALOG = json.dumps({"tools": AGENT_TOOLS, "instructions": INSTRUCTIONS}, ensure_ascii=False, separators=(",", ":"))


def app_tools():
    """The ``tools`` answer for a connected app: its MCP tool list and the server instructions."""
    return json.loads(_CATALOG)
