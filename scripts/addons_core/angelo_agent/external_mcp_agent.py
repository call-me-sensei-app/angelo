# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The launcher's agent tools (``external_mcp_tools.AGENT_TOOLS``), mixed into ``external_mcp.Server``.

Each tool is one ``agent`` command to Angelo's window (``external_agent``). ``angelo_read``, ``angelo_status`` and
``angelo_send`` with ``wait_seconds`` are a long poll (``_gather``): Angelo answers as soon as the conversation changes;
the launcher reads again with the new cursor, never on a timer, sends one ``notifications/progress`` per answer when the
client gave a progress token, and returns the rows it gathered once the turn needs the app's attention (it ended, waits
for the person, nothing is left running) or the wait is over. Pure standard library; doc/angelo/external_mcp.rst,
"Working through Angelo's agent".
"""

import json
import time

from .external_clients import AppRegistry
from .external_mcp_tools import MAX_AGENT_WAIT
from . import external_mcp_visibility as visibility, external_send_facts as send_facts

NOT_OPEN = ("Angelo isn't open ({reason}). Ask the person to open Angelo (and the file to work on); Angelo's agent works "
            "in its window, where they approve and can stop it. The first time, they also connect a model there (a "
            "subscription, an API key or their team).")
PLAN_FIRST = "Plan first arrives in Angelo 2.1.0; send the request without it."
# A state the app's model should hear about at once: the turn ended, or it waits for the person.
SETTLED = frozenset({"idle", "waiting_for_approval", "waiting_for_person"})


def _agent_wait(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return min(float(MAX_AGENT_WAIT), max(0.0, float(value)))
    return 0.0


def _agent_failure(envelope):
    """A refused or failed agent command: the CLI's code, or the reason the host gave (``needs_connection``)."""
    result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
    code = str(result.get("reason") or envelope.get("code") or "unavailable")
    message = str(envelope.get("message") or code)
    value = dict(result, code=code, message=message)
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": value, "isError": True}


def _agent_result(envelope, rows=None, sent=None):
    """An agent command's MCP result: its JSON as text and ``structuredContent``; ``rows`` gathered by a long poll.
    ``sent``: a waiting send's own answer, of which only its facts are kept (``external_send_facts``, the host's
    definition too); the state, its live page link included, is the read's alone."""
    if envelope.get("code") != "ok":
        return _agent_failure(envelope)
    value = send_facts.answer(sent, envelope.get("result") or {})
    if rows is not None:
        value["rows"] = [rows[index] for index in sorted(rows)]
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": value, "isError": False}


def _agent_image(envelope):
    if envelope.get("code") != "ok":
        return _agent_failure(envelope)
    image = (envelope.get("result") or {}).get("image") or {}
    if not image.get("data"):   # Never an empty picture: say what happened instead.
        return _agent_failure(dict(envelope, code="not_applied",
                                   message=envelope.get("message") or "No picture was returned"))
    return {"content": [{"type": "image", "data": image.get("data", ""), "mimeType": image.get("mimeType", "image/png")},
                        {"type": "text", "text": json.dumps({"id": image.get("id")})}], "isError": False}


