# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Print the always-sent schema sizes that native_coverage.rst records.

Host-only, offline and unbilled: it builds Claude's typed-plan schema and
Codex's dynamic tools exactly as a request does and prints their compact JSON
sizes, with token estimates at the 1.93 bytes per schema token measured in
performance.rst ("Token diet"). With ``--fit RUN_DIR`` it also prints tokens
from ``token_accounting.py``'s tokenizer approximation fitted to that recorded
Claude run (per-class rates for hex, letters and other bytes). Usage, from the
repository root::

    python3 tests/python/angelo_agent/coverage_prefix.py [--fit RUN_DIR]
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import plan_schema  # noqa: E402
from angelo_agent.action_schemas import OPERATION_SCHEMAS  # noqa: E402
from angelo_agent.providers import CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS, INSTRUCTIONS  # noqa: E402
from angelo_agent.tool_specs import dynamic_tools, native_plan_schema  # noqa: E402

BYTES_PER_SCHEMA_TOKEN = 1.93


def encoded(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def measure():
    plan, codex = encoded(native_plan_schema()), encoded(dynamic_tools())
    return {"claude_plan_schema_bytes": plan, "claude_plan_schema_tokens": round(plan / BYTES_PER_SCHEMA_TOKEN),
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
    args = parser.parse_args()
    report = measure()
    if args.fit:
        report["fitted_tokens"] = fitted_tokens(args.fit)
        report["fitted_on"] = args.fit
    print(json.dumps(report, indent=1))
