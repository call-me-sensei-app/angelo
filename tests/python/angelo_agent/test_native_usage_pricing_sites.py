# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No site sums model usage and then prices the sum as if it were one request (request_tiers.py).

A price step (GPT above 272K input tokens, Haiku 5.5 above 100K, ...) applies per model request, so a sum of
requests priced at one tier is wrong. Two source checks keep every site honest, product and harness alike:

* every call into the rate table (``model_profiles.rates``) is one of the reviewed pricing sites below, each of
  which prices one request, one tier of a split (``request_tiers``) or a rate table, never a sum of requests;
* every function that builds a summed usage (adds token fields across requests or turns) is reviewed: either it
  carries the per-request split (its source names ``above_step`` or ``request_tiers``) or a list of each
  request's usage, or its sums are never priced (counts, shares, display), with the reason.

A new site fails here until it is classified. Static checks only: no process, account or model.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
HARNESS = ROOT / "tests/python/angelo_agent"
TOKENS = {"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens"}
COLLECTIONS = {"TOKENS", "USAGE_KEYS", "USAGE_SUMS", "TOKEN_FIELDS", "USAGE_FIELDS", "USAGE_TOKENS", "TOKEN_KEYS",
               "FIELDS"}

# file:function -> what it prices. Each prices one request, one tier of a split, or a rate table.
PRICING_SITES = {
    "model_profiles.py:capability_profile": "the routing profile's rate table, no usage",
    "request_tiers.py:priced": "each tier of a request split at that tier's rates",
    "usage_meter.py:entry": "a request split by request_tiers, or one request at its own prompt; a sum of several "
                            "without their split at the base rates, a lower bound flagged as not fully reported",
    "list_prices.py:_rates": "one request's (or one tier's) rates",
    "list_prices.py:list_price_basis": "compares two TTLs' write rates; prices nothing",
    "long_session_cost_model.py:_rates": "one projected call's rates at its own prompt",
    "final_runs_plan.py:_rates": "one planned prompt's request at its own per-request prompt",
}
# file:function -> how its sum keeps each request's tier, or why it is never priced.
SUMMING_SITES = {
    "claude_stream.py:observed_usage": "fold",
    "provider_usage.py:_fold": "fold",
    "provider_usage.py:running_usage": "fold",
    "request_tiers.py:added": "fold",
    "request_tiers.py:fold": "fold",
    "request_tiers.py:from_messages": "fold",
    "request_tiers.py:summed": "fold",
    "telemetry.py:_add_usage": "fold",
    "provider_usage.py:claude_total": "count: one Claude Code process total, differenced per round; its requests come "
                                      "from the round's messages (claude_stream.observed_usage)",
    "list_prices.py:usage_from_claude": "per request: priced with claude_requests' list (bench_mcp_scenario, mcp_path)",
    "list_prices.py:claude_requests": "per request: each response's own usage",
    "usage_meter.py:add_to": "count: adds entries already priced one request each (entry); tokens are shown, not priced",
    "usage_view.py:_sum": "count: adds priced buckets for display",
    "telemetry.py:_usage_report": "count: percentiles and cache share for the metrics report, never priced",
    "telemetry.py:_consultation_report": "count: consultation totals for the metrics report, never priced",
    "bench_parallel_comms.py:_while_waiting": "count: input tokens while a lead waits, never priced",
    "bench_parallel_comms.py:_worker": "count: a worker's input and cached tokens, never priced",
    "material_display.py:combine": "not usage: shader fields",
}


def _files():
    return sorted(ADDON.glob("*.py")) + sorted(p for p in HARNESS.glob("*.py") if not p.name.startswith("test_"))


def _functions():
    for path in _files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path, node


def _collection(node):
    return any((isinstance(n, ast.Name) and n.id in COLLECTIONS) or (isinstance(n, ast.Attribute) and n.attr in COLLECTIONS)
               for n in ast.walk(node))


def _adds(value):
    return any((isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add))
               or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sum") for n in ast.walk(value))


def _sums_usage(function):
    """Whether ``function`` adds a usage token field: into a field named for one, into a key looped over (or filtered
    by) a token list, or into a dict literal or comprehension of token fields."""
    keys = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.For, ast.comprehension)):
            names = ([node.target.id] if isinstance(node.target, ast.Name) else
                     [e.id for e in node.target.elts if isinstance(e, ast.Name)] if isinstance(node.target, ast.Tuple) else [])
            body = node.body if isinstance(node, ast.For) else node.ifs
            filtered = any(isinstance(m, ast.Compare) and any(isinstance(o, ast.In) for o in m.ops) and _collection(m)
                           for part in body for m in ast.walk(part))
            if names and (_collection(node.iter) or filtered):
                keys.add(names[0])
    for node in ast.walk(function):
        target = None
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and _adds(node.value):
            target = node.targets[0]
        if isinstance(target, ast.Subscript):
            key = target.slice
            if (isinstance(key, ast.Constant) and key.value in TOKENS) or (isinstance(key, ast.Name) and key.id in keys):
                return True
        if isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value in TOKENS and v is not None
                                              and _adds(v) for k, v in zip(node.keys, node.values)):
            return True
        if isinstance(node, ast.DictComp) and _collection(node) and _adds(node.value):
            return True
    return False


def _calls_rates(function):
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "rates":
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == "model_profiles":
                return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "rates":
            return True
    return False


class PricingSiteTests(unittest.TestCase):
    def test_every_rate_table_call_is_a_reviewed_pricing_site(self):
        found = {"%s:%s" % (path.name, function.name) for path, function in _functions()
                 if path.name != "chat_waiting_measure.py" and _calls_rates(function)}   # Its rates() is not prices.
        self.assertEqual(found - set(PRICING_SITES), set(), "a new pricing site: price each request at its own tier "
                         "(request_tiers.priced or list_prices.list_cost), then add it to PRICING_SITES")
        self.assertEqual(set(PRICING_SITES) - found, set(), "a reviewed site no longer prices: drop it here")

    def test_every_usage_sum_keeps_each_requests_tier_or_is_never_priced(self):
        sources = {}
        found = set()
        for path, function in _functions():
            if _sums_usage(function):
                name = "%s:%s" % (path.name, function.name)
                found.add(name)
                sources[name] = ast.get_source_segment(path.read_text(encoding="utf-8"), function) or ""
        self.assertEqual(found - set(SUMMING_SITES), set(), "a new usage sum: carry above_step (request_tiers.added) "
                         "or each request's usage, or say why it is never priced, in SUMMING_SITES")
        self.assertEqual(set(SUMMING_SITES) - found, set(), "a reviewed sum no longer sums usage: drop it here")
        for name, how in SUMMING_SITES.items():
            if how == "fold":
                with self.subTest(site=name):
                    self.assertTrue("above_step" in sources[name] or "request_tiers" in sources[name]
                                    or name.startswith("request_tiers.py:"), "%s sums usage without the split" % name)


if __name__ == "__main__":
    unittest.main()
