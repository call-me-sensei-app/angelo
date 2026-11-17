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
to know its runtime. Only tool references are renamed, never literal data spelled as one (break-it MHM-L-1:
a merge's material swap from ``angelo_scene_settings`` came back as a swap from ``more__angelo_scene_settings``): a
word stays as it is wherever it appears (prose or a structured value) when the call itself gave it as a name, a whole
value or key of its arguments (``data_words``). A word only mentioned in longer argument text (a script's comment, a
brief) is no name of the call's, and a field that names tools (``REFERENCE_FIELDS``) gives none: those are renamed
(the C3 failure: a tool named plainly is not callable in code mode).
"""

import re

TOOL_WORD = re.compile(r"(?<!\w)angelo_[a-z0-9_]+")
TEXT_FIELDS = ("error", "recovery", "recovery_requirements", "refusal_details")   # Where refusal prose and hints live.
# Argument fields whose values name tools, never data: a plan or batch operation's ``tool`` (any tool), and the
# capabilities lookup's ``names`` (tools, action kinds, recipes).
REFERENCE_FIELDS = frozenset({(None, "tool"), ("angelo_agent_capabilities", "names")})


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


def named_for(controller, result, arguments=None, tool=None):
    """``result`` with every tool its refusal and recovery text names as ``controller``'s model calls it; never a word
    the call (``tool`` with its ``arguments``) gave as a name (``data_words``)."""
    if not isinstance(result, dict) or not any(key in result for key in TEXT_FIELDS):
        return result
    if callable_name(controller, "angelo_scene_settings") == "angelo_scene_settings":
        return result   # Plain names in this runtime (any deferred tool shows it): nothing to rename.
    from .tool_specs import TOOL_MAP
    data = data_words(arguments, tool)

    def rename(match):
        word = match.group(0)
        return callable_name(controller, word) if word in TOOL_MAP and word not in data else word

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


def data_words(arguments, tool=None, depth=0):
    """The tool-shaped names a call's arguments give: each whole string value or key that is one (a material, an
    object, a file name), never a word inside longer text, nor a value of a field that names tools
    (``REFERENCE_FIELDS``)."""
    found = set()
    if depth > 8:
        return found
    if isinstance(arguments, str):
        if TOOL_WORD.fullmatch(arguments):
            found.add(arguments)
    elif isinstance(arguments, dict):
        for key, value in arguments.items():
            found |= data_words(key, tool, depth + 1)
            if (None, key) not in REFERENCE_FIELDS and not (depth == 0 and (tool, key) in REFERENCE_FIELDS):
                found |= data_words(value, tool, depth + 1)
    elif isinstance(arguments, (list, tuple)):
        for value in arguments:
            found |= data_words(value, tool, depth + 1)
    return found
