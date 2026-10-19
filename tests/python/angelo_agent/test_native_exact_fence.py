# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact fences: what one read-only span may reuse, and every step that ends it; no bpy needed."""

import ast
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDON))
from angelo_agent import exact_fence  # noqa: E402
from angelo_agent import program_dependencies as dependencies  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402


class FenceTests(unittest.TestCase):
    def tearDown(self):
        exact_fence.end()

    def test_a_check_is_computed_once_per_fence_and_never_outside_one(self):
        calls = []
        compute = lambda: calls.append(1) or len(calls)
        self.assertEqual((exact_fence.check("c", (1,), compute), exact_fence.check("c", (1,), compute)), (1, 2))
        with exact_fence.fence():
            self.assertEqual([exact_fence.check("c", (1,), compute) for _ in range(3)], [3, 3, 3])
            self.assertEqual(exact_fence.check("c", (2,), compute), 4)  # Another identity is another check.
            self.assertEqual(exact_fence.check("other", (1,), compute), 5)
        with exact_fence.fence():  # A new fence never sees the old one's memo.
            self.assertEqual(exact_fence.check("c", (1,), compute), 6)

    def test_nested_fences_join_and_end_drops_the_memo_for_the_rest_of_the_block(self):
        calls = []
        compute = lambda: calls.append(1) or len(calls)
        with exact_fence.fence():
            outer = exact_fence.current()
            with exact_fence.fence():
                self.assertIs(exact_fence.current(), outer)
                exact_fence.check("c", (1,), compute)
            self.assertIs(exact_fence.current(), outer)
            exact_fence.end()  # A callback-capable step.
            self.assertIsNone(exact_fence.current())
            self.assertEqual(exact_fence.check("c", (1,), compute), 2)
            with exact_fence.fence():  # A later read-only span gets a fresh fence of its own.
                self.assertIsNot(exact_fence.current(), outer)
                self.assertEqual(exact_fence.check("c", (1,), compute), 3)
            self.assertIsNone(exact_fence.current())
        self.assertIsNone(exact_fence.current())

    def test_a_check_that_ends_the_fence_is_not_stored(self):
        calls = []

        def compute():
            calls.append(1)
            exact_fence.end()
            return len(calls)

        with exact_fence.fence():
            exact_fence.check("c", (1,), compute)
        with exact_fence.fence():
            exact_fence.check("c", (1,), lambda: calls.append(1) or len(calls))
            self.assertEqual(exact_fence.check("c", (1,), lambda: 99), 2)

    def test_failures_are_never_remembered(self):
        attempts = []

        def refuse():
            attempts.append(1)
            raise ProgramError("refused", code="STALE_REVISION")

        with exact_fence.fence():
            for _ in range(2):
                with self.assertRaises(ProgramError):
                    exact_fence.check("c", (1,), refuse)
        self.assertEqual(len(attempts), 2)

    def test_a_replay_that_declines_recomputes(self):
        values = iter(["first", "second"])
        with exact_fence.fence():
            exact_fence.check("c", (1,), lambda: next(values))
            self.assertEqual(exact_fence.check("c", (1,), lambda: next(values), replay=lambda result: False), "second")

    def test_another_thread_never_shares_the_main_threads_fence(self):
        seen = []
        with exact_fence.fence():
            worker = threading.Thread(target=lambda: seen.append(exact_fence.current()))
            worker.start()
            worker.join()
            with patch.object(exact_fence, "_main_thread", return_value=False):
                with exact_fence.fence():
                    seen.append(exact_fence.current())
            self.assertIsNotNone(exact_fence.current())
        self.assertEqual(seen, [None, None])

    def test_declaration_tables_are_built_once_per_type_per_fence(self):
        builds = []
        rna = SimpleNamespace(as_pointer=lambda: 7)
        build = lambda value: builds.append(value) or ("table",)
        exact_fence.table("links", rna, build)
        exact_fence.table("links", rna, build)
        self.assertEqual(len(builds), 2)
        with exact_fence.fence():
            for _ in range(3):
                self.assertEqual(exact_fence.table("links", rna, build), ("table",))
            exact_fence.table("values", rna, build)
        self.assertEqual(len(builds), 4)

    def test_reads_only_runs_a_function_in_a_fence(self):
        @exact_fence.reads_only
        def read():
            return exact_fence.current()
        self.assertIsNotNone(read())
        self.assertIsNone(exact_fence.current())


