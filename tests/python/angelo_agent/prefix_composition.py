# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bytes of the fixed opening every new chat sends, by section and by tool; pure Python, unbilled.

The parts Angelo owns, for both runtimes as they launch a chat that is not
signed in to a team (the gates and benchmarks):

* a Codex chat thread on OpenAI with typed plans: its base instructions
  (``providers.codex_instructions``, ending with the program reference), its
  dynamic tools as JSON (Codex renders them as TypeScript in code mode's
  ``exec``; ``codex_prompt_probe.py`` measures that rendering) and its strict
  output schema (``codex_plan.output_schema``);
* a Claude Code session with typed plans: the appended instructions and the
  plan schema (``providers.claude_session``), which Claude Code sends as its
  StructuredOutput tool (``claude_prompt_probe.py`` measures the request).

A schema's per-tool and per-kind figures are marginal: the schema's size with
everything minus its size without that tool or kind, shared ``$defs``
included, so they do not add up to the total exactly. Usage::

    python3 prefix_composition.py [--json]
"""

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_plan, plan_schema, providers  # noqa: E402
from angelo_agent.codex_reference import program_reference  # noqa: E402
from angelo_agent.team_library_spec import TEAM_NAMES  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, TOOLS, codex_tools, native_plan_schema  # noqa: E402

ALLOWED = frozenset(TOOL_MAP) - TEAM_NAMES  # A person not signed in to a team, as the gates run.


def size(value):
    if isinstance(value, str):
        return len(value.encode())
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


@contextmanager
def _without_core(kind):
    """The plan schemas with one core action kind deferred instead of exact."""
    codex_core = tuple(k for k in plan_schema.PLAN_CORE_ACTIONS if k != kind)
    document = plan_schema.plan_document
    with patch.object(plan_schema, "CLAUDE_CORE_ACTIONS", tuple(k for k in plan_schema.CLAUDE_CORE_ACTIONS if k != kind)), \
            patch.object(codex_plan, "plan_document", lambda tools, allowed=None: document(tools, allowed, codex_core)):
        codex_plan._documents.cache_clear()
        try:
            yield
        finally:
            codex_plan._documents.cache_clear()


def codex_sections():
    reference = program_reference(short_identifiers=True, plan=True)
    text = providers.codex_instructions(short_identifiers=True, deferred=True, plan=True, team=False)
    parts = {"claude_instructions": size(providers.CLAUDE_INSTRUCTIONS),
             "codex_tools_guidance": size(providers.codex_family_list(providers.CODEX_TOOLS_GUIDANCE, False)),
             "codex_context_guidance": size(providers.CODEX_CONTEXT_GUIDANCE),
             "plan_guidance": size(codex_plan.PLAN_GUIDANCE), "program_reference": size(reference)}
    lines = {}
    for line in reference.split("\n"):
        key = line.split("=", 1)[0][:40] if line.startswith("type ") else line[:40]
        lines[key] = size(line) + 1
    return {"total": size(text), "parts": parts, "reference_lines": dict(sorted(lines.items(), key=lambda kv: -kv[1]))}


def codex_tool_sizes():
    tools = codex_tools(ALLOWED, short_identifiers=True, defer=True)
    rows = {tool.get("name"): size(tool) for tool in tools}
    return {"total": size(tools), "count": len(tools), "tools": dict(sorted(rows.items(), key=lambda kv: -kv[1]))}


def _marginal(build, names):
    total = size(build(ALLOWED))
    return total, {name: total - size(build(ALLOWED - {name})) for name in names}


def schema_sizes():
    plan_tools = sorted(codex_plan.plan_tools(ALLOWED))
    codex_total, codex_tools_marginal = _marginal(codex_plan.output_schema, plan_tools)
    claude_names = sorted(tool["name"] for tool in TOOLS if tool["name"] in ALLOWED)
    claude_total, claude_tools_marginal = _marginal(native_plan_schema, claude_names)
    kinds = {}
    for kind in plan_schema.CLAUDE_CORE_ACTIONS:
        with _without_core(kind):
            kinds[kind] = {"codex_output_schema": codex_total - size(codex_plan.output_schema(ALLOWED)),
                           "claude_plan_schema": claude_total - size(native_plan_schema(ALLOWED))}
    order = lambda rows: dict(sorted(rows.items(), key=lambda kv: -kv[1]))  # noqa: E731
    return {"codex_output_schema": {"total": codex_total, "tools": order(codex_tools_marginal)},
            "claude_plan_schema": {"total": claude_total, "tools": order(claude_tools_marginal)},
            "core_kinds": kinds}


def claude_sections():
    text = providers.CLAUDE_INSTRUCTIONS + providers.CLAUDE_PLAN_INSTRUCTIONS
    return {"total": size(text), "parts": {"claude_instructions": size(providers.CLAUDE_INSTRUCTIONS),
                                           "claude_plan_instructions": size(providers.CLAUDE_PLAN_INSTRUCTIONS)}}


def report():
    schemas = schema_sizes()
    return {"codex": {"instructions": codex_sections(), "dynamic_tools_json": codex_tool_sizes(),
                      "output_schema": schemas["codex_output_schema"]},
            "claude": {"appended_instructions": claude_sections(), "plan_schema": schemas["claude_plan_schema"]},
            "core_kinds_marginal": schemas["core_kinds"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    value = report()
    if args.json:
        print(json.dumps(value, indent=1))
        return
    for runtime, sections in value.items():
        print("==", runtime)
        for name, section in sections.items():
            print("  %s: %s" % (name, json.dumps(section)[:2000]))


if __name__ == "__main__":
    main()
