# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Request-owned reviews: exact controls, cooperative rows and stale manual hits.

Pure host font stand-ins establish work ownership and output compatibility;
the Blender fixture separately measures real BLF/GPU and event dispatch.
"""

import ast
import copy
import importlib
import itertools
from pathlib import Path
import sys
import random
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import approval_review as review, approvals, program_checks, rtl_text, text_layout  # noqa: E402
from angelo_agent.controller import Controller, ToolCall, ToolDispatchBudget  # noqa: E402
from angelo_agent.program_context import ProgramBinding  # noqa: E402
from test_native_complete_checks import (canonical, cube, material_binding_facts,
                                         maximum_material_program, property_set)  # noqa: E402
from test_native_round_savings import RecipeExecutor  # noqa: E402

DRAIN_BUDGET = 0.5  # seconds per layout call while a test drains a review


class Font:
    def __init__(self):
        self.largest_probe = 0

    def size(self, font, size):
        pass

    def dimensions(self, font, value):
        self.largest_probe = max(self.largest_probe, len(value))
        return text_layout.columns(value), 1


FONT = Font()
with patch.dict(sys.modules, {"blf": FONT}):
    canvas_wrap = importlib.import_module("angelo_agent.canvas_wrap")
iter_wrap = canvas_wrap.iter_wrap


def near_budget_program():
    return canonical([property_set({"setting_%02d" % index: ["x" * 1010] * 32
                                    for index in range(32)})], bound=True)


def call_for(arguments=None, recipe=False):
    program = canonical([cube(0)]) if arguments is None else arguments
    call = ToolCall("review-call", "angelo_apply_recipe" if recipe else "angelo_scene_program",
                    {"recipe": "material-setup", "parameters": {}} if recipe else program, ("document", 1))
    if recipe:
        call.program_arguments = program
    facts = material_binding_facts(program) if program_checks.needs_material_binding(program) else None
    obligations = program_checks.resolve_obligations(program, facts)
    call.precondition = ProgramBinding(1, 2, (1, 0.), 3, (), None, obligations.checks_digest, obligations)
    call.precondition = review.prepare_review(call, call.precondition)
    return call


def drain(call, width=80, owner=1, size=14):
    # A drain, not a pacing test (the pacing tests below drive the clock): a budget long enough for one step on
    # any host, since the per-call work before the first step took longer than 4 ms on Windows.
    while review.advance_review_layout(call, owner, width, size, 0, 1, iter_wrap, time.monotonic() + DRAIN_BUDGET):
        pass
    return review.review_rows(call, owner, width, size, 0, 1, None, True)


def old_wrap(value, width, size=14, font=0, ordered=True):
    """The previous canvas algorithm, retained only as a parity oracle."""
    if ordered and rtl_text.has_rtl(value):
        return tuple((row, 0) for row in old_rtl_rows(value, lambda part: FONT.dimensions(font, part)[0], width))
    output, offset = [], 0
    for paragraph in value.split("\n"):
        remaining = paragraph
        if not remaining:
            output.append(("", offset))
        while remaining:
            low, high = 1, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if FONT.dimensions(font, remaining[:middle])[0] <= width:
                    low = middle
                else:
                    high = middle - 1
            cut = text_layout.line_break(remaining, low)
            output.append((remaining[:cut], offset))
            offset += cut
            remaining = remaining[cut:]
        offset += 1
    return tuple(output)


def old_rtl_rows(value, measure, width):
    rows = []
    for paragraph in value.split("\n"):
        logical = rtl_text.to_logical(paragraph) if rtl_text.is_visual(paragraph) else paragraph
        if not logical:
            rows.append("")
            continue
        remaining = logical
        while remaining:
            low, high = 1, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if measure(rtl_text.to_visual(remaining[:middle])) <= width:
                    low = middle
                else:
                    high = middle - 1
            cut = low
            if cut < len(remaining):
                space = remaining.rfind(" ", 0, cut + 1)
                if space > 0:
                    cut = space + 1
            rows.append(rtl_text.to_visual(remaining[:cut].rstrip()))
            remaining = remaining[cut:]
    return rows


def old_numbers_by_latin(classes):
    """The previous nearest-direction scans, an independent parity oracle."""
    count, position = len(classes), 0
    while position < count:
        if classes[position] != "D":
            position += 1
            continue
        end = position
        while end < count and classes[end] == "D":
            end += 1
        left = next((classes[index] for index in range(position - 1, -1, -1) if classes[index] in ("L", "R")), None)
        right = next((classes[index] for index in range(end, count) if classes[index] in ("L", "R")), None)
        if "L" in (left, right):
            classes[position:end] = ["L"] * (end - position)
        position = end


class PreparedReviewTests(unittest.TestCase):
    def test_oversized_scalar_arrays_keep_each_full_value_in_order_and_copy(self):
        from angelo_agent.agent_workspace_ui import control_lines
        self.assertEqual(control_lines(["a" * 510, "b" * 512], "values"), ["Values: " + "a" * 510 + ", " + "b" * 512])
        self.assertEqual(control_lines([1, True, 2.5, None], "values"), ["Values: 1, Yes, 2.5, None"])
        for value in ("1 " * 500 + "ﺏ", "a" + "\u0301" * 1022 + "ﺏ", "x" * 1024,
                      ("日本語 العربية e\u0301 123 " * 80)[:1024]):
            with self.subTest(prefix=value[:20]):
                values = [value] * 32
                lines = control_lines(values, "values")
                self.assertEqual(lines[0], "Values: 32 ordered items")
                self.assertEqual(lines[1:], ["  Item %d: %s" % (index, value) for index in range(1, 33)])
                self.assertLessEqual(max(map(len, lines)), 1035)
                call = call_for(canonical([property_set({"v": values})], bound=True))
                self.assertEqual(approvals.review_arguments(call)["operations"][0]["parameters"]["values"]["v"], values)

    def test_complete_program_and_recipe_rows_match_existing_text(self):
        arguments = canonical([cube(index) for index in range(32)])
        for recipe in (False, True):
            call = call_for(copy.deepcopy(arguments), recipe=recipe)
            summary, details = drain(call)
            display = approvals.review_arguments(call)
            message = approvals.approval_message(call.name, display)
            preview = message["title"] + "\n" + message["body"] + "\n\n" + message["warning"]
            self.assertEqual(summary, old_wrap(preview, 80))
            self.assertEqual(details, old_wrap(approvals.approval_details(call.name, display), 80))
            self.assertIn("$b31", "".join(row[0] for row in details))
            shown = display["program"] if recipe else display
            self.assertEqual(len(shown["expected_checks"]), 96)

    def test_repeated_closed_and_open_lookup_never_derives_copies_hashes_formats_or_wraps(self):
        call = call_for(maximum_material_program())
        summary, details = drain(call)
        forbidden = AssertionError("large request work ran in a prepared lookup")
        with patch.object(program_checks, "required_checks", side_effect=forbidden), \
                patch.object(review.copy, "deepcopy", side_effect=forbidden), \
                patch.object(review.hashlib, "sha256", side_effect=forbidden), \
                patch.object(review, "approval_message", side_effect=forbidden), \
                patch.object(review.json, "dumps", side_effect=forbidden):
            for _ in range(100):
                for expanded in (False, True):
                    rows = review.review_rows(call, 1, 80, 14, 0, 1, lambda *args: self.fail("wrap in draw"), expanded)
                    self.assertIs(rows[0], summary)
                    self.assertIs(rows[1], details if expanded else ())

    def test_display_copies_cannot_mutate_execution_or_poison_next_review(self):
        call = call_for()
        before = copy.deepcopy(call.arguments)
        display = approvals.review_arguments(call)
        display["operations"][0]["parameters"]["name"] = "poison"
        display["expected_checks"].clear()
        self.assertEqual(call.arguments, before)
        self.assertEqual(len(approvals.review_arguments(call)["expected_checks"]), 3)
        with self.assertRaises(TypeError):
            call._approval_review.arguments["operations"][0]["parameters"]["name"] = "poison"
        self.assertNotIn("poison", str(drain(call)))

    def test_replacements_hide_old_rows_and_explicit_updates_change_both_digests(self):
        for field in ("arguments", "precondition", "document"):
            call = call_for()
            drain(call)
            setattr(call, field, copy.deepcopy(getattr(call, field)) if field == "arguments" else ("changed",))
            self.assertEqual(review.review_rows(call, 1, 80, 14, 0, 1, None, True), ((), ()))
        call = call_for()
        old = call._approval_review
        call.arguments["operations"][0]["parameters"]["name"] = "Updated"
        obligations = program_checks.resolve_obligations(call.arguments)
        binding = call.precondition._replace(checks_digest=obligations.checks_digest, obligations=obligations)
        call.precondition = review.prepare_review(call, binding)
        self.assertNotEqual(call._approval_review.canonical_digest, old.canonical_digest)
        self.assertNotEqual(call._approval_review.checks_digest, old.checks_digest)
        self.assertIn("Updated", str(drain(call)))

    def test_changed_bound_verification_clears_snapshot_instead_of_reusing_old_review(self):
        call = call_for()
        with self.assertRaisesRegex(ValueError, "verification changed"):
            review.prepare_review(call, call.precondition._replace(checks_digest="0" * 64))
        self.assertIsNone(call._approval_review)

    def test_labels_locale_geometry_and_fonts_replace_only_layout(self):
        call = call_for()
        complete = drain(call)
        snapshot = call._approval_review
        call.target_labels = {"$b0": "Human name"}
        # Late display labels keep the drawn card (same review) until its replacement is ready.
        kept = review.review_rows(call, 1, 80, 14, 0, 1, None, True)
        self.assertTrue(kept[0] is complete[0] and kept[1] is complete[1])
        self.assertIn("Human name", str(drain(call)))
        for key in ((60, 14, 0, 1), (80, 16, 0, 1), (80, 14, 3, 4)):
            self.assertEqual(review.review_rows(call, 1, *key, None, True), ((), ()))
        with patch.object(review, "language", return_value="ja"):
            self.assertEqual(review.review_rows(call, 1, 80, 14, 0, 1, None, True), ((), ()))
            drain(call)
        self.assertIs(call._approval_review, snapshot)
        self.assertEqual(len(snapshot.layouts), 1)

    def test_late_labels_keep_complete_rows_until_each_replacement_part_is_published(self):
        call = call_for(maximum_material_program())
        old_preview, old_details = drain(call)
        call.target_labels = {"$b0": "Human name"}
        ticks = iter(index * .001 for index in range(100000))
        with patch.object(review.time, "monotonic", side_effect=lambda: next(ticks)):
            self.assertTrue(review.advance_review_layout(call, 1, 80, 14, 0, 1, iter_wrap, .002))
            layout = call._approval_review.layouts[1]
            self.assertEqual((layout.preview, layout.details), ((), ()))
            rows = review.review_rows(call, 1, 80, 14, 0, 1, None, True)
            self.assertTrue(rows[0] is old_preview and rows[1] is old_details, "the drawn card is kept")
            self.assertFalse(review.details_preparing(call, 1))
            while not layout.preview:
                review.advance_review_layout(call, 1, 80, 14, 0, 1, iter_wrap, next(ticks) + .002)
            self.assertIsNotNone(layout.iterator, "the new Details are still being prepared")
            rows = review.review_rows(call, 1, 80, 14, 0, 1, None, True)
            self.assertTrue(rows[0] is layout.preview and rows[1] is old_details)
            self.assertEqual(layout.kept, ((), old_details), "the replaced summary is released")
        new_preview, new_details = drain(call)
        self.assertTrue(new_preview is layout.preview and new_details is layout.details and new_details is not old_details)
        self.assertEqual(layout.kept, ((), ()))

    def test_kept_rows_never_survive_new_geometry_or_a_new_review(self):
        call = call_for()
        drain(call)
        call.target_labels = {"$b0": "Human name"}
        self.assertEqual(review.review_rows(call, 1, 60, 14, 0, 1, None, True), ((), ()))
        review.advance_review_layout(call, 1, 60, 14, 0, 1, iter_wrap, 0)
        self.assertEqual(call._approval_review.layouts[1].kept, ((), ()), "rows wrapped for another width are not kept")
        controller = SimpleNamespace(pending={call.id: call}, document=call.document)
        state = {"approval_review": review.review_token(call)}
        state["pressed_approval_review"] = state["approval_review"]
        call.arguments = copy.deepcopy(call.arguments)
        self.assertEqual(review.review_rows(call, 1, 80, 14, 0, 1, None, True), ((), ()))
        self.assertFalse(review.details_preparing(call, 1))
        self.assertFalse(review.approve_current_review(controller, call.id, state))

    def test_opened_details_report_preparation_until_complete(self):
        call = call_for(maximum_material_program())
        ticks = iter(index * .001 for index in range(100000))
        with patch.object(review.time, "monotonic", side_effect=lambda: next(ticks)):
            self.assertFalse(review.details_preparing(call, 1), "no card is drawn before any layout exists")
            layout = None
            while layout is None or not layout.preview:
                review.advance_review_layout(call, 1, 80, 14, 0, 1, iter_wrap, next(ticks) + .002)
                layout = call._approval_review.layouts[1]
            preview, details = review.review_rows(call, 1, 80, 14, 0, 1, None, True)
            self.assertTrue(preview and details == ())
            self.assertTrue(review.details_preparing(call, 1))
        drain(call)
        self.assertFalse(review.details_preparing(call, 1))
        script = ToolCall("script-call", "angelo_execute_python", {"code": "pass", "description": "x"}, ("document", 1))
        self.assertFalse(review.details_preparing(script, 1))

    def test_every_call_advances_even_when_its_own_work_spent_the_budget(self):
        """A loaded machine can use up the shared tick budget before the first step; the card still lays out."""
        call = call_for()
        calls = 0
        # The clock already reads past every deadline, as when a tick's work before the first step took too long.
        with patch.object(review.time, "monotonic", return_value=10.0):
            while review.advance_review_layout(call, 1, 80, 14, 0, 1, iter_wrap, 5.0):
                calls += 1
                self.assertLess(calls, 1000, "the layout never advanced")
        preview, details = review.review_rows(call, 1, 80, 14, 0, 1, None, True)
        self.assertTrue(preview and details)
        # Exactly one step per call: every row, plus the step that ends the summary and the one that ends Details.
        self.assertEqual(calls + 1, len(preview) + len(details) + 2)

    def test_cooperative_layout_yields_before_details_and_cancellation_discards_work(self):
        call = call_for(maximum_material_program())
        ticks = iter(index * .001 for index in range(10000))
        with patch.object(review.time, "monotonic", side_effect=lambda: next(ticks)):
            self.assertTrue(review.advance_review_layout(call, 1, 80, 14, 0, 1, iter_wrap, .005))
        layout = call._approval_review.layouts[1]
        self.assertLessEqual(len(layout.rows), 5)
        self.assertEqual(layout.details, ())
        review.clear_review(call)
        self.assertFalse(review.advance_review_layout(call, 1, 80, 14, 0, 1, iter_wrap, time.monotonic() + 1))
        self.assertEqual(review.review_rows(call, 1, 80, 14, 0, 1, None, True), ((), ()))

    def test_old_pressed_hit_and_in_place_edit_cannot_approve_replacement(self):
        call = call_for()
        controller = SimpleNamespace(pending={call.id: call}, document=call.document)
        state = {"approval_review": review.review_token(call)}
        self.assertTrue(review.approve_current_review(controller, call.id, state))
        state["pressed_approval_review"] = state["approval_review"]
        call.precondition = review.prepare_review(call, call.precondition)
        state["approval_review"] = review.review_token(call)
        self.assertFalse(review.approve_current_review(controller, call.id, state))
        call.arguments["operations"][0]["parameters"]["name"] = "Changed after draw"
        self.assertFalse(review.approve_current_review(controller, call.id, state))
        self.assertIsNone(call._approval_review)


class WrappingTests(unittest.TestCase):
    def test_linear_number_direction_matches_exhaustive_legacy_sequences(self):
        for length in range(9):
            for values in itertools.product("LRDN", repeat=length):
                old, current = list(values), list(values)
                old_numbers_by_latin(old)
                rtl_text._numbers_by_latin(current)
                self.assertEqual(current, old, values)

    def test_linear_direction_preserves_random_mixed_unicode_visual_and_logical_text(self):
        randomizer = random.Random(12578)
        alphabet = "ABC xyz日本語مرحباﺏ012345٠١٢ \u0301\u0651()[]-+.,:;"
        for _ in range(200):
            text = "".join(randomizer.choices(alphabet, k=randomizer.randint(1, 256)))
            with patch.object(rtl_text, "_numbers_by_latin", old_numbers_by_latin):
                old_visual, old_logical = rtl_text.to_visual(text), rtl_text.to_logical(text)
            self.assertEqual(rtl_text.to_visual(text), old_visual)
            self.assertEqual(rtl_text.to_logical(text), old_logical)

    def test_font_row_output_preserves_latin_cjk_bidi_combining_and_emoji(self):
        cases = ["ordinary exact settings 1.25\n\nLast line", "東京の岩を選択します。次の設定（幅）を確認。" * 5,
                 "مرحبا بالعالم · Blender 5.2 · 123.45" * 4, "مَرحبًا 日本語 (ABC 123)" * 8,
                 "a\u0301" * 512, "👨‍👩‍👧‍👦 🪨 👍🏽" * 40, "x" * 1024,
                 "مَرحبًا 123 ABC " * 64]
        for value in cases:
            for width in (12, 40, 120):
                with self.subTest(width=width, prefix=value[:30]):
                    self.assertEqual(tuple(iter_wrap(value, width, 14, 0)), old_wrap(value, width))

    def test_long_legal_array_line_measures_visible_prefixes_and_yields_one_row(self):
        FONT.largest_probe = 0
        iterator = iter_wrap("Values: " + ", ".join(["x" * 1024] * 32), 80, 14, 0)
        self.assertEqual(len(next(iterator)[0]), 8)
        self.assertLessEqual(FONT.largest_probe, 128)
        next(iterator)
        self.assertLessEqual(FONT.largest_probe, 128)


class ControllerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.set_document(("document", 1))
        self.controller.busy = True
        self.executor = RecipeExecutor()
        self.controller.request_tool("test", "angelo_scene_program", canonical([cube(0)]), lambda result: None)
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        self.call = next(iter(self.controller.pending.values()))

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def test_dispatch_prepares_before_approval_and_fresh_precondition_never_replaces_it(self):
        self.assertTrue(self.call.approval)
        original = self.call._approval_review
        self.assertIsNotNone(original)
        self.call.arguments["operations"][0]["parameters"]["name"] = "Changed"
        current, failure = self.controller._fresh_precondition(self.call, self.executor)
        self.assertIsNone(failure)
        self.assertNotEqual(current, self.call.precondition)
        self.assertIs(self.call._approval_review, original)

    def test_finish_and_document_invalidation_release_review(self):
        self.controller.set_document(("document", 2))
        self.assertIsNone(self.call._approval_review)
        self.assertNotIn(self.call.id, self.controller.pending)

    def test_decline_releases_review(self):
        self.controller.decline_tool(self.call.id)
        self.assertIsNone(self.call._approval_review)

    def test_automatic_dispatch_stages_without_display_preparation(self):
        self.controller.decline_tool(self.call.id)
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        answers = []
        self.controller.request_tool("automatic", "angelo_scene_program", canonical([cube(0)]), answers.append)
        with patch("angelo_agent.controller.prepare_review", side_effect=AssertionError("unused display work")) as prepare:
            for _ in range(4):
                self.controller.pump_tools(self.executor, self.controller.document, apply_mutation=Mock(),
                                           budget=ToolDispatchBudget(seconds=60))
                if answers:
                    break
        prepare.assert_not_called()
        self.assertEqual([name for name, _ in self.executor.started], ["angelo_scene_program"])
        self.assertEqual(answers[0]["error"], "fixture stops at staging")

    def test_manual_dispatch_prepares_exactly_once(self):
        self.controller.decline_tool(self.call.id)
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"  # No apply callback: this still needs a manual card.
        self.controller.request_tool("manual", "angelo_scene_program", canonical([cube(0)]), lambda result: None)
        with patch("angelo_agent.controller.prepare_review", wraps=review.prepare_review) as prepare:
            for _ in range(3):
                self.controller.pump_tools(self.executor, self.controller.document,
                                           budget=ToolDispatchBudget(seconds=60))
        prepare.assert_called_once()
        self.assertTrue(next(iter(self.controller.pending.values())).approval)

    def test_frame_rebind_refreshes_manual_review_but_skips_automatic_display_work(self):
        old = self.call._approval_review
        rebound = copy.deepcopy(self.call.arguments)
        executor = SimpleNamespace(rebind_program=lambda *args: {
            "arguments": rebound, "precondition": ("rebound",), "reason": "frame"})
        self.call.precondition = self.controller._rebind_once(self.call, executor, None, None)
        self.assertIsNot(self.call._approval_review, old)
        self.assertIs(self.call._approval_review.sources[1], rebound)
        automatic = ToolCall("automatic-rebind", "angelo_scene_program", self.call.arguments,
                             self.call.document, precondition=("bound",), automatic=True)
        with patch("angelo_agent.controller.prepare_review", side_effect=AssertionError("unused display work")):
            automatic.precondition = self.controller._rebind_once(automatic, executor, None, None)
        self.assertEqual(automatic.precondition, ("rebound",))
        self.assertIsNone(getattr(automatic, "_approval_review", None))


class CanvasTimerLifecycleTests(unittest.TestCase):
    def test_file_load_keeps_preparation_timer_and_unregister_releases_it(self):
        """Execute real lifecycle functions; a file load discards nonpersistent timers."""
        source = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/chat_view.py"
        tree = ast.parse(source.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"register", "unregister", "animate_popovers"}]
        timers = {}
        registry = SimpleNamespace(
            register=lambda callback, **options: timers.update({callback: options}),
            is_registered=lambda callback: callback in timers,
            unregister=lambda callback: timers.pop(callback))
        console = SimpleNamespace(draw_handler_add=Mock(return_value="draw-handler"), draw_handler_remove=Mock())
        bpy = SimpleNamespace(app=SimpleNamespace(background=False, timers=registry),
                              utils=SimpleNamespace(register_class=Mock(), unregister_class=Mock()),
                              types=SimpleNamespace(SpaceConsole=console),
                              context=SimpleNamespace(preferences=SimpleNamespace(view=SimpleNamespace()),
                                                      window_manager=SimpleNamespace(windows=[])))
        namespace = {"__package__": "angelo_agent", "bpy": bpy, "_handler": None, "_enabled": False,
                     "_input_generation": 0, "_views": {}, "_modals": set(), "_scroll": {},
                     "reload_fonts": Mock(), "draw": Mock(), "wrap": SimpleNamespace(cache_clear=Mock()),
                     "ANGELO_OT_chat_input": object, "gpu": SimpleNamespace(shader=SimpleNamespace(from_builtin=Mock()))}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
        marble = SimpleNamespace(poll=Mock(), stop=Mock())  # The marble wait's timer (marble_host).
        with patch.dict(sys.modules, {"angelo_agent.voice": SimpleNamespace(prime=Mock()),
                                      "angelo_agent.marble_host": marble}), \
                patch.object(review, "prepare_canvas_reviews", return_value=False) as prepare:
            namespace["register"]()
            callback = namespace["animate_popovers"]
            timers_copy = dict(timers)
            timers.clear()
            timers.update({fn: options for fn, options in timers_copy.items() if options.get("persistent", False)})
            self.assertIn(callback, timers)
            self.assertEqual(callback(), .1)
            prepare.assert_called_once_with()
            marble.poll.assert_called_once_with()
            namespace["unregister"]()
            marble.stop.assert_called_once_with()
            self.assertNotIn(callback, timers)
            console.draw_handler_remove.assert_called_once_with("draw-handler", "WINDOW")


if __name__ == "__main__":
    unittest.main()