class FingerprintBudgetTests(unittest.TestCase):
    """A reused fingerprint charges what hashing it again would, and fails exactly as it always did."""

    def setUp(self):
        self.hashes = 0

        def hash_closure(obj, budget):
            self.hashes += 1
            for slot, charge in enumerate((100, 40)[:len(budget)]):
                if charge > budget[slot]:
                    raise ProgramError("exhausted while hashing", code="LIMIT_EXCEEDED")
                budget[slot] -= charge
            return "digest"

        patcher = patch.object(dependencies, "_hash_closure", side_effect=hash_closure)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.obj = SimpleNamespace(as_pointer=lambda: 11)

    def test_reuse_charges_the_same_budget(self):
        with exact_fence.fence():
            first, second = [1000, 1000], [500, 500]
            self.assertEqual(dependencies._fingerprint(self.obj, first), "digest")
            self.assertEqual(dependencies._fingerprint(self.obj, second), "digest")
        self.assertEqual((first, second, self.hashes), ([900, 960], [400, 460], 1))

    def test_a_budget_reuse_would_exhaust_hashes_again_and_refuses_where_it_always_did(self):
        with exact_fence.fence():
            dependencies._fingerprint(self.obj, [1000, 1000])
            tight = [150, 10]
            with self.assertRaisesRegex(ProgramError, "exhausted while hashing"):
                dependencies._fingerprint(self.obj, tight)
        self.assertEqual((tight, self.hashes), ([50, 10], 2))

    def test_budgets_of_another_shape_are_not_shared(self):
        with exact_fence.fence():
            dependencies._fingerprint(self.obj, [1000, 1000])
            legacy = [1000]
            dependencies._fingerprint(self.obj, legacy)
        self.assertEqual((legacy, self.hashes), ([900], 2))


