# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Several trials at a glance, and what a run wrote when its still is missing (no Blender, process or account).

* A side that ran a test five times is one row: how many passed, failed and
  were inconclusive, then the
  median and the range of the seconds, the output tokens, the typed share of
  mutations and the lookups before the first change.
* A failed "final still" check is told apart: the side rendered something
  else (a review still, a render job under another name), or it rendered
  nothing.
"""

import copy
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import oneshot_group as group  # noqa: E402
import oneshot_mcp  # noqa: E402
import oneshot_metrics as metrics  # noqa: E402
import oneshot_produced as produced  # noqa: E402
import oneshot_report as report  # noqa: E402
import oneshot_tables as tables  # noqa: E402
import scenario_specs as specs  # noqa: E402
from test_native_oneshot_metrics import angelo_record, claude_lines, mcp_record  # noqa: E402
from test_native_oneshot_report import ReportTests  # noqa: E402

MINIS = specs.ONESHOT_GROUPS["oneshot-minis"]
PNG = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (2560).to_bytes(4, "big") + (1440).to_bytes(
    4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00" * 64
SMALL = PNG.replace((2560).to_bytes(4, "big"), (1280).to_bytes(4, "big")).replace(
    (1440).to_bytes(4, "big"), (720).to_bytes(4, "big"))


def side(label, seconds, verdict="passed", typed=(1, 1), lookups=0, tokens=30000, stop="finished", angelo=True):
    """One trial's side as a scorecard holds it, with the numbers a trial varies in."""
    path = copy.deepcopy(metrics.angelo_path(angelo_record(), "claude-opus-5-5") if angelo else metrics.mcp_path(
        mcp_record(), claude_lines(), {"messages": 1}, "claude-opus-5-5"))
    calls = sum(typed)
    path["wall_s"], path["stop"]["reason"] = seconds, stop
    path["usage"] = dict(path["usage"] or {}, output_tokens=tokens) if tokens is not None else None
    path["mutations"].update(typed_calls=typed[0], script_calls=typed[1],
                             typed_share=typed[0] / calls if calls else None)
    path["lookups"]["calls"] = lookups
    return {"label": label, "verdict": verdict, "path": path, "checks": {"passed": 5, "total": 5},
            "reasons": [] if verdict == "passed" else ["check failed: two rails"]}


def five():
    """Five trials of one test on each side: the same test took 144 s, then 229 s by another route."""
    seconds = (144.0, 229.0, 151.0, 160.0, 301.0)
    angelo = [side("Angelo %d" % (index + 1), value, typed=((1, 0), (0, 1), (1, 0), (2, 1), (0, 0))[index],
                   lookups=(0, 3, 0, 1, 6)[index], tokens=(4000, 9000, 4200, 5000, None)[index],
                   verdict="failed" if index == 4 else "passed", stop="time_cap" if index == 4 else "finished")
              for index, value in enumerate(seconds)]
    other = [side("Blender MCP (Codex) %d" % (index + 1), value + 30, typed=(0, 2), tokens=6000 + 100 * index,
                  angelo=False) for index, value in enumerate(seconds)]
    return angelo + other


class SpreadTests(unittest.TestCase):
    def test_median_then_least_and_most(self):
        self.assertEqual(tables._spread([144.0, 229.0, 151.0, 160.0, 301.0]), "160 [144-301]")
        self.assertEqual(tables._spread([144.04]), "144")                                # One trial: no range.
        self.assertEqual(tables._spread([0.5, 0.0, 1.0], percent=True), "50 % [0 %-100 %]")
        self.assertEqual(tables._spread([4000, 9000, 4200, 5000], 0), "4600 [4000-9000]")

    def test_a_number_a_trial_did_not_record_is_never_a_zero(self):
        self.assertEqual(tables._spread([4000, None, 9000], 0), "6500 [4000-9000] (1 unknown)")
        self.assertEqual(tables._spread([None, None]), "unknown")
        self.assertEqual(tables._spread([None, 1.0], percent=True, none="no mutation"), "100 % (1 no mutation)")
        self.assertEqual(tables._spread([]), "–")
        self.assertEqual(tables._spread([True, 3]), "3 (1 unknown)")                     # A flag is not a number.

    def test_a_side_is_named_without_its_trial_number(self):
        self.assertEqual(tables.side_name({"label": "Blender MCP (Claude Code) 12"}), "Blender MCP (Claude Code)")
        self.assertEqual(tables.side_name({"label": "Angelo 3", "side_label": "Angelo"}), "Angelo")


