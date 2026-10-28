# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cold start and returning user by launch position (``bench_cold_warm``), with each trial's first request.

The fixtures have the shapes the drivers record: Claude Code stream lines and
transcripts, Codex notifications, the Codex request log, a trial's
``trial.json`` and ``eval-live-agent.json``. The token counts are those of a
recorded Opus head-to-head of the heavy castle scene (MCP and Angelo writing
the opening), and of its Angelo rerun minutes later (reading it). A cache hit
is shown, never used to classify. Set ``ANGELO_BENCH_RECORDED_RUN`` to a
recorded Claude head-to-head folder to check that every trial of it gets a
first request on both sides. No process, account or model is involved.
"""

import json
import os
import sys
import tempfile
import unittest

from host_test_policy import environment_skip
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_cold_warm as cold_warm  # noqa: E402
import bench_mcp_report as report  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import final_runs_report  # noqa: E402

OPUS = "claude-opus-5-5"


def assistant(message_id, read, write, fresh=2, output=24):
    return {"type": "assistant", "message": {"id": message_id, "model": OPUS, "content": [], "usage": {
        "input_tokens": fresh, "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
        "output_tokens": output}}}


def stream(task, messages, clock=0.0):
    return [{"t_s": clock + index, "task": task, "message": message} for index, message in enumerate(messages)]


def token_usage(input_tokens, cached, written=0):
    last = {"inputTokens": input_tokens, "cachedInputTokens": cached, "cacheWriteInputTokens": written,
            "outputTokens": 76}
    return {"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"total": last, "last": last}}}


def write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def marked(side, trial, found, tasks=(("a", 30000.0, 0.1, 2), ("b", 30000.0, 0.1, 2)), launch="fresh"):
    """One trial's records: tasks of (id, wall ms, list-price USD, model requests), the first marked."""
    records = [{"side": side, "trial": trial, "task": task, "kind": "edit", "status": "completed", "wall_ms": wall,
                "list_price_usd": price, "api_calls": calls} for task, wall, price, calls in tasks]
    return cold_warm.mark(records, found, launch)


COLD_MCP = {"read": 10482, "write": 25951, "input": 36435, "source": "claude stream"}
COLD_ANGELO = {"read": 0, "write": 37366, "input": 37368, "source": "transcript"}
WARM_ANGELO = {"read": 34543, "write": 2828, "input": 37373, "source": "transcript"}


