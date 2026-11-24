# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every typed wrapped operation shares one in-turn time budget, and anything over it becomes a job (2.1.0).

The agent-verbs design (Part E, revision 3) had each wrapped operation declare its own ``budget_s``. The coordinator
ruled on 2026-11-15 that the budget is a single shared value. It is ``job_start_wait.MAX_HOLD``: how long a turn
already waits on a job before handing it back. So a wrapped operation never holds a turn longer than a job may.

Each operation still declares ``input_bound``: the largest measured size whose p95 (stage plus run) fits the budget
on Mac 1 and on the Windows VM. Above the bound the shared code refuses before staging. When the operation names
a ``job_kind``, the refusal says to start that job; otherwise it names the parameters that shrink the input.

Dependency-free: plain declarations and timings, no Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_start_wait, refusal_codes  # noqa: E402
from angelo_agent import wrapped_op_budget as budget  # noqa: E402

KINDS = ("RENDER_STILL", "UV_LAYOUT")
PARAMETERS = {"type": "object", "properties": {"columns": {"type": "integer"}, "rows": {"type": "integer"},
                                              "packer": {"enum": ["QUALITY", "FAST"], "default": "QUALITY"}}}


def trials(seconds, n=None):
    """``n`` trials of ``seconds`` (the shared code reads p95; flat samples make it exact)."""
    return [seconds] * (n or budget.MIN_TRIALS)


def declaration(**fields):
    base = dict(kind="shape_cut", parameters=PARAMETERS, input_bound={"triangles": 1000}, job_kind=None,
                levers=("columns", "rows"))
    base.update(fields)
    return base


class OneSharedBudget(unittest.TestCase):
    def test_the_budget_is_the_turns_hold_on_a_job(self):
        self.assertIs(budget.TURN_BUDGET_S, job_start_wait.MAX_HOLD)
        tree = ast.parse((ADDON / "wrapped_op_budget.py").read_text(encoding="utf-8"))
        literal = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(getattr(t, "id", "") == "TURN_BUDGET_S" for t in node.targets)
                   and isinstance(node.value, ast.Constant)]
        self.assertEqual(literal, [], "TURN_BUDGET_S is imported from job_start_wait, never a second literal")

    def test_a_declaration_never_carries_its_own_budget(self):
        with self.assertRaisesRegex(ValueError, "TURN_BUDGET_S"):
            budget.check_declaration(declaration(budget_s=30), KINDS)

    def test_no_declaring_module_names_a_budget(self):
        """Every ``WRAPPED_OPS`` in the add-on, whichever lane wrote it, leaves the budget to the shared value."""
        offenders = []
        for path in sorted(ADDON.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "WRAPPED_OPS" for t in node.targets):
                    for sub in ast.walk(node.value):
                        names = [k.arg for k in sub.keywords] if isinstance(sub, ast.Call) else \
                            [k.value for k in sub.keys if isinstance(k, ast.Constant)] if isinstance(sub, ast.Dict) \
                            else []
                        if "budget_s" in names:
                            offenders.append("%s:%d" % (path.name, sub.lineno))
        self.assertEqual(offenders, [])


