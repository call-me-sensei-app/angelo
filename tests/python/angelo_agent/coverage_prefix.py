# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Print the always-sent schema sizes that native_coverage.rst records.

Host-only, offline and unbilled: it builds Claude's typed-plan schema and
Codex's dynamic tools exactly as a request does and prints their compact JSON
sizes, with token estimates at the 1.93 bytes per schema token measured in
performance.rst ("Token diet"). With ``--fit RUN_DIR`` it also prints tokens
from ``token_accounting.py``'s tokenizer approximation fitted to that recorded
Claude run (per-class rates for hex, letters and other bytes). With ``--parallel-on`` it measures
the request parallel edit ON as shipped gives a lead chat: a capable machine's own check (two-digit worker count),
nothing forced, set before the tool list is built (``tool_specs`` builds it once per launch). Usage, from the
repository root::

    python3 tests/python/angelo_agent/coverage_prefix.py [--fit RUN_DIR] [--parallel-on]
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
if "--parallel-on" in sys.argv:   # As shipped on a capable machine (64 GB, 16 cores: 12 workers), nothing forced.
    from angelo_agent import parallel_capability
    parallel_capability._measured[:] = [parallel_capability.assess(True, "measured", 64.0, 16)]
from angelo_agent import plan_schema  # noqa: E402
from angelo_agent.action_schemas import OPERATION_SCHEMAS  # noqa: E402
from angelo_agent.providers import CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS, INSTRUCTIONS  # noqa: E402
from angelo_agent.tool_specs import codex_tools, dynamic_tools, native_plan_schema  # noqa: E402

BYTES_PER_SCHEMA_TOKEN = 1.93


def encoded(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def measure():
    try:
        from angelo_agent import parallel_access
        from angelo_agent.worker_spec import WORKER_ANNOUNCEMENT
        on = parallel_access.enabled()
    except ImportError:   # A build before parallel edit.
        on, WORKER_ANNOUNCEMENT = False, ""
    plan, codex = encoded(native_plan_schema()), encoded(dynamic_tools())
    code_mode = encoded(codex_tools(short_identifiers=True, defer=True))
    return {"parallel_on": on, "claude_plan_schema_bytes": plan,
            "claude_plan_schema_tokens": round(plan / BYTES_PER_SCHEMA_TOKEN),
            "codex_code_mode_tools_bytes": code_mode,
            "codex_code_mode_tools_tokens": round(code_mode / BYTES_PER_SCHEMA_TOKEN),
            # Appended to a lead chat's instructions every turn while ON (agent_guidance.lead_guidance).
            "lead_announcement_bytes": len(WORKER_ANNOUNCEMENT.encode()) if on else 0,
            "claude_instructions_bytes": len((CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS).encode()),
            "codex_dynamic_tools_bytes": codex, "codex_dynamic_tools_tokens": round(codex / BYTES_PER_SCHEMA_TOKEN),
            "codex_instructions_bytes": len(INSTRUCTIONS.encode()),
            "action_kinds": len(OPERATION_SCHEMAS), "core_kinds": len(plan_schema.PLAN_CORE_ACTIONS),
            "deferred_kinds": len(plan_schema.DEFERRED_ACTIONS), "deferred_tools": sorted(plan_schema.DEFERRED_TOOLS),
            "deferred_checks": list(plan_schema.DEFERRED_CHECKS)}


def fitted_tokens(run_dir):
    """Prefix parts in tokens by the accounting tokenizer fitted to a recorded run."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import token_accounting
    tokenizer = token_accounting.Tokenizer(*token_accounting.fit_samples(run_dir))
    texts = {"claude_plan_schema": json.dumps(native_plan_schema(), separators=(",", ":")),
             "claude_instructions": CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS,
             "codex_dynamic_tools": json.dumps(dynamic_tools(), separators=(",", ":")),
             "codex_instructions": INSTRUCTIONS}
    return {name: round(tokenizer.tokens(text)) for name, text in texts.items()}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit", help="recorded Claude run directory for token_accounting's tokenizer fit")
    parser.add_argument("--parallel-on", action="store_true", help="parallel edit ON as shipped (read at import)")
    args = parser.parse_args()
    report = measure()
    if args.fit:
        report["fitted_tokens"] = fitted_tokens(args.fit)
        report["fitted_on"] = args.fit
    print(json.dumps(report, indent=1))
