# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Route parity for data (scripts/angelo/route_parity.py, doc/angelo/prompt_audit.rst "Route parity for data").

Each route's real presentation code reads the recorded results in ``route_parity_corpus``: every field reaches
every route, or a registered filter drops it with a reason. The detection tests replay the four cases that made
this check necessary on recorded results: a field the terse receipt's key list dropped (``quoted_names``), a field
inside a light's row (``shines_from``), a new key list nobody registered, and a registered result fact cut by a
size budget.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import route_parity  # noqa: E402

PROGRAM = "angelo_scene_program"


def corpus_item(tool, predicate=lambda result: True):
    return next((name, item_tool, result) for name, item_tool, result in route_parity.load_corpus()
                if item_tool == tool and predicate(result))


def certified_commit(result):
    from angelo_agent.claude_outcomes import terse_receipt
    return result.get("status") == "committed" and terse_receipt(PROGRAM, result) is not None


def problems_for(item, registry=None, change=None):
    name, tool, result = item
    result = copy.deepcopy(result)
    if change:
        change(result)
    return route_parity.check(route_parity.load_registry() if registry is None else registry, [(name, tool, result)])[1]


class CorpusParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.findings, cls.problems = route_parity.check()

    def test_every_field_reaches_every_route_or_a_registered_filter_drops_it(self):
        self.assertEqual(self.problems, [])

    def test_the_corpus_covers_every_route_and_the_receipt_forms(self):
        routes = {(finding[3], finding[7]) for finding in self.findings}
        for route in ("codex", "claude.direct", "mcp.window", "claude.plan.verify", "plan.handoff",
                      "plan.handoff.certified", "codex.plan.correction"):
            self.assertIn(route, {name for name, _form in routes})
        for form in ("terse", "excerpt", "compact", "model_result"):
            self.assertIn(form, {form for _route, form in routes})

    def test_every_open_gap_is_reported(self):
        counts, lines = route_parity.summary(self.findings)
        self.assertGreater(counts.get("review", 0), 0)
        self.assertTrue(any("terse" in line and "delta.geometry[].shines_from " in line for line in lines),
                        "where a light shines from, dropped by the terse receipt")

    def test_the_registry_is_well_formed(self):
        self.assertEqual(route_parity.registry_problems(route_parity.load_registry()), [])

    def test_the_corpus_holds_no_path_or_personal_name(self):
        import route_parity_corpus
        for path in route_parity.CORPUS.glob("*.json"):
            for text in route_parity_corpus.strings(json.loads(path.read_text())):
                self.assertIsNone(route_parity_corpus.PATHS.search(text) or route_parity_corpus.PERSONAL.search(text),
                                  "%s: %r" % (path.name, text[:80]))

    def test_a_recorded_list_keeps_every_field_its_rows_hold(self):
        # A fourth check row held the only angle_error of a fresh result: cut to three rows, the corpus lost a field
        # the native suite saw, so no registry entry could describe it on both suites.
        import route_parity_corpus
        rows = [{"kind": "a", "x": 1, "y": 1}, {"kind": "b", "z": 1}, {"kind": "c", "w": 1},
                {"kind": "d", "observed": {"angle_error": .1}}, {"kind": "e"}]
        kept = route_parity_corpus.shrink(rows)
        self.assertEqual([row["kind"] for row in kept], ["a", "b", "c", "d"])
        self.assertEqual(len(route_parity_corpus.shrink([{"kind": str(index)} for index in range(6)])), 3)


