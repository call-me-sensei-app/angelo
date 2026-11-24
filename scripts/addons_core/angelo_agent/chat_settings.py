# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Non-secret, per-conversation controls and runtime-advertised capabilities."""

from . import managed_policy
from .ui_text import n_


APPROVALS = {
    "ASK": (n_("Ask before changes"), n_("Review each scene mutation. Python asks unless you trust scripts for this open chat or chose to always run that exact script in this project.")),
    "AUTO_BOUNDED": (n_("Auto · bounded edits"), n_("Auto-approve validated object edits and native sculpt passes. Python trust is separate.")),
    "READ_ONLY": (n_("Read only"), n_("Inspect and discuss the scene; reject every mutation, including Python.")),
}
# Every reasoning effort a runtime may advertise, weakest first (the order ``default_effort`` ranks by).
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
# What Claude Code's --effort accepts; a chat's choice outside it is no choice there (``request_effort``).
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# The default reasoning effort (maintainer's ruling, 2026-11-12): this level wherever a model offers it, else the
# highest level the model offers. ``default_effort`` is the only place that decides it; a chat stores an effort
# only when its person chose one (``stored_options``), so the default follows the model the chat runs.
DEFAULT_EFFORT = "high"
# Models whose default is another level, ``{model ID: effort}``, used only where the model offers that level.
# The one place a model's adapter states such an exception; a catalog's own default is never read.
DEFAULT_EFFORT_EXCEPTIONS = {
    # Gemini 4 Carbon: medium, its highest-but-one level (maintainer ruling 2026-11-12: "default effort for
    # everything should be high with exception of Carbon, which can be Medium since High is the highest").
    "gemini-4-carbon": "medium",
}
# A new chat, and a saved one with no approval choice, starts in Auto (doc/angelo/agent_chat.rst, "Approvals").
# A saved choice is kept. Python still asks in Auto unless the person opts in (always_allow, trusted_scripts).
DEFAULT_APPROVAL = "AUTO_BOUNDED"
# A saved chat's ``approval_version``: 2 since Auto became the default. Without it, a saved Ask was only the old
# default, so loading moves it to Auto once (``migrate_saved_approval``); a later Ask is the person's and stays.
# A build from before the marker drops it when it saves the chat (its save whitelist), so such a save makes a
# deliberate Ask look like the old default again: accepted, since every 1.0 build writes the marker.
APPROVAL_VERSION = 2
# Plan mode (doc/angelo/plan_mode.rst): a planning chat is a Read only chat with ``planning`` set, and the setting it
# builds in after the person's Go preselected (``plan_return``). Planning holds only in Read only (``stored_options``),
# so every Read only refusal applies to it and the flag can never weaken one.
PLAN_RETURNS = ("ASK", "AUTO_BOUNDED")


def stored_options(value):
    """A chat's options as the person chose them, valid and complete: what a chat keeps and saves.

    Only a missing approval takes the default (Auto); any other value outside
    ``APPROVALS`` (a typo, a mode a later build adds) reads as Ask, the
    restrictive reading. ``effort`` is the person's choice, or ``""`` when they
    made none (or it is no level any runtime knows): the request then runs at
    its model's default (``chat_effort``). A build from before the default
    moved saved "low" in every chat, so a saved "low" stays "low": it cannot be
    told from a choice.
    """
    value = value if isinstance(value, dict) else {}
    approval = value.get("approval")
    if approval is None:
        approval = DEFAULT_APPROVAL
    elif not isinstance(approval, str) or approval not in APPROVALS:
        approval = "ASK"
    options = {"model": str(value.get("model") or "")[:128],
               "effort": value.get("effort") if _level(value.get("effort")) else "",
               "fast": value.get("fast") is True,
               "approval": approval}
    from .planning_state import clean_mark
    mark = clean_mark(value.get("plan_draft"))   # Edit's mark on the draft, kept with the chat (plan_mode.rst, N2b).
    if mark is not None:
        options["plan_draft"] = mark
    if value.get("planning") is True and approval == "READ_ONLY":   # Only a Read only chat plans (plan_mode.rst).
        options["planning"] = True
        options["plan_return"] = value["plan_return"] if value.get("plan_return") in PLAN_RETURNS else "ASK"
    return options


def planning(value):
    """Whether a chat with these options is in plan mode: Read only with ``planning`` set (``stored_options``)."""
    return stored_options(value).get("planning") is True