class FirstRequestTests(unittest.TestCase):
    def test_a_claude_code_stream_gives_the_first_responses_final_usage(self):
        lines = stream("flag", [{"type": "system", "subtype": "init"}, assistant("m1", 0, 0),
                                assistant("m1", 10482, 25951), assistant("m2", 36433, 357)])
        lines += stream("trees", [assistant("m3", 37688, 354)], clock=10)
        self.assertEqual(cold_warm.from_claude(lines), COLD_MCP)
        self.assertEqual(cold_warm.from_claude(lines, "trees")["read"], 37688, "Only the named task's lines")
        self.assertIsNone(cold_warm.from_claude(stream("flag", [{"type": "result"}])))

    def test_codex_notifications_give_the_first_request_and_no_write(self):
        lines = stream("brief", [{"method": "turn/started"}, token_usage(21125, 12288),
                                 token_usage(30641, 20736)])
        self.assertEqual(cold_warm.from_codex(lines), {"read": 12288, "write": None, "input": 21125,
                                                       "source": "codex stream"})
        self.assertEqual(cold_warm.from_codex([token_usage(21125, 0, 500)])["write"], 500, "A reported write is kept")

    def test_an_mcp_trial_reads_its_stream_then_its_record_then_a_one_request_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            write_lines(folder / "claude-stream.jsonl", stream("flag", [assistant("m1", 10482, 25951)]))
            self.assertEqual(cold_warm.mcp_trial(folder, {"tasks": [{"task": "flag"}]}), COLD_MCP)
            codex = {"runtime": "codex", "tasks": [{"task": "brief", "first_request": {"input": 17000, "cached": 0}}]}
            self.assertEqual(cold_warm.mcp_trial(folder, codex),
                             {"read": 0, "write": None, "input": 17000, "source": "trial.json first_request"})
            usage = {"input_tokens": 40000, "cached_input_tokens": 38000, "cache_write_input_tokens": 1990}
            one, two = ({"tasks": [{"api_calls": calls, "provider_usage": usage}]} for calls in (1, 2))
            self.assertEqual(cold_warm.mcp_trial(folder / "none", one), {
                "read": 38000, "write": 1990, "input": 40000, "source": "first task's usage (one request)"})
            self.assertIsNone(cold_warm.mcp_trial(folder / "none", two), "Two requests' usage is not the first's")
            self.assertIsNone(cold_warm.mcp_trial(folder, {"tasks": []}))

    def test_an_angelo_trial_reads_its_transcript_then_its_request_log_then_a_one_request_usage(self):
        transcript = [{"type": "queue-operation"}, {"type": "user", "message": {"content": "Move the 'Flag'"}},
                      {"type": "attachment"}, assistant("a1", 0, 37366), {"type": "user", "message": {"content": []}},
                      assistant("a2", 37366, 1413)]
        log = [{"kind": "call", "method": "thread/start", "thread": "w"},
               {"kind": "call", "method": "turn/start", "thread": "w", "warm_up": True},
               {"kind": "event", "method": "thread/tokenUsage/updated", "thread": "w",
                "request": {"input": 25000, "cached": 0, "output": 1}},
               {"kind": "call", "method": "thread/fork", "thread": "c", "source": "w"},
               {"kind": "event", "method": "thread/tokenUsage/updated", "thread": "c",
                "request": {"input": 25200, "cached": 24800, "output": 40}}]
        claude = {"receipt": {"runtime": "claude"}, "provider_usage": {
            "requests": 1, "input_tokens": 37373, "cached_input_tokens": 34543, "cache_write_input_tokens": 2828}}
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            write_lines(folder / "transcript.jsonl", transcript)
            write_lines(folder / "qa/requests.jsonl", log)
            self.assertEqual(cold_warm.angelo_trial(folder / "transcript.jsonl", folder / "qa/requests.jsonl", {}),
                             COLD_ANGELO)
            self.assertEqual(cold_warm.angelo_trial(folder / "missing.jsonl", folder / "qa/requests.jsonl", {}),
                             {"read": 24800, "write": None, "input": 25200, "source": "request log"},
                             "The first chat's first request; the base warm-up is not a chat")
            self.assertEqual(cold_warm.angelo_trial(None, None, claude), dict(WARM_ANGELO, source=(
                "first task's usage (one request)")))
            codex = {"receipt": {"runtime": "codex"}, "provider_usage": dict(claude["provider_usage"],
                                                                             cache_write_input_tokens=0)}
            self.assertIsNone(cold_warm.angelo_trial(None, None, codex)["write"], "Codex's zero write is a default")
            several = {"provider_usage": dict(claude["provider_usage"], requests=2)}
            self.assertIsNone(cold_warm.angelo_trial(None, None, several))


class RuleTests(unittest.TestCase):
    def test_the_launch_position_decides_never_a_cache_hit(self):
        self.assertEqual([cold_warm.launch_of(trial) for trial in (1, 2, 5)], ["fresh"] * 3)
        self.assertEqual([cold_warm.launch_of(trial, returning=True) for trial in (1, 2, 3)],
                         ["fresh", "relaunch", "relaunch"])
        for found in (COLD_MCP, WARM_ANGELO, None):   # Wrote, read or unknown: a fresh launch is a cold start.
            with self.subTest(found=found):
                self.assertEqual(cold_warm.mark([{"task": "a"}], found)[0][cold_warm.MARK]["start"], "cold")
        self.assertEqual(cold_warm.mark([{"task": "a"}], COLD_MCP, "relaunch")[0][cold_warm.MARK]["start"],
                         "returning")

    def test_the_cache_use_is_shown_for_information(self):
        self.assertEqual(cold_warm.cache_state(COLD_MCP), ("wrote", "cache write"))
        self.assertEqual(cold_warm.cache_state(COLD_ANGELO), ("wrote", "cache write"))
        self.assertEqual(cold_warm.cache_state(WARM_ANGELO), ("read", "cache write"))
        self.assertEqual(cold_warm.cache_state({"read": 0, "write": 0}), ("wrote", "cache write"), "Nothing read")
        self.assertEqual(cold_warm.cache_state({"read": 12288, "write": None, "input": 21125}), ("read", "uncached input"))
        for found in (None, {}, {"read": None, "write": 5}, {"read": 5, "write": None, "input": None}):
            with self.subTest(found=found):
                self.assertEqual(cold_warm.cache_state(found), (None, None))


