# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The names a recovery hold's hints use for tools: the names the chat's model calls them by (``request_recovery``).

Codex threads on OpenAI defer whole tool families into a namespace (``tool_specs.codex_tools``): code mode calls them as
``tools.more__<name>``; a voice thread declares every tool in full (``dynamic_tools``), and Claude's routes name every
tool plainly (its instructions state the ``mcp__angelo__`` prefix once). A hint names a tool by the name the model calls.
Recorded (C3 early town-centre diagnostic, Sol on Codex): a hold's hint named ``angelo_scene_settings`` and
``tools.angelo_scene_settings`` was not a function. The same holds for a refusal's own prose (a binding refusal says
which read gives the current revision): the result a Codex thread receives names tools in its error and recovery text
as the model calls them (``named_for``), applied once at delivery (``providers.tool_content``) so no refusal site needs
to know its runtime.
"""

import re

TOOL_WORD = re.compile(r"(?<!\w)angelo_[a-z0-9_]+")
TEXT_FIELDS = ("error", "recovery", "recovery_requirements", "refusal_details")   # Where refusal prose and hints live.


def callable_name(controller, name):
    """``name`` as the model of ``controller``'s chat calls it."""
    state = getattr(controller, "state", None) or {}
    mode = state.get("provider")
    try:
        from .provider_registry import runtime_kind
        from .team_runtime import openai_service
        deferring = runtime_kind(mode) == "codex" and openai_service(mode) and not getattr(controller, "voice", None)
    except Exception:   # No coding runtime (an unset or media connection): plain names.
        deferring = False
    if not deferring:
        return name
    from .plan_schema import DEFERRED_TOOLS
    from .tool_specs import CODEX_DEFERRED_NAMESPACE
    return CODEX_DEFERRED_NAMESPACE + "__" + name if name in DEFERRED_TOOLS else name


def namer(controller):
    """A one-argument naming function for ``controller``'s chat (``RecoveryBarrier.naming``)."""
    return lambda name: callable_name(controller, name)


def restorable(controller):
    """``RecoveryBarrier.restorable`` for ``controller``: Restore takes its request snapshots unless it is a worker
    chat (``worker_routing.worker_snapshots``, the test Restore itself applies), on either path."""
    from . import worker_routing
    return lambda: not worker_routing.worker_snapshots(controller)


def named_for(controller, result):
    """``result`` with every tool named in its refusal and recovery text as ``controller``'s model calls it."""
    if not isinstance(result, dict) or not any(key in result for key in TEXT_FIELDS):
        return result
    if callable_name(controller, "angelo_scene_settings") == "angelo_scene_settings":
        return result   # Plain names in this runtime (any deferred tool shows it): nothing to rename.
    from .tool_specs import TOOL_MAP

    def rename(match):
        word = match.group(0)
        return callable_name(controller, word) if word in TOOL_MAP else word

    def walk(value, depth=0):
        if isinstance(value, str):
            return TOOL_WORD.sub(rename, value)
        if depth > 6:
            return value
        if isinstance(value, dict):
            return {key: walk(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, depth + 1) for item in value]
        return value
    return dict(result, **{key: walk(result[key]) for key in TEXT_FIELDS if key in result})
