# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test connection's tool requests: each model's own tool shape, the call it answers with, and its result sent back.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints" (Test connection).
A model mapped to an Angelo model calls tools the way that model does in Codex
(``endpoint_catalog``): a code-mode model (``tool_mode`` ``code_mode*``) reaches
every Angelo tool only through one freeform tool, ``exec`` (a ``custom`` tool
with a grammar, whose calls and results are ``custom_tool_call`` and
``custom_tool_call_output`` items), and a Responses Lite model
(``use_responses_lite``) receives its tools in an ``additional_tools`` input item
(role ``developer``, the tools in a ``functions`` namespace) rather than the
request's ``tools``, exactly as Codex sends them. A server that drops or rewrites either leaves such
a model no way to call Angelo, so the check sends each Responses model's own
shape; every other model, and every other format, gets one function tool. The
check then sends the call and its result back, as the runtime does after a tool
runs: a server that refuses the result can't finish any turn that calls a tool.

Each request keeps the layout the runtime itself sends (where the tools ride,
namespaces, tool kinds, ``tool_choice``, ``parallel_tool_calls``, a Gemini
declaration's ``parametersJsonSchema``): ``test_native_endpoint_tool_layouts.py``
holds them to the layouts recorded from the pinned runtime.

Standard library only; no bpy.
"""

import json

TOOL = "angelo_check"
FUNCTION, CUSTOM = "function", "custom"
PLAIN = (FUNCTION, False)          # A function tool in the request's own ``tools``.
# What the model reads: English, never translated.
PROBE_TOOL = "Report that the check reached you."  # i18n: model-facing
GRAMMAR = "start: /[\\s\\S]+/"  # i18n: a grammar the model writes against (any text)
RESULT = "ok"  # i18n: the tool result the model reads


def shapes(endpoint, catalog):
    """``{model id: (kind, lite)}`` of ``endpoint``'s Responses models from the runtime's bundled catalog
    (``endpoint_catalog.bundled``): each mapped model's profile entry decides; unmapped ones are ``PLAIN``."""
    found = {}
    for model in endpoint.models_in("openai-responses"):
        entry = catalog.get(model.profile or "") or {}
        kind = CUSTOM if str(entry.get("tool_mode") or "").startswith("code_mode") else FUNCTION
        found[model.id] = (kind, bool(entry.get("use_responses_lite")))
    return found


def _responses_tools(kind):
    if kind == CUSTOM:
        return [{"type": "custom", "name": TOOL, "description": PROBE_TOOL,
                 "format": {"type": "grammar", "syntax": "lark", "definition": GRAMMAR}}]
    return [{"type": "function", "name": TOOL, "description": PROBE_TOOL, "strict": False,   # As Codex sends it.
             "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"], "additionalProperties": False}}]


def responses_body(model, prompt, cache_key, shape=PLAIN, tools=True, turns=()):
    kind, lite = shape
    body = {"model": model, "input": [{"role": "user", "content": prompt}] + list(turns), "stream": True,
            "store": False, "prompt_cache_key": cache_key}
    if tools and lite:   # As Codex sends a Responses Lite request: an additional_tools item before the conversation.
        body["input"].insert(0, {"type": "additional_tools", "role": "developer",
                                 "tools": [{"type": "namespace", "name": "functions", "description": "",
                                            "tools": _responses_tools(kind)}]})
        body["tool_choice"], body["parallel_tool_calls"] = "auto", False
    elif tools:   # As Codex sends a plain request: tools at the top level, parallel calls allowed.
        body["tools"] = _responses_tools(kind)
        body["tool_choice"], body["parallel_tool_calls"] = "auto", True
    return body


def gemini_body(prompt, tools=True, turns=()):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}] + list(turns)}
    if tools:
        body["tools"] = [{"functionDeclarations": [{"name": TOOL, "description": PROBE_TOOL,   # Codex's Gemini wire:
                                                    "parametersJsonSchema": {"type": "object",
                                                                   "properties": {"ok": {"type": "boolean"}},
                                                                   "required": ["ok"]}}]}]
    return body


def messages_body(model, prompt, tools=True, turns=()):
    body = {"model": model, "max_tokens": 512, "stream": True,
            "messages": [{"role": "user", "content": prompt}] + list(turns)}
    if tools:
        body["tools"] = [{"name": TOOL, "description": PROBE_TOOL,
                          "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                           "required": ["ok"]}}]
    return body


def call(events):
    """``(the call, the text written)`` of one streamed reply in any format: the call as the model made it (its
    item, part or block, with a Messages block's streamed input), or None."""
    made, text, pieces = None, [], []
    for _, event in events:
        if not isinstance(event, dict):
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if item.get("type") in ("function_call", "custom_tool_call") and item.get("name") == TOOL:
            made = {"responses": item}
        block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
        if block.get("type") == "tool_use" and block.get("name") == TOOL:
            made = {"messages": block}
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        if delta.get("type") == "input_json_delta" and isinstance(delta.get("partial_json"), str):
            pieces.append(delta["partial_json"])
        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            text.append(delta["text"])
        if isinstance(event.get("delta"), str) and event.get("type", "").endswith("output_text.delta"):
            text.append(event["delta"])
        for candidate in event.get("candidates") or ():
            for part in ((candidate or {}).get("content") or {}).get("parts") or ():
                if isinstance(part, dict) and (part.get("functionCall") or {}).get("name") == TOOL:
                    made = {"gemini": part}
                elif isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought"):
                    text.append(part["text"])
    if made and "messages" in made:
        try:
            made["input"] = json.loads("".join(pieces) or "{}")
        except ValueError:
            made["input"] = {}
    return made, "".join(text)


def answer_turns(made):
    """The turns that follow the user's message to send ``made``'s result back: the call, then its result."""
    if "responses" in made:
        item = made["responses"]
        kept = {k: item[k] for k in ("type", "call_id", "name", "arguments", "input") if k in item}
        output = "custom_tool_call_output" if item["type"] == "custom_tool_call" else "function_call_output"
        return [kept, {"type": output, "call_id": item.get("call_id", ""), "output": RESULT}]
    if "gemini" in made:
        return [{"role": "model", "parts": [made["gemini"]]},
                {"role": "user", "parts": [{"functionResponse": {"name": TOOL, "response": {"result": RESULT}}}]}]
    block = made["messages"]
    return [{"role": "assistant", "content": [{"type": "tool_use", "id": block.get("id", ""), "name": TOOL,
                                               "input": made.get("input", {})}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block.get("id", ""),
                                          "content": RESULT}]}]