NARROWING_APPROVALS = frozenset({"ASK", "READ_ONLY"})   # Anything may choose them (doc/angelo/grant_operators.rst).


def options_widen(arguments):
    """Whether a chat's ``set_options(**changes)`` widens (``grant_guard.entry``; ``arguments`` bound, plain values),
    read from the call alone, never from the chat's state, which a script could write (the C guard review, SHOULD-3):
    Ask, Read only and Fast off narrow; anything else widens, a model or effort choice and an unknown key included
    (Copy setup break-it r2, S2: narrowing was refused to agent code). Plan mode: starting it narrows, ending it
    widens, and only Ask as its build setting narrows (doc/angelo/plan_mode.rst, "State")."""
    for key, value in arguments["changes"].items():
        if key == "approval":
            if value not in NARROWING_APPROVALS:
                return True
        elif key == "planning":
            if value is not True:
                return True
        elif key == "plan_return":
            if value != "ASK":
                return True
        elif key == "plan_draft":   # Edit's provenance mark: setting it is the person's Edit; clearing it narrows.
            if value is not None:
                return True
        elif key != "fast" or value is not False:
            return True
    return False

def clean_options(value):
    """A chat's effective options, for every reader: dispatch, views and the composer.

    ``RequireAskForEdits`` (managed or organization policy) reads Auto as Ask
    here, never in what the chat saves (``stored_options``), so the chat is
    back in the person's mode when the policy lifts.
    """
    options = stored_options(value)
    if options["approval"] == "AUTO_BOUNDED" and managed_policy.current().require_ask_for_edits:
        options["approval"] = "ASK"
    return options


def migrated(version):
    """Whether a saved chat's ``approval_version`` marks its approval as the person's (this version or later)."""
    return type(version) is int and version >= APPROVAL_VERSION


def migrate_saved_approval(options, version, *, external=False):
    """``(options, version)`` for a chat read from disk: an old default Ask becomes Auto, once.

    A chat saved before ``APPROVAL_VERSION`` holds "ASK" only because Ask was
    the default then. It becomes Auto and is marked, so an Ask chosen later
    survives reloads. Under ``RequireAskForEdits`` it still reads as Ask
    (``clean_options``) until the policy lifts. Read only is never changed, nor
    an External CLI conversation (``external``), which stays in Ask.
    """
    if migrated(version):
        return options, version
    if external or not isinstance(options, dict) or options.get("approval") != "ASK":
        return options, APPROVAL_VERSION
    return dict(options, approval=DEFAULT_APPROVAL), APPROVAL_VERSION


def normalize_models(models, provider="codex"):
    output = []
    for model in models[:100]:
        if not isinstance(model, dict) or model.get("hidden"):
            continue
        identity = model.get("model") or model.get("value") or model.get("id")
        if not isinstance(identity, str) or not identity or len(identity) > 128:
            continue
        efforts = model.get("supportedReasoningEfforts", model.get("supportedEffortLevels", []))
        efforts = [item.get("reasoningEffort") if isinstance(item, dict) else item for item in efforts]
        efforts = [item for item in efforts if item in EFFORTS]
        tiers = model.get("serviceTiers", [])
        tier = next((t["id"] for t in tiers if isinstance(t, dict) and t.get("id") in {"fast", "priority"}), None)
        # Claude's native runtime owns eligibility/org checks. Fast on a
        # subscription draws paid usage credits, not included plan usage.
        if provider == "claude" and (identity.split("[", 1)[0] == "opus" or any(x in identity for x in ("opus-5", "opus-4-8"))):
            tier = "native-fast"
        output.append({"id": identity, "name": str(model.get("displayName") or model.get("name") or identity)[:100],
                       "description": str(model.get("description") or "")[:500],
                       "default": bool(model.get("isDefault")) or (provider == "claude" and identity == "default"), "efforts": efforts,
                       "runtime_default_alias": provider == "claude" and identity == "default", "fast_tier": tier,
                       "images": "image" in model.get("inputModalities", []) or provider == "claude"})
    return output


def selected_model(controller, options=None):
    displaying = options is None
    options = clean_options(controller.state.get("options") if options is None else options)
    settings = getattr(controller, "selection_settings", controller.settings) if displaying else controller.settings
    models = getattr(controller, "selection_models", controller.models) if displaying else controller.models
    identity = options["model"] or settings.get("model")
    if not identity:
        # The catalog's default; a connection offering exactly one model has that one, chosen for the person.
        return next((m for m in models if m["default"]), models[0] if len(models) == 1 else None)
    found = next((m for m in models if m["id"] == identity), None)
    if found is None:
        # A full model ID that this catalog lists under its alias ("claude-opus-5-5" as "opus"):
        # the chip then shows the model's name instead of the raw ID. An unknown ID is never guessed.
        from .model_profiles import catalog_ids, resolve
        alias = catalog_ids(models).get(resolve(identity))
        found = next((m for m in models if m["id"] == alias), None)
    return found


