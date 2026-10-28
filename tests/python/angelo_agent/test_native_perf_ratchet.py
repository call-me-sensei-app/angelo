# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests for the instruction-count ratchet and the benchmark contracts it reads."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import perf_ratchet  # noqa: E402

PLATFORM = "darwin-arm64"


def ceilings(**operations):
    value = dict(perf_ratchet.empty_ceilings(), default_tolerance=.05)
    value["platforms"][PLATFORM] = {"operations": {name: dict(entry) for name, entry in operations.items()}}
    return value


def results(**operations):
    return {"ratchet": {"metric": "instructions", "statistic": "median", "platform": PLATFORM, "operations": operations}}


class CompareTests(unittest.TestCase):
    def test_classification_uses_a_symmetric_tolerance_band(self):
        rows = perf_ratchet.compare(ceilings(a={"ceiling": 1000}, b={"ceiling": 1000}, c={"ceiling": 1000},
                                             d={"ceiling": 1000}, e={"ceiling": 1000}, f={"ceiling": 1000},
                                             gone={"ceiling": 1000}),
                                    PLATFORM, {"a": 1051, "b": 1049, "c": 951, "d": 949, "e": 1000, "f": 1020, "new": 5})
        status = {row["operation"]: row["status"] for row in rows}
        self.assertEqual(status, {"a": "regressed", "b": "within", "c": "within", "d": "improved", "e": "within",
                                  "f": "within", "gone": "unmeasured", "new": "new"})

    def test_per_operation_tolerance_overrides_the_default(self):
        rows = perf_ratchet.compare(ceilings(noisy={"ceiling": 1000, "tolerance": .2}), PLATFORM, {"noisy": 1150})
        self.assertEqual(rows[0]["status"], "within")
        self.assertEqual(rows[0]["tolerance"], .2)

    def test_unknown_platform_has_no_ceilings(self):
        rows = perf_ratchet.compare(ceilings(a={"ceiling": 10}), "linux-x86_64", {"a": 1000})
        self.assertEqual(rows, [{"operation": "a", "status": "new", "measured": 1000}])


