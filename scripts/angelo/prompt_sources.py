# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every route's Angelo-authored text and tools, assembled from a source tree (``prompt_audit.py sources``).

The recordings show what a model received on the builds that were run; this shows what the tree in hand would send,
so a per-route gap fails before any trial runs (the class that left the Blender 5.2 facts on Codex only). Each
variant is built by the code the route launches with (``claude_session``, ``codex_instructions``, ``app_tools``,
the Studio editor's context) and named by the registry route a recording of it is classified as. Runtime-added text
is not here: only a recording shows it.

Imports the add-on's pure modules from ``tree`` (default: this checkout); no ``bpy``, launches nothing.
"""

import ast
import importlib.util
import itertools
import json
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def claude_prompt(initialize):
    """Angelo's text from a Claude Code initialize request: ``systemPrompt`` (the whole prompt) or the appended one."""
    request = initialize["request"]
    if "systemPrompt" in request:
        system = request["systemPrompt"]
        return "\n".join(system) if isinstance(system, list) else system
    return request["appendSystemPrompt"]


def _claude(context):
    """``(text, tools)`` of a Claude launch: the plan schema (StructuredOutput) or the direct tool list."""
    from angelo_agent import claude_direct
    from angelo_agent.claude_transport import claude_session
    _command, initialize = claude_session("claude", context)
    request = initialize["request"]
    if claude_direct.enabled(context):
        from angelo_agent import claude_direct_schema
        tools = [dict(tool, name="mcp__angelo__" + tool["name"]) for tool in claude_direct_schema.tool_list()]
    else:
        tools = [{"name": "StructuredOutput", "input_schema": request.get("jsonSchema")}]
    return claude_prompt(initialize), tools


def _collaborator_instructions(tree):
    """The text a delegated collaborator's context appends (``delegation``), read without starting one."""
    source = (Path(tree) / "scripts/addons_core/angelo_agent/delegation.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(getattr(t, "attr", None) == "instructions" for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError("delegation.py no longer assigns its instructions: update prompt_sources")


def assemble(tree=ROOT):
    """``[(route, variant, text, tools)]`` for every route the tree can launch."""
    sys.path[:0] = [str(Path(tree) / "scripts/addons_core")]
    from angelo_agent import external_mcp_tools, providers, studio_enhance, tool_specs
    from angelo_agent.external_clients import LEVELS
    out = []

    def claude_context(**fields):
        return SimpleNamespace(turn_options=None, state={"provider": "claude_subscription", "options": {}}, **fields)

    for direct, web in itertools.product((False, True), (True, False)):
        context = claude_context(native_tools=("WebSearch", "WebFetch") if web else (), claude_direct_tools=direct)
        out.append(("claude-direct" if direct else "claude-plan", "web" if web else "no web", *_claude(context)))
    collaborator = _collaborator_instructions(tree)
    context = claude_context(native_tools=("WebSearch", "WebFetch"), claude_direct_tools=False,
                             allowed_tools=tool_specs.COLLABORATOR_TOOLS, instructions=collaborator)
    out.append(("collaborator-claude", "plan", *_claude(context)))
    out.append(("collaborator-codex", "full plan",
                providers.codex_instructions(short_identifiers=True, deferred=False, plan=True) + collaborator,
                tool_specs.dynamic_tools(tool_specs.COLLABORATOR_TOOLS)))
    for short, deferred, plan, team in itertools.product((False, True), repeat=4):
        text = providers.codex_instructions(short_identifiers=short, deferred=deferred, plan=plan, team=team)
        route = "codex-%s-%s%s" % ("deferred" if deferred else "full", "plan" if plan else "chat", "" if short else "-long")
        out.append((route, "team" if team else "personal", text, tool_specs.dynamic_tools()))
        if not deferred and not plan:   # a third-party provider without web search appends its notice
            out.append((route, "no web", text + providers.NO_WEB_SEARCH, tool_specs.dynamic_tools()))
    out.append(("voice", "voice", providers.INSTRUCTIONS, tool_specs.dynamic_tools()))
    # Every level set an app can be granted: a read-only app reads less (no task fact, fewer tools).
    for size in range(1, len(LEVELS) + 1):
        for levels in itertools.combinations(LEVELS, size):
            answer = external_mcp_tools.app_tools(frozenset(levels))
            for client in ("mcp-claude-code", "mcp-codex"):
                out.append((client, ",".join(levels), answer["instructions"], answer["tools"]))
    # Studio's editor, and Generation Flows' parts check where the tree has it (same context, its own text).
    parts = None
    if importlib.util.find_spec("angelo_agent.parts_check") is not None:
        from angelo_agent import parts_check
        parts = parts_check.INSTRUCTIONS
    with tempfile.TemporaryDirectory() as folder:
        for kind, text in (("studio", None), ("parts-check", parts)):
            if kind == "parts-check" and text is None:
                continue
            for runtime, mode in (("claude", "claude_api"), ("codex", "grok_api"), ("codex", "codex_api")):
                extra = (text,) if text is not None else ()
                context = studio_enhance.EnhancementContext(folder, {"mode": mode}, {}, "probe", "probe", [],
                                                            threading.Event(), *extra)
                route = "%s-%s" % (kind, runtime)
                if runtime == "claude":
                    out.append((route, mode, *_claude(context)))
                else:
                    out.append((route, mode, context.base_instructions + getattr(context, "instructions", ""), []))
    return out


def recording(text, tools):
    """The shape ``prompt_audit.received`` reads, for assembled text."""
    return {"angelo_text": text, "angelo_tools": json.loads(json.dumps(tools))}
