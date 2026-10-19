# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Non-secret, per-conversation controls and runtime-advertised capabilities."""

from . import managed_policy
from .ui_text import n_


APPROVALS = {
    "ASK": (n_("Ask before changes"), n_("Review each scene mutation. Python asks unless you explicitly trust scripts for this open chat.")),
    "AUTO_BOUNDED": (n_("Auto · bounded edits"), n_("Auto-approve validated object edits and native sculpt passes. Python trust is separate.")),
    "READ_ONLY": (n_("Read only"), n_("Inspect and discuss the scene; reject every mutation, including Python.")),
}
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
# What Claude Code's --effort accepts; anything else is sent as low.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# A new chat, and a saved one with no approval choice, starts in Auto (doc/angelo/agent_chat.rst, "Approvals").
# A saved choice is kept. Python still asks in Auto unless the person opts in (always_allow, trusted_scripts).
DEFAULT_APPROVAL = "AUTO_BOUNDED"
# A saved chat's ``approval_version``: 2 since Auto became the default. Without it, a saved Ask was only the old
# default, so loading moves it to Auto once (``migrate_saved_approval``); a later Ask is the person's and stays.
# A build from before the marker drops it when it saves the chat (its save whitelist), so such a save makes a
# deliberate Ask look like the old default again: accepted, since every 1.0 build writes the marker.
APPROVAL_VERSION = 2


def stored_options(value):
    """A chat's options as the person chose them, valid and complete: what a chat keeps and saves.

    Only a missing approval takes the default (Auto); any other value outside
    ``APPROVALS`` (a typo, a mode a later build adds) reads as Ask, the
    restrictive reading.
    """
    value = value if isinstance(value, dict) else {}
    approval = value.get("approval")
    if approval is None:
        approval = DEFAULT_APPROVAL
    elif not isinstance(approval, str) or approval not in APPROVALS:
        approval = "ASK"
    return {"model": str(value.get("model") or "")[:128],
            "effort": value.get("effort") if value.get("effort") in EFFORTS else "low",
            "fast": value.get("fast") is True,
            "approval": approval}


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
                       "runtime_default_alias": provider == "claude" and identity == "default",
                       "default_effort": model.get("defaultReasoningEffort") or "low", "fast_tier": tier,
                       "images": "image" in model.get("inputModalities", []) or provider == "claude"})
    return output


def selected_model(controller, options=None):
    displaying = options is None
    options = clean_options(controller.state.get("options") if options is None else options)
    settings = getattr(controller, "selection_settings", controller.settings) if displaying else controller.settings
    models = getattr(controller, "selection_models", controller.models) if displaying else controller.models
    identity = options["model"] or settings.get("model")
    if not identity:
        return next((m for m in models if m["default"]), None)
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
    effort = options["effort"]
    if model and model["efforts"] and effort not in model["efforts"]:
        effort = model["default_effort"] if model["default_effort"] in model["efforts"] else model["efforts"][0]
    parameters["effort"] = effort
    # A per-turn override prevents sticky Fast billing when the toggle is off.
    # null would inherit the previous/native profile tier in newer runtimes.
    parameters["serviceTierForTurn"] = "default"
    if options["fast"]:
        if not model or not model["fast_tier"]:
            raise ValueError("Fast mode isn't advertised for this model/account. Turn it off or select a supported model.")
        parameters["serviceTierForTurn"] = model["fast_tier"]
    return parameters


def claude_effort(effort):
    """The effort a Claude launch actually sends for a chat's effort option."""
    return effort if effort in CLAUDE_EFFORTS else "low"
