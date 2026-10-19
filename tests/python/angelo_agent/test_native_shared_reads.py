# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests for the read-pass optimizations of scene observations.

* ``scene_shared_reads``: a datablock shared by many objects is read once per
  pass, and every later object replays the first read's budget charges,
  limitation appends and resource registrations in their original order, so
  budget exhaustion and reported limitations do not move.
* ``generic_settings.writable_paths``: the static RNA walk is cached per type
  and yields the same paths as ``rna_rules.writable_settings``.
* ``scene_fingerprints``: the stack walks report exactly the limitations and
  dependency IDs of the recursive walks they replaced.

The compiled-app proof (byte-identical query results on every benchmark
cohort, budget exhaustion included) is described in doc/angelo/performance.rst.
"""

import importlib
from pathlib import Path
import random
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import generic_settings, rna_rules  # noqa: E402
from angelo_agent.scene_read_values import BudgetExceeded, ReadBudget  # noqa: E402
from angelo_agent.scene_shared_reads import shared_read  # noqa: E402

if "angelo_agent.scene_fingerprints" in sys.modules:
    fingerprints = sys.modules["angelo_agent.scene_fingerprints"]
else:
    fake_bpy = ModuleType("bpy")
    fake_bpy.types = SimpleNamespace(Object=type("Object", (), {}), Collection=type("Collection", (), {}))
    fake_bpy.data = SimpleNamespace(collections=[], scenes=[])
    with patch.dict(sys.modules, {"bpy": fake_bpy}):
        fingerprints = importlib.import_module("angelo_agent.scene_fingerprints")


class LoggingBudget(ReadBudget):
    """A real budget that also logs each successful charge."""

    def __init__(self, items):
        super().__init__(10 ** 9, items, 60, packed=10 ** 9)
        self.charges = []

    def take(self, elements=0, items=0, packed=0):
        super().take(elements, items, packed)
        self.charges.append((elements, items, packed))


def read_graph(budget, limits, resource, nodes=3):
    """A stand-in read: interleaved charges, limitations and resource registrations."""
    resource("tree")
    for index in range(nodes):
        budget.take(items=2)
        if limits is not None:
            limits.append("limit-%d" % index)
        resource("node-%d" % index, "NODE")
    budget.take(elements=5, packed=7)
    return {"nodes": nodes}


def run(budget, objects, key=("graph", 1)):
    """Read the same graph for several objects; returns (results, limits, resources, exhausted at)."""
    limits, resources, results = [], [], []
    for index in range(objects):
        try:
            results.append(shared_read(budget, key, lambda b, l, r: read_graph(b, l, r), limits,
                                       lambda *args: resources.append(args)))
        except BudgetExceeded:
            return results, limits, resources, index
    return results, limits, resources, None


class SharedReadTests(unittest.TestCase):
    def test_replay_matches_reading_every_time(self):
        for items in (100, 13, 7, 6, 5, 1):  # Plenty, mid-object, at an object boundary, inside the first read.
            with self.subTest(items=items):
                shared = LoggingBudget(items)
                reread = LoggingBudget(items)
                reread.shared = None  # No memo: every object reads again (the behaviour before).
                got, expected = run(shared, 5), run(reread, 5)
                self.assertEqual(got, expected)
                self.assertEqual(shared.charges, reread.charges)
                self.assertEqual((shared.items, shared.elements, shared.packed),
                                 (reread.items, reread.elements, reread.packed))

    def test_value_is_read_once_and_shared(self):
        calls = []
        budget = ReadBudget(10 ** 6, 10 ** 6, 60)
        first = shared_read(budget, "k", lambda b, l, r: calls.append(1) or {"v": 1})
        second = shared_read(budget, "k", lambda b, l, r: calls.append(1) or {"v": 2})
        self.assertIs(first, second)
        self.assertEqual(calls, [1])

    def test_failed_read_is_not_remembered(self):
        budget, calls = ReadBudget(10 ** 6, 10 ** 6, 60), []

        def read(_budget, _limits, _resource):
            calls.append(1)
            raise BudgetExceeded()
        for _ in range(2):
            with self.assertRaises(BudgetExceeded):
                shared_read(budget, "k", read)
        self.assertEqual(len(calls), 2)

    def test_no_memo_without_a_pass_budget_or_key(self):
        stand_in = SimpleNamespace(take=lambda items=0: None)  # Host stand-ins take only what they need.
        calls = []
        for _ in range(2):
            shared_read(stand_in, "k", lambda b, l, r: calls.append(b) or 1)
            shared_read(ReadBudget(10, 10, 60), None, lambda b, l, r: calls.append(b) or 1)
        self.assertEqual(len(calls), 4)
        self.assertIs(calls[0], stand_in)  # The caller's own objects, not a recorder.

    def test_budgets_do_not_share_reads(self):
        calls = []
        for _ in range(2):
            shared_read(ReadBudget(10, 10, 60), "k", lambda b, l, r: calls.append(1) or 1)
        self.assertEqual(len(calls), 2)


class StructRNA:
    def __init__(self, identifier, properties):
        self.identifier, self.properties = identifier, properties


def prop(identifier, kind="FLOAT", **extra):
    return SimpleNamespace(identifier=identifier, type=kind, is_readonly=False, is_hidden=False, is_runtime=False,
                           subtype="NONE", array_length=0, array_dimensions=(0, 0, 0), hard_min=-1., hard_max=1.,
                           is_animatable=True, fixed_type=None, **extra)


class WritablePathTests(unittest.TestCase):
    def setUp(self):
        generic_settings._PATHS.clear()

    def test_paths_match_writable_settings_and_are_cached_per_type(self):
        rna = StructRNA("FakeNode", [prop("factor"), prop("use_clamp", "BOOLEAN"), prop("filepath", "STRING")])
        expected = tuple(entry["path"] for entry in rna_rules.writable_settings(rna, limit=256))
        self.assertEqual(generic_settings.writable_paths(rna, 256), expected)
        self.assertEqual(expected, ("factor", "use_clamp"))
        with patch.object(rna_rules, "writable_settings", side_effect=AssertionError("walked again")):
            self.assertEqual(generic_settings.writable_paths(rna, 256), expected)

    def test_another_property_count_or_limit_walks_again(self):
        rna = StructRNA("FakeNode", [prop("factor")])
        self.assertEqual(generic_settings.writable_paths(rna, 256), ("factor",))
        rna.properties.append(prop("strength"))
        self.assertEqual(generic_settings.writable_paths(rna, 256), ("factor", "strength"))
        self.assertEqual(generic_settings.writable_paths(rna, 1), ("factor",))

    def test_the_cache_is_bounded(self):
        with patch.object(generic_settings, "MAX_CACHED_TYPES", 2):
            for index in range(5):
                generic_settings.writable_paths(StructRNA("Fake%d" % index, [prop("factor")]), 256)
                self.assertLessEqual(len(generic_settings._PATHS), 2)


def recursive_limitations(value):
    if isinstance(value, dict):
        yield from value.get("revision_limitations", ())
        if any(not isinstance(driver, dict) or not driver.get("native") for driver in value.get("drivers") or ()):
            yield "animation_driver_expressions"  # A driver Blender cannot evaluate natively (driver_rules).
        for item in value.values():
            yield from recursive_limitations(item)
    elif isinstance(value, list):
        for item in value:
            yield from recursive_limitations(item)


def recursive_ids(state):
    ids = set()

    def visit(value):
        if isinstance(value, dict):
            ids.update(value.get("invalidation_ids", ()))
            for field in ("object", "id"):
                item = value.get(field)
                if type(item) is str and (item.startswith("scn:") or item.startswith("dat:")):
                    ids.add(int(item.rsplit(":", 1)[1]))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(state)
    return frozenset(ids)


def nested(rng, depth=0):
    if depth > 4 or rng.random() < .3:
        return rng.choice([None, 1, 2.5, "text", "scn:e:%d" % rng.randrange(50), True, ("tuple", "skipped")])
    if rng.random() < .5:
        return [nested(rng, depth + 1) for _ in range(rng.randrange(4))]
    value = {"k%d" % index: nested(rng, depth + 1) for index in range(rng.randrange(4))}
    for key, make in (("revision_limitations", lambda: ["l%d" % rng.randrange(5) for _ in range(rng.randrange(3))]),
                      ("drivers", lambda: rng.choice([[], [1], None, [{"native": True}], [{"native": False}]])),
                      ("invalidation_ids", lambda: [rng.randrange(50) for _ in range(rng.randrange(3))]),
                      ("object", lambda: "scn:e:%d" % rng.randrange(50)),
                      ("id", lambda: rng.choice(["dat:e:MESH:%d" % rng.randrange(50), "other:1"]))):
        if rng.random() < .3:
            value[key] = make()
    return value


class StackWalkTests(unittest.TestCase):
    def test_walks_match_the_recursive_ones(self):
        rng = random.Random(7)
        for _ in range(400):
            value = [nested(rng), nested(rng)]
            self.assertEqual(sorted(fingerprints._nested_limitations(value)), sorted(recursive_limitations(value)))
            self.assertEqual(fingerprints.dependency_ids({"captured": value}), recursive_ids({"captured": value}))


if __name__ == "__main__":
    unittest.main()
