# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A long draft scrolls in the composer itself, and its keys are a text field's: through the chat's real input
handler (``chat_view.ANGELO_OT_chat_input.modal``) and the composer's real draw (``chat_composer.draw``).

1.0.9 (maintainer report): the composer drew ``draft_lines[first:first + 8]`` with ``first = caret_row - 7``, so its
view followed the caret alone. A wheel or a swipe over it scrolled the transcript behind it, there was no scrollbar,
and earlier text could be read only by moving the caret. Each test here fails on that code: the wheel reached the
transcript, a click after scrolling landed on another row, the caret was drawn over another row.

The draw and the handler run on stand-ins for Blender (``blf`` with whole-pixel advances, recorded shapes and text),
at a 1x and a 1.5x scale and in Arabic (mirrored chat, left-to-right text field). The running app is
``test_blender_composer_gui``; the text-field keys through the same handler are
``test_native_composer_field_keys``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import functools
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(HERE))
import qa_operator  # noqa: E402
import test_native_scroll_and_caret as caret_tests  # noqa: E402  (its stub canvas; its tests are not collected here)

AREA, WINDOW = 31, 1
SIZE = 14
LINE = SIZE + 8
LONG = "\n".join("line %02d" % index for index in range(30))   # Thirty rows: 22 hidden behind the eight shown.


class Line:
    """``ConsoleLine``: the body, and a character caret clamped on write (RNA)."""

    def __init__(self, body=""):
        self.body, self._index = body, 0

    @property
    def current_character(self):
        return self._index

    @current_character.setter
    def current_character(self, index):
        self._index = max(0, min(len(self.body), index))


class Area:
    def __init__(self, line, width, height):
        self.x, self.y, self.width, self.height = 0, 0, width, height
        self.regions = [SimpleNamespace(type='WINDOW', x=0, y=0, width=width, height=height)]
        self.spaces = SimpleNamespace(active=SimpleNamespace(history=[line], font_size=int(SIZE / 0.8) + 1))
        self.redraws = 0

    def as_pointer(self):
        return AREA

    def tag_redraw(self):
        self.redraws += 1


def _reraise():
    raise