class BoundFromTimings(unittest.TestCase):
    def test_p95_is_nearest_rank(self):
        self.assertEqual(budget.p95([float(i) for i in range(1, 21)]), 19.0)
        self.assertEqual(budget.p95([float(i) for i in range(100, 0, -1)]), 95.0)

    def test_the_bound_is_the_largest_size_both_platforms_fit(self):
        limit = budget.TURN_BUDGET_S
        timings = {"mac1": {100: trials(1), 1000: trials(4), 10000: trials(limit), 100000: trials(limit * 3)},
                   "windows": {100: trials(2), 1000: trials(6), 10000: trials(limit + .5), 100000: trials(limit * 5)}}
        found = budget.bound_from_timings(timings)
        self.assertEqual(found["bound"], 1000)
        self.assertEqual(found["first_over"], 10000)
        self.assertEqual(found["budget_s"], limit)
        self.assertEqual(found["p95"][10000], {"mac1": limit, "windows": limit + .5})

    def test_exactly_the_budget_fits(self):
        limit = budget.TURN_BUDGET_S
        found = budget.bound_from_timings({"mac1": {5: trials(limit)}, "windows": {5: trials(limit)}})
        self.assertEqual(found["bound"], 5)
        self.assertIsNone(found["first_over"])

    def test_a_larger_size_that_happened_to_fit_never_raises_the_bound(self):
        limit = budget.TURN_BUDGET_S
        timings = {"mac1": {1: trials(1), 2: trials(limit + 1), 3: trials(1)},
                   "windows": {1: trials(1), 2: trials(1), 3: trials(1)}}
        self.assertEqual(budget.bound_from_timings(timings)["bound"], 1)

    def test_no_size_fits_gives_no_bound(self):
        limit = budget.TURN_BUDGET_S
        found = budget.bound_from_timings({"mac1": {1: trials(limit + 1)}, "windows": {1: trials(1)}})
        self.assertIsNone(found["bound"])

    def test_the_p95_is_the_tail_not_the_middle(self):
        limit = budget.TURN_BUDGET_S
        slow_tail = [1.0] * 18 + [limit + 1] * 2   # Median 1 s; 2 of 20 trials over: p95 is over.
        found = budget.bound_from_timings({"mac1": {1: trials(1), 2: slow_tail}, "windows": {1: trials(1), 2: trials(1)}})
        self.assertEqual(found["bound"], 1)

    def test_timings_must_cover_both_machines_and_enough_trials(self):
        with self.assertRaisesRegex(ValueError, "windows"):
            budget.bound_from_timings({"mac1": {1: trials(1)}})
        with self.assertRaisesRegex(ValueError, "trials"):
            budget.bound_from_timings({"mac1": {1: trials(1, budget.MIN_TRIALS - 1)}, "windows": {1: trials(1)}})
        with self.assertRaisesRegex(ValueError, "both"):
            budget.bound_from_timings({"mac1": {1: trials(1), 2: trials(1)}, "windows": {1: trials(1)}})
        for bad in (float("nan"), float("inf"), -1.0):
            with self.assertRaises(ValueError):
                budget.bound_from_timings({"mac1": {1: trials(bad)}, "windows": {1: trials(1)}})