class SummaryTests(unittest.TestCase):
    def test_every_fresh_launch_is_a_cold_start_whatever_its_first_request_read(self):
        records = (marked("mcp", 1, COLD_MCP, (("a", 50000.0, 0.4, 4), ("b", 40000.0, 0.1, 3)))
                   + marked("mcp", 2, dict(COLD_MCP, read=36000, write=400))
                   + marked("angelo", 1, WARM_ANGELO) + marked("angelo", 2, dict(WARM_ANGELO, read=30000, write=1000))
                   + [{"side": "angelo", "trial": 2, "task": "p", "kind": "preview", "wall_ms": 9e9}])
        summary = cold_warm.summarize(records, ("a", "b"))
        self.assertEqual(summary["sides"]["mcp"]["cold"]["trials"], [1, 2])
        self.assertEqual(summary["sides"]["angelo"]["cold"]["trials"], [1, 2], "a cached opening is still a cold start")
        self.assertEqual(summary["sides"]["mcp"]["returning"]["trials"], [])
        first = summary["trials"][0]
        self.assertEqual({key: first[key] for key in ("launch", "start", "cache", "start_wall_ms",
                                                      "start_list_price_usd", "wall_ms", "requests")},
                         {"launch": "fresh", "start": "cold", "cache": "wrote", "start_wall_ms": 50000.0,
                          "start_list_price_usd": 0.4, "wall_ms": 90000.0, "requests": 7})
        self.assertEqual(summary["sides"]["angelo"]["cold"]["wall_ms"]["median"], 60000.0, "The preview is not counted")
        self.assertEqual(summary["cold_vs_cold"], "Cold start vs cold start (first task after a fresh launch, warm-ups "
                         "and any opening wait included): MCP trials 1, 2, median: $0.250, 40.0 s; Angelo trials 1, 2, "
                         "median: $0.100, 30.0 s.")
        self.assertIsNone(summary["returning"])

    def test_warm_ups_and_an_opening_wait_are_in_the_cold_start_once(self):
        records = marked("angelo", 1, COLD_ANGELO, (("a", 30000.0, 0.35, 2), ("b", 20000.0, 0.26, 2)))
        records[0].update(warm_ups=1, warm_up_ms=4000.0, warm_up_list_price_usd=0.05,   # Already in its price.
                          opening={"prepared": True, "waited_s": 1.5})
        records[1].update(warm_ups=1, warm_up_ms=2500.0, warm_up_list_price_usd=0.06)   # A later warm-up.
        row = cold_warm.summarize(records, ("a", "b"))["trials"][0]
        # The 1.5 s wait overlapped the 6.5 s of warm-ups, so the longer, 6.5 s, is added.
        self.assertEqual((row["start_wall_ms"], row["start_list_price_usd"]), (36500.0, 0.41))
        self.assertEqual((row["warm_ups"], row["warm_up_ms"], row["opening_wait_ms"]), (2, 6500.0, 1500.0))
        self.assertEqual((row["wall_ms"], row["list_price_usd"]), (50000.0, 0.61), "the trial total counts it once too")
        records[1]["warm_up_ms"] = None   # A warm-up whose wall time is unknown leaves the start unknown.
        self.assertIsNone(cold_warm.summarize(records, ("a", "b"))["trials"][0]["start_wall_ms"])

    def test_a_returning_user_run_reports_its_relaunches_apart(self):
        records = (marked("mcp", 1, COLD_MCP) + marked("mcp", 2, COLD_MCP, launch="relaunch")
                   + marked("angelo", 1, COLD_ANGELO) + marked("angelo", 2, WARM_ANGELO, launch="relaunch")
                   + marked("angelo", 3, WARM_ANGELO, (("a", 10000.0, 0.02, 1), ("b", 9000.0, 0.02, 1)), "relaunch"))
        summary = cold_warm.summarize(records, ("a", "b"))
        self.assertEqual((summary["sides"]["angelo"]["cold"]["trials"], summary["sides"]["angelo"]["returning"]["trials"]),
                         ([1], [2, 3]))
        self.assertEqual(summary["returning"], "Returning user (first task after a relaunch on a kept profile, warm-ups "
                         "and any opening wait included): MCP trial 2: $0.100, 30.0 s; Angelo trials 2, 3, median: "
                         "$0.060, 20.0 s.")
        self.assertIn("MCP trial 1: $0.100, 30.0 s; Angelo trial 1: $0.100, 30.0 s", summary["cold_vs_cold"])

    def test_a_trial_missing_a_task_or_a_number_has_no_total(self):
        records = marked("mcp", 1, COLD_MCP, (("a", 30000.0, None, 2),)) + marked("angelo", 1, WARM_ANGELO)
        records[-1]["wall_ms"] = None
        trials = cold_warm.summarize(records, ("a", "b"))["trials"]
        self.assertEqual([(row["wall_ms"], row["list_price_usd"], row["requests"]) for row in trials],
                         [(None, None, None), (None, 0.2, 4)])

    def test_rate_limited_trials_and_unrecorded_first_requests(self):
        records = marked("mcp", 1, None) + marked("angelo", 1, COLD_ANGELO)
        for record in records[2:]:
            record["invalid"] = "rate_limited"
        summary = cold_warm.summarize(records, ("a", "b"))
        self.assertEqual(summary["sides"]["mcp"]["cold"]["trials"], [1], "an unknown first request is still a start")
        self.assertEqual(summary["sides"]["angelo"]["trials"], 0)
        self.assertEqual(summary["cold_vs_cold"], "Cold start vs cold start (first task after a fresh launch, warm-ups "
                         "and any opening wait included): MCP trial 1: $0.100, 30.0 s; Angelo: no cold start.")
        text = "\n".join(cold_warm.markdown_lines(summary))
        self.assertIn("| MCP | 1 | cold | unknown / unknown | none | 30.0 | $0.100 | 60.0 | $0.200 | 4 |", text)

    def test_a_summary_recorded_before_launch_positions_renders_as_recorded(self):
        old = {"rule": "A trial is cold when ...", "cold_vs_cold": "Cold vs cold: MCP cold trial 1: $0.2, 60.0 s.",
               "trials": [{"side": "mcp", "trial": 1, "start": "cold"}], "sides": {}}
        self.assertIn("Cold vs cold: MCP cold trial 1", "\n".join(cold_warm.markdown_lines(old)))