class RobustMetricTests(unittest.TestCase):
    """The 2026-10-21 rules: spread, noisy operations, ABAB verdicts, rejected samples and exact bytes."""

    def test_spread_above_a_third_of_the_tolerance_makes_an_operation_noisy(self):
        value = ceilings(steady={"ceiling": 1000, "tolerance": .09}, shaky={"ceiling": 1000, "tolerance": .09},
                         unknown={"ceiling": 1000, "tolerance": .09})
        details = {"spread": {"steady": .03, "shaky": .0301}}
        rows = {row["operation"]: row for row in perf_ratchet.compare(
            value, PLATFORM, {"steady": 1200, "shaky": 1200, "unknown": 1200}, details)}
        self.assertEqual(rows["steady"]["status"], "regressed", "a spread of exactly a third stays ceiling-gated")
        self.assertEqual((rows["shaky"]["status"], rows["shaky"]["ceiling_status"]), ("noisy", "regressed"))
        self.assertEqual(rows["unknown"]["status"], "regressed", "no spread recorded: the ceiling decides")
        self.assertNotIn("spread", rows["unknown"])

    def test_abab_verdicts_decide_noisy_operations_only(self):
        value = ceilings(a={"ceiling": 1000}, b={"ceiling": 1000}, c={"ceiling": 1000}, steady={"ceiling": 1000})
        details = {"spread": {"a": .5, "b": .5, "c": .5, "steady": 0.}}
        verdicts = {"a": "pass", "b": "regressed", "c": "inconclusive", "steady": "pass"}
        rows = {row["operation"]: row["status"] for row in perf_ratchet.compare(
            value, PLATFORM, {"a": 5000, "b": 900, "c": 1000, "steady": 5000}, details, verdicts)}
        self.assertEqual(rows, {"a": "abab_pass", "b": "abab_regressed", "c": "noisy", "steady": "regressed"})
        problems = perf_ratchet.gate_problems(perf_ratchet.compare(
            value, PLATFORM, {"a": 5000, "b": 900, "c": 1000, "steady": 1000}, details, verdicts))
        self.assertEqual([problem.split(":")[0] for problem in problems], ["abab_regressed", "noisy"])

    def test_a_noisy_run_never_moves_a_ceiling(self):
        value = ceilings(op={"ceiling": 1000})
        rows = perf_ratchet.compare(value, PLATFORM, {"op": 500}, {"spread": {"op": .2}})
        self.assertEqual(rows[0]["status"], "noisy")
        self.assertEqual(perf_ratchet.update(value, PLATFORM, rows)["platforms"][PLATFORM]["operations"]["op"],
                         {"ceiling": 1000})

    def test_rejected_operations_fail_the_gate_with_their_reason(self):
        rejected = {"op": {"samples": 9, "valid": 0, "rejected": 9, "reasons": {"swapouts": 9}}}
        rows = perf_ratchet.compare(ceilings(op={"ceiling": 1000}), PLATFORM, {}, {"rejected": rejected})
        self.assertEqual(rows, [{"operation": "op", "status": "rejected", "validity": rejected["op"]}])
        self.assertTrue(perf_ratchet.gate_problems(rows)[0].startswith("rejected: op (every sample had page-ins"))

    def test_byte_operations_are_exact_and_never_noisy(self):
        rows = perf_ratchet.compare(perf_ratchet.empty_ceilings(), PLATFORM, {"size": 4096},
                                    {"units": {"size": "bytes"}, "spread": {"size": .9}})
        self.assertEqual(rows, [{"operation": "size", "status": "new", "measured": 4096, "unit": "bytes"}])
        recorded = perf_ratchet.update(perf_ratchet.empty_ceilings(), PLATFORM, rows)
        entry = recorded["platforms"][PLATFORM]["operations"]["size"]
        self.assertEqual(entry, {"ceiling": 4096, "unit": "bytes", "tolerance": 0.0})
        rows = perf_ratchet.compare(recorded, PLATFORM, {"size": 4097}, {"units": {"size": "bytes"}, "spread": {"size": .9}})
        self.assertEqual((rows[0]["status"], rows[0]["unit"]), ("regressed", "bytes"))
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "measured in instructions but its ceiling is in bytes"):
            perf_ratchet.compare(recorded, PLATFORM, {"size": 4096})
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "unknown unit"):
            perf_ratchet.load_details({"ratchet": {"units": {"size": "seconds"}}})
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "unknown unit"):
            perf_ratchet.load_ceilings(ceilings(size={"ceiling": 1, "unit": "seconds"}))

    def test_stamp_records_spreads_abab_decisions_and_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_checkout(root)
            value = ceilings(op={"ceiling": 1000}, shaky={"ceiling": 1000})
            receipt = dict(results(op=1000, shaky=1300), suite=perf_ratchet.GATE_SUITE, success=True,
                           sources_sha256=perf_ratchet.sources_digest(root), loads={"bench.py": 1.4},
                           abab_verdicts={"shaky": "pass"})
            receipt["ratchet"]["spread"] = {"op": .01, "shaky": .2}
            last = perf_ratchet.stamp(value, receipt, root=root, today="2026-10-21")["platforms"][PLATFORM]["last_measured"]
            self.assertEqual(last["spreads"], {"op": .01, "shaky": .2})
            self.assertEqual(last["abab_decided"], ["shaky"])
            self.assertEqual(last["loads"], {"bench.py": 1.4})
            self.assertNotIn("max_load", last, "the default quiet rule is not recorded as an exception")
            loose = dict(receipt, max_load=5.0, conditions="desktop app open")
            last = perf_ratchet.stamp(value, loose, root=root, today="2026-10-22")["platforms"][PLATFORM]["last_measured"]
            self.assertEqual((last["max_load"], last["conditions"]), (5.0, "desktop app open"))
            del receipt["abab_verdicts"]
            with self.assertRaisesRegex(perf_ratchet.RatchetError, "noisy: shaky"):
                perf_ratchet.stamp(value, receipt, root=root)


