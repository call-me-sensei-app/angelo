# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a script's time went in ``bpy.ops`` (``script_operator_time``), without Blender.

Pinned here: every operator the script creates while it runs is counted and timed, its own attributes intact;
``bpy.ops`` is put back on the module it replaced however the script ends, and stacked with the Boolean observer each
puts back its own; an operator that calls ``bpy.ops`` itself counts once; its documentation and printed form stay its
own; a callable kept past the script counts nothing; the result names the costliest operators, and Blender's fact
about the per-call update only once calls that added objects took ``NOTE_SECONDS`` or more; a script with no operator
call, or a stand-in ``bpy`` with no ``ops``, gets nothing; both of the executor's endings carry the field. The real
operators are timed in ``test_blender_script_operator_time``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import contextlib
import itertools
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import script_booleans, script_operator_time as timing  # noqa: E402


class Operator:
    """A stand-in operator: ``__doc__`` and ``str`` are its own, as Blender's callable has."""

    def __init__(self, module, function, log, spent, objects):
        self.module, self.function, self.log, self.spent, self.objects = module, function, log, spent, objects
        self.__doc__ = "bpy.ops.%s.%s()\nThe operator's own description" % (module, function)

    def __str__(self):
        return "<function bpy.ops.%s.%s>" % (self.module, self.function)

    def __call__(self, *args, **kwargs):
        self.log.append((self.module, self.function, kwargs))
        self.spent[0] += self.spent[1]   # The stand-in clock moves by the operator's cost.
        self.objects.extend([None] * kwargs.get("adds", 1 if self.function.endswith("_add") else 0))
        if kwargs.get("nested"):   # A Python operator that calls bpy.ops itself.
            kwargs["nested"]()
        if kwargs.get("fail"):
            raise RuntimeError("Operator failed")
        return {"FINISHED"}

    def poll(self):
        return True


def stand_in(cost=0.01):
    log, now, objects = [], [0.0, cost], []
    ops = SimpleNamespace()
    ops._op_create_function = lambda module, function: Operator(module, function, log, now, objects)
    return SimpleNamespace(ops=ops, data=SimpleNamespace(objects=objects)), ops, log, now


