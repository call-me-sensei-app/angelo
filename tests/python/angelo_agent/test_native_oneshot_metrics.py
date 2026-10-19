# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Path metrics of a one-shot test, from recorded receipts, chat rows and client streams.

The fixtures have the shapes the drivers write: an Angelo task record with its
request receipt and chat rows, a Claude Code stream and Codex notifications.
No process, account or model is involved.
"""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import oneshot_metrics as metrics  # noqa: E402
import oneshot_tables as tables  # noqa: E402

SCRIPT = "import bpy\nmesh = bpy.data.meshes.new('Deck')\nbpy.data.objects.new('Footbridge Deck', mesh)\n"
RENDER = "import bpy\nbpy.context.scene.render.filepath = '/tmp/x.png'\nbpy.ops.render.render(write_still=True)\n"
# The chat rows of one Angelo message, as the controller keeps them (``state["messages"]``).
ROWS = [
    {"role": "user", "text": "the brief"},
    {"role": "activity", "tool": "angelo_scene_program", "status": "completed",
     "arguments": {"program": {"operations": [{"op": "a"}, {"op": "b"}, {"op": "c"}]}}, "result": {"success": True}},
    {"role": "activity", "tool": "angelo_execute_python", "status": "failed",
     "arguments": {"description": "Plank the deck", "code": SCRIPT}, "result": {"error": "NameError: name 'x'"}},
    {"role": "activity", "tool": "angelo_execute_python", "status": "completed",
     "arguments": {"description": "Plank the deck", "code": SCRIPT}, "result": {"success": True}},
    {"role": "activity", "tool": "angelo_view", "status": "completed",
     "arguments": {"views": ["SCENE_CAMERA", "TOP"], "shading": "MATERIAL"}, "result": {"views": []}},
    {"role": "activity", "tool": "angelo_view", "status": "completed", "arguments": {"views": ["FRONT"]}, "result": {}},
    {"role": "activity", "tool": "angelo_job_start", "status": "completed",
     "arguments": {"kind": "RENDER_STILL", "output": "renders/footbridge-final.png"}, "result": {"job": "j1"}},
    {"role": "assistant", "text": "Done. The still is in renders/footbridge-final.png."},
]
RECEIPT = {"status": "completed", "runtime": "claude",
           "phases": {"first_text_ms": 4200.0, "first_native_action_ms": 2600.0, "approval_wait_ms": 1840.0,
                      "total_ms": 512000.0},
           "counts": {"model_rounds": 6, "provider_turn_requests": 6, "python_fallbacks": 2,
                      "python_fallback_geometry": 2, "tool_invocations": 6}}
USAGE = {"input_tokens": 1_200_000, "cached_input_tokens": 1_000_000, "cache_write_input_tokens": 100_000,
         "output_tokens": 30_000, "cost_usd": 1.9}


def angelo_record(**extra):
    record = {"task": "brief", "status": "completed", "receipt": RECEIPT, "total_ms": 512000.0,
              "approved": ["angelo_scene_program", "angelo_execute_python", "angelo_execute_python", "angelo_job_start"],
              "declined": [], "provider_usage": USAGE,
              "oneshot": {"user_messages": 1, "sends": 1, "rows": metrics.summarize_rows(ROWS), "stop": None, "watch": {}}}
    record.update(extra)
    return record


def claude_lines():
    def assistant(identity, blocks, output_tokens=100):
        return {"type": "assistant", "message": {"id": identity, "content": blocks, "usage": {
            "input_tokens": 1000, "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 0,
            "output_tokens": output_tokens}}}

    def result(identity, content, error=False):
        return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": identity,
                                                         "content": content, "is_error": error}]}}
    tool = "mcp__blender__execute_blender_code"
    return [
        assistant("m1", [{"type": "tool_use", "id": "t1", "name": tool, "input": {"code": SCRIPT}}]),
        result("t1", [{"type": "text", "text": json.dumps({"status": "error", "message": "NameError"})}]),
        assistant("m2", [{"type": "tool_use", "id": "t2", "name": tool, "input": {"code": SCRIPT}}]),
        result("t2", [{"type": "text", "text": json.dumps({"status": "ok"})}]),
        assistant("m3", [{"type": "tool_use", "id": "t3", "name": tool, "input": {
            "code": RENDER + "space.shading.type = 'RENDERED'\n"}}]),
        result("t3", [{"type": "text", "text": json.dumps({"status": "ok"})}]),
        assistant("m4", [{"type": "tool_use", "id": "t4", "name": "Read",
                          "input": {"file_path": "/run/mcp-trial-01/renders/footbridge-final.png"}}]),
        result("t4", [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]),
        assistant("m5", [{"type": "tool_use", "id": "t5", "name": "mcp__blender__get_screenshot_of_window_as_image",
                          "input": {}}]),
        result("t5", [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]),
        assistant("m6", [{"type": "text", "text": "Done."}], output_tokens=500),
    ]


def mcp_record(**extra):
    names = ["mcp__blender__execute_blender_code"] * 3 + ["Read", "mcp__blender__get_screenshot_of_window_as_image"]
    calls = [{"name": name, "issued_ms": 1000.0 * index, "returned_ms": 1000.0 * index + 300, "is_error": index == 0}
             for index, name in enumerate(names)]
    record = {"task": "brief", "status": "completed", "wall_ms": 431000.0, "first_text_ms": 9000.0,
              "first_tool_call_ms": 3100.0, "api_calls": 6, "tool_calls": calls, "tool_names": names,
              "provider_usage": {"input_tokens": 60000, "cached_input_tokens": 54000, "cache_write_input_tokens": 0,
                                 "output_tokens": 1000, "cost_usd": 0.07},
              "result": {"subtype": "success", "is_error": False, "num_turns": 6, "stop_reason": "end_turn",
                         "permission_denials": []}}
    record.update(extra)
    return record


class UsageTests(unittest.TestCase):
    def test_list_cost_is_tokens_at_published_prices(self):
        # GPT-6 Astra: 10 / 50 / 1 / 12.5 USD per million fresh, output, cached and cache-write tokens.
        usage = {"input_tokens": 1_000_000, "cached_input_tokens": 600_000, "cache_write_input_tokens": 100_000,
                 "output_tokens": 20_000}
        self.assertAlmostEqual(metrics.list_cost(usage, "gpt-6-astra"), 0.3 * 10 + 0.6 * 1 + 0.1 * 12.5 + 0.02 * 50)
        # Claude Opus 5.5: 4 / 20 / 0.20, cache writes 5 (5-minute) or 8 (1-hour) USD per million.
        self.assertAlmostEqual(metrics.list_cost(USAGE, "claude-opus-5-5", "1h"), 0.1 * 4 + 1.0 * 0.2 + 0.1 * 8 + 0.03 * 20)
        self.assertAlmostEqual(metrics.list_cost(USAGE, "claude-opus-5-5", "5m"), 0.1 * 4 + 1.0 * 0.2 + 0.1 * 5 + 0.03 * 20)
        for usage, model in ((None, "gpt-6-astra"), (USAGE, ""), (USAGE, "unknown-model"),
                             ({"input_tokens": 5}, "gpt-6-astra")):
            self.assertIsNone(metrics.list_cost(usage, model))    # Unknown, never zero.

    def test_running_usage_of_both_clients(self):
        usage = metrics.usage_from_claude(claude_lines())
        self.assertEqual((usage["requests"], usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"]),
                         (6, 60000, 54000, 1000))
        partial = claude_lines()[:1] * 3                          # Partial messages repeat an id: counted once.
        self.assertEqual(metrics.usage_from_claude(partial)["requests"], 1)
        self.assertIsNone(metrics.usage_from_claude([{"type": "system"}]))
        updates = [(0.0, {"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"total": {
            "inputTokens": 1000 * n, "cachedInputTokens": 800 * n, "outputTokens": 50 * n}}}}) for n in (1, 2, 3)]
        usage = metrics.usage_from_codex(updates)
        self.assertEqual((usage["requests"], usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"]),
                         (3, 3000, 2400, 150))
        self.assertIsNone(metrics.usage_from_codex([]))


class AngeloPathTests(unittest.TestCase):
    def test_rows_are_kept_as_facts_without_code(self):
        rows = metrics.summarize_rows(ROWS)
        self.assertEqual([row.get("tool") or row["role"] for row in rows],
                         ["user", "angelo_scene_program", "angelo_execute_python", "angelo_execute_python",
                          "angelo_view", "angelo_view", "angelo_job_start", "assistant"])
        self.assertNotIn("bpy.data", json.dumps(rows))            # The code stays in the chat's own files.
        script = rows[3]
        self.assertEqual((script["purpose"], script["categories"], script["renders"]), ("Plank the deck", ["geometry"], False))
        self.assertEqual((rows[1]["typed_mutation"], rows[1]["operations"]), (True, 3))
        self.assertEqual((rows[4]["shading"], rows[4]["views"], rows[5]["shading"]), ("MATERIAL", 2, "SOLID"))
        self.assertEqual((rows[6]["job"], rows[6]["job_id"]), ("RENDER_STILL", "j1"))
        self.assertEqual(rows[2]["error"], "NameError: name 'x'")

    def test_metrics_from_a_recorded_receipt_and_rows(self):
        path = metrics.angelo_path(angelo_record(), "claude-opus-5-5")
        self.assertEqual((path["messages_typed"], path["model_requests"], path["rounds"]), (1, 6, 6))
        self.assertEqual(path["tool_calls"], {"total": 6, "by_tool": {"angelo_execute_python": 2, "angelo_view": 2,
                                                                     "angelo_job_start": 1, "angelo_scene_program": 1}})
        mutations = path["mutations"]
        self.assertEqual((mutations["typed_calls"], mutations["typed_operations"], mutations["script_calls"],
                          mutations["typed_share"]), (1, 3, 1, 0.5))
        self.assertEqual((mutations["script_categories"], mutations["script_purposes"]), ({"geometry": 1}, ["Plank the deck"]))
        self.assertEqual(path["approvals"], {"asked": 4, "approved": 4, "declined": 0, "declined_tools": [],
                                             "declined_by_policy": {}, "wait_s": 1.84})
        errors = path["errors"]
        self.assertEqual((errors["total"], errors["recovered_in_turn"], errors["ended_turn"]), (1, 1, 0))
        looked = path["looked"]
        self.assertEqual((looked["images"], looked["calls"], looked["view_modes"]), (3, 2, {"MATERIAL": 1, "SOLID": 1}))
        self.assertIsNone(looked["delivered_to_model"])           # The receipt does not say: unknown.
        self.assertEqual((looked["rendered"], looked["final_render_seen"]), (["job RENDER_STILL"], False))
        self.assertEqual((path["first_activity_s"], path["wall_s"]), (2.6, 512.0))
        self.assertAlmostEqual(path["cost_list_usd"], 0.1 * 4 + 1.0 * 0.2 + 0.1 * 8 + 0.03 * 20)
        self.assertEqual((path["cost_reported_usd"], path["stop"]), (1.9, {"reason": "finished", "detail": ""}))

    def test_the_live_row_naming_the_work_is_visible_activity(self):
        """A typed plan's provisional steps or a reasoning title count, as the other side's first tool call does."""
        path = metrics.angelo_path(angelo_record(first_progress_ms=1200.0), "claude-opus-5-5")
        self.assertEqual((path["first_activity_s"], path["first_progress_s"]), (1.2, 1.2))
        later = metrics.angelo_path(angelo_record(first_progress_ms=9000.0), "claude-opus-5-5")
        self.assertEqual((later["first_activity_s"], later["first_progress_s"]), (2.6, 9.0))
        self.assertIsNone(metrics.angelo_path(angelo_record(), "claude-opus-5-5")["first_progress_s"],
                          "A record from before the live row was watched")
        lookup = metrics.angelo_path(angelo_record(first_progress_ms=9000.0, first_step_ms=2100.0), "claude-opus-5-5")
        self.assertEqual((lookup["first_activity_s"], lookup["first_progress_s"]), (2.1, 9.0),
                         "A tool call's row (a Codex exec lookup) is visible activity too")

    def test_an_error_that_ends_the_turn_is_not_recovered(self):
        rows = ROWS[:3] + [{"role": "error", "text": "The plan stopped: NameError"}]
        record = angelo_record(receipt=dict(RECEIPT, status="failed"),
                               oneshot={"user_messages": 1, "rows": metrics.summarize_rows(rows), "stop": None})
        path = metrics.angelo_path(record)
        self.assertEqual((path["errors"]["total"], path["errors"]["ended_turn"]), (2, 2))
        self.assertEqual(path["stop"]["reason"], "error")
        self.assertIn("NameError", path["stop"]["detail"])
        self.assertIsNone(path["cost_list_usd"])                  # No model named: unknown.

    def test_stop_reasons(self):
        def reason(**extra):
            return metrics.angelo_path(angelo_record(**extra))["stop"]["reason"]
        self.assertEqual(reason(), "finished")
        self.assertEqual(reason(status="timeout"), "time_cap")
        self.assertEqual(reason(status="invalid"), "provider_limit")
        self.assertEqual(reason(status="error"), "setup_error")
        capped = dict(angelo_record()["oneshot"], stop={"reason": "cost_cap", "cost_usd": 26.0, "cap_usd": 25.0})
        self.assertEqual(reason(oneshot=capped, receipt=dict(RECEIPT, status="interrupted")), "cost_cap")
        refused = dict(angelo_record()["oneshot"], rows=[{"role": "error", "text": "The model refused this request"}])
        self.assertEqual(reason(oneshot=refused, receipt=dict(RECEIPT, status="failed")), "refusal")
        limit = "Native plan iteration limit reached; inspect before continuing"
        rounds = dict(refused, rows=[{"role": "error", "text": limit}])
        self.assertEqual(reason(oneshot=rounds, receipt=dict(RECEIPT, status="failed")), "round_cap")
        rejected = dict(refused, rows=[{"role": "error", "text": "Host rejected the plan before running any operation:\nx"}])
        self.assertEqual(reason(oneshot=rejected, receipt=dict(RECEIPT, status="failed")), "plan_rejected")
        declined = dict(angelo_record()["oneshot"], rows=[])
        self.assertEqual(reason(oneshot=declined, receipt=dict(RECEIPT, status="failed"),
                                declined=["angelo_write_file"]), "approval_declined")
        self.assertEqual(metrics.angelo_path(angelo_record(job_wait_timed_out=True))["stop"]["detail"],
                         "a render job was still running at the job cap")
        self.assertTrue({"finished", "time_cap", "cost_cap", "refusal", "error"} <= set(metrics.STOP_REASONS))

    def test_a_declined_call_is_an_approval_not_an_error(self):
        rows = ROWS[:2] + [{"role": "activity", "tool": "angelo_write_file", "status": "failed", "arguments": {},
                            "result": {"error": "The user declined this operation. Do not repeat it."}}] + ROWS[3:]
        record = angelo_record(declined=["angelo_write_file"],
                               oneshot={"user_messages": 1, "rows": metrics.summarize_rows(rows), "stop": None})
        path = metrics.angelo_path(record)
        self.assertEqual((path["errors"]["total"], path["approvals"]["declined"], path["approvals"]["declined_tools"]),
                         (0, 1, ["angelo_write_file"]))
        self.assertEqual(path["stop"]["reason"], "finished")     # The agent carried on and ended its turn.

    def test_cache_writes_are_priced_at_the_sessions_own_ttl(self):
        five = angelo_record(receipt=dict(RECEIPT, route={"cache_ttl": "5m"}))
        self.assertAlmostEqual(metrics.angelo_path(five, "claude-opus-5-5")["cost_list_usd"],
                               0.1 * 4 + 1.0 * 0.2 + 0.1 * 5 + 0.03 * 20)

    def test_codex_rounds_are_unknown_and_requests_come_from_the_watch(self):
        record = angelo_record(receipt=dict(RECEIPT, runtime="codex", counts={"provider_turn_requests": 1}))
        record["oneshot"]["watch"] = {"requests": 14}
        path = metrics.angelo_path(record, "gpt-6-astra")
        self.assertEqual((path["rounds"], path["model_requests"]), (None, 14))


class McpPathTests(unittest.TestCase):
    def test_metrics_from_a_recorded_claude_code_stream(self):
        state = {"messages": 1, "stop": None}
        path = metrics.mcp_path(mcp_record(), claude_lines(), state, "claude-opus-5-5", "footbridge-final.png")
        self.assertEqual((path["messages_typed"], path["model_requests"], path["rounds"]), (1, 6, 6))
        self.assertEqual(path["tool_calls"]["by_tool"], {"execute_blender_code": 3, "Read": 1,
                                                         "get_screenshot_of_window_as_image": 1})
        mutations = path["mutations"]
        self.assertEqual((mutations["typed_calls"], mutations["script_calls"], mutations["typed_share"]), (0, 2, 0.0))
        self.assertEqual(mutations["script_categories"]["geometry"], 2)
        self.assertEqual((path["approvals"]["note"], path["approvals"]["denied_by_client"]), ("no approval gate", 0))
        errors = path["errors"]
        self.assertEqual((errors["total"], errors["recovered_in_turn"], errors["ended_turn"]), (1, 1, 0))
        looked = path["looked"]
        self.assertEqual((looked["images"], looked["delivered_to_model"], looked["rendered"]), (2, 2, ["script"]))
        self.assertEqual((looked["shading_set_in_scripts"], looked["final_render_seen"]), ({"RENDERED": 1}, True))
        self.assertEqual((path["first_activity_s"], path["wall_s"], path["stop"]["reason"]), (3.1, 431.0, "finished"))
        self.assertAlmostEqual(path["cost_list_usd"], (6000 * 4 + 54000 * 0.2 + 1000 * 20) / 1e6, places=4)

    def test_denied_reads_and_stop_reasons_of_the_client(self):
        denied = mcp_record(result={"subtype": "success",
                                    "permission_denials": [{"tool_name": "Read"}, {"tool_name": "Bash"}]})
        approvals = metrics.mcp_path(denied, [], {"messages": 1})["approvals"]
        self.assertEqual((approvals["denied_by_client"], approvals["denied_tools"]), (2, ["Bash", "Read"]))

        def reason(state=None, **extra):
            return metrics.mcp_path(mcp_record(**extra), [], state or {})["stop"]["reason"]
        self.assertEqual(reason(status="timeout"), "time_cap")
        self.assertEqual(reason(status="failed", result={"subtype": "error_max_turns"}), "round_cap")
        self.assertEqual(reason(status="failed", result={"subtype": "success", "stop_reason": "refusal"}), "refusal")
        self.assertEqual(reason(status="failed", result={"subtype": "error_during_execution"}), "error")
        self.assertEqual(reason(status="failed", abort="Claude Code exited"), "runtime_exit")
        self.assertEqual(reason(status="invalid"), "provider_limit")
        self.assertEqual(reason({"stop": {"reason": "cost_cap"}}, status="failed"), "cost_cap")

    def test_metrics_from_recorded_codex_notifications(self):
        def item(kind, identity, **fields):
            return {"t_s": 1.0, "task": "brief", "message": {"method": "item/completed", "params": {
                "item": dict(type=kind, id=identity, **fields)}}}
        lines = [item("mcpToolCall", "a", server="blender", tool="execute_blender_code", arguments={"code": RENDER},
                      result={"content": [{"type": "text", "text": "ok"}]}),
                 item("mcpToolCall", "b", server="blender", tool="get_screenshot_of_window_as_image", arguments={},
                      result={"content": [{"type": "image", "data": "AAAA"}]}),
                 item("imageView", "c", path="/run/tmp/footbridge-final.png")]
        names = ["mcp__blender__execute_blender_code", "mcp__blender__get_screenshot_of_window_as_image"]
        record = {"runtime": "codex", "status": "completed", "wall_ms": 9000.0, "api_calls": 4, "tool_names": names,
                  "tool_calls": [{"name": name, "returned_ms": 5.0, "is_error": False} for name in names],
                  "result": {"subtype": "success", "is_error": False}, "provider_usage": None}
        path = metrics.mcp_path(record, lines, {"messages": 1}, "gpt-6-astra", "footbridge-final.png")
        self.assertEqual((path["looked"]["images"], path["looked"]["delivered_to_model"]), (2, 2))
        self.assertEqual((path["looked"]["rendered"], path["looked"]["final_render_seen"]), (["script"], True))
        self.assertEqual((path["rounds"], path["usage"], path["cost_list_usd"]), (None, None, None))
        self.assertEqual(path["mutations"]["script_categories"]["render_world"], 1)


class TableTests(unittest.TestCase):
    def sides(self):
        angelo = metrics.angelo_path(angelo_record(), "claude-opus-5-5")
        other = metrics.mcp_path(mcp_record(), claude_lines(), {"messages": 1}, "claude-opus-5-5", "footbridge-final.png")
        return angelo, other

    def test_the_table_puts_the_sides_next_to_each_other(self):
        angelo, other = self.sides()
        lines = tables.table([("Angelo 1", angelo, {"verdict": "passed", "checks": "26 / 26"}),
                               ("Blender MCP (Claude Code) 1", other, {"verdict": "failed", "checks": "24 / 26"})])
        self.assertEqual(lines[0], "| Path metric | Angelo 1 | Blender MCP (Claude Code) 1 |")
        self.assertEqual(lines[2], "| Verdict (closed checks only) | passed | failed |")
        self.assertEqual(len(lines), 5 + len(tables.ROWS))   # And the cost row, on the comparison's one basis.
        self.assertTrue(all(line.count("|") == 4 for line in lines))
        text = "\n".join(lines)
        for expected in ("| Messages typed | 1 | 1 |", "| Stop reason | finished | finished |",
                         "| Mutations: typed calls / script calls (typed share) | 1 / 1 (50 %) | 0 / 2 (0 %) |",
                         "| Approvals asked / approved / declined | 4 / 4 / 0 | no approval gate |",
                         "| Seconds waited on approvals | 1.84 | – |",
                         "| Errors: total / recovered in the turn / ended the turn | 1 / 1 / 0 | 1 / 1 / 0 |",
                         "| Saw its final render | no | yes |", "| Rounds | 6 | 6 |",
                         "| Time to first visible activity, s | 2.6 | 3.1 |", "| Wall time, s | 512.0 | 431.0 |",
                         "| Live row first named the work, s | unknown | – |"):
            self.assertIn(expected, text)
        self.assertIn("3 image(s) (MATERIAL 1, SOLID 1); reached the model: unknown", text)
        self.assertIn("2 image(s); reached the model: 2", text)
        self.assertIn("| Rendered in the turn | job RENDER_STILL | script |", text)

    def test_the_intended_path_line(self):
        angelo, _ = self.sides()
        self.assertEqual(tables.intended_path(angelo, 26, 26),
                         "Intended path: one message: yes (1 typed); error-ended turns: 0 (stop reason: finished); "
                         "mutations by typed tools: 50 % (1 of 2 calls); checks passed: 26 of 26 (0 inconclusive).")
        stopped = metrics.angelo_path(angelo_record(status="timeout"))
        self.assertIn("error-ended turns: 1 (stop reason: time_cap)", tables.intended_path(stopped, 3, 26))

    def test_what_is_unknown_names_what_would_expose_it(self):
        self.assertTrue({"angelo.image_delivered_to_model", "angelo.final_render_seen", "angelo.codex_rounds",
                         "mcp.view_mode"} <= set(metrics.UNKNOWN))
        self.assertTrue(all(len(text) > 40 for text in metrics.UNKNOWN.values()))


if __name__ == "__main__":
    unittest.main()
