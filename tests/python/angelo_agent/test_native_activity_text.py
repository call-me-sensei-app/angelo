# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent progress text in the chat: friendly titles, kinds, chips, groups and notices; no bpy."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import activity_text as text  # noqa: E402
from angelo_agent import chat_rows  # noqa: E402
from angelo_agent.ui_text import notice  # noqa: E402

RAW = re.compile(r"angelo_|[A-Z]{2,}_[A-Z]+|[A-Z][a-z]+Error|\b[0-9a-f]{8}-[0-9a-f]{4}-|\{\"|scn:")


def step(tool, status="completed", **extra):
    return dict({"role": "activity", "tool": tool, "status": status, "arguments": {}}, **extra)


class StepTextTests(unittest.TestCase):
    def test_unknown_and_unnamed_tools_never_show_their_id(self):
        for tool in ("angelo_blender_api", "angelo_agent_capabilities", "angelo_brand_new_tool", "mcp__thing__do",
                     "", "angelo_flow_whatever", "angelo_team_sync"):
            title = text.step_title(step(tool))
            self.assertTrue(title)
            self.assertIsNone(RAW.search(title), (tool, title))
            self.assertNotIn(tool or "\0", title)

    def test_known_titles_and_kinds(self):
        self.assertEqual(text.step_title(step("angelo_execute_python")), "Ran Blender Python")
        self.assertEqual(text.step_title(step("angelo_scene_program")), "Scene edit")
        self.assertEqual(text.step_kind(step("angelo_execute_python")), "script")
        self.assertEqual(text.step_kind(step("angelo_view")), "look")
        self.assertEqual(text.step_kind(step("angelo_scene_search")), "search")
        self.assertEqual(text.step_kind(step("angelo_studio_generate")), "generate")
        self.assertEqual(text.step_kind(step("angelo_create_cube")), "create")
        self.assertEqual(text.step_kind(step("angelo_delete_file")), "delete")
        self.assertEqual(text.step_kind(step("never_heard_of_it")), "work")
        for tool in ("angelo_inspect_scene", "angelo_sculpt_batch", "angelo_job_start", "angelo_delegate"):
            self.assertIn(text.step_kind(step(tool)), text.KINDS)

    def test_the_claude_plan_call_reads_as_planning(self):
        plan = step("StructuredOutput", arguments={"reply": "Secret reply", "operations": [{"action": "add"}]})
        self.assertEqual(text.step_title(plan), "Planned the changes")
        self.assertEqual(text.step_kind(plan), "plan")
        self.assertNotIn("StructuredOutput", text.group_title([plan, step("angelo_view")], live=False))
        detail = chat_rows._details(plan, "activity")
        self.assertIn('"add"', detail)
        self.assertNotIn("Secret reply", detail)
        self.assertNotIn("StructuredOutput", detail)

    def test_titles_carry_no_state_suffix_the_chip_shows_it(self):
        failed = step("angelo_execute_python", "failed", result={"error": "IndexError: list index out of range"})
        self.assertNotIn("failed", text.step_title(failed))
        self.assertEqual(text.step_status(failed), "failed")
        self.assertEqual(text.status_tone("failed"), "coral")
        self.assertEqual(text.step_status(step("angelo_execute_python", "approval")), "needs_you")
        self.assertEqual(text.status_tone("needs_you"), "amber")
        self.assertEqual(text.step_status(step("angelo_view", "running")), "running")
        self.assertEqual(text.step_status(step("angelo_view")), "done")
        self.assertEqual(text.status_tone("done"), "teal")
        self.assertEqual(text.step_status(step("angelo_scene_program", result={"status": "preview"})), "preview")
        for status in text.STATUSES:
            self.assertTrue(text.status_label(status))

    def test_friendly_errors_name_no_code(self):
        self.assertEqual(text.friendly_error("object_delete OBJECT_IN_USE: another object depends on it"),
                         "Another object still depends on it")
        self.assertEqual(text.friendly_error("IndexError: list index out of range (line 7)"),
                         "The script reached past the end of a list")
        self.assertEqual(text.friendly_error("KeyError: 'Scale' (line 3)"),
                         "The script asked for something that doesn't exist")
        for unknown in ("WEIRD_CODE happened", "FooBarError: x", "", None, '{"error": "x"}'):
            self.assertEqual(text.friendly_error(unknown), "Something went wrong")


