# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Why a chat that looks ready can't start yet, as the kit's inline notice with the one action that fixes it.

Never a dead click (doc/angelo/agent_chat.rst, "When the chat can't start"). Two cases, each a card dict for
``ui_kit_status.notice`` (kind, title, body, actions, details):

* ``model_card``: the account is connected but the chat has no models of its own. They are loading (no action: it
  ends by itself), failed to load (Retry, the provider's text under Details) or loaded empty (Retry); or other
  ready connections offer models (Choose a model, which opens the model list). With models of its own and none
  chosen, the connection's default runs and the model button says Default, as the list's checked row does
  (``default_label``). Drawn on the empty chat's welcome, between the subtitle and the example prompts
  (``chat_welcome.draw_empty``).
* ``blocked_card``: a click reached the chat while another tool holds this window's input
  (``input_policy.canvas_blocking``). It names the tool and says how to end it (Esc, or turning off an add-on
  that keeps running); it has no button, since the chat's
  clicks are exactly what that tool holds. Drawn above the composer for ``BLOCKED_SECONDS`` after the click, and gone
  with the chat's first event after the tool ends (``chat_view``).

Pure: no Blender module, so the host tests drive every state. Every string is translated where it is built.
"""

from .ui_text import iface_, n_

LOADING, FAILED, EMPTY, CHOOSE = "loading", "failed", "empty", "choose"
BLOCKED_SECONDS = 12.0
CHOOSE_MODEL, RETRY_MODELS = "welcome_choose_model", "welcome_retry_models"
DETAILS = "model_details"   # The card's Details disclosure: chat_welcome maps it to the chat's expand action.
EXPANDED = ("notice", "models")

_MODEL_TEXT = {
    LOADING: ("info", n_("Loading your models…"), n_("Your account is connected. The chat is ready as soon as its "
                                                      "models arrive.")),
    FAILED: ("error", n_("Couldn't load your models"), n_("Your account is connected, but its list of models didn't "
                                                          "load. Try again.")),
    EMPTY: ("warn", n_("No models available"), n_("Your account is connected, but it offered this chat no models. "
                                                  "Try again, or check your plan with the provider.")),
    CHOOSE: ("info", n_("Choose a model to start"), n_("Your account is connected. Pick the model this chat uses; "
                                                       "you can change it at any time from the model button below.")),
}
_ACTIONS = {FAILED: (RETRY_MODELS, n_("Retry")), EMPTY: (RETRY_MODELS, n_("Retry")),
            CHOOSE: (CHOOSE_MODEL, n_("Choose a model"))}


def needs_model(model, explicit_model, team_named):
    """True when the composer's model button would read "Choose model": no model resolved, no model ID typed in,
    and not a team connection (which names itself and serves its own models)."""
    return not (model or explicit_model or team_named)


def model_problem(own, choices, loading, error):
    """Which state an unchosen model is in, or None when nothing needs the person. ``own`` are the models of the
    chat's connection: with them and no explicit choice, that connection's default runs (the model list's checked
    Default row), so the model button reads Default and no card shows. ``choices`` are every ready connection's
    models: with none of its own, the chat picks one there. Else its models are loading, failed or empty."""
    if own:
        return None
    if choices:
        return CHOOSE
    if loading:
        return LOADING
    if error:
        return FAILED
    return EMPTY


def model_card(problem, error="", expanded=False):
    kind, title, body = _MODEL_TEXT[problem]
    actions = ()
    if problem in _ACTIONS:
        action, label = _ACTIONS[problem]
        actions = ((action, None, iface_(label), "primary"),)
    details = str(error).strip() if problem == FAILED and error else None   # The provider's text, never translated.
    return dict(kind=kind, title=iface_(title), body=iface_(body), actions=actions, details=details,
                expanded=bool(details and expanded), details_action=DETAILS if details else None)


def default_label(own):
    """The model button's text when nothing is chosen: Default while the chat's connection has models (its default
    runs, as the model list's checked Default row says), else Choose model."""
    return iface_("Default") if own else iface_("Choose model")


def blocked_card(tools):
    """The card for a click the chat could not take because ``tools`` (names as the person sees them) hold it."""
    names = ", ".join(str(tool) for tool in tools if tool) or iface_("Another tool")
    return dict(kind="warn", title=iface_("The chat is waiting for another tool"),
                body=iface_("“{tool}” is running in this window and is holding its clicks. Finish it or press Esc. "
                            "If it is an add-on that keeps running, turn it off in Preferences > Add-ons.",
                            tool=names),
                actions=(), details=None, expanded=False, details_action=None)


def blocked_shown(blocked, now):
    """The tool names of a recent blocked click (``(tools, time)`` as ``chat_view`` stores it), else None."""
    if not blocked:
        return None
    tools, at = blocked
    return tools if 0 <= now - at <= BLOCKED_SECONDS else None