class ClockTests(unittest.TestCase):
    def run_script(self, script, cost=0.01):
        bpy, ops, log, now = stand_in(cost)
        with mock.patch.object(timing.time, "perf_counter", lambda: now[0]):
            with timing.Clock(bpy) as clock:
                script(ops)
        return clock, ops, log

    def test_each_call_is_counted_and_timed_and_the_operator_is_its_own(self):
        def script(ops):
            cube = ops._op_create_function("mesh", "primitive_cube_add")
            for _ in range(5):
                cube(size=1)
            self.assertTrue(cube.poll())
            self.assertEqual(cube(size=2), {"FINISHED"})
            ops._op_create_function("object", "shade_smooth")()
        clock, _ops, log = self.run_script(script)
        self.assertEqual(len(log), 7)
        self.assertEqual(clock.outcome(), {"bpy_ops": {"calls": 7, "seconds": 0.07, "costliest": [
            ["mesh.primitive_cube_add", 6, 0.06], ["object.shade_smooth", 1, 0.01]]}})

    def test_the_cost_fact_comes_only_once_calls_that_added_objects_took_a_noticeable_time(self):
        clock, _ops, _log = self.run_script(lambda ops: ops._op_create_function("mesh", "primitive_cube_add")(),
                                            cost=timing.NOTE_SECONDS)
        self.assertEqual(clock.outcome()["bpy_ops"]["cost"], timing.COST)
        clock, _ops, _log = self.run_script(lambda ops: ops._op_create_function("mesh", "primitive_cube_add")(),
                                            cost=timing.NOTE_SECONDS / 2)
        self.assertNotIn("cost", clock.outcome()["bpy_ops"])
        clock, _ops, _log = self.run_script(lambda ops: ops._op_create_function("render", "render")(),
                                            cost=timing.NOTE_SECONDS * 5)   # Slow, but it added nothing.
        self.assertEqual((clock.outcome()["bpy_ops"]["seconds"], "cost" in clock.outcome()["bpy_ops"]),
                         (timing.NOTE_SECONDS * 5, False))

    def test_only_the_outermost_call_counts_when_an_operator_calls_bpy_ops_itself(self):
        def script(ops):
            inner = ops._op_create_function("mesh", "primitive_cube_add")
            ops._op_create_function("image", "import_as_mesh_planes")(nested=lambda: inner(), adds=0)
        clock, _ops, log = self.run_script(script)
        self.assertEqual(len(log), 2)   # Both ran;
        self.assertEqual(clock.outcome()["bpy_ops"]["costliest"], [["image.import_as_mesh_planes", 1, 0.02]])

    def test_the_operators_documentation_and_printed_form_are_its_own(self):
        bpy, ops, _log, _now = stand_in()
        plain = ops._op_create_function("mesh", "primitive_cube_add")
        with timing.Clock(bpy):
            timed = ops._op_create_function("mesh", "primitive_cube_add")
            self.assertIsInstance(timed, timing._Timed)
            # Each lookup makes a new callable; Windows prints its address in upper case.
            unplaced = lambda text: re.sub(r" at 0x[0-9a-fA-F]+", "", text)
            self.assertEqual((timed.__doc__, unplaced(str(timed)), unplaced(repr(timed))),
                             (plain.__doc__, unplaced(str(plain)), unplaced(repr(plain))))
            import copy
            self.assertEqual(unplaced(str(copy.copy(timed))), unplaced(str(plain)))   # No recursion while it is built.

    def test_bpy_ops_is_put_back_on_the_module_it_replaced_even_if_the_script_rebinds_it(self):
        bpy, ops, _log, _now = stand_in()
        real = ops._op_create_function
        with timing.Clock(bpy):
            bpy.ops = SimpleNamespace()
        self.assertIs(ops._op_create_function, real)

    def test_at_most_the_three_costliest_are_named(self):
        def script(ops):
            for index, name in enumerate(("a", "b", "c", "d")):
                for _ in range(index + 1):
                    ops._op_create_function("mesh", name)()
        rows = self.run_script(script)[0].outcome()["bpy_ops"]
        self.assertEqual((rows["calls"], [row[0] for row in rows["costliest"]]), (10, ["mesh.d", "mesh.c", "mesh.b"]))

    def test_a_failed_call_is_timed_and_bpy_ops_is_put_back_however_the_script_ends(self):
        for ending in (None, ValueError, SystemExit, KeyboardInterrupt):
            bpy, ops, _log, _now = stand_in()
            real = ops._op_create_function
            with self.subTest(ending=ending):
                try:
                    with timing.Clock(bpy) as clock:
                        with contextlib.suppress(RuntimeError):
                            ops._op_create_function("object", "join")(fail=True)
                        if ending is not None:
                            raise ending()
                except (ValueError, SystemExit, KeyboardInterrupt):
                    pass
                self.assertIs(ops._op_create_function, real)
                self.assertEqual(clock.outcome()["bpy_ops"]["calls"], 1)

    def test_a_callable_kept_past_the_script_counts_nothing(self):
        bpy, ops, log, _now = stand_in()
        with timing.Clock(bpy) as clock:
            kept = ops._op_create_function("mesh", "primitive_cube_add")
        kept()
        self.assertEqual((len(log), clock.outcome()), (1, {}))

    def test_no_call_or_no_ops_states_nothing(self):
        bpy, _ops, _log, _now = stand_in()
        with timing.Clock(bpy) as clock:
            pass
        self.assertEqual(clock.outcome(), {})
        with timing.Clock(SimpleNamespace(context=None)) as alone:
            pass
        self.assertEqual(alone.outcome(), {})

    def test_stacked_on_the_boolean_observer_each_puts_back_its_own(self):
        bpy, ops, log, _now = stand_in()
        bpy.context = SimpleNamespace(object=None, active_object=None)
        real = ops._op_create_function
        with contextlib.ExitStack() as stack:   # As ``script_after.Observing`` enters them.
            stack.enter_context(script_booleans.Applied(bpy))
            clock = stack.enter_context(timing.Clock(bpy))
            ops._op_create_function("object", "modifier_apply")(modifier="Cut")
            ops._op_create_function("mesh", "primitive_cube_add")()
        self.assertIs(ops._op_create_function, real)
        self.assertEqual([row[:2] for row in log], [("object", "modifier_apply"), ("mesh", "primitive_cube_add")])
        self.assertEqual(clock.outcome()["bpy_ops"]["calls"], 2)


class WiringTests(unittest.TestCase):
    def test_the_observer_holds_the_clock_and_both_endings_of_a_script_carry_its_field(self):
        tools = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        tree = ast.parse(tools)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and ast.unparse(node.func) == "observe.clock.outcome"]
        self.assertEqual(len(calls), 2, "the completed and the failed result")
        after = (ADDON / "script_after.py").read_text(encoding="utf-8")
        self.assertIn("script_operator_time.Clock(bpy)", after)
        self.assertIn("stack.enter_context(self.clock)", after)


