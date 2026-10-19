# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure logic of the shared UI kit (``ui_kit*``) on a fake view: state, layout maths, hits, masking, keys.

No Blender here: the fake view measures text at a fixed width per character. The real pixels of every component
in every state are checked by the GUI gallery (``test_blender_ui_kit_gallery``).
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from test_native_notice_cards import FakeView  # noqa: E402
from angelo_agent import card_kit as kit, ui_kit, ui_kit_status as status  # noqa: E402


def inside(outer, inner):
    return (outer[0] - 0.01 <= inner[0] and inner[0] + inner[2] <= outer[0] + outer[2] + 0.01
            and outer[1] - 0.01 <= inner[1] and inner[1] + inner[3] <= outer[1] + outer[3] + 0.01)


class StateTests(unittest.TestCase):
    def test_state_for_reads_every_host_shape(self):
        for ui in (kit.UiState(hover=("a", 1), pressed=("a", 1), focus=("a", 1)),
                   {"hover": ("a", 1), "pressed": ("a", 1), "focus": ("a", 1)}, ui_kit.Ui(("a", 1), ("a", 1), ("a", 1))):
            self.assertEqual(ui_kit.state_for(ui, "a", 1)[:4], (True, True, True, True))
            self.assertEqual(ui_kit.state_for(ui, "a", 2)[:4], (False, False, False, True))
            self.assertEqual(ui_kit.state_for(ui, "a", 1, enabled=False)[:4], (False, False, False, False))

    def test_pressed_shows_only_while_the_pointer_stays_on_it(self):
        ui = kit.UiState(hover=("b", None), pressed=("a", None))
        self.assertFalse(ui_kit.state_for(ui, "a").pressed)
        self.assertEqual(ui_kit.state_for(None, "a"), ui_kit.REST)


class CanvasTests(unittest.TestCase):
    def test_register_hits_marks_kit_controls_for_canvas_controls(self):
        from angelo_agent import canvas_controls
        state = {"hover": ("toggle", "a")}
        ui_kit.register_hits(state, [((0, 0, 10, 10), "toggle", "a")])
        self.assertEqual(state["hits"], [((0, 0, 10, 10), "toggle", "a")])
        self.assertEqual(state["hit_style"][("toggle", "a")], "kit")
        self.assertTrue(ui_kit.state_for(state, "toggle", "a").hover)
        self.assertEqual(canvas_controls.focus_order(state["hits"]), [("toggle", "a")])


class ClipTests(unittest.TestCase):
    def test_clip_stays_inside_the_callers_scissor_and_restores_it(self):
        import sys
        import types
        calls = []
        state = types.SimpleNamespace(
            scissor_get=lambda: (100, 100, 200, 200),
            scissor_set=lambda *rect: calls.append(("set", rect)),
            scissor_test_set=lambda on: calls.append(("test", on)))
        fake = types.ModuleType("gpu")
        fake.state = state
        saved = sys.modules.get("gpu")
        sys.modules["gpu"] = fake
        try:
            with ui_kit.clip(50, 150, 400, 100):
                calls.append("draw")
        finally:
            if saved is None:
                sys.modules.pop("gpu")
            else:
                sys.modules["gpu"] = saved
        self.assertEqual(calls, [("test", True), ("set", (100, 150, 200, 100)), "draw", ("set", (100, 100, 200, 200))])
        self.assertNotIn(("test", False), calls)   # Never unclips the caller.