class ComposerCase(caret_tests.StubbedCanvas):
    SCALE = 1.0
    WIDTH = 420   # Layout units.
    RTL = False

    def setUp(self):
        self.blf = caret_tests.FakeBlf()
        for module in (self.view, self.view.canvas_wrap):
            self.patch(module, "blf", self.blf)
        self.view.wrap.cache_clear()
        self.patch(self.view, "_font", 7)
        self.patch(self.view, "_scale", 1.0)
        from angelo_agent import chat_composer, ui_text
        self.patch(ui_text, "is_rtl", lambda: self.RTL)
        # Untranslated (Blender's translations are a stand-in here).
        self.patch(chat_composer, "iface_", lambda text, **values: text.format(**values) if values else text)
        # This text-field fixture has no held or pending goal; avoid reading the real goal store.
        self.patch(chat_composer.chat_goal, "_goal_and_pending", lambda controller: (None, None))
        from angelo_agent import composer_input
        for name in ("_HISTORIES", "_SCROLLS"):   # Kept module-wide: none from another test.
            self.patch(composer_input, name, type(getattr(composer_input, name, {}))())
        self.line = Line()
        self.area = Area(self.line, int(self.WIDTH * self.SCALE), int(800 * self.SCALE))
        self.state = {"focused": True}
        self.views = {AREA: self.state}
        self.transcript = []   # Events the transcript's scroll took.
        self.controller = SimpleNamespace(state={"id": "chat", "options": {}}, attachments=[], chosen_tools=[],
                                          trusted_scripts=False, always_allow_scripts=False, voice=None, status="",
                                          busy=False, selection_models=[], external_client="",
                                          selection_connection_id="")
        self._stand_ins()
        self.operator = self._modal()
        window = SimpleNamespace(as_pointer=lambda: WINDOW, modal_operators=[])
        self.wm = SimpleNamespace(clipboard="")
        self.context = SimpleNamespace(window=window, window_manager=self.wm,
                                       preferences=SimpleNamespace(inputs=SimpleNamespace(mouse_double_click_time=350)))

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stand_ins(self):
        controller = self.controller
        options = {"model": "", "approval": "ASK", "effort": "", "fast": False}
        stand_ins = {
            "console": SimpleNamespace(is_chat=lambda area: area.as_pointer() == AREA, execute=lambda context: None),
            "ui": SimpleNamespace(get_controller=lambda: controller, connection_record=lambda c: None,
                                  ready_connections=lambda: [1],
                                  team_account_ui=SimpleNamespace(note_window_event=lambda kind: None)),
            "marble_host": SimpleNamespace(note_event=lambda kind, value: None),
            "chat_settings": SimpleNamespace(clean_options=lambda value: options, selected_model=lambda c: None),
            "chat_popovers": SimpleNamespace(effective_effort=lambda *a: "", effort_name=lambda e: "",
                                             model_name=lambda m: "Model", supports_fast=lambda m: False),
            "chat_start_states": SimpleNamespace(default_label=lambda models: "Default"),
            "voice_ui": SimpleNamespace(mic_visible=lambda c: False),
            "document_scene": SimpleNamespace(notice=lambda c: ""),
        }
        package = sys.modules["angelo_agent"]
        for name, stand_in in stand_ins.items():
            patcher = mock.patch.dict(sys.modules, {"angelo_agent." + name: stand_in})
            patcher.start()
            self.addCleanup(patcher.stop)
            self.patch(package, name, stand_in)

    def _modal(self):
        path = ADDON / "chat_view.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        body = [node for node in tree.body if getattr(node, "name", None) in {"inside", "_kept_alive",
                                                                               "ANGELO_OT_chat_input"}]
        namespace = dict(vars(self.view))

        def transcript(event, *rest):
            if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN"}:
                self.transcript.append(event.type)
                return True
            return False
        from angelo_agent.canvas_controls import releases
        namespace.update({
            "functools": functools, "traceback": SimpleNamespace(print_exc=_reraise),
            "bpy": SimpleNamespace(types=SimpleNamespace(Operator=object), context=None),
            "modal_options": lambda identity: set(), "_enabled": True, "_input_generation": 0, "_views": self.views,
            "_modals": set(), "_scroll": {}, "live_windows": SimpleNamespace(areas=lambda window: [self.area]),
            "canvas_controls": SimpleNamespace(key=lambda *a: None, leave_areas=lambda *a: None,
                                               pointer=lambda *a: False, press=lambda *a: None,
                                               release=lambda *a: None, releases=releases,
                                               DISABLED="disabled"),
            "chat_scrollbar": SimpleNamespace(handle=transcript), "ui_scale": lambda: self.SCALE,
            "over_overlay": lambda window, event: False, "clear_blocked_notice": lambda window: None,
            "note_blocked_click": lambda *a: None, "release_active": lambda *a: False,
            "__name__": "angelo_agent.chat_view", "__package__": "angelo_agent"})
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)
        return qa_operator.stand_in(namespace["ANGELO_OT_chat_input"], input_generation=0, window_pointer=WINDOW,
                                    selected_all=False, anchor=None, dragging=False, session="chat",
                                    pending_click=None, focused_area=AREA)

    # -- the draw ------------------------------------------------------------------------------------------------

    def draw(self, blink_on=True):
        """The composer as ``chat_view._draw_chat`` lays it out and draws it: rows, shapes and text recorded."""
        from angelo_agent import chat_composer
        from angelo_agent.chat_layout import composer_layout
        view, width, draft = self.view, self.WIDTH, self.line.body
        view._scale = self.SCALE
        draft_lines = view.wrap(draft, chat_composer.hint_width(width), SIZE, 7, ordered=False)
        shown = chat_composer.composer_rows(draft, draft_lines, self.controller, width, SIZE, view.wrap, 7)
        geometry = composer_layout(width, shown, 0)
        _, bottom, _, height = geometry["rect"]
        height += max(0, min(8, shown) - 1) * (LINE - 22)
        self.state["hits"] = []
        self.state["draft_wrap"] = lambda value: view._wrap(value, chat_composer.hint_width(width), SIZE, 7, False,
                                                              self.SCALE)
        self.shapes, self.texts = [], []
        clock = SimpleNamespace(monotonic=lambda: 1000.25 if blink_on else 1000.75)
        with mock.patch.object(view, "shape", lambda x, y, w, h, color, radius=0: self.shapes.append((x, y, w, h))), \
                mock.patch.object(view, "text", lambda value, x, y, *a, **k: self.texts.append((value, x, y))), \
                mock.patch.object(view, "icon", lambda *a, **k: None), \
                mock.patch.object(chat_composer, "time", clock):
            chat_composer.draw(self.state, sys.modules["angelo_agent.ui"], self.controller, geometry=geometry,
                               composer_bottom=bottom, composer_height=height, line=self.line, draft=draft,
                               draft_lines=draft_lines, size=SIZE, line_height=LINE, width=width, attachment_rows=0,
                               connected=True)
        view._scale = 1.0   # As after the chat's draw: the handler runs unscaled.
        self.rows = draft_lines

    def shown(self):
        """The draft rows drawn, top first."""
        return [value for value, x, y in self.texts if value.startswith("line ") or value.startswith("row ")]

    def row_y(self, text):
        return next(y for value, x, y in self.texts if value == text)

    def carets(self):
        return [(x, y) for x, y, w, h in self.shapes if w == 1]

    # -- events --------------------------------------------------------------------------------------------------

    def event(self, kind, x, y, value='PRESS', unicode="", prev=None, **held):
        """An event at layout ``(x, y)`` over the drawn text (``x`` as drawn: the field is never mirrored)."""
        flags = dict(shift=False, ctrl=False, alt=False, oskey=False)
        flags.update(held)
        mx, my = x * self.SCALE, y * self.SCALE
        px, py = prev if prev else (mx, my)
        return SimpleNamespace(type=kind, value=value, unicode=unicode, mouse_x=mx, mouse_y=my, mouse_prev_x=px,
                               mouse_prev_y=py, **flags)

    def send(self, event):
        return self.operator.modal(self.context, event)

    def centre(self):
        x, y, w, h = self.state["composer"]
        return x + w / 2, y + h / 2

    def wheel(self, kind, times=1):
        return [self.send(self.event(kind, *self.centre())) for _ in range(times)]

    def type(self, text):
        for char in text:
            self.send(self.event(char.upper() if char.isalpha() else 'A', *self.centre(), unicode=char))

    def command(self, kind, **extra):
        """The platform's primary modifier with ``kind`` (Cmd on macOS, Ctrl elsewhere), as the handler reads it."""
        held = {"oskey": True} if sys.platform == "darwin" else {"ctrl": True}
        held.update(extra)
        return self.send(self.event(kind, *self.centre(), **held))

    def click(self, text, column, value='PRESS'):
        """A click on drawn row ``text`` just after its ``column``-th character."""
        from angelo_agent import chat_composer
        x = chat_composer.column_x(text, column, SIZE, self.SCALE) + 0.5
        self.send(self.event('LEFTMOUSE', x, self.row_y(text) + SIZE * 0.35, value))
        self.send(self.event('LEFTMOUSE', x, self.row_y(text) + SIZE * 0.35, 'RELEASE'))

    def long_draft(self):
        self.line.body = LONG
        self.line.current_character = len(LONG)
        self.draw()