# Leading words that claim a step happened. "Read" is past and present alike in English, but its translations are
# not, so a title for a step that did not happen never starts with it either.
PAST = re.compile(r"^(\w+ed|Ran|Read|Made|Built|Chose|Brought|Began|Found|Got|Took|Sent|Wrote|Saw|Did|Undid|Set)\b")
PROGRESSIVE = re.compile(r"^(?!Bring\b)\w+ing\b")   # "Bring in…" is a command, not a progressive.


class TenseTests(unittest.TestCase):
    """A row reads true for its state (evidence: the release-1.0.6 bug sweep showed "Ran Blender Python" on a script
    still waiting for approval, and above "The script was declined, so it didn't run."). Table-driven over every tool
    the title tables name, both title paths, and every state: done reads in the past, running in the progressive,
    and a step that did not happen names its action, never claiming it happened or is under way."""

    NOT_HAPPENED = (
        {"status": "queued"}, {"status": "approval"},
        {"status": "failed", "result": {"error": "IndexError: list index out of range"}},
        {"status": "failed", "result": {"error_code": "DECLINED", "error": "The user declined this operation."}},
        {"status": "failed", "result": {"error_code": "CANCELLED", "error": "Stopped"}},
        {"status": "completed", "result": {"script_error": "NameError: name 'x' is not defined"}},
        {},   # No status and no result: a planned step or a call under review.
    )
    NOUNS = {"Scene edit", "Recipe edit", "Live web preview", "Media Studio"}

    def tools(self):
        from angelo_agent import chat_titles
        from angelo_agent.studio_spec import STUDIO_NAMES
        names = set(chat_titles.NATIVE) | set(chat_titles.OTHER) | set(text.EXTRA) | set(STUDIO_NAMES)
        return sorted(names | {"angelo_delegate", "angelo_brand_new_tool", "angelo_material_new", "angelo_render_x"})

    def row(self, tool, **state):
        return dict({"role": "activity", "tool": tool, "arguments": {"model": "Opus"}}, **state)

    def titles(self, message):
        from angelo_agent.chat_titles import tool_title
        return text.step_title(message), tool_title(message)

    def test_no_title_claims_a_step_that_did_not_happen(self):
        for tool in self.tools():
            done = text.step_title(step(tool, arguments={"model": "Opus"}))
            for state in self.NOT_HAPPENED:
                message = self.row(tool, **state)
                with self.subTest(tool=tool, state=state):
                    for title in self.titles(message):
                        self.assertIsNone(PAST.match(title), title)
                        self.assertIsNone(PROGRESSIVE.match(title), title)
                    if PAST.match(done):
                        self.assertNotEqual(text.step_title(message), done)

    def test_a_running_step_reads_in_the_progressive_on_both_paths(self):
        for tool in self.tools():
            message = self.row(tool, status="running")
            with self.subTest(tool=tool):
                for title in self.titles(message):
                    lead = title.split(" · ")[0]
                    if lead in self.NOUNS:
                        continue
                    self.assertRegex(title, PROGRESSIVE, "running: progressive, never a command or the past")
                    self.assertNotIn("· running", title, "the progressive already says it")

    def test_every_entry_has_its_three_forms(self):
        """Done is past, in progress is progressive, not done is neither; or one noun phrase for all three."""
        from angelo_agent import chat_titles
        tables = (chat_titles.NATIVE, chat_titles.STUDIO, chat_titles.OTHER, text.EXTRA, text._GENERIC,
                  {"fallback": chat_titles.UNKNOWN, "studio": chat_titles.STUDIO_FALLBACK})
        for table in tables:
            for key, forms in table.items():
                with self.subTest(key=key):
                    self.assertEqual(len(forms), 3)
                    done, running, pending = forms
                    if done == running == pending:
                        self.assertIn(done, self.NOUNS, "one entry for every state must be a noun phrase")
                        continue
                    self.assertTrue(PAST.match(done), done)
                    self.assertTrue(PROGRESSIVE.match(running), running)
                    self.assertIsNone(PAST.match(pending), pending)
                    self.assertIsNone(PROGRESSIVE.match(pending), pending)

    def test_the_script_row_of_the_bug_sweep(self):
        from angelo_agent.chat_titles import tool_title
        python = "angelo_execute_python"
        declined = step(python, "failed", result={"error_code": "DECLINED", "error": "The user declined."})
        self.assertEqual(text.step_title(step(python, "approval")), "Run Blender Python")
        self.assertEqual(text.step_title(declined), "Run Blender Python")
        self.assertEqual(tool_title(declined), "Run Blender Python · declined")
        self.assertEqual(tool_title(step(python, "approval")), "Run Blender Python · needs your approval")
        self.assertEqual(text.step_title(step(python, "running")), "Running Blender Python")
        self.assertEqual(tool_title(step(python, "running")), "Running Blender Python")
        self.assertEqual(text.step_title(step(python, result={"success": True})), "Ran Blender Python")
        self.assertEqual(tool_title(step(python, result={"success": True})), "Ran Blender Python")
        reverted = step(python, result={"success": True}, reverted=True)
        self.assertEqual(tool_title(reverted), "Ran Blender Python · reverted", "it ran, then was reverted")

    def test_the_plan_row_says_planning_while_it_is_written(self):
        plan = "StructuredOutput"
        self.assertEqual(text.step_title(step(plan, "running")), "Planning the changes")
        self.assertEqual(text.step_title(step(plan)), "Planned the changes")
        self.assertEqual(text.step_title(step(plan, "queued")), "Plan the changes")

    def test_a_generation_says_generating_until_its_media_is_ready(self):
        generate = "angelo_studio_generate"
        self.assertEqual(text.step_title(step(generate, "approval")), "Generate media")
        self.assertEqual(text.step_title(step(generate, "running")), "Generating media")
        self.assertEqual(text.step_title(step(generate, result={"status": "running"})), "Generating media")
        self.assertEqual(text.step_title(step(generate, result={"status": "ready"})), "Generated media")

    def test_a_consultation(self):
        from angelo_agent.chat_titles import tool_title
        self.assertEqual(text.step_title(step("angelo_delegate", "queued", arguments={"model": "Sol"})), "Consult Sol")
        self.assertEqual(text.step_title(step("angelo_delegate", "running", arguments={"model": "Sol"})), "Consulting Sol")
        self.assertEqual(tool_title(step("angelo_delegate", "running", arguments={"model": "Sol"})), "Consulting Sol")
        self.assertEqual(text.step_title(step("angelo_delegate", arguments={"model": "Sol"})), "Consulted Sol")
        declined = step("angelo_delegate", "failed", arguments={"model": "Sol"}, result={"error_code": "DECLINED"})
        self.assertEqual(text.step_title(declined), "Consult Sol")

    def test_a_noun_title_keeps_its_working_suffix(self):
        from angelo_agent.chat_titles import tool_title
        self.assertEqual(tool_title(step("angelo_scene_program", "running", result={})), "Scene edit · working")