class UpdateTests(unittest.TestCase):
    def test_update_lowers_improvements_and_records_new_operations_only(self):
        value = ceilings(better={"ceiling": 1000, "tolerance": .1}, same={"ceiling": 1000}, gone={"ceiling": 7})
        rows = perf_ratchet.compare(value, PLATFORM, {"better": 800, "same": 1010, "fresh": 42})
        updated = perf_ratchet.update(value, PLATFORM, rows, source="results.json")
        operations = updated["platforms"][PLATFORM]["operations"]
        self.assertEqual(operations["better"], {"ceiling": 800, "tolerance": .1})
        self.assertEqual(operations["same"], {"ceiling": 1000})
        self.assertEqual(operations["gone"], {"ceiling": 7})
        self.assertEqual(operations["fresh"], {"ceiling": 42})
        self.assertEqual(value["platforms"][PLATFORM]["operations"]["better"]["ceiling"], 1000, "input unchanged")

    def test_raising_requires_explicit_permission(self):
        value = ceilings(slower={"ceiling": 1000}, better={"ceiling": 1000})
        rows = perf_ratchet.compare(value, PLATFORM, {"slower": 2000, "better": 500})
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "allow-raise.*slower"):
            perf_ratchet.update(value, PLATFORM, rows)
        raised = perf_ratchet.update(value, PLATFORM, rows, allow_raise=True)
        self.assertEqual(raised["platforms"][PLATFORM]["operations"]["slower"]["ceiling"], 2000)
        self.assertEqual(raised["platforms"][PLATFORM]["operations"]["better"]["ceiling"], 500)


SUM = "snapshot/dense_500mb_repeat_capture_three_write_sum"


class RestampToleranceTests(unittest.TestCase):
    """The 2026-10-27 rule: three-write sums get twice their run-to-run range, with no floor; others are unchanged."""

    def test_three_write_sums_get_twice_their_range_with_no_floor(self):
        for document in ("dense_50mb", "dense_500mb", "many_ids_10000", "history_10"):
            with self.subTest(document=document):
                name = "snapshot/%s_repeat_capture_three_write_sum" % document
                self.assertTrue(perf_ratchet.range_only(name))
                self.assertEqual(perf_ratchet.restamp_tolerance(name, [1000, 1003, 1006]), .012)
                self.assertEqual(perf_ratchet.restamp_tolerance(name, [1000, 1003, 1006], writes_files=True), .012,
                                 "no 10% file-write floor")
                self.assertEqual(perf_ratchet.restamp_tolerance(name, [1000, 1000]), 0.0)
                self.assertEqual(perf_ratchet.restamp_tolerance(name, [1000, 1100, 1060]), .2)

    def test_other_operations_keep_the_standing_floors_and_cap(self):
        for name in ("snapshot/dense_500mb_repeat_capture_paired_net", "snapshot/dense_500mb_repeat_capture_gross",
                     "default/search_100_objects", "conversation_journal/save_500"):
            with self.subTest(operation=name):
                self.assertFalse(perf_ratchet.range_only(name))
                self.assertEqual(perf_ratchet.restamp_tolerance(name, [1000, 1003, 1006]), .05)
                self.assertEqual(perf_ratchet.restamp_tolerance(name, [1000, 1003, 1006], writes_files=True), .1)
                self.assertEqual(perf_ratchet.restamp_tolerance(name, [1000, 1080]), .16)
                self.assertEqual(perf_ratchet.restamp_tolerance(name, [1000, 1080], default=.2), .2)
        self.assertEqual(perf_ratchet.restamp_tolerance("default/x", [1000, 1400]), .25)
        self.assertEqual(perf_ratchet.restamp_tolerance(SUM, [1000, 1400]), .25, "the cap is not a floor")

    def test_the_range_is_relative_to_the_lowest_run_and_rounding_never_narrows_it(self):
        # The 2026-10-22 rebaseline's dense_200mb runs: 2 x (49,581,728 - 47,052,978) / 47,052,978 = 0.107485.
        self.assertEqual(perf_ratchet.restamp_tolerance(SUM, [47052978, 47595133, 49581728]), .10749)
        values = [1502760267, 1508518448]
        self.assertGreaterEqual(perf_ratchet.restamp_tolerance(SUM, values), 2 * (values[1] - values[0]) / values[0])

    def test_one_run_or_an_invalid_count_has_no_range(self):
        for values in ([1000], [], [1000, 0], [1000, 1.5]):
            with self.subTest(values=values), self.assertRaises(perf_ratchet.RatchetError):
                perf_ratchet.restamp_tolerance(SUM, values)