class Detects(unittest.TestCase):
    """Each class this check exists for fails it, on a recorded result."""

    def test_a_new_result_field_the_terse_receipt_drops_fails(self):
        # quoted_names, 2026-10-20: added to results, read by Codex, dropped by the terse receipt's key list.
        problems = problems_for(corpus_item(PROGRAM, certified_commit),
                                change=lambda result: result.__setitem__("parity_new_fact", {"names": ["Bridge"]}))
        self.assertTrue(any("plan.handoff.certified (terse) drops parity_new_fact" in line for line in problems),
                        problems)
        self.assertFalse(any("(model_result) drops parity_new_fact" in line for line in problems), problems)

    def test_a_new_field_inside_an_object_row_fails(self):
        # shines_from, 2026-10-21: in each light's geometry row, read by Codex, 0 of 18 Claude typed plans.
        def add(result):
            for row in result["delta"]["geometry"]:
                row["parity_row_fact"] = {"elevation": 0.6}
        problems = problems_for(corpus_item(PROGRAM, lambda result: certified_commit(result) and
                                            (result.get("delta") or {}).get("geometry")), change=add)
        self.assertTrue(any("drops parity_row_fact at delta.geometry[]" in line for line in problems), problems)

    def test_a_known_gap_fails_once_its_review_entry_is_removed(self):
        registry = route_parity.load_registry()
        terse = next(entry for entry in registry["filters"] if entry["id"] == "terse-receipt")
        terse["review"] = {path: note for path, note in terse["review"].items()
                           if not path.startswith("delta.geometry[].light")}
        problems = problems_for(corpus_item(PROGRAM, lambda result: certified_commit(result) and any(
            "light" in row for row in (result.get("delta") or {}).get("geometry") or ())), registry)
        self.assertTrue(any("drops light at delta.geometry[]" in line for line in problems), problems)

    def test_a_new_key_list_on_a_route_fails(self):
        # A route that starts keeping only the fields it lists: a new field there stops silently.
        original = route_parity.VIEWS["claude.direct"]

        def listed(tool, result):
            kept = {key: result[key] for key in ("status", "success", "changes", "checks") if key in result}
            return original(tool, kept)
        with mock.patch.dict(route_parity.VIEWS, {"claude.direct": listed}):
            problems = problems_for(corpus_item(PROGRAM, certified_commit))
        self.assertTrue(any("claude.direct (model_result) drops zz_route_probe at the result" in line
                            for line in problems), problems)

    def test_a_registered_drop_the_code_no_longer_makes_is_stale(self):
        registry = route_parity.load_registry()
        registry["filters"][0]["drops"] = registry["filters"][0]["drops"] + ["changes.no_such_field"]
        problems = route_parity.stale(registry, set())
        self.assertIn("filter %s: drops changes.no_such_field matched nothing" % registry["filters"][0]["id"], problems)

    def test_a_drop_needs_a_reason(self):
        registry = {"filters": [{"id": "x", "routes": ["codex"], "tools": ["*"], "says": "s", "reasons": {},
                                 "drops": ["after"]}]}
        self.assertEqual(route_parity.registry_problems(registry),
                         ["filter x: drop after has no reason (reasons names no prefix of it)"])

    def test_host_bookkeeping_needs_no_entry(self):
        # A successful script's consent and timing are left out of every model copy by design (model_results).
        rows = route_parity.analyse("angelo_execute_python", corpus_item(
            "angelo_execute_python", lambda result: result.get("success") is True)[2])
        reaching = {row["key"] for row in rows if not row["at"] and any(row["reached"].values())}
        self.assertFalse(reaching & {"native_exec_ms", "consent", "undo_scope"})


class ResultFacts(unittest.TestCase):
    """A registered result fact must reach every route, past every filter and budget."""

    FACT = {"id": "test.fact", "field": "parity_fact", "tools": [PROGRAM, "angelo_execute_python"], "at": "",
            "sample": {"names": ["Gate"], "said": "a 'Gate' at the front"}}

    def test_a_result_fact_dropped_by_a_receipt_fails(self):
        problems = route_parity.result_fact_problems([self.FACT], [corpus_item(PROGRAM, certified_commit)])
        self.assertTrue(any("does not reach plan.handoff.certified (terse)" in line for line in problems), problems)

    def test_a_result_fact_past_the_excerpt_budget_fails(self):
        # A script whose own output fills the excerpt's 5,000 characters: the excerpt keeps a result's head first
        # (read-backs go last, result_facts.excerpt_order), so a fact placed after a long head falls past the budget.
        name, tool, result = corpus_item("angelo_execute_python", lambda result: result.get("success") is True
                                         and (result.get("after") or {}).get("objects"))
        result = copy.deepcopy(result)
        result["output"] = "printed line\n" * 500
        problems = route_parity.result_fact_problems([self.FACT], [(name, tool, result)])
        self.assertTrue(any("does not reach plan.handoff (excerpt)" in line for line in problems), problems)

    def test_a_result_fact_must_say_where_it_is(self):
        self.assertEqual(route_parity.result_fact_problems([{"id": "test.bare", "field": "x"}], []),
                         ["result fact test.bare lacks tools, at, sample"])

    def test_every_registered_result_fact_reaches_every_route(self):
        registry = json.loads((ROOT / "scripts/angelo/prompt_registry.json").read_text())
        self.assertEqual(route_parity.result_fact_problems(registry.get("result_facts", [])), [])


class Routes(unittest.TestCase):
    def test_the_mcp_routes_present_results_through_the_direct_route(self):
        # Both MCP routes reuse DirectRequest (the window's ExternalWork, and the headless child's): one view.
        source = (ROOT / "scripts/addons_core/angelo_agent/external_work.py").read_text()
        self.assertIn("from .claude_direct import DirectRequest", source)
        self.assertIn("self.direct.call(name, arguments, ticket)", source)
        headless = ROOT / "scripts/addons_core/angelo_agent/headless_host.py"
        if headless.is_file():
            self.assertIn("ExternalWork", headless.read_text())

    def test_every_route_has_a_view(self):
        self.assertEqual(set(route_parity.RESULT_ROUTES), set(route_parity.VIEWS))


if __name__ == "__main__":
    unittest.main()
