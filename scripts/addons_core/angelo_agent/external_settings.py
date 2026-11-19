# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connected app's choices for its External conversation (``external_agent``: ``angelo_settings``, ``angelo_send``):
a model among the person's connected ones, an effort the model offers, and an approval mode that only narrows. A new
or moved conversation never loosens the mode of the one it replaces (``stricter``). Widening is the person's press;
the app is told so and the person sees the request. doc/angelo/external_mcp.rst, "Who presses what". No bpy.
"""

NARROWER = frozenset({"ASK", "READ_ONLY"})   # The approval settings an app may choose: they only narrow.
STRICTNESS = {"AUTO_BOUNDED": 0, "ASK": 1, "READ_ONLY": 2}
WAITS_FOR_PERSON = "Waiting for the person in Angelo: {what}. Only they can do this; keep reading."


def stricter(first, second):
    """The stricter of two approval modes (Read only, then Ask, then Approve for me); an unknown one reads as Ask."""
    return max((first, second), key=lambda mode: STRICTNESS.get(mode, STRICTNESS["ASK"]))


def _chosen(app, found):
    """The notice of an app's model choice: the model, the person's connection it runs on and how that bills."""
    from .provider_registry import API_BILLING, TEAM_BILLING
    from .ui_text import notice
    fields = {"app": app, "model": found.get("name") or found["id"],
              "connection": found.get("connection_name") or found.get("connection_id") or ""}
    if found.get("billing") == API_BILLING:
        return notice("'{app}' chose {model} ({connection} · API billing) for this conversation.", **fields)
    if found.get("billing") == TEAM_BILLING:
        return notice("'{app}' chose {model} ({connection} · Angelo team) for this conversation.", **fields)
    return notice("'{app}' chose {model} ({connection} · Subscription) for this conversation.", **fields)


def choose(env, controller, app, payload):
    """Apply the app's model, effort and approval choices (``_settings``, ``angelo_send``); (notice rows, what the
    person is needed for or None, (code, message) of a refusal or None)."""
    from .chat_popovers import effort_choices
    from .chat_settings import clean_options, selected_model
    from .ui_text import notice
    changed, needs = [], None
    try:
        if payload.get("model"):
            choices = env.models(controller)
            wanted = payload.get("connection") or ""
            found = next((m for m in choices if m.get("id") == payload["model"]
                          and (not wanted or m.get("connection_id") == wanted)), None)
            if found is None:
                return changed, None, ("invalid", "Not one of the models the person has connected in Angelo; read "
                                       "angelo_settings for the list")
            env.choose_model(controller, found["id"], found.get("connection_id") or "")
            changed.append(_chosen(app, found))
        if "effort" in payload:
            offered = effort_choices(selected_model(controller))
            if payload["effort"] and payload["effort"] not in offered:
                return changed, None, ("invalid", "This model offers effort " + (", ".join(offered) or "none"))
            controller.set_options(effort=payload["effort"])
            changed.append(notice("'{app}' set reasoning effort to {effort}.", app=app, effort=payload["effort"])
                           if payload["effort"] else notice("'{app}' set reasoning effort to the model's default.",
                                                            app=app))
        current = clean_options(controller.state.get("options"))["approval"]
        if payload.get("approval") and payload["approval"] == current:
            pass   # Already so: nothing changes.
        elif payload.get("approval"):
            if payload["approval"] in NARROWER and STRICTNESS[payload["approval"]] > STRICTNESS.get(current, 0):
                controller.set_options(approval=payload["approval"])
                changed.append(notice("'{app}' set this conversation to Ask before changes.", app=app)
                               if payload["approval"] == "ASK" else
                               notice("'{app}' set this conversation to Read only.", app=app))
            else:   # Only the person widens: their press on the approval control.
                needs = WAITS_FOR_PERSON.format(what="their choice of the approval setting in this conversation")
                changed.append(notice("'{app}' asks you to set this conversation to Approve for me. Only you can: "
                                      "use the approval control below.", app=app)
                               if payload["approval"] == "AUTO_BOUNDED" else
                               notice("'{app}' asks you to set this conversation to Ask before changes. Only you "
                                      "can: use the approval control below.", app=app))
    except (RuntimeError, ValueError) as error:
        return changed, None, ("not_applied", str(error))
    return changed, needs, None


def record_end(directory, label, state, goal=None):
    """What an app's conversation that ends leaves on the app's record (``AppRegistry.record_end``): its approval
    mode, and whether the person ended its goal (Stop) or its budget did. ``state``: the conversation's saved or live
    state. Best effort: a failed save leaves the next conversation to the chat default and the plain goal rule."""
    from .chat_settings import clean_options
    from .external_clients import AppRegistry
    from . import goal_state as G
    try:
        AppRegistry(directory).record_end(label, clean_options((state or {}).get("options"))["approval"],
                                          bool(goal) and goal.get("status") in {G.STOPPED, G.BUDGET})
    except OSError as error:
        print("Angelo: an app's conversation end could not be saved:", error)


def record_deleted(directory, session_id, controller=None):
    """The person deletes a conversation: when it is an app's, ``record_end`` from its live state or, unloaded, its
    saved one (its label from its first row, ``external_session``), never only from an open controller."""
    import json
    from .goal_store import GoalStore
    from .sessions import SessionStore
    from .team_org_policy import read_regular
    state = controller.state if controller is not None else None
    if state is None:
        try:
            state = json.loads(read_regular(SessionStore(directory, session_id).path))
        except (OSError, ValueError):
            return
    label = getattr(controller, "external_client", "") or next(
        (row.get("client") for row in (state.get("messages") or ())[:3]
         if isinstance(row, dict) and row.get("request_kind") == "external_session"), "")
    if label:
        record_end(directory, label, state, GoalStore.shared(directory).get(session_id))