class UnstampedTests(unittest.TestCase):
    def test_a_missing_three_write_sum_ceiling_fails_closed_with_how_to_record_it(self):
        value = ceilings(old={"ceiling": 1000})
        rows = perf_ratchet.compare(value, PLATFORM, {"old": 1000, SUM: 1500, "fresh": 7})
        status = {row["operation"]: row["status"] for row in rows}
        self.assertEqual(status, {"old": "within", SUM: "unstamped", "fresh": "new"})
        problems = perf_ratchet.gate_problems(rows)
        self.assertEqual([problem.split(":")[0] for problem in problems], ["new", "unstamped"])
        self.assertIn(SUM, problems[1])
        self.assertIn("perf_rebaseline.py", problems[1])
        updated = perf_ratchet.update(value, PLATFORM, rows)["platforms"][PLATFORM]["operations"]
        self.assertNotIn(SUM, updated, "one run has no run-to-run range")
        self.assertEqual(updated["fresh"], {"ceiling": 7})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_checkout(root)
            receipt = dict(results(old=1000, **{SUM: 1500}), suite=perf_ratchet.GATE_SUITE, success=True,
                           sources_sha256=perf_ratchet.sources_digest(root))
            with self.assertRaisesRegex(perf_ratchet.RatchetError, "unstamped"):
                perf_ratchet.stamp(value, receipt, root=root)


def run(binary="b" * 64, **operations):
    return dict(results(**operations), binary_sha256=binary)


class RebaselineTests(unittest.TestCase):
    def test_records_the_highest_run_median_and_the_rule_tolerance(self):
        import perf_rebaseline
        runs = [run(**{SUM: value, "other/op": 10}) for value in (1502760267, 1508518448, 1505000000, 1504000000)]
        updated = perf_rebaseline.rebaseline(ceilings(), runs, [SUM], source="r1, r2", today="2026-10-28")
        entry = updated["platforms"][PLATFORM]
        self.assertEqual(entry["operations"][SUM], {"ceiling": 1508518448, "tolerance": .00767})
        self.assertNotIn("other/op", entry["operations"])
        self.assertEqual(entry["rebaselined"], [{"date": "2026-10-28", "source": "r1, r2", "runs": 4, "operations": {
            SUM: {"runs": [1502760267, 1508518448, 1505000000, 1504000000], "tolerance": .00767}}}])
        status = lambda measured: perf_ratchet.compare(updated, PLATFORM, {SUM: measured})[0]["status"]  # noqa: E731
        self.assertEqual(status(1520000000), "within", "0.761% over the ceiling, inside the 0.767% band")
        self.assertEqual(status(1521000000), "regressed", "0.828% over: no 5% floor absorbs it")

    def test_refuses_mixed_builds_missing_runs_bytes_one_run_and_unasked_raises(self):
        import perf_rebaseline
        mixed = [run("a" * 64, **{SUM: 100}), run("b" * 64, **{SUM: 101})]
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "one build"):
            perf_rebaseline.rebaseline(ceilings(), mixed, [SUM])
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "missing from run 2"):
            perf_rebaseline.rebaseline(ceilings(), [run(**{SUM: 100}), run(other=1)], [SUM])
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "at least 2 runs"):
            perf_rebaseline.rebaseline(ceilings(), [run(**{SUM: 100})], [SUM])
        sized = [dict(run(**{"snapshot/x_bytes": 9}), ratchet=dict(run(**{"snapshot/x_bytes": 9})["ratchet"],
                                                                   units={"snapshot/x_bytes": "bytes"}))] * 2
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "exact bytes"):
            perf_rebaseline.rebaseline(ceilings(), sized, ["snapshot/x_bytes"])
        recorded = ceilings(**{SUM: {"ceiling": 100, "tolerance": .01}})
        higher = [run(**{SUM: 100}), run(**{SUM: 102})]
        with self.assertRaisesRegex(perf_ratchet.RatchetError, "allow-raise"):
            perf_rebaseline.rebaseline(recorded, higher, [SUM])
        raised = perf_rebaseline.rebaseline(recorded, higher, [SUM], allow_raise=True)
        self.assertEqual(raised["platforms"][PLATFORM]["operations"][SUM], {"ceiling": 102, "tolerance": .04})

    def test_command_line_records_matching_operations(self):
        import perf_rebaseline
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            limits = folder / "ceilings.json"
            limits.write_text(json.dumps(ceilings(**{"default/op": {"ceiling": 5}})))
            paths = []
            for index, value in enumerate((1000, 1004, 1002)):
                paths.append(folder / ("run%d.json" % index))
                paths[-1].write_text(json.dumps(run(**{SUM: value, "default/op": 5})))
            with contextlib.redirect_stdout(io.StringIO()):
                code = perf_rebaseline.main([*map(str, paths), "--operations", "snapshot/*_three_write_sum",
                                             "--ceilings", str(limits)])
            self.assertEqual(code, 0)
            operations = json.loads(limits.read_text())["platforms"][PLATFORM]["operations"]
            self.assertEqual(operations, {"default/op": {"ceiling": 5}, SUM: {"ceiling": 1004, "tolerance": .008}})
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(perf_rebaseline.main([str(paths[0]), "--operations", "snapshot/*",
                                                       "--ceilings", str(limits)]), 2)


