# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The effort a live-agent run's chat runs at, for its report (test_native_gate_effort).

A run that asks for "default" sets no effort, so the chat runs at the app's own default: what a release gate
measures (maintainer, 2026-11-12: "Shipping default (high)"). The report names the effort the chat resolved."""


import os


def chat_options(**others):
    """``(options, effort)``: the chat options a run sets (``others``, plus its ``ANGELO_EVAL_EFFORT`` unless that is
    "default" or unset) and the effort it asked for."""
    effort = os.environ.get("ANGELO_EVAL_EFFORT") or "default"
    return dict(others, **({} if effort == "default" else {"effort": effort})), effort


def report(effort, controller):
    """The report's effort fields: what the run asked for and what the chat runs at."""
    return {"effort_requested": effort, "effort_resolved": resolved(controller)}


def resolved(controller):
    """The effort the chat runs at: the one the run set, else the app's own default for the chat's model
    (``chat_settings.chat_effort`` where this build has it)."""
    from angelo_agent import chat_settings
    stored = dict(controller.state.get("options") or {})
    resolve = getattr(chat_settings, "chat_effort", None)
    if resolve is None:
        return stored.get("effort") or "app default"
    try:
        return resolve(stored, chat_settings.selected_model(controller, stored))
    except Exception as error:   # A report field: say why it is unknown, never fail the run over it.
        return "unknown (%s: %s)" % (type(error).__name__, error)