class ReportTests(unittest.TestCase):
    def tearDown(self):
        report.use_tasks(live.build_tasks())

    def test_gather_marks_each_trials_first_record_and_summary_md_reports_it(self):
        tasks = live.build_tasks()
        report.use_tasks(tasks)
        first, second = tasks[0], tasks[1]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "mcp-trial-01").mkdir()
            (output / "mcp-trial-01/trial.json").write_text(json.dumps({"side": "mcp", "trial": 1, "tasks": [
                {"side": "mcp", "trial": 1, "task": task["id"], "kind": task["kind"], "status": "completed",
                 "sent_at_s": float(index), "wall_ms": 1000.0} for index, task in enumerate((first, second))]}))
            write_lines(output / "mcp-trial-01/claude-stream.jsonl",
                        stream(first["id"], [assistant("m1", 10482, 25951)])
                        + stream(second["id"], [assistant("m2", 36433, 357)], clock=1))
            harness = {"trials": [{"tasks": [{"task": task["id"], "kind": task["kind"], "status": "completed",
                                              "total_ms": 900.0, "receipt": {"runtime": "claude"}}
                                             for task in (first, second)]}]}
            (output / "angelo-trial-01").mkdir()
            (output / "angelo-trial-01/eval-live-agent.json").write_text(json.dumps(harness))
            write_lines(output / "angelo-trial-01-transcript.jsonl",
                        [{"type": "user", "message": {"content": first["prompt"]}}, assistant("a1", 34543, 2828)])
            records, _ = bench.gather(output, 1, None, tasks)
        self.assertEqual(records[0][cold_warm.MARK], dict(COLD_MCP, launch="fresh", start="cold", cache="wrote",
                                                          classified_by="cache write"))
        self.assertNotIn(cold_warm.MARK, records[1])
        self.assertEqual((records[2][cold_warm.MARK]["start"], records[2][cold_warm.MARK]["cache"]), ("cold", "read"))
        text = report.markdown({"summary": report.summarize(records), "meta": {}, "records": records})
        self.assertIn("| MCP | 1 | cold | 10,482 / 25,951 |", text)
        self.assertIn("| Angelo | 1 | cold | 34,543 / 2,828 |", text)
        self.assertIn("Cold start vs cold start (first task after a fresh launch, warm-ups and any opening wait "
                      "included): MCP trial 1: unknown, 1.0 s; Angelo trial 1: unknown, 0.9 s.", text)

    def test_a_summary_made_before_the_section_still_renders(self):
        summary = report.summarize([])
        del summary["cold_warm"]
        self.assertNotIn("Cold vs cold", report.markdown({"summary": summary, "meta": {}, "records": []}))

    def test_the_campaign_report_gives_each_step_its_cold_warm_line(self):
        steps = [{"id": "h2h-opus-scenario-heavy", "tool": "bench", "sides": ("mcp", "angelo"), "model": OPUS,
                  "runtime": "claude"},
                 {"id": "smoke-opus-gate7", "tool": "eval", "sides": ("angelo",), "model": OPUS, "runtime": "claude"}]
        bench_summary = cold_warm.summarize(marked("mcp", 1, COLD_MCP) + marked("angelo", 1, WARM_ANGELO), ("a", "b"))
        usage = {"requests": 1, "input_tokens": 37368, "cached_input_tokens": 0, "cache_write_input_tokens": 37366,
                 "output_tokens": 600}
        eval_tasks = [{"trial": 1, "task": "t%d" % index, "kind": "edit", "status": "completed", "total_ms": 8000.0,
                       "receipt": {"runtime": "claude"}, "provider_usage": usage} for index in range(2)]
        with tempfile.TemporaryDirectory() as campaign:
            for step in steps:
                (Path(campaign) / step["id"]).mkdir()
            (Path(campaign) / steps[0]["id"] / "bench-mcp-vs-angelo.json").write_text(json.dumps(
                {"summary": {"cold_warm": bench_summary}, "records": []}))
            (Path(campaign) / steps[1]["id"] / "eval-live-agent.json").write_text(json.dumps(
                {"tasks": {"t0": {"runs": 1}}, "trials": [{"tasks": eval_tasks}]}))
            result = final_runs_report.report({}, steps, campaign)
        priced = [dict(task, side="angelo") for task in eval_tasks]
        bench.price_records(priced, OPUS, "claude")
        self.assertEqual(result["cold_warm"][steps[0]["id"]]["cold_vs_cold"], bench_summary["cold_vs_cold"])
        self.assertEqual(result["cold_warm"][steps[1]["id"]]["cold_vs_cold"],
                         "Cold start vs cold start (first task after a fresh launch, warm-ups and any opening wait "
                         "included): MCP: no cold start; Angelo trial 1: $%.3f, 8.0 s." % priced[0]["list_price_usd"])
        self.assertEqual([row["median_s"] for row in result["rows"] if row["step"] == steps[1]["id"]], [8.0],
                         "The live eval's task rows are its trials' tasks, their wall its total_ms")

    @unittest.skipUnless(os.environ.get("ANGELO_BENCH_RECORDED_RUN"),
                         environment_skip("set ANGELO_BENCH_RECORDED_RUN to a recorded Claude head-to-head folder"))
    def test_a_recorded_run_gives_every_trials_first_request_on_both_sides(self):
        output = Path(os.environ["ANGELO_BENCH_RECORDED_RUN"])
        data = json.loads((output / bench.RESULTS).read_text())
        summary = cold_warm.for_folder(output, [{key: value for key, value in record.items() if key != cold_warm.MARK}
                                                for record in data["records"]])
        self.assertEqual({row["side"] for row in summary["trials"]}, {"mcp", "angelo"})
        for row in summary["trials"]:
            with self.subTest(side=row["side"], trial=row["trial"]):
                self.assertIsNotNone(row["read"])
                self.assertIsNotNone(row["write"], "Claude reports its cache writes")
                self.assertEqual(row["start"], "cold")


if __name__ == "__main__":
    unittest.main()
