# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tool layout of a model request: where its tools ride and in what form, never which tools.

Test connection (``endpoint_check_tools``) imitates the requests the runtimes send,
so a server is judged on the shape a chat will use. The layout is what that
imitation must keep in step with: the item that carries the tools (top-level
``tools``, or an input item's type and role), namespaces, the kinds of tool,
a custom tool's grammar format, ``tool_choice`` and ``parallel_tool_calls``,
and the keys of one tool entry. ``capture_endpoint_tool_layouts.py`` records the
runtimes' own layouts into ``fixtures/endpoint_tool_layouts.json``;
``test_native_endpoint_tool_layouts.py`` holds the check's layouts to them.
"""


def _entries(tools):
    """The tool entries inside ``tools``, namespaces opened; and the namespaces' names."""
    entries, namespaces = [], []
    for tool in tools or ():
        if isinstance(tool, dict) and tool.get("type") == "namespace":
            namespaces.append(tool.get("name"))
            entries += [t for t in tool.get("tools") or () if isinstance(t, dict)]
        elif isinstance(tool, dict):
            entries.append(tool)
    return entries, namespaces


def responses(body):
    carriers, entries, namespaces = [], [], []
    if body.get("tools"):
        carriers.append("tools")
        found, names = _entries(body["tools"])
        entries += found
        namespaces += names
    for item in body.get("input") or ():
        if isinstance(item, dict) and item.get("tools"):
            carriers.append("input:%s:%s" % (item.get("type"), item.get("role")))
            found, names = _entries(item["tools"])
            entries += found
            namespaces += names
    custom = next((e for e in entries if e.get("type") == "custom"), None)
    function = next((e for e in entries if e.get("type") == "function"), None)
    return {"carriers": sorted(set(carriers)), "namespaces": sorted(set(namespaces)),
            "kinds": sorted({e.get("type") for e in entries}),
            "custom_format": custom and {k: (custom.get("format") or {}).get(k) for k in ("type", "syntax")},
            "function_keys": function and sorted(function),
            "tool_choice": body.get("tool_choice"), "parallel_tool_calls": body.get("parallel_tool_calls")}


def gemini(body):
    tools = [t for t in body.get("tools") or () if isinstance(t, dict)]
    declarations = [d for t in tools for d in t.get("functionDeclarations") or () if isinstance(d, dict)]
    return {"tool_keys": sorted({k for t in tools for k in t}),
            "declaration_keys": sorted(declarations[0]) if declarations else None,
            "tool_config": sorted(body.get("toolConfig") or {})}


def messages(body, headers=None):
    """A loaded tool's keys (``defer_loading`` false or absent: one the model can call at once), the values of
    ``defer_loading`` the tools carry, and the ``anthropic-beta`` tokens sent with them."""
    tools = [t for t in body.get("tools") or () if isinstance(t, dict) and "input_schema" in t]
    loaded = [t for t in tools if not t.get("defer_loading")]
    betas = (headers or {}).get("anthropic-beta")
    return {"tool_keys": sorted(loaded[0]) if loaded else None, "tool_choice": body.get("tool_choice"),
            "defer_loading": sorted({t["defer_loading"] for t in tools if "defer_loading" in t}),
            "anthropic_beta": sorted(b.strip() for b in betas.split(",")) if betas else []}


LAYOUTS = {"openai-responses": responses, "gemini": gemini, "anthropic-messages": messages}


def layout(format_id, body, headers=None):
    return LAYOUTS[format_id](body, headers) if format_id == "anthropic-messages" else LAYOUTS[format_id](body)


def differences(check, runtime, format_id):
    """How ``check``'s layout departs from ``runtime``'s, or [] when it is the runtime's own form. The check sends
    one tool where the runtime sends many: its kinds must be among the runtime's; a tool entry's keys are the
    runtime's exactly (a proxy that refuses one refuses real chats too)."""
    problems = []
    for key, value in check.items():
        expected = runtime.get(key)
        if key == "kinds":
            if not set(value) <= set(expected or ()):
                problems.append("tool kinds %s, the runtime sends %s" % (value, expected))
        elif key.endswith("_keys"):   # None: the check sends no such entry.
            if value is not None and sorted(value) != sorted(expected or ()):
                problems.append("%s %s, the runtime sends %s" % (key, value, expected))
        elif key == "defer_loading":   # Values the check uses must be the runtime's; it never defers its one tool.
            if not set(value) <= set(expected or ()):
                problems.append("defer_loading %s, the runtime sends %s" % (value, expected))
        elif key == "anthropic_beta":   # Recorded only: Claude Code is not pinned, its betas move with its version.
            continue
        elif key == "custom_format" and value is None:
            continue
        elif key == "tool_config" and not value:
            continue
        elif value != expected:
            problems.append("%s %r, the runtime sends %r" % (key, value, expected))
    return problems
