# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The MCP tool entries for Angelo without its window (``external_mcp_hidden``), part of the agent tools
(``external_mcp_tools.AGENT_TOOLS``). doc/angelo/mcp_headless_window.rst."""

HIDDEN_TOOLS = [
    {"name": "angelo_open_file", "title": "Work on a .blend without Angelo's window",
     "description": ("When Angelo isn't open (macOS): start Angelo on this .blend with its window hidden, so "
                     "angelo_send, angelo_read and the other agent tools work with Angelo's agent as with the window. "
                     "Angelo works on a copy next to the file, saved as it works and when the session ends; the file "
                     "itself is never written. Refused while Angelo is already open: then use that window. Approvals "
                     "wait for the person: angelo_read shows the card, angelo_show brings the window up."),
     "inputSchema": {"type": "object", "properties": {
         "file": {"type": "string", "description": "Absolute path to the .blend file."},
         "caps": {"type": "object", "description": "Lower the session's limits (minutes, usd, tokens); never raise.",
                  "properties": {"minutes": {"type": "number"}, "usd": {"type": "number"},
                                 "tokens": {"type": "integer"}}, "additionalProperties": False}},
         "required": ["file"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_file_status", "title": "The file Angelo works on without its window",
     "description": "The .blend this session works on without Angelo's window, if any.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_close_file", "title": "End the session without Angelo's window",
     "description": ("End the session angelo_open_file started: Angelo stops its turn, saves next to the file and "
                     "quits."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_answer_approval", "title": "Decline a waiting approval",
     "description": ("Only after angelo_open_file: decline the card angelo_read shows in "
                     "state.approval (its card). You can only decline: approve answers needs_person, because only "
                     "the person approves, in Angelo (angelo_show brings the window up)."),
     "inputSchema": {"type": "object", "properties": {
         "card": {"type": "string", "description": "state.approval.card from angelo_read."},
         "answer": {"type": "string", "enum": ["decline", "approve"]}},
         "required": ["card", "answer"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": "angelo_show", "title": "Show Angelo's window",
     "description": ("Only after angelo_open_file (Angelo working with its window hidden): bring "
                     "Angelo's window up on that file so the person can answer an approval angelo_read shows in "
                     "state.approval, or take over. Tell your human first. Afterwards it is the person's window: "
                     "they answer approvals there and your calls go to it."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]