class StatusTests(unittest.TestCase):
    view = FakeView()

    def test_every_chip_kind_has_a_label_and_a_pill_size(self):
        for kind in status.CHIP_KINDS:
            w, h = status.chip_size(self.view, kind, 1.0)
            self.assertGreater(w, 40)
            self.assertEqual(h, 22)
        self.assertGreater(status.chip_size(self.view, "done", 2.0)[0], status.chip_size(self.view, "done", 1.0)[0] * 1.9)

    def test_notice_grows_with_text_and_details_and_keeps_hits_inside(self):
        short, _ = status.notice(self.view, 10, 500, 420, 1.0, draw=False, kind="warn", title="Sign in again")
        long_, hits = status.notice(self.view, 10, 500, 420, 1.0, draw=False, kind="error", title="Could not connect",
                                    body="The provider did not answer. " * 6,
                                    actions=[("retry", None, "Try again", "primary"), ("help", None, "Help", "ghost")],
                                    details="NET-3 timeout after 30 s", details_action="details")
        self.assertGreater(long_, short)
        rect = (10, 500 - long_, 420, long_)
        self.assertEqual([hit[1] for hit in hits], ["retry", "help", "details"])
        for hit in hits:
            self.assertTrue(inside(rect, hit[0]), hit)
        expanded, _ = status.notice(self.view, 10, 500, 420, 1.0, draw=False, kind="error", title="Could not connect",
                                    details="NET-3 timeout after 30 s", details_action="details", expanded=True)
        collapsed, _ = status.notice(self.view, 10, 500, 420, 1.0, draw=False, kind="error", title="Could not connect",
                                     details="NET-3 timeout after 30 s", details_action="details")
        self.assertGreater(expanded, collapsed)

    def test_details_hit_carries_the_notice_key(self):
        _, hits = status.notice(self.view, 0, 400, 400, 1.0, draw=False, kind="error", title="Failed",
                                details="trace", details_action="details", details_value="job-7")
        self.assertEqual(hits[-1][1:], ("details", "job-7"))

    def test_draw_functions_blend_their_own_shapes(self):
        calls = []

        class Recorder(FakeView):
            def shape(self, *args, **kwargs):
                calls.append("shape")

        wrapped = ui_kit.blended(Recorder())
        self.assertIs(ui_kit.blended(wrapped), wrapped)
        self.assertEqual(wrapped._font, 1)
        self.assertTrue(hasattr(wrapped, "measure"))
        for module, names in ((status, ("draw_chip", "notice", "draw_code_block")),):
            for name in names:
                self.assertTrue(hasattr(getattr(module, name), "__wrapped__"), name)

    def test_notice_buttons_wrap_in_a_narrow_notice(self):
        actions = [("a%d" % i, None, "Action number %d" % i, "secondary") for i in range(4)]
        height, hits = status.notice(self.view, 0, 400, 220, 1.0, draw=False, title="Narrow", actions=actions)
        rect = (0, 400 - height, 220, height)
        self.assertGreater(len({hit[0][1] for hit in hits}), 1)
        for hit in hits:
            self.assertTrue(inside(rect, hit[0]), hit)

    def test_code_block_height_and_copy_hit(self):
        code = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2)\nprint('done')"
        bare = status.code_block_height(self.view, code, 1.0, header=False)
        framed = status.code_block_height(self.view, code, 1.0, header=True)
        self.assertGreater(framed, bare)
        rect = (20, 100, 300, framed)
        hits = status.code_block_hits(rect, 1.0, "copy", 7, self.view)
        self.assertEqual(hits[0][1:], ("copy", 7))
        self.assertTrue(inside(rect, hits[0][0]))
        self.assertGreater(hits[0][0][0] + hits[0][0][2], rect[0] + rect[2] * 0.8)   # At the end of the header.
        self.assertEqual(status.code_block_hits(rect, 1.0), [])


class FieldTests(unittest.TestCase):
    def test_typing_editing_and_caret(self):
        from angelo_agent.ui_kit_field import FieldState, field_key
        fs = FieldState(editing=True)
        for ch in "cubes":
            self.assertEqual(field_key(fs, ch.upper(), ch), "changed")
        self.assertEqual(field_key(fs, 'BACK_SPACE'), "changed")
        self.assertEqual(fs.text, "cube")
        self.assertEqual(field_key(fs, 'HOME'), "moved")
        field_key(fs, 'A', "a")
        self.assertEqual((fs.text, fs.caret()), ("acube", 1))
        field_key(fs, 'DEL')
        self.assertEqual(fs.text, "aube")
        self.assertIsNone(field_key(fs, 'TAB'))
        self.assertIsNone(field_key(fs, 'A', "a", command=True))
        self.assertEqual(field_key(fs, 'RET'), "submit")
        self.assertFalse(fs.editing)

    def test_mask_reveal_and_paste(self):
        from angelo_agent.ui_kit_field import FieldState, field_key, masked
        fs = FieldState(text="sk-123", secret=True, editing=True)
        self.assertEqual(masked(fs), "\u2022" * 6)
        fs.revealed = True
        self.assertEqual(masked(fs), "sk-123")
        self.assertEqual(field_key(fs, 'V', "v", command=True, clipboard=lambda: "45\n6"), "changed")
        self.assertEqual(fs.text, "sk-123456")
        fs.error = "Wrong key"
        field_key(fs, 'BACK_SPACE')
        self.assertEqual(fs.error, "")
        self.assertEqual(masked(FieldState(text="plain")), "plain")

    def test_secret_field_buttons_sit_inside_the_field_and_win(self):
        from angelo_agent.ui_kit_field import field_height, field_hits
        rect = (10, 10, 300, field_height(1.0))
        hits = field_hits(rect, 1.0, "field", "k", secret=True)
        self.assertEqual([hit[1] for hit in hits], ["paste", "reveal", "field"])
        for hit in hits[:2]:
            self.assertTrue(inside(hits[2][0], hit[0]))
        self.assertGreater(field_height(1.0, "error"), field_height(1.0))