class CountingTests(unittest.TestCase):
    def test_a_cheap_operator_in_a_loop_never_pays_for_an_object_count(self):
        reads = []

        class Objects:
            def __len__(self):
                reads.append(1)
                return 8000

        clock = timing.Clock(SimpleNamespace(data=SimpleNamespace(objects=Objects())))
        clock._active = True
        with mock.patch.object(timing.time, "perf_counter", side_effect=itertools.count(0.0, 0.000001)):
            for _call in range(1000):
                clock.call("object.select_all", lambda: None, (), {})
        self.assertEqual(clock.calls["object.select_all"][0], 1000)
        self.assertEqual(len(reads), 2, "the first call alone, before and after")

    def test_an_operator_that_turns_slow_is_counted_again(self):
        objects = []
        clock = timing.Clock(SimpleNamespace(data=SimpleNamespace(objects=objects)))
        clock._active = True
        now, costs = [0.0], iter([0.0001, 0.002, 2.0, 2.0])

        def add():
            now[0] += next(costs)
            objects.append(1)
        with mock.patch.object(timing.time, "perf_counter", side_effect=lambda: now[0]):
            for _call in range(4):
                clock.call("mesh.primitive_cube_add", add, (), {})
        self.assertEqual(clock.outcome()["bpy_ops"]["cost"], timing.COST)

    def test_a_cheap_call_just_before_an_adding_one_never_loses_its_objects(self):
        """A loop alternating a call of the operator that adds nothing (or fails at once) with one that adds: before,
        the cheap call turned the count off for the adding one, every time, and the cost note never came."""
        objects, now = [], [0.0]
        clock = timing.Clock(SimpleNamespace(data=SimpleNamespace(objects=objects)))
        clock._active = True

        def cheap():
            now[0] += 0.0001

        def add():
            now[0] += 0.2
            objects.append(1)
        with mock.patch.object(timing.time, "perf_counter", side_effect=lambda: now[0]):
            for _call in range(10):
                clock.call("object.duplicate", add, (), {})
                clock.call("object.duplicate", cheap, (), {})
        self.assertAlmostEqual(clock.calls["object.duplicate"][2], 2.0)
        self.assertEqual(clock.outcome()["bpy_ops"]["cost"], timing.COST)

    def test_any_run_of_cheap_calls_between_adding_ones_never_loses_them(self):
        """1.0.10 break-it: with eight or more cheap calls between adding ones, the decay alone stopped counting."""
        for between in (1, 8, 50):
            objects, now = [], [0.0]
            clock = timing.Clock(SimpleNamespace(data=SimpleNamespace(objects=objects)))
            clock._active = True

            def cheap():
                now[0] += 0.0001

            def add():
                now[0] += 0.2
                objects.append(1)
            with mock.patch.object(timing.time, "perf_counter", side_effect=lambda: now[0]):
                for _call in range(10):
                    clock.call("object.duplicate", add, (), {})
                    for _cheap in range(between):
                        clock.call("object.duplicate", cheap, (), {})
            with self.subTest(between=between):
                self.assertAlmostEqual(clock.calls["object.duplicate"][2], 2.0)

    def test_an_operator_that_added_once_pays_at_most_its_budget_for_counts(self):
        now = [0.0]

        class Objects:
            def __len__(self):
                now[0] += 0.0005   # A large scene's walk.
                return 40000

        def add_once():
            now[0] += 0.05

        def cheap():
            now[0] += 0.00001
        clock = timing.Clock(SimpleNamespace(data=SimpleNamespace(objects=Objects())))
        clock._active = True
        with mock.patch.object(timing.time, "perf_counter", side_effect=lambda: now[0]):
            clock.calls["mesh.primitive_cube_add"] = [0, 0.0, 0.0, timing.COUNTED_FROM, 0.0]
            clock.call("mesh.primitive_cube_add", add_once, (), {})
            clock.calls["mesh.primitive_cube_add"][2] = 0.05   # It added (a stand-in count cannot grow).
            for _call in range(10000):
                clock.call("mesh.primitive_cube_add", cheap, (), {})
        row = clock.calls["mesh.primitive_cube_add"]
        self.assertLessEqual(row[4], timing.COUNT_BUDGET * row[1] + 2 * 0.0005)

    def test_after_a_slow_call_a_cheap_loop_stops_counting_within_a_few_calls(self):
        reads, now = [], [0.0]

        class Objects:
            def __len__(self):
                reads.append(1)
                return 8000

        def slow():
            now[0] += 1.0

        def cheap():
            now[0] += 0.000001
        clock = timing.Clock(SimpleNamespace(data=SimpleNamespace(objects=Objects())))
        clock._active = True
        with mock.patch.object(timing.time, "perf_counter", side_effect=lambda: now[0]):
            clock.call("object.select_all", slow, (), {})
            for _call in range(1000):
                clock.call("object.select_all", cheap, (), {})
        self.assertLessEqual(len(reads), 2 * 12, "log2(1 s / 1 ms) calls after the slow one, at most")

    def test_a_scene_whose_count_costs_more_raises_the_bar_for_counting(self):
        now = [0.0]

        class Objects:
            reads = 0

            def __len__(self):
                Objects.reads += 1
                now[0] += 0.0005   # A large scene's walk.
                return 40000

        def cheap():
            now[0] += 0.002   # Past the fixed bar, yet the count would cost half of it.
        clock = timing.Clock(SimpleNamespace(data=SimpleNamespace(objects=Objects())))
        clock._active = True
        with mock.patch.object(timing.time, "perf_counter", side_effect=lambda: now[0]):
            for _call in range(1000):
                clock.call("object.mode_set", cheap, (), {})
        self.assertEqual(Objects.reads, 2, "the first call alone")


if __name__ == "__main__":
    unittest.main()