class AgentTools:
    """``self.request``, ``self.state``, ``self.client``, ``self._progress`` and ``self._cancelled_request`` are
    ``external_mcp.Server``'s."""

    def agent_request(self, args, wait=0.0):
        """One ``agent`` command to Angelo's window; an unreachable window answers ``angelo_not_open``, a launcher
        code (never a CLI one, ``external_cli_protocol``), with how to go on."""
        value = self.request({"command": "agent", "args": args}, wait)
        if value.get("code") == "unavailable" and AppRegistry(self.state).key(self.client) \
                and not (value.get("call") or {}).get("state"):
            if "disconnected" not in str(value.get("message")) and "does not match" not in str(value.get("message")):
                return {"code": "angelo_not_open",
                        "message": NOT_OPEN.format(reason=str(value.get("message") or "not running").rstrip("."))}
        return value

    def agent(self, identity, name, arguments, params):
        """One agent tool (``external_mcp_tools.AGENT_TOOLS``) as ``agent`` commands."""
        wait = _agent_wait(arguments.get("wait_seconds"))
        conversation = {"conversation": arguments["conversation"]} if arguments.get("conversation") else {}
        if name == "angelo_send":
            if "plan_first" in arguments:   # Refused before anything is sent (doc/angelo/external_mcp.rst, "Plan first").
                return _agent_failure({"code": "not_available", "message": PLAN_FIRST})
            first = self.agent_request(dict({key: arguments[key] for key in ("model", "connection", "effort",
                                                                             "attachments", "goal") if key in arguments},
                                            action="send", message=arguments.get("message"), wait=0))
            if first.get("code") != "ok" or not wait:
                return _agent_result(first)
            return self._gather(identity, params, first["result"], first["result"]["cursor"], wait, "brief",
                                {"action": "status", "task": first["result"].get("task") or ""})
        if name == "angelo_read":
            detail = arguments.get("detail", "brief")
            if not wait:
                return _agent_result(self.agent_request({"action": "read", "after": arguments.get("cursor"),
                                                         "detail": detail, "wait": 0, **conversation}))
            return self._gather(identity, params, None, arguments.get("cursor"), wait, detail, conversation)
        if name == "angelo_status":
            extra = dict(conversation, action="status", task=arguments.get("task") or "")
            if not wait:
                return _agent_result(self.agent_request(dict(extra, after=arguments.get("cursor"), detail="brief",
                                                             wait=0)))
            return self._gather(identity, params, None, arguments.get("cursor"), wait, "brief", extra)
        if name == "angelo_image":
            return _agent_image(self.agent_request({"action": "image", "id": arguments.get("id"), **conversation}))
        if name == "angelo_viewport":
            return _agent_image(self.agent_request({"action": "viewport"}))
        if name in visibility.VISIBILITY_NAMES:
            return visibility.call(self, name, arguments)
        if name == "angelo_scene":
            return _agent_result(self.agent_request({"action": "scene"}))
        if name == "angelo_settings":
            return _agent_result(self.agent_request(dict({key: arguments[key] for key in
                                                          ("model", "connection", "effort", "approval")
                                                          if key in arguments}, action="settings")))
        if name == "angelo_new_chat":
            return _agent_result(self.agent_request({"action": "new_chat"}))
        return _agent_result(self.agent_request({"action": "stop"}))

    def _gather(self, identity, params, sent, cursor, wait, detail, conversation):
        """The long poll: read again whenever Angelo answers with a change, until the turn settles or ``wait`` ends;
        the rows gathered (newest content per row) and the last state."""
        deadline, rows, answers, last = time.monotonic() + wait, {}, 0, None
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            value = self.agent_request({"action": "read", **conversation, "after": cursor, "detail": detail,
                                        "wait": round(min(MAX_AGENT_WAIT, remaining), 3)}, remaining)
            if self._cancelled_request(identity):
                return None
            if value.get("code") not in ("ok", "busy"):   # An end (access ended, another build, not reachable): said,
                gathered = {"rows": [rows[index] for index in sorted(rows)]} if rows else {}   # never the last state.
                if sent is not None:   # The message went (the app must not send it again), with its task; never
                    gathered.update(send_facts.facts(sent))   # a link the session may have lost with its access.
                return _agent_failure(dict(value, result=gathered))
            if value.get("code") != "ok":
                if last is not None:
                    return _agent_result(last, rows, sent)
                if sent is not None:   # The message went: answered as sent, never an error the app would resend.
                    # No read yet, so the send's own state is the latest there is, as a send without a wait answers.
                    return _agent_result({"code": "ok", "result": sent}, rows)
                return _agent_result(value)
            result, last = value["result"], value
            for row in result.get("rows") or ():
                rows[row["i"]] = row
            cursor, answers = result.get("cursor") or cursor, answers + 1
            if result.get("turn") in SETTLED or time.monotonic() >= deadline or result.get("more"):
                return _agent_result(value, rows, sent)
            self._progress(params, answers, "Angelo's agent: %s, %d rows" % (result.get("turn"), len(rows)))