class SelectTests(unittest.TestCase):
    def test_keyboard_navigation(self):
        from angelo_agent.ui_kit_select import select_key
        options = [("a", "A"), ("b", "B"), ("c", "C")]
        self.assertEqual(select_key(options, None, 'DOWN_ARROW'), ("a", None, False))
        self.assertEqual(select_key(options, "c", 'DOWN_ARROW'), ("a", None, False))
        self.assertEqual(select_key(options, "a", 'UP_ARROW'), ("c", None, False))
        self.assertEqual(select_key(options, "b", 'END'), ("c", None, False))
        self.assertEqual(select_key(options, "b", 'RET'), ("b", "b", True))
        self.assertEqual(select_key(options, "b", 'ESC'), ("b", None, True))

    def test_popover_opens_below_and_flips_above_at_the_bottom(self):
        from angelo_agent.ui_kit_select import popover_rects
        panel, rows = popover_rects((100, 500, 200, 36), 3, 1.0)
        self.assertLess(panel[1] + panel[3], 500)
        self.assertEqual(len(rows), 3)
        self.assertGreater(rows[0][1], rows[1][1])   # First option on top.
        flipped, _ = popover_rects((100, 20, 200, 36), 3, 1.0, region_h=800)
        self.assertGreater(flipped[1], 56)
        kept, _ = popover_rects((100, 20, 200, 36), 3, 1.0, flip=False)
        self.assertLess(kept[1], 20)


class ControlTests(unittest.TestCase):
    def test_toggle_row_grows_when_its_caption_wraps(self):
        from angelo_agent.ui_kit_controls import toggle_row_height
        view = FakeView()
        one = toggle_row_height(view, 520, 1.0, "Sync scene changes", "Short caption")
        two = toggle_row_height(view, 300, 1.0, "Sync scene changes",
                                "On: saved in your login Keychain for this profile. Off: memory only.")
        self.assertEqual(toggle_row_height(view, 520, 1.0, "Show experimental tools"), 44)
        self.assertLess(one, 54)
        self.assertGreater(two, one)


class RowTests(unittest.TestCase):
    def test_scrollbar_maths(self):
        from angelo_agent import ui_kit_rows as rows
        rect = (0, 0, 300, 400)
        self.assertIsNone(rows.scrollbar_rects(rect, 400, 400, 0, 1.0))
        track, top = rows.scrollbar_rects(rect, 1200, 400, 0, 1.0)
        _, bottom = rows.scrollbar_rects(rect, 1200, 400, 800, 1.0)
        self.assertAlmostEqual(top[1] + top[3], track[1] + track[3])      # At the start: the thumb is at the top.
        self.assertAlmostEqual(bottom[1], track[1])                         # At the end: at the bottom.
        self.assertGreater(track[0], rect[2] - 12)                          # Inside the trailing edge.
        travel = track[3] - top[3]
        self.assertAlmostEqual(rows.drag_offset(0, -travel / 2, rect, 1200, 400, 1.0), 400)
        self.assertEqual(rows.drag_offset(0, 50, rect, 1200, 400, 1.0), 0)  # Clamped at the start.
        self.assertEqual(rows.drag_offset(800, -500, rect, 1200, 400, 1.0), 800)

    def test_scrollbar_hides_when_idle(self):
        from angelo_agent.ui_kit_rows import scrollbar_alpha
        self.assertEqual(scrollbar_alpha(0.5), 1.0)
        self.assertTrue(0 < scrollbar_alpha(1.4) < 1)
        self.assertEqual(scrollbar_alpha(5), 0.0)
        self.assertEqual(scrollbar_alpha(5, hover=True), 1.0)
        self.assertEqual(scrollbar_alpha(5, dragging=True), 1.0)

    def test_provider_tiles_and_rows_in_a_card(self):
        b = kit.Builder(FakeView(), 1.0, 560)
        b.list_row("row", "a", "Anthropic API", "Billed to your account", lead=("letter", "A", "ok"), trailing=("chip", "done"))
        b.list_row("row", "off", "Off", enabled=False)
        b.provider_tiles("provider", [(str(i), "X", "Name %d" % i, "API key") for i in range(5)], "1")
        plan = b.place(0, 900, False, (1000, 1000))
        tiles = [rect for rect, action, _ in plan.hits if action == "provider"]
        self.assertEqual(len(tiles), 5)
        self.assertEqual(len({round(rect[1]) for rect in tiles}), 2)   # Three per row, then two.
        for a, c in zip(tiles, tiles[1:]):
            self.assertFalse(a[0] < c[0] + c[2] and c[0] < a[0] + a[2] and a[1] < c[1] + c[3] and c[1] < a[1] + a[3])
        self.assertNotIn(("row", "off"), plan.focus_order)
        selected = [op[3]["value"] for op in plan.ops if op[0] == "kit" and op[1] == "provider_tile" and op[3]["selected"]]
        self.assertEqual(selected, ["1"])