def codex_turn_options(controller, options=None):
    options = clean_options(controller.state.get("options") if options is None else options)
    model = selected_model(controller, options)
    parameters = {}
    identity = options["model"] or controller.settings.get("model") or (model["id"] if model else "")
    if identity:
        if controller.models and not any(m["id"] == identity for m in controller.models):
            raise ValueError("The selected model is no longer available on this connection. Choose another model.")
        parameters["model"] = identity
    if not (model and model.get("untested")):
        # An unmapped model of a custom endpoint (endpoint_connections) gets none: its server's own default, never
        # a reasoning level the model may refuse.
        parameters["effort"] = chat_effort(options, model)
    # A per-turn override prevents sticky Fast billing when the toggle is off.
    # null would inherit the previous/native profile tier in newer runtimes.
    parameters["serviceTierForTurn"] = "default"
    if options["fast"]:
        if not model or not model["fast_tier"]:
            raise ValueError("Fast mode isn't advertised for this model/account. Turn it off or select a supported model.")
        parameters["serviceTierForTurn"] = model["fast_tier"]
    return parameters


def _level(value, accepted=EFFORTS):
    """Whether ``value`` is one of the ``accepted`` effort levels (any other type or text is not)."""
    return type(value) is str and value in accepted


def default_effort(efforts=(), identity=""):
    """The effort a chat runs at when its person chose none, on a model that offers ``efforts``.

    ``DEFAULT_EFFORT`` where the model offers it, else the highest level it
    offers (``EFFORTS`` order), never merely the first one it lists. A model in
    ``DEFAULT_EFFORT_EXCEPTIONS`` takes its own level where it offers that one.
    A model that advertises no level (its catalog not read yet, or no such
    control) is taken to offer them all. Every caller asks here, so the
    defaults cannot drift apart (``test_native_default_effort.py``).
    """
    offered = [level for level in EFFORTS if level in (efforts or ())] or list(EFFORTS)
    exception = DEFAULT_EFFORT_EXCEPTIONS.get(identity) if type(identity) is str else None
    if _level(exception, offered):
        return exception
    return DEFAULT_EFFORT if DEFAULT_EFFORT in offered else offered[-1]


def chat_effort(options, model=None, accepted=EFFORTS):
    """The effort a chat's request runs at on ``model``: its person's choice where it holds, else the default.

    The choice (``options["effort"]``; ``""`` is none) holds while the model
    offers it, or advertises no level at all. ``accepted`` narrows the model's
    levels and the choice to what the runtime takes (``request_effort``). What
    the chip and the reasoning card show is this same value
    (``chat_popovers.effective_effort``).
    """
    options = options if isinstance(options, dict) else {}
    model = model if isinstance(model, dict) else {}
    offered = [level for level in accepted if level in (model.get("efforts") or ())]
    chosen = options.get("effort")
    if _level(chosen, offered or accepted):
        return chosen
    return default_effort(offered or accepted, model.get("id") or options.get("model") or "")


def carried_effort(options, model):
    """The effort choice a chat keeps when its model becomes ``model``: its own while that model offers it, else none.

    A choice the new model lacks is dropped (``""``), never replaced by a level
    written into the chat: the chat then follows its model's default, also
    after a later model change.
    """
    chosen = stored_options(options)["effort"]
    return chosen if chosen and chat_effort({"effort": chosen}, model) == chosen else ""


def request_effort(context, options, runtime):
    """The effort ``context``'s request sends on ``runtime`` ("claude" or "codex"), as its receipt records it.

    The model is the one the request runs (``selected_model``); a context with
    no catalog has none, and the default is then the runtime's own. Claude Code
    accepts ``CLAUDE_EFFORTS`` only (its ``--effort``). ``codex_turn_options``
    sends the same value for a Codex turn.
    """
    options = clean_options(options)
    model = selected_model(context, options) if getattr(context, "models", None) and hasattr(context, "settings") else None
    return chat_effort(options, model, CLAUDE_EFFORTS if runtime == "claude" else EFFORTS)