class GroupTests(unittest.TestCase):
    def test_summary_and_live_titles(self):
        steps = [step("angelo_inspect_scene"), step("angelo_scene_program"), step("angelo_execute_python"),
                 step("angelo_view")]
        self.assertEqual(text.group_title(steps, live=True), "Working on the scene · 4 steps")
        self.assertEqual(text.group_status(steps, live=True), "running")
        self.assertEqual(text.group_title(steps, live=False), "Made 2 changes · Checked 2 things")
        self.assertEqual(text.group_status(steps, live=False), "done")
        reads = [step("angelo_inspect_scene"), step("angelo_view")]
        self.assertEqual(text.group_title(reads, live=False), "Looked at 2 things")
        failed = [step("angelo_inspect_scene"), step("angelo_execute_python", "failed")]
        self.assertEqual(text.group_title(failed, live=False), "Looked at 1 thing · 1 step failed")
        self.assertEqual(text.group_status(failed, live=False), "failed")
        asking = [step("angelo_view"), step("angelo_execute_python", "approval")]
        self.assertEqual(text.group_status(asking, live=False), "needs_you")

    def test_a_declined_or_stopped_step_is_the_persons_choice_never_a_failure(self):
        """A card the person declined, or a step their Stop ended (the controller's DECLINED and CANCELLED results,
        lead and worker chats alike), says so; it is neither a failure nor work done."""
        declined = step("angelo_execute_python", "failed",
                        result={"error_code": "DECLINED", "error": "The user declined this operation. Do not repeat it."})
        stopped = step("angelo_scene_program", "failed", result={"error_code": "CANCELLED", "error": "Stopped"})
        self.assertEqual((text.step_status(declined), text.step_status(stopped)), ("declined", "stopped"))
        self.assertEqual({text.status_tone("declined"), text.status_tone("stopped")}, {"neutral"})
        self.assertEqual(text.group_title([step("angelo_inspect_scene"), declined], live=False),
                         "Looked at 1 thing · 1 step declined")
        self.assertEqual(text.group_status([step("angelo_inspect_scene"), declined], live=False), "done")
        self.assertEqual(text.group_title([declined, stopped, stopped], live=False),
                         "1 step declined · 2 steps stopped")
        broken = step("angelo_execute_python", "failed", result={"error_code": "VALIDATION", "error": "bad"})
        self.assertEqual(text.group_title([step("angelo_create_cube"), broken, declined], live=False),
                         "Made 1 change · 1 step failed · 1 step declined")
        self.assertEqual(text.step_status(step("angelo_execute_python", "approval",
                                               result={"error_code": "DECLINED"})), "needs_you")
        for status in ("declined", "stopped"):   # The row's chip names it; never the "Done" fallback.
            self.assertIn(status, chat_rows._CHIP)
            self.assertEqual(chat_rows._CHIP[status][0], "preview")

    def block(self, role, key, **message):
        return (role, dict({"role": role, "tool": "angelo_view", "status": "completed"}, **message), (), 36, key, 0)

    def test_consecutive_steps_fold_into_one_group(self):
        blocks = [self.block("user", "u"), self.block("activity", "a1"), self.block("thinking", "t1"),
                  self.block("activity", "a2"), self.block("activity", "a3"), self.block("assistant", "r"),
                  self.block("activity", "lone"), self.block("assistant", "r2")]
        folded = chat_rows.group_blocks(blocks, set(), live=False)
        self.assertEqual([b[0] for b in folded], ["user", "activity_group", "assistant", "activity", "assistant"])
        group = folded[1]
        self.assertEqual(group[4], ("group", "a1"))
        self.assertFalse(group[1]["opened"])
        self.assertEqual(group[1]["title"], "Looked at 3 things")
        opened = chat_rows.group_blocks(blocks, {("group", "a1")}, live=False)
        self.assertEqual([b[0] for b in opened][:5], ["user", "activity_group", "activity", "thinking", "activity"])
        self.assertTrue(all(b[1].get("_grouped") for b in opened[2:6]))
        self.assertNotIn("_grouped", blocks[1][1])  # Stored messages are never marked.

    def test_a_step_with_a_card_is_never_folded_away(self):
        blocks = [self.block("activity", "a1"), self.block("activity", "job", tool="angelo_job_start", _attached=True),
                  self.block("activity", "a2"), self.block("activity", "a3")]
        folded = chat_rows.group_blocks(blocks, set(), live=False)
        self.assertEqual([b[4] for b in folded], ["a1", "job", ("group", "a2")])
        jobs = [step("angelo_job_start"), step("angelo_job_start")]
        self.assertEqual(text.group_title(jobs, live=False), "Ran 2 background jobs")

    def test_a_live_group_keeps_its_newest_step_in_view(self):
        blocks = [self.block("user", "u"), self.block("activity", "a1"), self.block("activity", "a2", status="running")]
        folded = chat_rows.group_blocks(blocks, set(), live=True)
        self.assertEqual([b[0] for b in folded], ["user", "activity_group", "activity"])
        self.assertTrue(folded[1][1]["live"])
        self.assertEqual(folded[1][1]["title"], "Working on the scene · 2 steps")
        self.assertEqual(folded[2][4], "a2")