class PreparationHandOffTests(unittest.TestCase):
    """The controller's preparation fence runs inside a fencing backend's commit, after its last evaluation."""

    def run_commit(self, fencing):
        from test_native_program_jobs import Backend, program, finish
        from angelo_agent.program_jobs import ProgramJob, WriterLease

        class Fencing(Backend):
            fences_before_apply = fencing
            pending_before_apply = None

            def commit(self, stage):
                handed = self.pending_before_apply
                self.pending_before_apply = None
                self._record("commit:" + ("handed" if handed else "none"))
                if handed is not None:
                    handed()
                return super().commit(stage)

        backend = Fencing()
        job = ProgramJob(backend, program(), writer=WriterLease())
        finish(job)
        result = job.commit(before_apply=lambda: backend._record("before_apply"))
        return result, backend.calls[backend.calls.index("verify", backend.calls.index("delta") + 2):]

    def test_a_fencing_backend_receives_it_and_others_run_it_first(self):
        result, calls = self.run_commit(True)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(calls[-3:], ["commit:handed", "before_apply", "commit"])
        result, calls = self.run_commit(False)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(calls[-3:], ["before_apply", "commit:none", "commit"])

    def test_a_commit_that_never_runs_the_handed_off_fence_fails_loudly(self):
        from test_native_program_jobs import Backend, program, finish
        from angelo_agent.program_jobs import ProgramJob, WriterLease

        class Skipping(Backend):
            fences_before_apply = True
            pending_before_apply = None

        backend = Skipping()
        job = ProgramJob(backend, program(), writer=WriterLease())
        finish(job)
        result = job.commit(before_apply=lambda: backend._record("before_apply"))
        self.assertEqual((result["status"], result["error_code"]), ("failed", "INTERNAL_ERROR"))
        self.assertIn("preparation fence", result["error"])
        self.assertTrue(result["partial_changes_possible"])  # Its commit may have written: never "unchanged".
        self.assertNotIn("before_apply", backend.calls)

    def test_a_commit_that_clears_the_handed_off_fence_without_running_it_fails_loudly(self):
        from test_native_program_jobs import Backend, program, finish
        from angelo_agent.program_jobs import ProgramJob, WriterLease

        class Clearing(Backend):
            fences_before_apply = True
            pending_before_apply = None

            def commit(self, stage):
                self.pending_before_apply = None  # Taken and dropped, never run.
                return super().commit(stage)

        backend = Clearing()
        job = ProgramJob(backend, program(), writer=WriterLease())
        finish(job)
        result = job.commit(before_apply=lambda: backend._record("before_apply"))
        self.assertEqual((result["status"], result["error_code"]), ("failed", "INTERNAL_ERROR"))
        self.assertIn("preparation fence", result["error"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertNotIn("before_apply", backend.calls)

    def test_the_mixins_commit_refuses_before_its_first_write_when_its_final_checks_skip_the_fence(self):
        from angelo_agent.program_commit import ProgramCommitMixin

        class Skipping(ProgramCommitMixin):
            def final_checks(self, stage, before_apply=None):
                pass  # An override that never runs the controller's preparation fence.

        backend = Skipping()
        backend.pending_before_apply = exact_fence.HandOff(lambda: None)
        with self.assertRaises(ProgramError) as caught:
            backend.commit(SimpleNamespace(extras={}))
        self.assertEqual(caught.exception.code, "INTERNAL_ERROR")
        self.assertIn("preparation fence", str(caught.exception))
        self.assertFalse(getattr(backend, "mutation_started", False))

    def test_a_mixin_listed_before_the_commit_mixin_must_declare_too(self):
        """The check resolves commit across the whole MRO, not only the subclass's own body."""
        from angelo_agent.program_commit import ProgramCommitMixin

        class Mixin:
            def commit(self, stage):
                return {}

        with self.assertRaisesRegex(TypeError, "resolves commit to Mixin: declare fences_before_apply"):
            type("Bypass", (Mixin, ProgramCommitMixin), {})
        declares = type("Declares", (Mixin,), {"fences_before_apply": False})
        self.assertFalse(type("Declared", (declares, ProgramCommitMixin), {}).fences_before_apply)
        own = type("Own", (ProgramCommitMixin,), {"commit": Mixin.commit, "fences_before_apply": True})
        type("InheritsOwn", (own,), {})

    def test_overriding_the_mixins_commit_must_declare_whether_it_runs_the_fence(self):
        from angelo_agent.program_commit import ProgramCommitMixin
        with self.assertRaisesRegex(TypeError, "declare fences_before_apply"):
            type("SilentCommit", (ProgramCommitMixin,), {"commit": lambda self, stage: {}})
        declared = type("DeclaredCommit", (ProgramCommitMixin,), {"commit": lambda self, stage: {},
                                                                  "fences_before_apply": False})
        self.assertFalse(declared.fences_before_apply)
        type("InheritedCommit", (ProgramCommitMixin,), {})  # Inheriting the mixin's commit needs nothing.


def _calls_end_first(function):
    """Whether a function's first statements end the fence before any other work."""
    for statement in function.body[:4]:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if name == "end":
                    return True
    return False


class CallbackCapableStepsEndTheFenceTests(unittest.TestCase):
    """Defence in depth: every step that can change data ends the fence before it runs."""

    STEPS = {
        "program_objects.py": ("copy_object", "finish_stage", "_update_stage", "apply"),
        "program_stage_bundle.py": ("suspend_stage", "resume_stage"),
        "program_storage.py": ("_remove_ids", "_remove_ids_unchecked", "_remove_stage_ids", "discard"),
        "program_verification.py": ("prepare_commit",),
        "action_budget.py": ("_update_stage",),
    }

    def test_each_step_ends_the_fence_first(self):
        for module, names in self.STEPS.items():
            tree = ast.parse((ADDON / "angelo_agent" / module).read_text(encoding="utf-8"))
            functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
            for name in names:
                with self.subTest(module=module, step=name):
                    self.assertIn(name, functions)
                    self.assertTrue(_calls_end_first(functions[name]), "%s.%s must call exact_fence.end() first"
                                    % (module, name))

    def test_create_stage_ends_the_fence_before_its_first_new_id(self):
        source = (ADDON / "angelo_agent/program_objects.py").read_text(encoding="utf-8")
        body = source[source.index("    def create_stage"):source.index("    def _track")]
        self.assertLess(body.index("exact_fence.end()"), body.index("bpy.data.scenes.new"))
        self.assertLess(body.index("stage_borrowing.plan(binding)"), body.index("exact_fence.end()"))

    def test_commit_evaluates_the_stage_before_its_fence_and_writes_after_it(self):
        source = (ADDON / "angelo_agent/program_verification.py").read_text(encoding="utf-8")
        body = source[source.index("    def final_checks"):source.index("    def prepare_commit")]
        order = [body.index(token) for token in ("self._update_stage(stage)", "with exact_fence.fence():",
                                                  "self.check_stage(stage)", "before_apply()",
                                                  "self.verify(stage.binding)", "program_preimages.check(")]
        self.assertEqual(order, sorted(order))
        commit = (ADDON / "angelo_agent/program_commit.py").read_text(encoding="utf-8")
        self.assertLess(commit.index("self.final_checks(stage, before_apply)"), commit.index("self.mutation_started = True"))


if __name__ == "__main__":
    unittest.main()