class Declarations(unittest.TestCase):
    def test_a_job_kind_is_a_real_job(self):
        budget.check_declaration(declaration(job_kind="UV_LAYOUT"), KINDS)
        budget.check_declaration(declaration(job_kind=None), KINDS)
        with self.assertRaisesRegex(ValueError, "UV_PACK_JOB"):
            budget.check_declaration(declaration(job_kind="UV_PACK_JOB"), KINDS)

    def test_job_if_names_one_enum_parameter_and_needs_a_job(self):
        budget.check_declaration(declaration(job_kind="UV_LAYOUT", job_if=("packer", {"QUALITY"})), KINDS)
        with self.assertRaisesRegex(ValueError, "job_kind"):
            budget.check_declaration(declaration(job_if=("packer", {"QUALITY"})), KINDS)
        for bad in (("packer", set()), ("packer", {"SLOW"}), ("columns", {1}), ("nothing", {"A"}), "packer",
                    ("packer", "QUALITY"), ("packer", {"QUALITY"}, "extra")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                budget.check_declaration(declaration(job_kind="UV_LAYOUT", job_if=bad), KINDS)

    def test_a_job_if_parameter_has_a_value_when_left_out(self):
        """Left out with no default, the operator's own default would decide, and it may be a job_if value."""
        no_default = dict(PARAMETERS, properties=dict(PARAMETERS["properties"], packer={"enum": ["QUALITY", "FAST"]}))
        with self.assertRaisesRegex(ValueError, "packer"):
            budget.check_declaration(declaration(parameters=no_default, job_kind="UV_LAYOUT",
                                                 job_if=("packer", {"QUALITY"})), KINDS)
        budget.check_declaration(declaration(parameters=dict(no_default, required=["packer"]), job_kind="UV_LAYOUT",
                                             job_if=("packer", {"QUALITY"})), KINDS)

    def test_levers_are_parameters(self):
        with self.assertRaisesRegex(ValueError, "density"):
            budget.check_declaration(declaration(levers=("density",)), KINDS)

    def test_a_bound_is_positive_whole_counts(self):
        budget.check_declaration(declaration(input_bound=None), KINDS)   # Constant cost: measured, nothing to bound.
        for bad in ({}, {"triangles": 0}, {"triangles": 2.5}, {"triangles": True}, {"": 4}, [("triangles", 4)]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                budget.check_declaration(declaration(input_bound=bad), KINDS)


class Admission(unittest.TestCase):
    def test_at_or_under_the_bound_runs_in_the_turn(self):
        self.assertIsNone(budget.admit(declaration(), {"triangles": 1000}))
        self.assertIsNone(budget.admit(declaration(input_bound=None), {}))

    def refusal(self, decl, measured, parameters=None):
        with self.assertRaises(refusal_codes.RefusalError) as caught:
            budget.admit(decl, measured, parameters)
        return caught.exception

    def test_over_the_bound_with_a_job_becomes_that_job(self):
        error = self.refusal(declaration(kind="uv_layout", job_kind="UV_LAYOUT", levers=()), {"triangles": 1001})
        self.assertEqual(error.code, "LIMIT_EXCEEDED")
        self.assertIn(error.code, refusal_codes.CORRECTABLE_CODES)
        self.assertEqual(error.details, {"limit": "turn_budget", "budget_s": budget.TURN_BUDGET_S,
                                         "bound": {"triangles": 1000}, "measured": {"triangles": 1001},
                                         "job_kind": "UV_LAYOUT", "levers": [], "job_if": None})
        self.assertIn("angelo_job_start", str(error))
        self.assertIn("UV_LAYOUT", str(error))
        self.assertIs(error.partial_changes_possible, False)
        self.assertIs(refusal_codes.error_result(error)["scene_changed"], False)

    def test_over_the_bound_without_a_job_names_the_levers(self):
        error = self.refusal(declaration(), {"triangles": 5000})
        self.assertIsNone(error.details["job_kind"])
        self.assertEqual(error.details["levers"], ["columns", "rows"])
        self.assertIn("columns", str(error))
        self.assertNotIn("angelo_job_start", str(error))

    def test_the_words_never_name_the_running_app_blender(self):
        for decl in (declaration(), declaration(job_kind="UV_LAYOUT"), declaration(levers=())):
            self.assertNotIn("Blender", str(self.refusal(decl, {"triangles": 10 ** 6})))

    def test_a_job_if_value_is_the_job_at_any_size(self):
        decl = declaration(kind="uv_layout", input_bound=None, job_kind="UV_LAYOUT", job_if=("packer", {"QUALITY"}),
                           levers=())
        error = self.refusal(decl, {}, {"packer": "QUALITY"})
        self.assertEqual(error.code, "LIMIT_EXCEEDED")
        self.assertEqual(error.details["job_if"], {"packer": "QUALITY"})
        self.assertEqual(error.details["job_kind"], "UV_LAYOUT")
        self.assertIn("angelo_job_start", str(error))
        self.assertIn("packer QUALITY", str(error))
        self.assertIs(error.partial_changes_possible, False)
        self.assertIsNone(budget.admit(decl, {}, {"packer": "FAST"}))

    def test_an_omitted_parameter_is_its_default(self):
        decl = declaration(input_bound=None, job_kind="UV_LAYOUT", job_if=("packer", {"QUALITY"}))
        self.assertEqual(self.refusal(decl, {}, {}).details["job_if"], {"packer": "QUALITY"})

    def test_a_job_if_call_is_the_job_before_its_bound_is_read(self):
        decl = declaration(job_kind="UV_LAYOUT", job_if=("packer", {"QUALITY"}))   # Bounded by triangles too.
        self.assertEqual(self.refusal(decl, {"triangles": 5}, {"packer": "QUALITY"}).details["job_if"],
                         {"packer": "QUALITY"})
        self.assertIsNone(budget.admit(decl, {"triangles": 5}, {"packer": "FAST"}))

    def test_every_bounded_measure_is_measured(self):
        with self.assertRaisesRegex(ValueError, "triangles"):
            budget.admit(declaration(), {"faces": 4})


if __name__ == "__main__":
    unittest.main()