class TrialsTests(unittest.TestCase):
    def test_five_trials_are_one_row_per_side(self):
        angelo, other = tables.trials(five())
        self.assertEqual((angelo["side"], angelo["trials"], angelo["passed"], angelo["not_started"]),
                         ("Angelo", 5, 4, 0))
        self.assertEqual(angelo["stops"], {"finished": 4, "time_cap": 1})
        self.assertEqual((other["side"], other["passed"], other["typed_share"]), ("Blender MCP (Codex)", 5, [0.0] * 5))
        self.assertEqual(angelo["typed_share"], [1.0, 0.0, 1.0, 2 / 3, None])

    def test_the_row_reads_at_a_glance(self):
        lines = tables.trials_header() + tables.trials_rows(five())
        self.assertEqual(lines[0], "| Side | Passed | Failed | Inconclusive | Seconds | Output tokens | Typed share "
                                   "of mutations | Lookups before the first change | Cost USD | How the turns ended |")
        self.assertEqual(lines[2].split(" | ")[:8], [
            "| Angelo", "4 of 5", "1 of 5", "0 of 5", "160 [144-301]", "4600 [4000-9000] (1 unknown)",
            "83 % [0 %-100 %] (1 no mutation)", "1 [0-6]"])
        self.assertTrue(lines[2].endswith("| finished 4, time_cap 1 |"))
        self.assertEqual(lines[3].split(" | ")[:7], ["| Blender MCP (Codex)", "5 of 5", "0 of 5", "0 of 5",
                                                     "190 [174-331]", "6200 [6000-6400]", "0 % [0 %-0 %]"])
        self.assertTrue(all(line.count("|") == 11 for line in lines))

    def test_an_inconclusive_trial_is_counted_apart_from_passed_and_failed(self):
        sides = five()
        sides[1].update(verdict="inconclusive", reasons=["check inconclusive: roof colour"])
        angelo, _ = tables.trials(sides)
        self.assertEqual((angelo["passed"], angelo["failed"], angelo["inconclusive"], angelo["trials"]), (3, 1, 1, 5))
        self.assertEqual(tables.trials_rows(sides)[0].split(" | ")[1:4], ["3 of 5", "1 of 5", "1 of 5"])
        self.assertEqual(tables.verdicts_line(sides),
                         "Angelo passed 3, failed 1, inconclusive 1 of 5 trials; "
                         "Blender MCP (Codex) passed 5, failed 0, inconclusive 0 of 5 trials")
        self.assertEqual(tables.checks_text({"passed": 3, "total": 5, "failed": [
            {"label": "a"}, {"label": "b", "inconclusive": True}]}), "3 / 1 / 1 of 5")
        self.assertEqual(tables.checks_text({"passed": 5, "total": 5}), "5 / 0 / 0 of 5")
        self.assertEqual(tables.checks_text(None), "0 / 0 / 0 of 0")

    def test_a_trial_that_never_started_counts_against_the_side(self):
        sides = five()[:2] + [{"label": "Angelo 3", "verdict": "failed", "path": None, "checks": None}]
        row, = tables.trials(sides)
        self.assertEqual((row["trials"], row["passed"], row["not_started"], row["seconds"]), (3, 2, 1, [144.0, 229.0]))
        self.assertTrue(tables.trials_rows(sides)[0].endswith("| finished 2, not started 1 |"))

    def test_a_tests_report_adds_the_table_only_for_several_trials(self):
        result = ReportTests().result()
        self.assertNotIn("## Over the trials", report.markdown(result))
        second = copy.deepcopy(result["sides"][1])
        second.update(trial=2, label="Angelo 2", verdict="failed", reasons=["check failed: handrail top"])
        second["path"]["wall_s"] = 229.0
        result["sides"].append(second)
        text = report.markdown(result)
        self.assertIn("## Over the trials", text)
        self.assertIn("| Angelo | 1 of 2 | 1 of 2 | 0 of 2 | 370.5 [229-512] |", text)
        self.assertIn("| Blender MCP (Claude Code) | 0 of 1 | 1 of 1 | 0 of 1 | 431 |", text)

    def test_the_group_gives_each_test_and_side_over_its_trials(self):
        with tempfile.TemporaryDirectory() as base:
            for member in MINIS[:2]:
                Path(base, member).mkdir()
                Path(base, member, report.SCORECARD).write_text(json.dumps({"sides": five()}))
            found = group.summarize(base, "oneshot-minis", {MINIS[0]: 0, MINIS[1]: 1})
            text = Path(base, group.SUMMARY).read_text()
            saved = json.loads(Path(base, group.RESULTS).read_text())
        self.assertEqual(len(found), 20)
        self.assertIn("## Each test and side over its trials", text)
        self.assertIn("| Test | Side | Passed | Failed | Inconclusive | Seconds | Output tokens | Typed share of "
                      "mutations | Lookups before the first change | Cost USD | How the turns ended |", text)
        for member in MINIS[:2]:
            self.assertIn("| `%s` | Angelo | 4 of 5 | 1 of 5 | 0 of 5 | 160 [144-301] | 4600 [4000-9000] (1 unknown) | "
                          "83 %% [0 %%-100 %%] (1 no mutation) | 1 [0-6] |" % member, text)
            self.assertIn("| `%s` | Blender MCP (Codex) | 5 of 5 | 0 of 5 | 0 of 5 | 190 [174-331] |" % member, text)
        self.assertIn("Verdicts over all tests and trials: Angelo passed 8, failed 2, inconclusive 0 of 10 trials; "
                      "Blender MCP (Codex) passed 10, failed 0, inconclusive 0 of 10 trials.", text)
        self.assertLess(text.index("## Each test and side over its trials"), text.index("## Every trial"))
        self.assertIn("| %s | Angelo 5 | failed | 5 / 0 / 0 of 5 | time_cap |" % MINIS[0], text)   # Every trial listed.
        self.assertIn("- `%s`, Angelo 5 (failed): check failed: two rails" % MINIS[0], text)
        over = saved["trials"][MINIS[0]]
        self.assertEqual([(row["side"], row["passed"], row["trials"]) for row in over],
                         [("Angelo", 4, 5), ("Blender MCP (Codex)", 5, 5)])
        self.assertEqual(saved["trials"][MINIS[2]], [])                                    # Not run: no row.


