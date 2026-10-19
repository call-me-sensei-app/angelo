# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The final render a grader judges: the file the agent names as final, by one rule on every side (no Blender).

``final_render.decide``: the final reply, then the last render's job receipt,
then the requested name. Angelo never overwrites, so a re-render to a taken
name writes ``tree-final-j3.png``; without this rule the grader judged
``tree-final.png`` (the older render).
"""

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

import bench_mcp_scenario  # noqa: E402
import final_render  # noqa: E402
import oneshot_checks  # noqa: E402
import oneshot_mcp  # noqa: E402
import oneshot_report  # noqa: E402
import scenario_session  # noqa: E402
import scenario_specs as specs  # noqa: E402


def png(width, height):
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + \
        b"\x08\x02\x00\x00\x00"


WANTED = [{"name": "tree-final.png", "min_size": [2560, 1440], "label": "the final still"}]


def write(folder, name, size, when):
    path = Path(folder, name)
    path.write_bytes(png(*size))
    os.utime(path, (when, when))
    return str(path)


class RuleTests(unittest.TestCase):
    def test_a_name_answers_to_the_request_only_as_itself_or_as_a_taken_names_suffix(self):
        self.assertTrue(final_render.answers("Tree-Final.PNG", "tree-final.png", taken=False))
        for name in ("tree-final-j3.png", "tree-final-j3-2.png", "tree-final_v2.png", "tree-final0001.png"):
            self.assertTrue(final_render.answers(name, "tree-final.png", taken=True), name)
            self.assertFalse(final_render.answers(name, "tree-final.png", taken=False), name)
        for name in ("tree_final.png", "tree-final.jpg", "render.png", "final-tree.png"):
            self.assertFalse(final_render.answers(name, "tree-final.png", taken=True), name)

    def test_names_a_reply_gives(self):
        reply = ("Final: `tree-final-j4.png` in the workspace. The earlier **tree-final.png** was kept; see "
                 "/tmp/x/Tree-Final-j4.png and a preview (preview.jpg).")
        self.assertEqual(final_render.mentioned(reply), ["tree-final-j4.png", "tree-final.png", "preview.jpg"])

    def test_the_reply_decides_then_the_receipt_then_the_requested_name(self):
        with tempfile.TemporaryDirectory() as folder:
            older = write(folder, "tree-final.png", (2560, 1440), 1000.0)
            newer = write(folder, "tree-final-j3.png", (2560, 1440), 2000.0)
            paths = [older, newer]
            # Both named: the one written last is the final render.
            reply = "The final PNG is `tree-final-j3.png`. `tree-final.png` already existed and is unchanged."
            decided = final_render.decide("tree-final.png", paths, reply)
            self.assertEqual(decided, {"file": "tree-final-j3.png", "source": "reply",
                                       "named": ["tree-final-j3.png", "tree-final.png"], "newest": True,
                                       "newest_file": None})
            receipts = [{"kind": "RENDER_STILL", "status": "done", "output": "tree-final.png", "wrote": "tree-final.png"},
                        {"kind": "RENDER_STILL", "status": "done", "output": "tree-final.png",
                         "wrote": "tree-final-j3.png"}]
            self.assertEqual(final_render.decide("tree-final.png", paths, "Done.", receipts)["source"], "job receipt")
            self.assertEqual(final_render.decide("tree-final.png", paths, "Done.", receipts)["file"],
                             "tree-final-j3.png")
            # A render asked for another name is not a render of the requested one.
            other = [{"kind": "RENDER_STILL", "status": "done", "output": "tree-final-v2.png",
                      "wrote": "tree-final-v2.png"}]
            fallback = final_render.decide("tree-final.png", paths, "Done.", other)
            self.assertEqual((fallback["file"], fallback["source"], fallback["newest"], fallback["newest_file"]),
                             ("tree-final.png", "requested path", False, "tree-final-j3.png"))

    def test_an_older_file_the_reply_names_is_judged_and_recorded_as_not_the_newest(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [write(folder, "tree-final.png", (2560, 1440), 1000.0),
                     write(folder, "tree-final-j2.png", (2560, 1440), 2000.0)]
            decided = final_render.decide("tree-final.png", paths, "Saved PNG: `tree-final.png`.")
            self.assertEqual((decided["file"], decided["source"], decided["newest"], decided["newest_file"]),
                             ("tree-final.png", "reply", False, "tree-final-j2.png"))
            self.assertIn("not the newest render: tree-final-j2.png is newer", final_render.describe(decided))

    def test_a_name_the_brief_did_not_ask_for_never_stands_in(self):
        with tempfile.TemporaryDirectory() as folder:
            # No file of the requested name: a suffixed one does not answer to it, whatever the reply says.
            paths = [write(folder, "tree-final-2.png", (2560, 1440), 2000.0), write(folder, "render.png", (8, 8), 2000.0)]
            for reply in ("Final: tree-final-2.png", "Final: render.png"):
                decided = final_render.decide("tree-final.png", paths, reply)
                self.assertEqual((decided["file"], decided["source"], decided["newest"]),
                                 ("tree-final.png", "requested path", None), reply)

    def test_a_named_file_from_an_earlier_run_is_not_this_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [write(folder, "tree-final.png", (2560, 1440), 5000.0),
                     write(folder, "tree-final-j3.png", (2560, 1440), 1000.0)]   # Before the message was sent.
            decided = final_render.decide("tree-final.png", paths, "Final: tree-final-j3.png", since=4000.0)
            self.assertEqual((decided["file"], decided["source"]), ("tree-final.png", "requested path"))


class OneShotCheckTests(unittest.TestCase):
    """The one-shot output check judges the named file, on the Angelo side and the Blender MCP side alike."""

    def test_the_named_re_render_is_judged_not_the_older_file_at_the_requested_name(self):
        with tempfile.TemporaryDirectory() as folder:
            now = time.time()
            write(folder, "tree-final.png", (2560, 1440), now - 20)
            write(folder, "tree-final-j3.png", (1280, 720), now - 10)     # The named re-render is too small.
            before, = oneshot_checks.evaluate_outputs(WANTED, [folder], now - 60)
            self.assertEqual((before["passed"], before["details"]["final"]["source"]), (True, "requested path"))
            self.assertFalse(before["details"]["final"]["newest"])
            entry, = oneshot_checks.evaluate_outputs(WANTED, [folder], now - 60, reply="Final: tree-final-j3.png")
            self.assertFalse(entry["passed"])
            self.assertEqual(entry["details"]["files"][0]["problems"], ["size"])
            self.assertEqual(entry["details"]["final"]["file"], "tree-final-j3.png")
            write(folder, "tree-final-j3.png", (2560, 1440), now - 10)
            entry, = oneshot_checks.evaluate_outputs(WANTED, [folder], now - 60, reply="Final: tree-final-j3.png")
            self.assertEqual((entry["passed"], os.path.basename(entry["path"])), (True, "tree-final-j3.png"))

    def test_the_mcp_side_judges_the_file_its_whole_final_reply_names(self):
        task = specs.tasks("oneshot_tree")[0]
        with tempfile.TemporaryDirectory() as folder:
            trial, working = Path(folder, "trial"), Path(folder, "blender-cwd")
            trial.mkdir()
            working.mkdir()
            now = time.time()
            write(working, "tree-final.png", (2560, 1440), now - 20)
            write(working, "tree-final-2.png", (2560, 1440), now - 10)
            reply = "x" * 2000 + " The final render is tree-final-2.png."   # Past the 1,500 ``result`` keeps.
            record = {"host_check": {"success": True, "passed": 1, "total": 1, "results": [{"index": 0, "passed": True}]},
                      "result": {"reply": reply[:1500]}, "final_reply": reply}
            output = {"oneshot": {"folder": str(trial), "sent_wall": now - 30}}
            config = {"task_set": "oneshot-tree", "claude_cwd": str(Path(folder, "cwd"))}
            oneshot_mcp.grade_outputs(NS(execute=lambda code: {"path": "/tmp/"}), task, config, record, output,
                                      [str(working)])
            entry = record["host_check"]["results"][-1]
            self.assertEqual((entry["details"]["file"], entry["details"]["final"]["source"]),
                             ("tree-final-2.png", "reply"))
            self.assertEqual(bench_mcp_scenario.final_reply({"result": {"reply": "short"}}), "short")

    def test_angelo_job_records_keep_the_name_a_job_wrote(self):
        self.assertEqual(scenario_session.written({"receipt": {"output": "tree-final-j3.png", "files": 1}}),
                         "tree-final-j3.png")
        self.assertEqual(scenario_session.written({"receipt": {"files": 1},
                                                   "detail": {"output_path": "/w/tree-final.png"}}), "tree-final.png")
        self.assertIsNone(scenario_session.written({"receipt": {}}))
        rows = [{"role": "user", "text": "Build"}, {"role": "assistant", "text": "Working"},
                {"role": "activity", "tool": "angelo_job_start"}, {"role": "assistant", "text": "Final: x.png"}]
        self.assertEqual(scenario_session.final_reply(rows), "Final: x.png")
        self.assertEqual(scenario_session.final_reply([]), "")


class StatedPathTests(unittest.TestCase):
    def test_the_linear_scan_finds_what_the_old_pattern_found(self):
        import random
        import re

        def pattern(text, name):   # The scan as it was: quadratic on a long stretch without a space.
            found = []
            for match in re.finditer(r"(~?/[^\s\"'`<>|*?\\]*%s)" % re.escape(name), text, re.IGNORECASE):
                path = os.path.expanduser(match.group(1))
                if path not in found:
                    found.append(path)
            return found[:50]
        pieces = ["/", "~", "a", " ", "\"", "'", "-", ".", "\\", "|", "\n", "x.png", "X.PNG", "tree-final.png",
                  "Tree-Final.png", "tree-final", ".png", "~/"]
        generator = random.Random(7)
        for _ in range(3000):
            text = "".join(generator.choice(pieces) for _ in range(generator.randint(0, 30)))
            for name in ("x.png", "tree-final.png"):
                self.assertEqual(oneshot_checks.mentioned_paths(text, name), pattern(text, name), (text, name))

    def test_a_stream_with_base64_images_is_read_whole(self):
        stream = "abc " + "A/b+" * 500000 + " saved to /w/tree-final.png."   # 2 MB without a space first.
        self.assertEqual(oneshot_checks.mentioned_paths(stream, "tree-final.png"), ["/w/tree-final.png"])


class ReportTests(unittest.TestCase):
    def test_the_report_says_which_still_was_judged_and_why(self):
        from test_native_oneshot_report import ReportTests as Base
        result = Base().result()
        result["sides"][1]["final_still_judged"] = {"file": "tree-final-j3.png", "source": "reply",
                                                    "named": ["tree-final-j3.png"], "newest": True,
                                                    "newest_file": None}
        text = oneshot_report.markdown(result)
        self.assertIn("The still judged is the file the agent names as final", text)
        self.assertIn("- Angelo 1: tree-final-j3.png (named in the reply; the newest render)", text)
        self.assertIn("- Blender MCP (Claude Code) 1: the requested name (recorded before this rule)", text)
        self.assertEqual(json.loads(json.dumps(result))["sides"][1]["final_still_judged"]["source"], "reply")


if __name__ == "__main__":
    unittest.main()