class NoticeTests(unittest.TestCase):
    def test_host_notices_read_as_friendly_headlines(self):
        cases = [
            (dict(notice("The plan stopped before changing the scene; asking for a corrected plan:\n{problem}",
                         problem="Native commit failed; object_delete OBJECT_IN_USE: another object depends on it"),
                  role="system"), "amber", "Another object still depends on it"),
            (dict(notice("The script stopped with an error. Scene data was restored, but image pixels and external "
                         "effects were not verified:\n{error}", error="IndexError: list index out of range (line 7)"),
                  role="system"), "coral", "past the end of a list"),
            # A row stored before 1.0.6 keeps its old English text and still reads as a restored scene.
            (dict(notice("The script stopped with an error. Blender data was restored, but image pixels and external "
                         "effects were not verified:\n{error}", error="IndexError: list index out of range (line 7)"),
                  role="system"), "coral", "past the end of a list"),
            (dict(notice("The script stopped with an error. Some changes may remain; inspect the scene before "
                         "continuing:\n{error}", error="KeyError: 'Scale' (line 3)"), role="system"), "coral", "check"),
            ({"role": "system", "text": "Host-verified result (no further model round was needed):\nCommitted.\n"
              "Host checks passed:\n- object_name on 'A': name 'A'\n- dimensions on 'A': [2, 2, 2]"}, "teal", "2 things"),
            ({"role": "system", "text": "Native plan completed. Tool read-backs are shown above; scene edits have "
              "Undo, external effects do not."}, "teal", "Undo"),
            ({"role": "system", "text": "The scene was replaced ... Chat 905f09c4-0555-48b6-bc4e-83a6e84e747b "
              "history could not be saved", "restore_state": "partial", "restore_checkpoint": "6a7b1eec"}, "amber", "Check"),
            ({"role": "system", "text": "Restored the document ...", "restore_state": "applied",
              "restore_checkpoint": "x"}, "teal", "Undo restore"),
            ({"role": "system", "text": "Restore was not applied ...", "restore_state": "not_applied",
              "restore_checkpoint": "x"}, "coral", "nothing was lost"),
        ]
        for message, tone, phrase in cases:
            view = text.notice_view(message)
            self.assertIsNotNone(view, message["text"])
            self.assertEqual(view["tone"], tone)
            shown = view["headline"] + " " + view["body"]
            self.assertIn(phrase, shown)
            self.assertIsNone(RAW.search(shown), shown)
            self.assertNotIn("line 7", shown)

    def test_job_rows_name_the_work_not_the_job_id(self):
        stored = {"role": "system", "notice": True, "job": "j3", "text": "Job j3 (render) done · frame.png"}
        view = text.notice_view(stored)
        self.assertEqual(view["headline"], "Background job finished")
        self.assertTrue(view["quiet"])
        self.assertNotIn("j3", view["headline"])
        live = text.job_view(stored, "Render", "Finished in 12 s", "done")
        self.assertEqual(live["headline"], "Render: Finished in 12 s")
        failed = text.notice_view(dict(stored, text="Job j4 (bake) failed"))
        self.assertEqual((failed["tone"], failed["quiet"]), ("coral", True))

    def test_other_notices_are_left_alone(self):
        self.assertIsNone(text.notice_view({"role": "system", "text": "API Error: Connection error."}))
        self.assertIsNone(text.notice_view({"role": "tool", "text": "angelo_view: {}"}))


if __name__ == "__main__":
    unittest.main()
