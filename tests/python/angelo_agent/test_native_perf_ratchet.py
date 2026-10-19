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
                "date": "2026-10-09", "commit": "abc", "binary_sha256": "b" * 64,
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
                     "eval_live_agent.py", "eval_live_agent_session.py", "eval_live_tasks.py",
                     "test_blender_eval_live_agent_dryrun.py", "test_blender_perf_ratchet.py",
                     "benchmark_blender_request_host_time.py", "benchmark_blender_view_scene_check.py",
                     "benchmark_blender_long_session.py", "benchmark_blender_session_journal.py",
                     "long_session_replay.py", "long_session_timers.py",
                     "benchmark_blender_native_reads.py", "benchmark_blender_chat_waiting.py"):
            with self.subTest(file=name):
                text = (HERE / name).read_text()
                self.assertIn("SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.", text)
                self.assertIn("SPDX-License-Identifier: GPL-2.0-or-later", text)
                self.assertLessEqual(len(text.splitlines()), 500)


if __name__ == "__main__":
    unittest.main()