class ProducedTests(unittest.TestCase):
    def test_paths_a_side_states(self):
        said = ('saved /runs/x/footbridge-review.png, then "/Users/a/b c/final.PNG" and ~/out/a.jpeg; '
                '{\\"filepath\\": \\"/var/t/my r.exr\\"} /tmp/a.pngx renders/relative.png')
        self.assertEqual(produced.stated(said), ["/runs/x/footbridge-review.png", "/Users/a/b c/final.PNG",
                                                 os.path.expanduser("~/out/a.jpeg"), "/var/t/my r.exr"])
        self.assertEqual(produced.stated(None), [])

    def test_a_folder_other_programs_write_to_is_never_walked(self):
        self.assertTrue(produced.shared(tempfile.gettempdir()))
        self.assertTrue(produced.shared(os.path.expanduser("~")))
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(produced.shared(folder))                    # A folder of its own inside it is the side's.

    @unittest.skipIf(os.name == "nt", "The one-shot eval harness runs on macOS: it reads POSIX paths from a reply")
    def test_what_a_run_wrote_whatever_it_is_called(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            workspace = Path(folder, "state", "workspace")
            for path in (workspace, Path(folder, "state", "references", "chat"), Path(folder, "qa", "oneshot-final")):
                path.mkdir(parents=True)
            (workspace / "footbridge-review.png").write_bytes(SMALL)
            (workspace / "notes.txt").write_text("not an image")
            (workspace / "earlier.png").write_bytes(PNG)
            os.utime(workspace / "earlier.png", (now - 3600, now - 3600))            # Another run's.
            Path(folder, "state", "references", "chat", "hero.png").write_bytes(PNG)      # An attached reference.
            Path(folder, "qa", "oneshot-final", "harness-final.png").write_bytes(PNG)     # The harness's render.
            Path(folder, "qa", "agent-final.png").write_bytes(PNG)                        # The harness's copy.
            named, unnamed = Path(elsewhere, "probe.png"), Path(elsewhere, "someone-elses.png")
            named.write_bytes(PNG)
            unnamed.write_bytes(PNG)
            found = produced.images([str(workspace), str(Path(folder, "qa")), tempfile.gettempdir()], now - 60,
                                    said="I checked %s" % named)
            self.assertEqual(sorted((entry["file"], tuple(entry["size"]), entry["where"]) for entry in found),
                             [("footbridge-review.png", (1280, 720), "own folder"),
                              ("probe.png", (2560, 1440), "named by the side")])
            self.assertEqual(found[0]["bytes"], len(PNG))
            # A reference the side read by its path is named in its stream, and is still not its work.
            reference = Path(folder, "state", "references", "chat", "hero.png")
            self.assertEqual(produced.images([], now - 60, said=str(reference)), [])
            self.assertEqual(produced.images([str(workspace)], None, limit=1)[0]["file"], "footbridge-review.png")

    def test_a_plain_render_at_the_scenes_render_path(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as folder:
            for name in ("shot.png", "shot0001.png", "shotgun.png", "0001.png"):
                Path(folder, name).write_bytes(SMALL)
            files = lambda path: sorted(entry["file"] for entry in produced.images([], now - 60, path))
            self.assertEqual(files(str(Path(folder, "shot"))), ["shot.png", "shot0001.png"])
            self.assertEqual(files(str(Path(folder, "shot.png"))), ["shot.png", "shot0001.png"])
            self.assertEqual(files(folder + os.sep), ["0001.png"])
            self.assertEqual(produced.images([], now - 60, str(Path(folder, "shot")))[0]["where"], "render path")
        # Blender's default render path is a folder every program's frames land in: nothing there is the side's.
        self.assertEqual(produced._at_render_path(tempfile.gettempdir() + os.sep), [])
        self.assertEqual(produced.images([], now - 60, "/nonexistent/folder/shot"), [])

    JOB = {"job": "j1", "kind": "RENDER_STILL", "status": "done", "reason": None, "seconds": 8.3,
           "output": "footbridge-review.png"}
    REVIEW = {"file": "footbridge-review.png", "size": [1280, 720], "bytes": 1000, "where": "own folder"}

    def test_rendered_something_else_or_rendered_nothing(self):
        name = "footbridge-final.png"
        self.assertEqual(produced.missing_still(name, [self.REVIEW], [self.JOB]),
                         "`footbridge-final.png` is missing, but the side did render: it wrote "
                         "`footbridge-review.png` (1280 x 720); it started 1 render job(s): j1 RENDER_STILL done -> "
                         "`footbridge-review.png`.")
        self.assertIn("the side rendered nothing: no image file was written", produced.missing_still(name, [], []))
        self.assertIn("rendered nothing", produced.missing_still(name, [], None))        # No job system at all.
        small = dict(self.REVIEW, file="Footbridge-Final.png")
        self.assertIn("a file of that name was written but did not pass (`Footbridge-Final.png` (1280 x 720))",
                      produced.missing_still(name, [small], []))
        # A run of an older harness recorded its jobs and not its files: say so, and still show the job.
        self.assertIn("it started 1 render job(s): j1", produced.missing_still(name, None, [self.JOB]))
        self.assertIn("was not recorded in this run", produced.missing_still(name, None, []))

    def test_one_cell_of_the_table(self):
        self.assertEqual(produced.text([self.REVIEW], [self.JOB]),
                         "files: `footbridge-review.png` (1280 x 720); jobs: j1 RENDER_STILL done -> "
                         "`footbridge-review.png`")
        self.assertEqual(produced.text([], []), "files: none; jobs: none")
        self.assertEqual(produced.text(None, [self.JOB]).split(";")[0], "files: not recorded")
        self.assertEqual(produced.text([{"file": "a.exr", "size": None, "bytes": 3}], None), "files: `a.exr`")
        # A report of a run that kept no list looks in what is left of its trial folder, and says so.
        self.assertEqual(produced.text([], [], late=True), "files: none (%s); jobs: none" % produced.LATE)
        self.assertTrue(produced.missing_still("x.png", [self.REVIEW], [], late=True).endswith(
            "it wrote `footbridge-review.png` (1280 x 720) (%s)." % produced.LATE))

    def test_the_report_says_which_it_was(self):
        result = ReportTests().result()
        failed, passed = result["sides"]
        failed.update(produced=[], jobs=None, still_missing=produced.missing_still("footbridge-final.png", [], None))
        passed.update(verdict="failed", reasons=["check failed: the final still was rendered and saved"],
                      produced=[self.REVIEW], jobs=[self.JOB],
                      still_missing=produced.missing_still("footbridge-final.png", [self.REVIEW], [self.JOB]))
        text = report.markdown(result)
        why = text[text.index("## Why a side did not pass"):text.index("## The brief")]
        self.assertIn("  - `footbridge-final.png` is missing and the side rendered nothing:", why)
        self.assertIn("  - `footbridge-final.png` is missing, but the side did render: it wrote "
                      "`footbridge-review.png` (1280 x 720); it started 1 render job(s): j1 RENDER_STILL done", why)
        self.assertIn("| Image files written and render jobs started in the run | files: none | files: "
                      "`footbridge-review.png` (1280 x 720); jobs: j1 RENDER_STILL done -> `footbridge-review.png` |",
                      text)
        with tempfile.TemporaryDirectory() as base:
            Path(base, "oneshot-footbridge").mkdir()
            Path(base, "oneshot-footbridge", report.SCORECARD).write_text(json.dumps(result))
            rows = group.rows(base, ["oneshot-footbridge"])
        self.assertIn("is missing, but the side did render", rows[1]["reasons"][-1])       # The group says it too.

    def test_the_other_side_records_what_it_wrote(self):
        task = specs.tasks("oneshot_footbridge")[0]
        with tempfile.TemporaryDirectory() as folder:
            trial, working = Path(folder, "trial"), Path(folder, "blender-cwd")
            trial.mkdir()
            working.mkdir()
            (working / "footbridge-preview.png").write_bytes(SMALL)
            client = NS(execute=lambda code: {"path": "/tmp/"})
            record = {"host_check": {"success": True, "passed": 1, "total": 1, "results": [{"index": 0, "passed": True}]}}
            output = {"oneshot": {"folder": str(trial), "sent_wall": time.time() - 30}}
            config = {"task_set": "oneshot-footbridge", "claude_cwd": str(Path(folder, "cwd"))}
            oneshot_mcp.grade_outputs(client, task, config, record, output, [str(working), tempfile.gettempdir()])
            self.assertFalse(record["host_check_passed"])
            self.assertEqual([(entry["file"], entry["size"]) for entry in output["oneshot"]["produced"]],
                             [("footbridge-preview.png", [1280, 720])])
            (working / "footbridge-final.png").write_bytes(PNG)
            oneshot_mcp.grade_outputs(client, task, config, record, output, [str(working)])
            # The harness's copy of the passing still (agent-final.png) is not listed as a second picture.
            self.assertEqual(sorted(entry["file"] for entry in output["oneshot"]["produced"]),
                             ["footbridge-final.png", "footbridge-preview.png"])


if __name__ == "__main__":
    unittest.main()
