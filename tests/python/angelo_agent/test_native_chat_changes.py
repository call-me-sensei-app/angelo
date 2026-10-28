# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which tool results the chat counts as scene changes (summary and popup)."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.chat_changes import change_counts, scene_change_report

# Shapes produced today: blender_tools.scene_diff, program_commit and sculpt.change_report.
SCENE_DIFF = {"added": [{"name": "Cube", "type": "MESH", "vertices": 8}], "removed": [{"name": "Old", "type": "MESH"}],
              "modified": [{"name": "Target", "fields": ["matrix"]}], "truncated": False, "scope": "read-back"}
SCULPT = {"added": [], "removed": [], "modified": [{"name": "Head", "fields": ["geometry"]}], "truncated": False}
# angelo_scene_delta: "changes" is a list of observation rows, not a mutation report.
DELTA_ROWS = [{"change": "MODIFIED", "object": {"object": "scn:1:7", "name": "Target"}, "fields": ["transform"]}]


def tool(result, reverted=False):
    return {"role": "tool", "result": result, "reverted": reverted}


class ChatChangeTests(unittest.TestCase):
    def test_scene_delta_rows_are_observations_not_changes(self):
        self.assertIsNone(scene_change_report({"changes": DELTA_ROWS}))
        # The chat summary previously indexed this list as a dict and failed on every redraw.
        self.assertEqual(change_counts([tool({"changes": DELTA_ROWS})]), (0, 0, 0))

    def test_mutation_reports_are_counted_once_per_object(self):
        messages = [tool({"changes": SCENE_DIFF}), tool({"changes": SCULPT}), tool({"changes": DELTA_ROWS}),
                    tool({"changes": SCENE_DIFF}, reverted=True)]
        self.assertEqual(change_counts(messages), (4, 1, 1))  # Cube, Old, Target, Head.

    def test_missing_changes_are_ignored_and_a_drifting_report_is_read_never_dropped(self):
        """Break-it REVIEW-CHAT-GUARD M3: a report whose groups are missing, None or not lists passed the gate and
        vanished from the summary. Any dict that names a group is a report: what it lacks is empty, the gate names it."""
        from angelo_agent.chat_changes import report_problems
        for result in (None, {}, {"changes": None}, {"changes": {}}, {"changes": {"scope": "x"}}, "text result"):
            with self.subTest(result=result):
                self.assertIsNone(scene_change_report(result))
        for changes, problem in (({"added": [{"name": "A", "type": "MESH"}], "removed": []}, "modified is not a list"),
                                 ({"added": "x", "removed": [], "modified": []}, "added is not a list"),
                                 ({"added": None, "removed": (), "modified": []}, "added is not a list"),
                                 ({"added": [], "removed": ({"name": "B", "type": "MESH"},), "modified": []},
                                  "removed is not a list")):
            with self.subTest(changes=changes):
                report = scene_change_report({"changes": changes})
                self.assertEqual({group: type(report[group]) for group in ("added", "removed", "modified")},
                                 {"added": list, "removed": list, "modified": list})
                self.assertTrue(any(line.startswith(problem) for line in report_problems(changes)), problem)
        self.assertEqual(scene_change_report({"changes": {"added": [], "removed": ({"name": "B", "type": "MESH"},),
                                                          "modified": []}})["removed"], [{"name": "B", "type": "MESH"}])
        self.assertEqual(change_counts([{"role": "assistant", "text": "hello"}, tool(None), "not a message",
                                        {"role": "tool", "result": None}]), (0, 0, 0))

    def test_a_report_of_names_or_odd_rows_never_raises_and_counts_what_it_names(self):
        """Director capture, 2026-10-25: the parallel merge reported object names, and every draw of the chat raised."""
        merge = {"added": ["Desk", "Chair"], "removed": [], "modified": [], "truncated": False, "scope": "merge"}
        odd = {"added": [7, None, {"type": "MESH"}, {"name": "Lamp"}], "removed": ["Cube"],
               "modified": [{"name": "Desk"}, "Chair"], "truncated": True}
        self.assertEqual(change_counts([tool({"changes": merge})]), (2, 2, 0))
        # Every entry is a row, an unreadable one as what it is (never silently dropped: M3).
        self.assertEqual(change_counts([tool({"changes": merge}), tool({"changes": odd})]), (7, 6, 1))
        report = scene_change_report({"changes": odd})
        self.assertEqual(report["added"], [{"name": "7", "type": ""}, {"name": "None", "type": ""},
                                           {"name": "{'type': 'MESH'}", "type": "MESH"},
                                           {"type": "", "name": "Lamp"}])   # What the Changes popup prints.
        self.assertEqual(report["modified"], [{"type": "", "name": "Desk", "fields": []},
                                              {"type": "", "name": "Chair", "fields": []}])
        self.assertIs(report["truncated"], True)
        coerced = scene_change_report({"changes": {"added": [{"name": "A", "type": None}], "removed": [],
                                                   "modified": [{"name": "B", "fields": [1, "x"]}]}})
        self.assertEqual((coerced["added"][0]["type"], coerced["modified"][0]["fields"], coerced["truncated"]),
                         ("", ["1", "x"], False))   # L2: strings and a bool, whatever was sent.
        self.assertEqual(scene_change_report({"changes": SCENE_DIFF})["added"], SCENE_DIFF["added"])

    def test_one_schema_names_what_a_drifting_report_breaks(self):
        from angelo_agent.chat_changes import report_problems
        self.assertEqual(report_problems(SCENE_DIFF), [])
        self.assertEqual(report_problems(SCULPT), [])
        self.assertEqual(report_problems(DELTA_ROWS), [])   # No scene change report: nothing to break.
        self.assertEqual(report_problems(None), [])
        names = {"added": ["Desk"], "removed": [{"name": "Cube"}], "modified": [{"name": "Desk", "fields": "x"}],
                 "truncated": "no"}
        self.assertEqual(report_problems(names), ["added[0] is not a record with a name: 'Desk'", "removed[0] has no "
                                                  "type", "modified[0] has no list of field names",
                                                  "truncated is not true or false"])
        files = {"written": ["a.py"], "deleted": []}
        self.assertEqual(report_problems(None, files), [])
        self.assertEqual(report_problems(SCULPT, {"written": "a.py", "deleted": [3]}),
                         ["file_changes.written is not a list of paths", "file_changes.deleted is not a list of paths"])
        self.assertEqual(report_problems(None, ["a.py"]), ["file_changes is not a record: ['a.py']"])

    def test_every_producer_on_this_side_keeps_the_schema(self):
        """The producers a host can run: a typed edit's and a team library import's read-back (scene_diff) and the
        controller's unverified-change report. The rest (staged programs, sculpt, the parallel merge) are checked in
        every native suite: the runner sets STRICT, so a drifting report is the tool's error there."""
        from angelo_agent.chat_changes import STRICT, report_problems
        from angelo_agent.scene_signature import scene_diff
        row = {"name": "Old", "type": "MESH", "matrix": [0] * 16, "vertices": 8, "polygons": 6, "materials": [],
               "modifiers": [], "geometry": "a", "motion": None}
        before = {"objects": {"1": row, "2": dict(row, name="Kept")}, "count": 2, "truncated": False}
        after = {"objects": {"2": dict(row, name="Kept", vertices=9), "3": dict(row, name="New")}, "count": 2,
                 "truncated": False}
        self.assertEqual(report_problems(scene_diff(before, after)), [])
        controller = (ROOT / "scripts/addons_core/angelo_agent/controller.py").read_text(encoding="utf-8")
        self.assertIn('"modified": [{"name": call.arguments.get(\'object\', \'Scene\'), "fields": [\'unverified changes\']}]',
                      controller)
        self.assertIn('chat_changes.report_problems(result.get("changes"), result.get("file_changes"))', controller)
        runner = (ROOT / "tests/python/angelo_agent/run_native_suites.py").read_text(encoding="utf-8")
        self.assertIn('%s="1"' % STRICT, runner)
        self.assertIn('env.pop("%s")' % STRICT, runner)   # M4: LIVE suites read reports as shipped.

    def test_a_drifting_report_is_the_tools_error_in_a_suite_and_kept_otherwise(self):
        import os
        from unittest.mock import patch
        from angelo_agent import chat_changes
        result = {"changes": {"added": ["Desk"], "removed": [], "modified": []}, "partial_changes_possible": True,
                  "recovery": "r", "file_changes": {"written": ["a"], "deleted": []}}
        with patch.dict(os.environ, {chat_changes.STRICT: "1"}):
            strict = chat_changes.drifted("angelo_workers_merge", ["added[0] is not a record"], result)
            failed = chat_changes.drifted("angelo_workers_merge", ["x"], dict(result, error="real", error_code="E"))
        self.assertEqual(strict["error_code"], "CHANGE_REPORT_SCHEMA")
        self.assertIn("angelo_workers_merge", strict["error"])
        # M4: everything the result says is kept; a real error stays the error, the drift is added beside it.
        self.assertEqual({key: strict[key] for key in result}, result)
        self.assertEqual((failed["error"], failed["error_code"], failed["change_report_problems"]), ("real", "E", ["x"]))
        with patch.dict(os.environ, {chat_changes.STRICT: ""}):
            self.assertIs(chat_changes.drifted("angelo_workers_merge", ["x"], result), result)

    def test_no_row_stops_the_chat_or_the_popup(self):
        """Director capture, 2026-10-25: one report raised on every draw and the whole chat showed console text."""
        from unittest.mock import patch
        from types import SimpleNamespace
        from angelo_agent import chat_blocks, chat_guard
        messages = [{"role": "user", "text": "Build a desk"}, {"role": "assistant", "text": "Done"}]
        controller = SimpleNamespace(state={"id": "s", "messages": messages})
        def broken(*args):
            raise TypeError("string indices must be integers, not 'str'")
        with patch.object(chat_blocks, "_block", side_effect=lambda c, ui, message, *rest:
                          broken() if message["role"] == "user" else ("reply", message, (), 30, "k", 0)), \
                patch("angelo_agent.reply_overlay.shown", lambda c, rows: rows):
            blocks = chat_blocks.build(controller, None, set(), {}, 400, 14, 22, 300)
        self.assertEqual([block[0] for block in blocks], ["notice", "reply"])   # The rest as designed.
        self.assertIn("Build a desk", " ".join(value for value, _ in blocks[0][2]))   # The broken row, plainly.
        self.assertLessEqual(len(blocks[0][2]), chat_guard.LINES + 1)   # L3: a label and a few lines, never pages.
        self.assertEqual(chat_guard.call("x", broken, default=(0, 0, 0)), (0, 0, 0))
        view = (ROOT / "scripts/addons_core/angelo_agent/chat_view.py").read_text(encoding="utf-8")
        self.assertIn("chat_guard.call(\"The change summary\", change_counts", view)
        self.assertIn("chat_guard.draw_plain(state, saved, hits_at, role, message, rows, key, error,", view)
        self.assertIn("_errors[area.as_pointer()] = chat_guard.errors()", view)   # -2 N10: every chat's failures.
        self.assertIn("chat_guard.call(\"The workspace cards\", native_cards, controller)", view)   # M2.
        self.assertIn("chat_guard.checked(list(blocks) + chat_blocks.cards(", view)
        self.assertIn("gpu.matrix.push_pop():   # Popped even when drawing raises.", view)   # M5.
        popup = (ROOT / "scripts/addons_core/angelo_agent/ui.py").read_text(encoding="utf-8")
        self.assertIn("changes_popup.draw_message(box, message, chat_view.tool_title)", popup)

    def test_a_row_drawn_plainly_is_still_the_draws_failure(self):
        """Break-it REVIEW-CHAT-GUARD H1: ~20 GUI suites read ``chat_view.last_error is None`` as "the chat drew"; a
        caught row must still fail them, so each draw's failures become its last_error."""
        from angelo_agent import chat_guard
        view = (ROOT / "scripts/addons_core/angelo_agent/chat_view.py").read_text(encoding="utf-8")
        self.assertIn("chat_guard.begin_draw()", view)
        self.assertIn("_errors[area.as_pointer()] = chat_guard.errors()", view)
        self.assertIn('last_error = "; ".join(error for error in _errors.values() if error) or None', view)
        self.assertNotIn("last_error = None\n    except", view)
        chat_guard.begin_draw()
        self.assertIsNone(chat_guard.errors())
        chat_guard.call("The change summary", lambda: {}["x"])
        self.assertIn("The change summary: KeyError", chat_guard.errors())
        chat_guard.begin_draw()
        self.assertIsNone(chat_guard.errors())   # The next draw starts clean.

    def test_a_failure_is_logged_once_per_kind_whatever_its_text(self):
        """L5: the log-once key was the error's text, so a varying message logged every frame."""
        import contextlib
        import io
        from angelo_agent import chat_guard
        def fails(value):
            raise ValueError("row %d" % value)
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed):
            for value in range(50):
                chat_guard.call("A varying row", fails, value)
        self.assertEqual(printed.getvalue().count("A varying row"), 1)
        self.assertLessEqual(len(chat_guard._logged), chat_guard.LOG_LIMIT)

    def test_a_block_of_the_wrong_shape_is_a_plain_row(self):
        """M2: a builder's slip (a short tuple, a height that is no number) stopped the draw loop's unpacking."""
        from angelo_agent import chat_guard
        good = ("reply", {"role": "assistant", "text": "Done"}, (), 30, "k", 0)
        bad = [("reply", {"role": "assistant", "text": "Short"}, (), 30), ("reply", {"text": "No height"}, (), None,
                                                                            "k2", 0), None, ("x", {}, 5, 10, "k3", 0),
               ("reply", {"text": "Endless"}, (), float("inf"), "k4", 0)]   # -2 I1: scrolling would break.
        chat_guard.begin_draw()
        blocks = chat_guard.checked([good] + bad, 400, 14, 22)
        self.assertIs(blocks[0], good)
        self.assertEqual([block[0] for block in blocks[1:]], ["notice"] * 5)
        self.assertTrue(all(isinstance(block[3], (int, float)) for block in blocks))
        self.assertIn("No height", " ".join(value for value, _ in blocks[2][2]))
        self.assertIsNotNone(chat_guard.errors())

    def test_an_approval_or_card_that_cannot_be_built_still_answers(self):
        """M1/M2 and -2 N1: a card that raised left no Approve or Decline, then a plain card titled "Used a tool" for
        every tool. The plain card says what the real one says before its Details (preview_text) and offers Approve only
        when approving needs nothing else the real card holds (a program's review, a script's code, a flow run's
        selection); when its words cannot be built either, Decline alone."""
        import types
        from types import SimpleNamespace
        from unittest.mock import patch
        from angelo_agent import chat_blocks, chat_guard, chat_titles
        view = types.ModuleType("angelo_agent.chat_view")   # chat_view needs blf: its wrap and font, stood in.
        view.wrap = lambda text, width, size, font: [(line, 0) for line in text.split("\n")]
        view._font, view.tool_title = 0, chat_titles.tool_title
        def broken(*args, **kwargs):
            raise KeyError("rows")
        said = "Move Cube\nTo 1, 2, 3 m\n\nUses API credits"
        for name, approvable in (("angelo_set_transform", True), ("angelo_studio_generate", True),
                                 ("angelo_execute_python", False), ("angelo_scene_program", False),
                                 ("angelo_apply_recipe", False)):
            with self.subTest(name=name):
                call = SimpleNamespace(id="c1", name=name, approval=True, arguments={})
                controller = SimpleNamespace(pending={"c1": call}, busy=True, state={"id": "s", "messages": []})
                with patch.dict("sys.modules", {"angelo_agent.chat_view": view}), \
                        patch("angelo_agent.chat_card_draw.approval_block", broken), \
                        patch("angelo_agent.approval_review.preview_text", lambda call: said), \
                        patch("angelo_agent.flow_chat.is_run_review", lambda call: False):
                    blocks = chat_blocks.cards(controller, None, [{"state": "RUNNING", "title": "Bake"}], set(), {},
                                               400, 14, 22, None, None)
                self.assertEqual(blocks[0][0], "approval_plain")
                self.assertEqual(blocks[0][1]["approvable"], approvable)
                self.assertEqual([row[0] for row in blocks[0][2]], said.split("\n"))   # The real card's words.
        with patch.dict("sys.modules", {"angelo_agent.chat_view": view}), \
                patch("angelo_agent.chat_card_draw.approval_block", broken), \
                patch("angelo_agent.approval_review.preview_text", broken), \
                patch("angelo_agent.flow_chat.is_run_review", lambda call: False):
            unsaid = chat_blocks.cards(controller, None, [], set(), {}, 400, 14, 22, None, None)
        self.assertFalse(unsaid[0][1]["approvable"])   # Its words could not be built: Decline only.
        self.assertEqual(unsaid[0][2][0][0], chat_titles.tool_title({"tool": "angelo_apply_recipe"}))
        with patch.dict("sys.modules", {"angelo_agent.chat_view": view}), \
                patch("angelo_agent.chat_card_draw.approval_block", broken), \
                patch("angelo_agent.approval_review.preview_text", lambda call: said), \
                patch("angelo_agent.flow_chat.is_run_review", lambda call: True):
            run = chat_blocks.cards(controller, None, [], set(), {}, 400, 14, 22, None, None)
        self.assertFalse(run[0][1]["approvable"])   # A flow run's selection is the card's to show.
        # The title is the tool's own, never the generic one (the bug: chat_titles reads "tool", not "name").
        transform = SimpleNamespace(name="angelo_set_transform")
        self.assertNotEqual(chat_guard._title(transform), chat_titles.tool_title({"tool": ""}))
        self.assertEqual(chat_guard._title(transform), chat_titles.tool_title({"tool": "angelo_set_transform"}))
        controller = SimpleNamespace(pending={}, busy=False, state={"id": "s", "messages": []})
        with patch("angelo_agent.chat_card_draw.job_block", broken):
            blocks = chat_blocks.cards(controller, None, [{"state": "RUNNING", "title": "Bake"}], set(), {}, 400, 14,
                                       22, None, None)
        self.assertEqual(blocks[0][0], "notice")
        self.assertIn("Bake", " ".join(value for value, _ in blocks[0][2]))
        approval_review = (ROOT / "scripts/addons_core/angelo_agent/approval_review.py").read_text(encoding="utf-8")
        self.assertIn('_PROGRAM_TOOLS = frozenset({"angelo_scene_program", "angelo_apply_recipe"})', approval_review)
        draw = (ROOT / "scripts/addons_core/angelo_agent/chat_guard.py").read_text(encoding="utf-8")
        self.assertIn("call_ = controller.pending.get(key) if role in APPROVALS else None", draw)   # -2 N2.

    def test_a_card_whose_fallback_raises_notes_its_cause_first(self):
        """-2 I2: only the fallback's error was noted, so the root cause was lost."""
        from angelo_agent import chat_guard
        def build():
            raise KeyError("the cause")
        def fallback(error):
            raise ValueError("the fallback")
        chat_guard.begin_draw()
        self.assertIsNone(chat_guard.card("A card", build, fallback))
        errors = chat_guard.errors()
        self.assertLess(errors.index("the cause"), errors.index("the fallback"))

    def test_counting_reads_every_shape_and_keeps_up_with_a_growing_report(self):
        """-2 N3/N6: counts come from names without building rows, kept per report while its groups keep their
        lengths; a group that is a dict reads by name, a single name as one row."""
        from angelo_agent import chat_changes
        report = {"added": [{"name": "A", "type": "MESH"}], "removed": [], "modified": []}
        messages = [tool({"changes": report})]
        self.assertEqual(change_counts(messages), (1, 1, 0))
        report["added"].append({"name": "B", "type": "MESH"})   # The same report, grown.
        self.assertEqual(change_counts(messages), (2, 2, 0))
        shaped = {"added": {"Cube": {"type": "MESH"}, "Lamp": None}, "removed": "Old", "modified": []}
        self.assertEqual(change_counts([tool({"changes": shaped})]), (3, 2, 1))
        rows = chat_changes.scene_change_report({"changes": shaped})
        self.assertEqual([row["name"] for row in rows["added"]], ["Cube", "Lamp"])
        self.assertEqual(rows["removed"], [{"name": "Old", "type": ""}])
        for scalar in (3, 0, True, False, "", 2.5):   # A count or a flag names no object (-3 L6).
            self.assertEqual(change_counts([tool({"changes": {"added": scalar, "removed": [], "modified": []}})]),
                             (0, 0, 0), scalar)
        import time
        big = [tool({"changes": {"added": [{"name": "O%d" % i, "type": "MESH"} for i in range(1000)], "removed": [],
                                 "modified": []}}) for _ in range(50)]
        change_counts(big)
        started = time.perf_counter()
        for _ in range(5):
            change_counts(big)
        self.assertLess((time.perf_counter() - started) / 5, 0.005, "a draw's count of 50 kept reports")

    def test_a_plain_approval_keeps_its_answer_when_a_row_cannot_be_drawn(self):
        """-3 L1: the buttons are registered before the rows are drawn."""
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        from angelo_agent import chat_guard
        pressed = []
        card_draw = ModuleType("angelo_agent.chat_card_draw")
        card_draw.BUTTON_Y = 8
        card_draw._button = lambda state, x, y, label, action, ident, style: pressed.append(action) or x + 100
        view = ModuleType("angelo_agent.chat_view")
        view.TEXT = (1, 1, 1, 1)
        view.text = lambda *args: 1 / 0
        text_module = ModuleType("angelo_agent.ui_text")
        text_module.iface_ = lambda value: value
        with patch.dict(sys.modules, {"angelo_agent.chat_card_draw": card_draw, "angelo_agent.chat_view": view,
                                      "angelo_agent.ui_text": text_module}):
            chat_guard.draw_approval({}, SimpleNamespace(id="c1"), True, [("Move Cube", 0)], 200, 0, 12, 18)
        self.assertEqual(pressed, ["approve", "decline"])

    def test_a_message_that_is_no_record_is_a_plain_row(self):
        """-2 N5: the row's identity was read outside its guard, so a message that is no dict stopped the chat."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from angelo_agent import chat_blocks
        controller = SimpleNamespace(state={"id": "s", "messages": ["not a record", {"role": "assistant", "text": "Hi"}]})
        with patch.object(chat_blocks, "_block", side_effect=lambda c, ui, message, *rest:
                          ("reply", message, (), 30, "k", 0) if isinstance(message, dict) else message["role"]), \
                patch("angelo_agent.reply_overlay.shown", lambda c, rows: rows):
            blocks = chat_blocks.build(controller, None, set(), {}, 400, 14, 22, 300)
        self.assertEqual([block[0] for block in blocks], ["notice", "reply"])

    def test_chat_summary_uses_the_shared_definition(self):
        # chat_view draws with GPU modules; the popup's behaviour is exercised in
        # test_native_dispatch_fixes, which runs its actual draw code.
        source = (ROOT / "scripts/addons_core/angelo_agent/chat_view.py").read_text()
        self.assertIn("from .chat_changes import change_counts", source)
        self.assertNotIn("def change_counts", source)


if __name__ == "__main__":
    unittest.main()
