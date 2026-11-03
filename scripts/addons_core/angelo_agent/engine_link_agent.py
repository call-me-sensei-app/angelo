# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the agent's engine link tools (``engine_link_tool_host``) ask of the link (``engine_link``), on its state.

The link stays the person's: nothing here turns it on, opens a file or answers a request. ``ask`` raises the same
request card an editor's Edit in Angelo raises, for the person to answer; ``save`` saves the linked asset's own
.blend as File > Save does, so the link's save handler writes it back. Main thread.
"""

import time

import bpy

from . import engine_link as link, engine_link_assets as assets
from .engine_link_facts import plain

STOP_AFTER = 2   # Agent requests in a row the person didn't open, after which no caller asks until the person acts.


def session_number():
    """The linked session's number (0: none): a new link, even of the same asset, has a new one."""
    found = link._state["session"]
    return found.get("number", 0) if found else 0


def link_facts():
    """What was pushed, saved and acknowledged for the linked asset (``engine_link_facts``); read only."""
    return link._state["facts"]


def last_error():
    """The link's last error, with no path in it (``engine_link_facts.plain``), or None."""
    error = link._state["error"]
    return plain(error) if error else None


def followed_window():
    """The window whose scene the link follows (``engine_link._window``, through ``live_windows``), or None in a
    session without windows (a background Angelo follows the context's scene)."""
    return None if bpy.app.background else link._window()


def dirty():
    """Whether a change to the linked scene waits to be exported live."""
    return link._state["dirty_at"] is not None


def agent_acting():
    """Whether any conversation's agent call is executing now (a script, a tool, a connected app's): a link switched
    on then is the agent's doing, never the person's, whatever route reached it (break-it 7-1). True when unknown."""
    try:
        from . import ui
        return any(getattr(call, "executing", False) for controller in list(ui._controllers.values())
                   for call in list(controller.pending.values()))
    except Exception:   # noqa: BLE001  An unreadable state keeps every hold.
        return True


def caller_of(controller):
    """Who asks (``ask``): ``who`` is the conversation (Angelo's agent) or the connected MCP app; ``spoke_at`` is when
    the person last sent their own words in that conversation (``Controller.send``: never Angelo's own requests,
    escalations or worker events, nor an app's task; break-it 4-1), None for an app."""
    app = getattr(controller, "external_client", None) or None
    state = getattr(controller, "state", None) or {}
    return {"who": ("app", app) if app else ("chat", state.get("id") or id(controller)), "app": app,
            "spoke_at": None if app else getattr(controller, "person_spoke_at", None)}


def refusal(asset, caller):
    """Why ``caller`` may not ask for ``asset`` now, or None. Explicit state (``engine_link_facts.new_policy``) that only
    the person moves, never a clock, Angelo's own turns or the engine's requests (break-it 4-1..5-P): a hold is
    released for a conversation when the person has written there after the decline that set it (an app: when the
    person opens any request, or turns the link off and on).

    * a caller whose own request the person didn't open waits for that;
    * after ``STOP_AFTER`` such requests in a row, every caller waits for the person's words after the latest;
    * an asset whose agent request the person didn't open waits the same way.

    Opening any request releases everyone (``engine_link_facts.request_ended``)."""
    from .engine_link_facts import new_policy
    policy = link._state.setdefault("agent_policy", new_policy())
    spoke = caller["spoke_at"]

    def held(since):
        return since is not None and (spoke is None or spoke <= since)

    if held(policy["held"].get(caller["who"])):
        reason = "The person didn't open this conversation's earlier request"
    elif policy["streak"] >= STOP_AFTER and held(policy["last_decline"]):
        reason = "The person didn't open the last %d requests" % policy["streak"]
    elif held(policy["assets"].get((asset.engine, asset.project, asset.ident))):
        reason = "The person didn't open %s when asked; they can open it from the engine" % asset.ident
    else:
        return None
    if caller["app"]:
        return reason + ". It can ask again once the person opens a request from the engine or turns the link off and on"
    return reason + ". Ask them in the chat; once they reply there, you may ask again"


def ask(asset, project_name, live, caller):
    """The agent's request to open ``asset``: the same card an editor's Edit in Angelo raises, answered only by the
    person; nothing opens here. No editor sent it, so none hears a reply until the person opens it (then every
    editor of the project hears ``opened``, ``engine_link._begin``). ``caller`` (``caller_of``) names who asked on the
    card. Raises RuntimeError while another request waits for the person, or with ``refusal``'s reason."""
    state = link._state
    if state["pending"] is not None:
        raise RuntimeError("A request is already waiting for the person's answer")
    reason = refusal(asset, caller)
    if reason:
        raise RuntimeError(reason)
    state["pending"] = {"asset": asset, "client": None, "id": "", "at": time.monotonic(), "by_agent": True,
                        "who": caller["who"], "app": caller["app"],
                        "project_name": str(project_name)[:200], "node": "", "live": bool(live)}
    link._notify("request", state["pending"])
    link._attention()
    return state["pending"]


def save(window):
    """Save the linked asset's open .blend, as the person's File > Save does: the save handler writes the asset back
    (or, for a .blend, tells the editors). Returns the save's number (``engine_link_facts``); raises RuntimeError
    when nothing is linked, the open file isn't the asset's, or Blender didn't save."""
    state = link._state
    found = state["session"]
    if found is None:
        raise RuntimeError("No engine asset is linked")
    if not bpy.data.filepath or not assets.same_file(bpy.data.filepath, found["asset"].editable):
        raise RuntimeError("The open file is not the linked asset's")
    before = state.get("saves", 0)
    try:
        result = link._in(window, lambda: bpy.ops.wm.save_mainfile(filepath=bpy.data.filepath))
    except RuntimeError as error:   # Blender's report names the file: the agent reads none (engine_link_facts.plain).
        raise RuntimeError("Blender didn't save the file: " + plain(error)) from None
    if "FINISHED" not in result or state.get("saves", 0) == before:
        raise RuntimeError("Blender didn't save the file")
    return state["saves"]