class TooltipTests(unittest.TestCase):
    def test_tooltip_stays_inside_a_narrow_region(self):
        from angelo_agent.canvas_controls import tip_rect, tip_wrap_width
        send = (258, 20, 28, 28)                   # The send button near the right edge of a 300 px chat.
        x, y, w, h = tip_rect(send, 230, 30, 1.0, region_width=300, region_height=600)
        self.assertGreaterEqual(x, 8)
        self.assertLessEqual(x + w, 300 - 8)
        self.assertGreater(y, send[1] + send[3])     # Above the control.
        self.assertAlmostEqual(x + w, send[0] + send[2])   # Flush with the button's outer edge.
        x, _, w, _ = tip_rect((2, 20, 28, 28), 230, 30, 1.0, region_width=300)
        self.assertGreaterEqual(x, 8)
        _, y, _, h = tip_rect((100, 570, 28, 24), 120, 30, 1.0, region_width=300, region_height=600)
        self.assertLess(y + h, 570)                  # No room above: below the control.
        self.assertLessEqual(tip_wrap_width(1.0, 200) + 16, 200)
        self.assertEqual(tip_wrap_width(1.0, 1200), 260)
        self.assertEqual(tip_wrap_width(2.0), 520)


class BuilderTests(unittest.TestCase):
    def test_builder_places_kit_components_with_hits_and_focus(self):
        view = FakeView()
        b = kit.Builder(view, 1.0, 520)
        b.heading("Gallery")
        b.chip("running")
        b.notice("error", "Could not connect", "Body", actions=[("retry", None, "Try again", "primary")],
                 details="x", details_action="details")
        b.code_block("a = 1\nb = 2", copy_action="copy", copy_value=1)
        plan = b.place(100, 900, False, (1200, 1000))
        kinds = [op[1] for op in plan.ops if op[0] == "kit"]
        self.assertEqual(kinds, ["chip", "notice", "code_block"])
        self.assertIn(("retry", None), plan.focus_order)
        self.assertIn(("copy", 1), plan.focus_order)
        for rect, action, value in plan.hits:
            self.assertTrue(inside(plan.card, rect), (action, rect))
        self.assertTrue(all(len(op) == 6 for op in plan.ops if op[0] == "kit"))
        notice_op = next(op for op in plan.ops if op[0] == "kit" and op[1] == "notice")
        retry = next(rect for rect, action, _ in plan.hits if action == "retry")
        self.assertTrue(inside(notice_op[2], retry))

    def test_open_select_popover_is_drawn_last_and_hit_first(self):
        from angelo_agent.ui_kit_field import FieldState
        b = kit.Builder(FakeView(), 1.0, 400)
        b.select("select", "m", [("a", "Alpha"), ("b", "Beta")], "a", open=True)
        b.text_field("field", "under", FieldState(), placeholder="Below the select")
        plan = b.place(0, 800, False, (1000, 1000))
        self.assertEqual(plan.ops[-1][1], "select_popover")
        beta = next(rect for rect, action, value in plan.hits if action == "option" and value == "b")
        middle = (beta[0] + beta[2] / 2, beta[1] + beta[3] / 2)
        self.assertEqual(kit.hit_test(plan, *middle), ("option", "b"))


if __name__ == "__main__":
    unittest.main()