class InputTests(unittest.TestCase):
    def test_results_accept_a_report_or_a_bare_section(self):
        self.assertEqual(perf_ratchet.load_results(results(a=5)), (PLATFORM, {"a": 5}))
        self.assertEqual(perf_ratchet.load_results(results(a=5)["ratchet"]), (PLATFORM, {"a": 5}))

    def test_invalid_results_are_rejected_not_passed(self):
        for value in ({}, results(), results(a=0), results(a=-1), results(a=1.5), results(a=True),
                      {"ratchet": {"platform": "", "operations": {"a": 1}}},
                      {"ratchet": {"metric": "wall_ms", "platform": PLATFORM, "operations": {"a": 1}}}):
            with self.subTest(value=value), self.assertRaises(perf_ratchet.RatchetError):
                perf_ratchet.load_results(value)

    def test_invalid_ceilings_are_rejected(self):
        bad_tolerance = ceilings(a={"ceiling": 1, "tolerance": .9})
        for value in ({}, dict(perf_ratchet.empty_ceilings(), version=2), ceilings(a={"ceiling": 0}), bad_tolerance,
                      dict(perf_ratchet.empty_ceilings(), default_tolerance=-.1)):
            with self.subTest(value=value), self.assertRaises(perf_ratchet.RatchetError):
                perf_ratchet.load_ceilings(value)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = perf_ratchet.main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_exit_codes_and_update_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            limits, measured = folder / "ceilings.json", folder / "results.json"
            measured.write_text(json.dumps(results(op=1000)))
            self.assertEqual(self.run_cli(str(measured), "--ceilings", str(limits))[0], 2, "no ceilings yet")
            self.assertEqual(self.run_cli(str(measured), "--ceilings", str(limits), "--update")[0], 0)
            self.assertEqual(json.loads(limits.read_text())["platforms"][PLATFORM]["operations"]["op"]["ceiling"], 1000)
            self.assertEqual(self.run_cli(str(measured), "--ceilings", str(limits))[0], 0)
            measured.write_text(json.dumps(results(op=2000)))
            code, _, stderr = self.run_cli(str(measured), "--ceilings", str(limits))
            self.assertEqual(code, 1)
            self.assertIn("op", stderr)
            self.assertEqual(self.run_cli(str(measured), "--ceilings", str(limits), "--update")[0], 1)
            self.assertEqual(json.loads(limits.read_text())["platforms"][PLATFORM]["operations"]["op"]["ceiling"], 1000,
                             "a refused raise must not write")
            self.assertEqual(self.run_cli(str(measured), "--ceilings", str(limits), "--update", "--allow-raise")[0], 0)
            self.assertEqual(json.loads(limits.read_text())["platforms"][PLATFORM]["operations"]["op"]["ceiling"], 2000)
            measured.write_text("not json")
            self.assertEqual(self.run_cli(str(measured), "--ceilings", str(limits))[0], 2)

    def test_allow_raise_needs_update(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            perf_ratchet.main(["results.json", "--allow-raise"])


def fake_checkout(folder):
    """A minimal tree with the measured sources at their real relative paths."""
    addon = folder / "scripts/addons_core/angelo_agent"
    harness = folder / "tests/python/angelo_agent"
    addon.mkdir(parents=True)
    harness.mkdir(parents=True)
    (addon / "scene_queries.py").write_text("A = 1\n")
    (addon / "__pycache__").mkdir()
    (addon / "__pycache__" / "scene_queries.cpython-313.pyc").write_bytes(b"ignored")
    for name in perf_ratchet.MEASURED_HARNESS:
        (harness / name).write_text("# " + name + "\n")
    return addon


class GateTests(unittest.TestCase):
    def test_gate_fails_on_regressed_unmeasured_and_new_operations(self):
        value = ceilings(slower={"ceiling": 1000}, lost={"ceiling": 1000}, same={"ceiling": 1000})
        problems = perf_ratchet.gate_problems(perf_ratchet.compare(value, PLATFORM, {"slower": 2000, "same": 1000,
                                                                                     "fresh": 5}))
        self.assertEqual([problem.split(":")[0] for problem in problems], ["regressed", "unmeasured", "new"])
        self.assertIn("slower", problems[0])
        self.assertIn("lost", problems[1])
        self.assertIn("fresh", problems[2])
        self.assertEqual(perf_ratchet.gate_problems(perf_ratchet.compare(value, PLATFORM, {
            "slower": 1000, "lost": 990, "same": 1010})), [])

    def test_source_digest_follows_code_but_not_caches_or_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            addon = fake_checkout(root)
            first = perf_ratchet.sources_digest(root)
            (addon / "__pycache__" / "scene_queries.cpython-313.pyc").write_bytes(b"changed")
            self.assertEqual(perf_ratchet.sources_digest(root), first)
            (addon / "scene_queries.py").write_bytes(b"A = 1\r\n")
            self.assertEqual(perf_ratchet.sources_digest(root), first)
            (addon / "scene_queries.py").write_text("A = 2\n")
            self.assertNotEqual(perf_ratchet.sources_digest(root), first)
            (addon / "scene_queries.py").write_text("A = 1\n")
            (root / "tests/python/angelo_agent/benchmark_fixtures.py").write_text("# changed\n")
            self.assertNotEqual(perf_ratchet.sources_digest(root), first)

    def test_stamp_records_only_a_passing_receipt_of_these_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_checkout(root)
            value = ceilings(op={"ceiling": 1000})
            receipt = dict(results(op=1010), suite=perf_ratchet.GATE_SUITE, success=True, commit="abc",
                           binary_sha256="b" * 64, sources_sha256=perf_ratchet.sources_digest(root))
            stamped = perf_ratchet.stamp(value, receipt, root=root, today="2026-10-09")
            self.assertEqual(stamped["platforms"][PLATFORM]["last_measured"], {
                "date": "2026-10-09", "commit": "abc", "binary_sha256": "b" * 64, "binary_uuid": None,
                "sources_sha256": receipt["sources_sha256"], "operations": 1,
                "highest_ratio": {"operation": "op", "ratio": 1.01}})
            self.assertNotIn("last_measured", value["platforms"][PLATFORM], "the input is not modified")
            for change, message in (({"success": False}, "passing"), ({"suite": "other"}, "passing"),
                                    ({"sources_sha256": "0" * 64}, "other sources"),
                                    ({"ratchet": results(op=2000)["ratchet"]}, "regressed")):
                with self.subTest(change=change), self.assertRaisesRegex(perf_ratchet.RatchetError, message):
                    perf_ratchet.stamp(value, dict(receipt, **change), root=root)

    def test_stamp_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            limits, receipt = folder / "ceilings.json", folder / "perf-ratchet.json"
            limits.write_text(json.dumps(ceilings(op={"ceiling": 1000})))
            receipt.write_text(json.dumps(dict(results(op=1000), suite=perf_ratchet.GATE_SUITE, success=True,
                                               sources_sha256=perf_ratchet.sources_digest())))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(perf_ratchet.main([str(receipt), "--ceilings", str(limits), "--stamp"]), 0)
            self.assertIn("last_measured", json.loads(limits.read_text())["platforms"][PLATFORM])
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                perf_ratchet.main([str(receipt), "--stamp", "--update"])


class CheckedInContractsTests(unittest.TestCase):
    def test_checked_in_ceilings_are_valid(self):
        value = perf_ratchet.load_ceilings(json.loads(perf_ratchet.CEILINGS_PATH.read_text()))
        self.assertIn(PLATFORM, value["platforms"])
        operations = value["platforms"][PLATFORM]["operations"]
        prefixes = ("default/", "many_objects/", "dense_geometry/", "deep_collections/", "stage_bundle/", "snapshot/",
                    "view_check/", "view_policy/", "long_session/", "conversation_journal/", "snapshot_worker/",
                    "native_reads/", "chat_waiting/")
        self.assertTrue(operations)
        for name in operations:
            with self.subTest(operation=name):
                self.assertTrue(name.startswith(prefixes), name)
                # Wall-clock poll splitting is load-dependent; only work-clock programs are ratcheted.
                if "staged_" in name:
                    self.assertTrue(name.endswith("_work_clock"), name)

    def test_snapshot_paired_net_is_not_gated_and_three_write_sums_carry_their_own_tolerance(self):
        # Maintainer, 2026-10-27: the three-write sum is gated, the paired net is reported only.
        value = perf_ratchet.load_ceilings(json.loads(perf_ratchet.CEILINGS_PATH.read_text()))
        operations = value["platforms"][PLATFORM]["operations"]
        self.assertEqual([name for name in operations if name.endswith("_paired_net")], [])
        for name, entry in operations.items():
            if perf_ratchet.range_only(name):
                with self.subTest(operation=name):
                    self.assertIn("tolerance", entry, "never the default 5% floor")
        for name, entry in operations.items():
            if name.startswith("snapshot/") and name.endswith("_bytes"):
                self.assertEqual((entry["unit"], entry["tolerance"]), ("bytes", 0.0), name)

    def test_ceilings_were_measured_on_the_current_sources(self):
        # CI has no instruction counters, but it can see that the add-on or the
        # benchmark harness changed after the last macOS measurement.
        value = perf_ratchet.load_ceilings(json.loads(perf_ratchet.CEILINGS_PATH.read_text()))
        last = value["platforms"][PLATFORM].get("last_measured")
        self.assertIsNotNone(last, "perf_ceilings.json records no measurement")
        self.assertRegex(last["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(last["sources_sha256"], perf_ratchet.sources_digest(), (
            "The add-on or the benchmark harness changed since the instruction ceilings were last measured (%s). "
            "On macOS run: python3 tests/python/angelo_agent/run_native_suites.py --binary <Angelo> --include %s; "
            "then python3 tests/python/angelo_agent/perf_ratchet.py <its fixture/perf-ratchet.json> --stamp "
            "and commit perf_ceilings.json (doc/angelo/performance.rst)." % (last["date"], perf_ratchet.GATE_SUITE)))

    def test_stage_bundle_compression_default_is_unchanged(self):
        from angelo_agent import program_storage
        self.assertIs(program_storage.STAGE_BUNDLE_COMPRESSION, True)

    def test_new_measurement_files_are_small_and_licensed(self):
        for name in ("perf_counters.py", "perf_ratchet.py", "benchmark_fixtures.py", "benchmark_blender_snapshot_cost.py",
                     "benchmark_blender_stage_compression.py", "benchmark_blender_agent_execution.py",
                     "eval_live_agent.py", "eval_live_agent_session.py", "eval_live_tasks.py", "eval_live_journeys.py",
                     "test_blender_eval_live_agent_dryrun.py", "test_blender_perf_ratchet.py",
                     "benchmark_blender_request_host_time.py", "benchmark_blender_view_scene_check.py",
                     "benchmark_blender_long_session.py", "benchmark_blender_session_journal.py",
                     "long_session_replay.py", "long_session_timers.py",
                     "benchmark_blender_native_reads.py", "benchmark_blender_chat_waiting.py",
                     "snapshot_paired.py", "perf_abab.py", "perf_rebaseline.py", "test_native_perf_paired.py", "bench_floor_launch.py",
                     "bench_floor_blender.py", "floor_launch_probe.py", "floor_addon_trace.py",
                     "test_native_floor_launch.py"):
            with self.subTest(file=name):
                text = (HERE / name).read_text()
                self.assertIn("SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.", text)
                self.assertIn("SPDX-License-Identifier: GPL-2.0-or-later", text)
                self.assertLessEqual(len(text.splitlines()), 500)


if __name__ == "__main__":
    unittest.main()