class GoalIntegration(ComposerCase):
    def test_no_goal_reserves_no_chip_row_and_draws_no_chip(self):
        from angelo_agent import chat_composer
        with mock.patch.object(chat_composer.chat_goal, "draw_chip") as draw_chip:
            self.assertEqual(chat_composer.attachment_rows(self.controller), 0)
            self.draw()
            draw_chip.assert_not_called()

    def test_held_and_pending_goals_reserve_and_draw_the_chip(self):
        from angelo_agent import chat_composer
        for held, pending in ((object(), None), (None, object())):
            with self.subTest(pending=pending is not None), \
                    mock.patch.object(chat_composer.chat_goal, "_goal_and_pending", return_value=(held, pending)), \
                    mock.patch.object(chat_composer.chat_goal, "draw_chip") as draw_chip:
                self.assertEqual(chat_composer.attachment_rows(self.controller), 1)
                self.draw()
                draw_chip.assert_called_once()
                self.assertIs(draw_chip.call_args.args[2], self.controller)


class OwnScroll(ComposerCase):

    def test_a_long_draft_opens_at_the_caret(self):
        self.long_draft()
        self.assertEqual(self.shown(), ["line %02d" % i for i in range(22, 30)])

    def test_the_wheel_over_a_long_draft_scrolls_it_and_never_the_transcript(self):
        self.long_draft()
        self.assertEqual(self.wheel('WHEELUPMOUSE'), [{'RUNNING_MODAL'}])
        self.draw()
        self.assertEqual(self.shown()[0], "line 19", "three rows a notch, as the system scrolls text")
        self.assertEqual(self.transcript, [], "the transcript behind the composer never moved")

    def test_at_either_end_the_wheel_stops_in_the_composer(self):
        self.long_draft()
        self.assertEqual(self.wheel('WHEELUPMOUSE', 20), [{'RUNNING_MODAL'}] * 20)
        self.draw()
        self.assertEqual(self.shown()[0], "line 00")
        self.wheel('WHEELDOWNMOUSE', 20)
        self.draw()
        self.assertEqual(self.shown()[-1], "line 29")
        self.assertEqual(self.transcript, [])

    def test_a_trackpad_swipe_scrolls_the_draft_in_the_systems_direction(self):
        self.long_draft()
        x, y = self.centre()
        # The window manager's delta y - prev_y is the fingers' travel after the system's direction: view up 2 rows.
        self.send(self.event('TRACKPADPAN', x, y, prev=(x * self.SCALE, (y + 2 * LINE) * self.SCALE)))
        self.draw()
        self.assertEqual(self.shown()[0], "line 20")
        self.assertEqual(self.transcript, [])

    def test_a_draft_that_fits_leaves_the_wheel_to_the_transcript(self):
        self.line.body = "line 00\nline 01"
        self.draw()
        self.wheel('WHEELUPMOUSE')
        self.assertEqual(self.transcript, ['WHEELUPMOUSE'])

    def test_a_redraw_keeps_the_rows_the_person_scrolled_to(self):
        self.long_draft()
        self.wheel('WHEELUPMOUSE', 4)
        for blink in (True, False, True):
            self.draw(blink)
            self.assertEqual(self.shown()[0], "line 10")

    def test_no_caret_is_drawn_over_another_row_while_its_row_is_scrolled_away(self):
        self.long_draft()
        self.assertEqual(len(self.carets()), 1)
        self.wheel('WHEELUPMOUSE')
        self.draw()
        self.assertEqual(self.carets(), [], "the caret's row (29) is below the shown rows")

    def test_the_caret_is_drawn_on_its_own_row_after_a_scroll(self):
        self.long_draft()
        self.wheel('WHEELUPMOUSE', 10)
        self.draw()
        self.click("line 03", 4)
        self.draw()
        (x, y), = self.carets()
        self.assertAlmostEqual(y, self.row_y("line 03") - 3)

    def test_a_click_after_scrolling_lands_on_the_row_drawn_there(self):
        self.long_draft()
        self.wheel('WHEELUPMOUSE', 10)
        self.draw()
        self.click("line 03", 5)
        self.assertEqual(self.line.current_character, LONG.index("line 03") + 5)
        self.draw()
        self.assertEqual(self.shown()[0], "line 00", "a click in view moves nothing")

    def test_typing_after_scrolling_brings_the_caret_back_only_as_far_as_needed(self):
        self.long_draft()
        self.wheel('WHEELUPMOUSE', 10)
        self.draw()
        self.type("x")
        self.draw()
        self.assertEqual(self.shown()[-1], "line 29x", "the caret's row is the last shown, not the middle")
        self.assertEqual(len(self.carets()), 1)

    def test_up_moves_by_row_and_the_view_follows_minimally(self):
        self.long_draft()
        self.wheel('WHEELUPMOUSE', 10)
        self.draw()
        self.send(self.event('UP_ARROW', *self.centre()))
        self.draw()
        self.assertEqual(self.line.current_character, LONG.index("line 28") + 7)
        self.assertEqual(self.shown()[-1], "line 28")

    def test_a_long_paste_opens_at_the_caret(self):
        self.line.body, self.line.current_character = "start", 5
        self.draw()
        self.wm.clipboard = "\n" + "\n".join("row %02d" % i for i in range(40))
        self.assertEqual(self.command('V'), {'RUNNING_MODAL'})
        self.draw()
        self.assertEqual(self.shown()[-1], "row 39")
        self.assertEqual(len(self.carets()), 1)

    def test_the_scrollbar_shows_and_its_thumb_drags_the_draft(self):
        self.long_draft()
        self.wheel('WHEELUPMOUSE')
        self.draw()
        track, thumb = self.state["composer_bar"]
        x, y = thumb[0] + thumb[2] / 2, thumb[1] + thumb[3] / 2
        self.send(self.event('MOUSEMOVE', x, y))
        self.assertEqual(self.send(self.event('LEFTMOUSE', x, y)), {'RUNNING_MODAL'})
        self.send(self.event('MOUSEMOVE', x, track[1] + track[3] + 50))   # Past the top: the start.
        self.send(self.event('LEFTMOUSE', x, track[1] + track[3] + 50, 'RELEASE'))
        self.draw()
        self.assertEqual(self.shown()[0], "line 00")
        self.assertEqual(self.line.current_character, len(LONG), "the bar never moved the caret")
        self.assertTrue(self.state["focused"], "the bar is the field's: editing goes on")

    def test_a_drag_select_past_the_top_edge_scrolls_and_extends_the_selection(self):
        self.long_draft()
        self.wheel('WHEELUPMOUSE', 4)   # Rows 10..17 shown.
        self.draw()
        from angelo_agent import chat_composer
        x = chat_composer.column_x("line 10", 2, SIZE, self.SCALE) + 0.5
        timers = sys.modules["bpy"].app.timers
        timers.register.reset_mock()
        self.send(self.event('LEFTMOUSE', x, self.row_y("line 12") + 5))
        self.send(self.event('MOUSEMOVE', x, self.row_y("line 10") + 3 * LINE))
        self.draw()
        self.assertEqual(self.shown()[0], "line 09", "a row at once")
        tick = timers.register.call_args[0][0]
        self.patch(sys.modules["angelo_agent"], "live_windows",
                   SimpleNamespace(window_areas=lambda wm: [(None, self.area)]))
        self.patch(sys.modules["angelo_agent"], "chat_view", self.view)
        with mock.patch.object(self.view, "_views", self.views):
            tick()
        self.draw()
        self.assertEqual(self.shown()[0], "line 08", "and on while the button stays down there")
        start = LONG.index("line 08") + 2
        self.assertEqual(self.state["selection"], (start, LONG.index("line 12") + 2))
        self.send(self.event('LEFTMOUSE', x, 900, 'RELEASE'))
        self.assertNotIn("composer_drag", self.state)

    def test_double_and_triple_clicks_select_a_word_and_a_paragraph(self):
        self.line.body = "first words here\nsecond line"
        self.draw()
        for _ in range(2):
            self.click("first words here", 8)
        self.assertEqual(self.state["selection"], (6, 11))
        self.click("first words here", 8)
        self.assertEqual(self.state["selection"], (0, 17))


    def test_a_bar_drag_released_outside_the_chat_ends(self):
        """Break-it R1-6: the release landed below the chat's area, so the drag went on with every later move."""
        self.long_draft()
        self.wheel('WHEELUPMOUSE')
        self.draw()
        track, thumb = self.state["composer_bar"]
        x, y = thumb[0] + thumb[2] / 2, thumb[1] + thumb[3] / 2
        self.send(self.event('MOUSEMOVE', x, y))
        self.send(self.event('LEFTMOUSE', x, y))
        self.send(self.event('MOUSEMOVE', x, -40))
        self.assertEqual(self.send(self.event('LEFTMOUSE', x, -40, 'RELEASE')), {'RUNNING_MODAL'})
        self.assertNotIn("composer_bar_drag", self.state)
        self.draw()
        first = self.shown()[0]
        self.send(self.event('MOUSEMOVE', x, y + 60))
        self.draw()
        self.assertEqual(self.shown()[0], first, "a move after the release drags nothing")

    def test_a_resize_keeps_a_caret_in_view_in_view(self):
        """Break-it R1-4: the rows were wrapped anew and the kept row index showed other text, the caret hidden."""
        self.line.body = " ".join("word%02d" % i for i in range(120))
        self.line.current_character = len(self.line.body)
        self.draw()
        self.assertEqual(len(self.carets()), 1)
        self.WIDTH = int(self.WIDTH * 0.6)
        self.draw()
        self.assertEqual(len(self.carets()), 1, "the caret is still drawn after the narrower rewrap")

    def test_a_new_drag_stops_the_last_drags_autoscroll(self):
        """Break-it R1-9: a release and a new drag within a tick left two timers scrolling."""
        self.long_draft()
        self.wheel('WHEELUPMOUSE', 4)
        self.draw()
        timers = sys.modules["bpy"].app.timers
        ticks = []
        for _ in range(2):
            timers.register.reset_mock()
            x, y = 60, self.row_y("line 12") + 5
            self.send(self.event('LEFTMOUSE', x, y))
            self.send(self.event('MOUSEMOVE', x, y + 6 * LINE))
            ticks.append(timers.register.call_args[0][0])
            self.send(self.event('LEFTMOUSE', x, y + 6 * LINE, 'RELEASE'))
            self.send(self.event('LEFTMOUSE', x, y))   # The next drag is on its way.
            self.draw()
        self.patch(sys.modules["angelo_agent"], "live_windows",
                   SimpleNamespace(window_areas=lambda wm: [(None, self.area)]))
        self.patch(sys.modules["angelo_agent"], "chat_view", self.view)
        with mock.patch.object(self.view, "_views", self.views):
            self.assertIsNone(ticks[0](), "the first drag's timer ends")


class OwnScrollAtOneAndAHalf(OwnScroll):
    SCALE = 1.5


class OwnScrollInArabic(OwnScroll):
    """The chat is mirrored; the draft is a left-to-right field there, as every Blender text field."""
    RTL = True

    def test_the_bar_is_on_the_texts_right(self):
        self.long_draft()
        self.wheel('WHEELUPMOUSE')
        self.draw()
        track, _ = self.state["composer_bar"]
        self.assertGreater(track[0], self.WIDTH / 2)


if __name__ == "__main__":
    unittest.main()
