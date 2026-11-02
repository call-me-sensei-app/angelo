# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What every kind of chat Angelo builds is told and offered, for ``test_native_tool_mentions``. No Blender.

Run in a fresh interpreter, since the tool list is built when the add-on is imported (the parallel family's presence
and a worker Blender's own set depend on the environment it starts in). Prints one JSON object: for each kind, the
model-facing text sent up front (the base instructions with the chat's own text and its parallel-edit guidance, and
every declared tool's description) and the names of the tools it declares, deferred ones included. Every piece is
built by the code a request uses: ``claude_transport.claude_session`` (a Claude typed plan, and the direct-tools
route), ``CodexProvider._thread_parameters`` with ``tool_specs.codex_tools`` (OpenAI and third-party Codex threads) or
``dynamic_tools`` (voice), and ``external_mcp_tools.app_tools`` (a connected app's levels), over the allowed sets
``routing.RuntimeContext.begin`` and ``delegation`` give a lead, a signed-in team member, an app's task, a
collaborator and a worker's own chat (in the lead's Angelo, and in a worker Blender that hosts its chats).
"""

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import (claude_transport, delegation, external_clients, external_mcp_tools,  # noqa: E402
                          parallel_access, team_library_access)
from angelo_agent.claude_direct_schema import tool_list  # noqa: E402
from angelo_agent.providers import CodexProvider  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, codex_tools, dynamic_tools, native_plan_schema  # noqa: E402

TEXT_RUNTIMES = ("claude_plan", "claude_direct", "codex_openai", "codex_third_party")
CODEX_MODES = {"codex_openai": "codex_subscription", "codex_third_party": "kimi_api", "voice": "codex_subscription"}


def _names(value, known):
    """Every JSON string in ``value`` that is exactly a tool name (a schema's tool constants and enums): a name only
    mentioned inside a longer string (a description) is not one."""
    if isinstance(value, str):
        return {value} & known
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return set().union(set(), *(_names(item, known) for item in items))


def _descriptions(tools):
    return [text for tool in tools for text in [tool.get("description", "")] + _descriptions(tool.get("tools", ()))]


def _flat(tools):
    return [entry for tool in tools for entry in (tool["tools"] if tool.get("type") == "namespace" else [tool])]


def _collaborator_text():
    """The collaborator's own instructions, read from ``delegation`` as it sets them (one string literal)."""
    source = Path(delegation.__file__).read_text(encoding="utf-8")
    start = source.index("self.instructions = (") + len("self.instructions = ")
    return eval(source[start:source.index("\n        self.text = {}", start)].strip(), {})   # noqa: S307


def roles(chat):
    """``{role: (patch, runtimes, allowed, the chat's own text)}`` as a request computes them in this environment."""
    def allowed():
        return parallel_access.for_chat(team_library_access.allowed_tools(TOOL_MAP), TOOL_MAP, chat)
    if parallel_access.in_worker():   # Every chat of a worker Blender that hosts its chats is a worker's own.
        return {"worker_hosted": (contextlib.nullcontext, TEXT_RUNTIMES, allowed(), "")}
    signed_in = lambda: patch.object(team_library_access, "enabled", lambda current=None: True)  # noqa: E731
    with signed_in():
        team = allowed()
    lead = allowed()
    found = {"lead": (contextlib.nullcontext, TEXT_RUNTIMES + ("voice",), lead, ""),
             "team": (signed_in, TEXT_RUNTIMES + ("voice",), team, ""),
             "app_task": (contextlib.nullcontext, TEXT_RUNTIMES,
                          frozenset(lead or TOOL_MAP) - external_clients.NEVER_FOR_APPS, ""),
             "collaborator": (contextlib.nullcontext, TEXT_RUNTIMES, delegation.COLLABORATOR_TOOLS,
                              _collaborator_text())}
    if parallel_access.enabled():   # A worker's chat in the lead's Angelo (the lanes path).
        lanes = lambda: patch.object(parallel_access, "worker_chat", lambda controller: True)  # noqa: E731
        with lanes():
            found["worker_lanes"] = (lanes, TEXT_RUNTIMES, allowed(), "")
    return found


def _context(chat, allowed, own, mode="claude_api", **fields):
    return SimpleNamespace(host=chat, state={"id": "chat", "provider": mode, "options": {}},
                           checkpoints=chat.checkpoints, turn_options=None, allowed_tools=allowed, instructions=own,
                           models=[], settings={}, voice=None, short_identifiers=True, workspace="workspace", **fields)


def tail(runtime, allowed):
    """The text after what every chat of ``runtime`` shares (tool_guidance): the blocks naming tools a worker lacks."""
    from angelo_agent.claude_text import claude_tail
    from angelo_agent.codex_text import codex_tail
    if runtime.startswith("claude_"):
        return claude_tail(allowed)
    return codex_tail(allowed, deferred=runtime == "codex_openai", team=allowed is None)


def _kind(chat, runtime, allowed, own):
    """``(instructions, tool descriptions, declared names)`` of one chat on one runtime."""
    if runtime.startswith("claude_"):
        direct = runtime == "claude_direct"
        _command, initialize = claude_transport.claude_session("claude", _context(chat, allowed, own,
                                                                                   claude_direct_tools=direct))
        text = "\n".join(initialize["request"]["systemPrompt"])   # The prompt as sent, never JSON-escaped.
        if not direct:
            return text, "", _names(native_plan_schema(allowed), set(TOOL_MAP))
        tools = tool_list(allowed)
        return text, "\n".join(_descriptions(tools)), {tool["name"] for tool in tools}
    voice, mode = runtime == "voice", CODEX_MODES[runtime]
    controller = _context(chat, allowed, own, mode=mode)
    controller.voice = object() if voice else None
    parameters, _model = CodexProvider(controller, {"model": "m"})._thread_parameters(
        [{"id": "m", "default": True}], voice=voice)
    tools = dynamic_tools(allowed) if voice else codex_tools(allowed, short_identifiers=True,
                                                             defer=runtime == "codex_openai")
    return parameters["baseInstructions"], "\n".join(_descriptions(tools)), {tool["name"] for tool in _flat(tools)}


def kinds():
    chat = SimpleNamespace(state={"id": "chat"}, checkpoints=SimpleNamespace(session_id="chat"))
    result = {}
    for role, (scope, runtimes, allowed, own) in roles(chat).items():
        for runtime in runtimes:
            with scope():
                instructions, descriptions, declared = _kind(chat, runtime, allowed, own)
            result["%s/%s" % (role, runtime)] = {"instructions": instructions, "text": instructions + "\n" + descriptions,
                                                 "tail": tail(runtime, allowed), "declared": sorted(declared)}
    if not parallel_access.in_worker():
        read, edit = external_clients.READ, external_clients.EDIT
        for levels in (external_clients.LEVELS, (read,), (read, edit)):
            catalog = external_mcp_tools.app_tools(levels)
            result["mcp_app/" + "+".join(levels)] = {
                "instructions": catalog["instructions"],
                "text": catalog["instructions"] + "\n" + "\n".join(_descriptions(catalog["tools"])),
                "declared": sorted(tool["name"] for tool in catalog["tools"])}
    return result


if __name__ == "__main__":
    print(json.dumps({"kinds": kinds(), "catalog": sorted(TOOL_MAP)}))
