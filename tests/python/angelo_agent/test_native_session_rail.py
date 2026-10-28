# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parallel agents phase 1 without Blender: the session rail's layout, the reload schedule, the live layout sync,
live-view input, the lead's worker tools and their gate.

The in-Blender proofs are ``test_blender_live_view.py`` (background, real workers) and the session rail's GUI suite.
"""

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import live_layout, live_schedule, live_view_nav as nav, parallel_access  # noqa: E402
from angelo_agent import rail_redraw, session_rail as rail, worker_spec  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP  # noqa: E402


def tile(session, kind=rail.IDLE, active=False):
    return rail.Tile(session, "Title " + session, "Main" if session == "lead" else session, "Idle", kind, active)


class RailLayoutTests(unittest.TestCase):
    def test_no_sessions_draws_nothing(self):
        plan = rail.layout([], (1600, 900), 1.0)
        self.assertEqual(plan.tiles, [])
        self.assertEqual(plan.panel, (0, 0, 0, 0))
        self.assertFalse(rail.covers(plan, 10, 10))

    def test_tiles_stack_top_down_on_the_left_lead_first(self):
        plan = rail.layout([tile("lead", active=True), tile("w1"), tile("w2")], (1600, 900), 1.0)
        self.assertEqual([t.session for t in plan.tiles], ["lead", "w1", "w2"])
        tops = [t.rect[1] + t.rect[3] for t in plan.tiles]
        self.assertEqual(tops, sorted(tops, reverse=True))
        x, y, w, h = plan.panel
        self.assertLess(x, 40)
        self.assertLessEqual(y + h, 900)
        for t in plan.tiles:
            self.assertIsNotNone(t.picture)
            px, py, pw, ph = t.picture
            self.assertAlmostEqual(ph / pw, rail.PICTURE_RATIO, places=2)
            self.assertTrue(x <= t.rect[0] and t.rect[0] + t.rect[2] <= x + w)

    def test_every_tile_is_clickable_and_switches_its_own_session(self):
        plan = rail.layout([tile("lead"), tile("w1")], (1600, 900), 1.0)
        for t in plan.tiles:
            cx, cy = t.rect[0] + t.rect[2] / 2, t.rect[1] + t.rect[3] / 2
            self.assertEqual(rail.hit(plan, cx, cy), ("switch", t.session))
            self.assertTrue(rail.covers(plan, cx, cy))
        self.assertIsNone(rail.hit(plan, 1500, 450))
        self.assertFalse(rail.covers(plan, 1500, 450))

    def test_a_short_view_shrinks_pictures_then_drops_them(self):
        tall = rail.layout([tile(s) for s in ("lead", "w1", "w2", "w3", "w4")], (1600, 1400), 1.0)
        short = rail.layout([tile(s) for s in ("lead", "w1", "w2", "w3", "w4")], (1600, 700), 1.0)
        tiny = rail.layout([tile(s) for s in ("lead", "w1", "w2", "w3", "w4")], (1600, 360), 1.0)
        self.assertGreater(tall.tiles[0].picture[3], short.tiles[0].picture[3])
        self.assertTrue(tiny.compact)
        self.assertTrue(all(t.picture is None for t in tiny.tiles))
        for plan, height in ((tall, 1400), (short, 700)):
            self.assertGreaterEqual(plan.panel[1], 0)
            self.assertLessEqual(plan.panel[1] + plan.panel[3], height)

    def test_scale_and_toolbar_inset_move_the_rail(self):
        base = rail.layout([tile("lead"), tile("w1")], (1600, 900), 1.0)
        big = rail.layout([tile("lead"), tile("w1")], (1600, 900), 2.0)
        inset = rail.layout([tile("lead"), tile("w1")], (1600, 900), 1.0, left_inset=60)
        self.assertAlmostEqual(big.tiles[0].rect[2], 2 * base.tiles[0].rect[2])
        self.assertAlmostEqual(inset.panel[0], base.panel[0] + 60)

    def test_the_rail_starts_below_a_header_drawn_over_the_view(self):
        from types import SimpleNamespace as Region
        view = Region(type='WINDOW', alignment='NONE', x=0, y=0, width=1600, height=900)
        regions = [view, Region(type='HEADER', alignment='TOP', x=0, y=874, width=1600, height=26),        # Over it,
                   Region(type='TOOL_HEADER', alignment='TOP', x=0, y=848, width=1600, height=26),   # stacked.
                   Region(type='TOOLS', alignment='LEFT', x=0, y=0, width=48, height=848),
                   Region(type='UI', alignment='RIGHT', x=1400, y=0, width=200, height=848)]
        self.assertEqual(rail.top_inset(regions, view), 52)
        flipped = [view, Region(type='HEADER', alignment='BOTTOM', x=0, y=0, width=1600, height=26),   # P1-9 Q1:
                   Region(type='TOOL_HEADER', alignment='BOTTOM', x=0, y=26, width=1600, height=26),  # at the
                   Region(type='TOOLS', alignment='LEFT', x=0, y=52, width=48, height=848)]           # bottom.
        self.assertEqual(rail.top_inset(flipped, view), 0)
        base = rail.layout([tile("lead"), tile("w1")], (1600, 900), 1.0)
        below = rail.layout([tile("lead"), tile("w1")], (1600, 900), 1.0, top_inset=26)
        self.assertAlmostEqual(below.panel[1] + below.panel[3], base.panel[1] + base.panel[3] - 26)

    def test_the_rail_lays_out_below_where_blender_says_its_text_info_ended(self):
        """Seen on Mac 1: the view's name and the active object's name showed behind the rail's top. Break-it P1-13
        U1/U2: the inset is Blender's own (its last line, whatever it drew and at any display density), never lines
        counted in Python. The GUI suite checks it in the real window on held-out views."""
        self.assertEqual(rail.text_inset(900, 812), 88)
        self.assertEqual(rail.text_inset(900, None), 0)    # Before the view's first draw.
        self.assertEqual(rail.text_inset(900, 950), 0)
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        body = draw[draw.index("def _draw("):draw.index("\ndef ", draw.index("def _draw("))]
        self.assertIn("session_rail_place.column(window, area, region, tiles, scale, text, first, keep, label)", body)
        place = (ADDON / "session_rail_place.py").read_text(encoding="utf-8")
        self.assertIn("headers=rail.top_inset(area.regions, region)", place)   # One obstacle set, every position.
        strip = (ADDON / "session_rail_strip.py").read_text(encoding="utf-8")
        self.assertIn("rail.fit_below_text(hard, max(text, hard), count, height, scale, bottom)", strip)
        self.assertIn("rail.text_inset(region.height, _text_end(region))", draw[draw.index("def _steady_text("):])
        self.assertIn("text_info_end(region)", draw[draw.index("def _text_end("):])
        self.assertNotIn("pixel_size", draw)   # No line metric of its own to drift from Blender's.

    def test_the_rail_holds_still_while_the_views_text_changes_with_each_frame(self):
        """Break-it P1-14 V1: a selection's statistics, an axis view's grid unit or a shader compile change Blender's
        text from frame to frame; the rail keeps the deepest since the view's text settings changed."""
        settings = ("stats on",)
        kept = rail.steady_inset(None, settings, 88, held=False)
        self.assertEqual(kept, (settings, 88))
        self.assertEqual(rail.steady_inset(kept, settings, 60, held=False), kept)              # Less text: stays.
        self.assertEqual(rail.steady_inset(kept, settings, 120, held=False), (settings, 120))  # More: at once.
        self.assertEqual(rail.steady_inset(kept, settings, 120, held=True), kept)              # Not under a press,
        self.assertEqual(rail.steady_inset(kept, ("stats off",), 40, held=True), kept)          # whatever changed
        self.assertEqual(rail.steady_inset(kept, ("stats off",), 40, held=False), (("stats off",), 40))  # (P1-15 L2).

    def test_many_sessions_page_inside_the_view_and_every_worker_stays_reachable(self):
        """Audit P2 (e2e f67fd95c7ef): the main tile and 16 workers at 1280x720, UI scale 1, ran 7 tiles wholly below
        the view: a new session could be invisible and unclickable. Other sizes and scales, a shelf, a short view."""
        workers = [rail.Tile("w%d" % n, "Part %d" % n, "Worker %d" % n, "Working", rail.WORKING) for n in range(1, 17)]
        workers[12] = rail.Tile("w13", "Part 13", "Worker 13", "Approval", rail.APPROVAL)
        tiles = [tile("lead")] + workers
        for (width, height), scale, shelf in (((1280, 720), 1.0, 0), ((1280, 1440), 2.0, 0), ((1024, 600), 1.25, 0),
                                              ((1600, 900), 3.0, 0), ((1280, 400), 1.0, 0), ((1280, 720), 1.0, 150),
                                              ((1280, 260), 1.0, 0), ((1600, 900), 4.5, 0)):   # One tile's room.
            top = rail.fit_below_text(52 * scale, 88 * scale, len(tiles), height, scale, shelf)
            seen, first, plan = set(), 0, None
            for _ in tiles:
                plan = rail.layout(tiles, (width, height), scale, 0, top, shelf, first)
                self.assertTrue(plan.paged)
                self.assertTrue(plan.tiles[0].session == "lead" or len(plan.tiles) == 1)   # Kept when two fit.
                for placed in plan.tiles + [rail.Tile("arrows", "", "", "", rect=r) for r in plan.arrows]:
                    self.assertGreaterEqual(placed.rect[1], shelf - 1e-6, (scale, height, placed.session))
                    self.assertLessEqual(placed.rect[1] + placed.rect[3], height - top + 1e-6)
                seen |= {t.session for t in plan.tiles}
                if not plan.hidden[1]:
                    break
                first = plan.first + 1
            self.assertEqual(seen, {t.session for t in tiles}, (scale, height))
        plan = rail.layout(tiles, (1280, 720), 1.0, 0, 88)
        self.assertEqual((plan.hidden[0], plan.attention), (0, (False, True)))   # Its arrow says one waits below.
        self.assertIn(("scroll", 1), [(action, value) for _r, action, value in plan.hits])
        kept = rail.layout(tiles, (1280, 720), 1.0, 0, 88, 0, 0, keep=15)   # A switch to the last worker shows it.
        self.assertIn("w16", [t.session for t in kept.tiles])
        self.assertFalse(rail.layout(tiles[:3], (1600, 1258), 1.0, 0, 88).paged)   # Room for all: as before.
        tight = rail.layout(tiles[:2], (1280, 230), 1.0, 0, 88)   # The main tile and one worker, room for one at
        self.assertEqual((len(tight.tiles), tight.hidden, tight.paged), (2, (0, 0), False))   # 1x: both, smaller,
        self.assertLess(tight.scale, 1.0)   # never Main paged away (Windows T4) and no dead arrows (P1-18 L4).
        self.assertEqual(rail.pan_steps(0.0, 45.0, 20.0), (2, 5.0))
        self.assertEqual(rail.pan_steps(5.0, -12.0, 20.0), (0, -12.0))   # A change of direction starts again.
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        self.assertIn('elif found[0] in drawn.PAGING:\n' + " " * 24 + "drawn.scroll_by(self.window, found[1])", source)
        self.assertIn("if on_rail and plan.paged and event.type in scroll_input.SCROLL_EVENTS:", source)

    def test_a_turn_ends_as_it_ended_and_the_lead_reads_its_reply_with_the_hosts_account(self):
        """Live trial 2026-10-25 (desk_chair): a worker that built its part in one plan read as idle with no reply.
        Break-it P1-18 H3, P1-19 M1/M2, P1-20 M1-M3: every ending counts, read from what the controller recorded as the
        turn finished (later actions rewrite its live fields), a stop only when a person stopped it, a provider's
        "interrupted" otherwise a failure with its own problem, and a reply keeps the host's account after it."""
        brief, said = {"role": "user", "text": "Build the chair."}, {"role": "assistant", "text": "Built the chair."}
        activity = {"role": "activity", "text": ""}
        summary = {"role": "system", "host_summary": True, "text": "Host-verified result (no further model round was "
                   "needed):\nCommitted with one Blender Undo step: added 'Chair Base Hub' (MESH) and 18 more.\n"
                   "- object_name on 'Chair Base Hub'\n- transform\nStandalone verification passed (3 checks)."}
        notice = {"role": "system", "text": "Native plan completed."}
        failure = {"role": "system", "text": "The plan failed its checks: 'Seat' is 2 m wide."}
        accept = {"role": "user", "text": "Accept this reviewed staged preview.",
                  "request_kind": "native_preview_accept"}

        def end(rows, status="completed", stopped=False, not_sent=False, finished=None, **kw):
            return rail.turn_end(rows, outcome=(status, stopped, not_sent, len(rows) if finished is None else finished),
                                 **kw)
        self.assertEqual(rail.turn_end([brief, said]), ("", ""))   # Nothing recorded: not ended.
        self.assertEqual(end([said]), ("", ""))                    # No brief: no turn.
        self.assertEqual(end([brief, said], working=True), ("", ""))
        self.assertEqual(end([brief, said], finished=0), ("", ""))   # Recorded before this brief.
        state, reply = end([brief, said, activity, summary])   # The order both plan paths write.
        self.assertEqual(state, "completed")
        self.assertTrue(reply.startswith("Built the chair."))
        self.assertIn("Committed with one Blender Undo step", reply)
        self.assertIn("Standalone verification passed", reply)
        self.assertNotIn("- object_name", reply)
        self.assertIn("Committed", end([brief, activity, summary])[1])
        self.assertEqual(end([brief, said, activity, notice]), ("completed", "Built the chair."))
        self.assertEqual(end([brief, activity, notice]), ("completed", rail.NO_REPLY))
        self.assertEqual(end([brief, said, activity], status="escalated")[0], "completed")
        self.assertEqual(end([brief, said, failure], status="failed"),
                         ("failed", rail.TURN_FAILED % "The plan failed its checks: 'Seat' is 2 m wide."))
        self.assertEqual(end([brief, said, failure], status="interrupted")[0], "failed")   # No one stopped it.
        self.assertEqual(end([brief, said], status="interrupted", stopped=True), ("stopped", rail.STOPPED))
        timed_out = {"role": "error", "text": "Timed out"}
        self.assertEqual(end([brief, timed_out, notice], status="failed")[1], rail.TURN_FAILED % "Timed out")
        later = {"role": "system", "text": "A stronger model may handle this request better."}
        self.assertIn("2 m wide", end([brief, failure, later], status="failed", finished=2)[1])   # After it ended.
        self.assertEqual(end([brief], status="failed", not_sent=True), ("not_sent", rail.NOT_SENT))
        self.assertEqual(end([brief, said, brief, activity])[1], rail.NO_REPLY)   # Re-briefed.
        self.assertEqual(end([brief, said, activity, accept, summary])[1].split("\n")[0], "Built the chair.")
        self.assertLess(len(end([brief, {"role": "error", "text": "x" * 5000}], status="failed")[1]), 700)
        self.assertEqual(end([brief, said], reply=False), ("completed", ""))   # The draw skips the text.
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        states = draw[draw.index("def states("):draw.index("\ndef ", draw.index("def states("))]
        self.assertIn("end = turn_end(controller, reply=False)[0]", states)
        self.assertIn('replied = end == "completed"', states)
        helper = draw[draw.index("def turn_end(controller"):draw.index("def _last_failed(")]
        self.assertIn('getattr(controller, "turn_outcome", None)', helper)
        controller = (ADDON / "controller.py").read_text(encoding="utf-8")
        finished = controller[controller.index('elif kind == "finished":'):][:400]
        self.assertIn('self.turn_outcome = (data.get("status"), bool(self.cancelled), data.get("not_sent") is True, '
                      'len(self.state["messages"]))', finished)
        external = (ADDON / "controller_external.py").read_text(encoding="utf-8")   # Every request's end records.
        self.assertIn("self.turn_outcome = (", external[external.index("def close_external_read("):])
        status = (ADDON / "worker_sessions.py").read_text(encoding="utf-8")
        self.assertIn("turn, reply = session_rail_draw.turn_end(worker)", status)
        self.assertIn('"turn": turn,', status)
        cases = (ADDON.parents[2] / "tests/python/angelo_agent/parallel_live_cases.py").read_text(encoding="utf-8")
        self.assertIn('part.get("turn") == "completed"', cases)

    def test_an_ended_worker_can_be_closed_whatever_its_tile_says(self):
        """Audit finding 3: a crashed worker whose last turn failed showed FAILED with no close button, so its tile
        stayed. Closable follows the session (ended or merged); the tile keeps saying what happened."""
        crashed = rail.Tile("w1", "Room", "Worker 1", "Stopped with an error", rail.FAILED, closable=True)
        failing = rail.Tile("w2", "Lamp", "Worker 2", "Stopped with an error", rail.FAILED)
        plan = rail.layout([tile("lead"), crashed, failing], (1600, 900), 1.0)
        self.assertEqual([t.kind for t in plan.tiles[1:]], [rail.FAILED, rail.FAILED])
        self.assertIsNotNone(plan.tiles[1].close)
        self.assertIsNone(plan.tiles[2].close)   # A worker still running after a failed turn stays.
        self.assertIn(("dismiss", "w1"), [(action, value) for _r, action, value in plan.hits])
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        self.assertIn("closable=bool(gone or parallel_sessions.is_merged(session_id))", draw)
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        dismiss = source[source.index("def dismiss("):source.index("def remove(")]
        self.assertIn("parallel_sessions.ended(session_id) or parallel_sessions.is_merged(session_id)", dismiss)
        # A press acts only when released on the same action: a press on a tile, a release on its X does nothing.
        self.assertIn("if found and drawn.pressed.get(self.window) == found:", source)

    def test_selecting_the_leads_tile_brings_its_scene_back_and_a_workers_never_sets_the_windows_scene(self):
        """Break-it E2E-20 R5-M-1 (the maintainer: the selected session shows as the big view, nothing else changes):
        the lead's tile sets the main window's scene to the lead's own (``document_scene.show``); a worker's tile draws
        its part over the big view and never touches ``window.scene``."""
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        switch = ast.parse(source[source.index("def switch("):source.index("def dismiss(")])
        shows = [node for node in ast.walk(switch) if isinstance(node, ast.Call)
                 and ast.unparse(node.func) == "document_scene.show"]
        self.assertEqual([ast.unparse(node) for node in shows],
                         ["document_scene.show(ui._controllers[session_id], ui.main_window())"])
        guard = next(node for node in ast.walk(switch) if isinstance(node, ast.If) and shows[0] in ast.walk(node))
        self.assertEqual(ast.unparse(guard.test),
                         "not parallel_sessions.is_worker(session_id) and session_id in ui._controllers")
        self.assertNotIn(".scene =", source)   # No other route in the rail's input sets a window's scene.
        draw = (ADDON / "live_view_draw.py").read_text(encoding="utf-8")
        self.assertNotIn(".scene =", draw)

    def test_a_switch_or_the_start_of_paging_brings_the_active_worker_into_view(self):
        """Break-it P1-18 L1/M3: a switch to a hidden worker shows it; the artist's own scroll stays otherwise."""
        self.assertEqual(rail.keep_in_view(5, "w6", "w2", True), 5)    # Switched to another worker.
        self.assertEqual(rail.keep_in_view(5, "w6", None, True), 5)    # From the main session.
        self.assertIsNone(rail.keep_in_view(5, "w6", "w6", True))      # Same worker, already paged: the scroll stays.
        self.assertEqual(rail.keep_in_view(5, "w6", "w6", False), 5)   # The rail starts to page.
        self.assertIsNone(rail.keep_in_view(None, None, "w6", True))   # The main session is active.
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        self.assertIn("count = str(len(tiles))", draw)   # The header counts every session, the hidden ones too.

    def test_tiles_stay_above_every_region_stacked_at_the_bottom(self):
        """Break-it P1-16 L1: an asset shelf's tab strip sits on the shelf, above the view's bottom edge."""
        from types import SimpleNamespace as Region
        view = Region(type='WINDOW', alignment='NONE', x=0, y=0, width=1600, height=900)
        shelf = Region(type='ASSET_SHELF', alignment='BOTTOM', x=0, y=0, width=1600, height=120)
        strip = Region(type='ASSET_SHELF_HEADER', alignment='BOTTOM', x=0, y=120, width=1600, height=26)
        toolbar = Region(type='TOOLS', alignment='LEFT', x=0, y=0, width=48, height=900)
        self.assertEqual(rail.bottom_inset([view, shelf, strip, toolbar], view), 146)
        self.assertEqual(rail.bottom_inset([view, toolbar], view), 0)
        hidden = Region(type='ASSET_SHELF', alignment='BOTTOM', x=0, y=0, width=1600, height=1)
        self.assertEqual(rail.bottom_inset([view, hidden], view), 0)

    def test_only_a_drawn_tile_holds_the_rail_and_nothing_outlives_the_handler(self):
        """Break-it P1-16 L3: a hovered tile removed under a still pointer, another modal holding the input, a window
        closed: none leaves the rail held."""
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        self.assertIn("held = hover.get(key) in drawn or hover.get(key) in arrows or press[1] in drawn or press[0] in "
                      "PAGING", draw)
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        blocking = source[source.index("input_policy.canvas_blocking(window):"):]
        self.assertIn("self._leave(window, cursor=False)", blocking[:blocking.index("return {'PASS_THROUGH'}")])
        cancel = source[source.index("    def cancel(self, context):"):source.index("    @_fail_closed")]
        self.assertIn("for table in (drawn.hover, drawn.pressed, drawn.pan):", cancel)

    def test_every_setting_that_changes_the_views_text_is_in_its_key(self):
        """Break-it P1-15 L3, P1-16 L4: a setting dropped from the key would leave the rail low after it changes."""
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        key = draw[draw.index("def _text_settings("):draw.index("def _steady_text(")]
        for member in ("overlay.show_overlays", "overlay.show_text", "overlay.show_stats", "overlay.show_performance",
                       "overlay.show_floor", "overlay.show_axis_x", "overlay.show_axis_y", "overlay.show_axis_z",
                       "view.show_view_name", "view.show_object_info", "view.show_playback_fps",
                       "window.screen.is_animation_playing", "bpy.context.mode", "space.region_quadviews",
                       "space.shading.type", "window.scene.render.engine", "space.show_region_header",
                       "space.show_region_tool_header", "system.use_region_overlap", "system.ui_scale",
                       ".widget.points"):
            self.assertIn(member, key)

    def test_a_press_ends_with_its_release_wherever_it_lands(self):
        """Break-it P1-15 M1/L1: a press dragged off its tile and released over the view, or a window left while
        hovering, kept the rail held, so new text hid behind it. Operators are mocks here: pinned at the source."""
        from angelo_agent import session_rail_guard
        from types import SimpleNamespace
        unpressed = []
        handler = SimpleNamespace(window=7, _unpress=lambda window: unpressed.append(window))
        window = SimpleNamespace(as_pointer=lambda: 7)

        def raises(_self, _context, _event):
            raise RuntimeError("a failing event")
        for event_type, value in (('LEFTMOUSE', 'RELEASE'), ('LEFTMOUSE', 'PRESS'), ('MOUSEMOVE', 'NOTHING')):
            event = SimpleNamespace(type=event_type, value=value)
            ended = session_rail_guard.ends_presses(lambda _self, _context, _event: {'PASS_THROUGH'})
            self.assertEqual(ended(handler, SimpleNamespace(window=window), event), {'PASS_THROUGH'})
            with self.assertRaises(RuntimeError):   # Even when the handler raises (fail_closed catches it after).
                session_rail_guard.ends_presses(raises)(handler, SimpleNamespace(window=window), event)
        self.assertEqual(unpressed, [window, window])   # Only the two releases ended the press.
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        self.assertIn("    @_fail_closed\n    @_ends_presses\n    def modal(self, context, event):", source)
        body = source[source.index("    def modal(self, context, event):"):]
        deactivate = body[body.index("if event.type == 'WINDOW_DEACTIVATE':"):body.index("if input_policy.not_input")]
        self.assertIn("self._leave(window)", deactivate)
        leave = source[source.index("    def _leave("):source.index("    def _unpress(")]
        self.assertIn("self._unpress(window)", leave)
        self.assertIn("self._redraw(window)", source[source.index("    def _unpress("):])

    def test_tiles_never_leave_a_short_view_for_the_text(self):
        """Break-it P1-14 V5: below the text when there is room; over its last lines rather than out of reach."""
        self.assertEqual(rail.fit_below_text(52, 200, 3, 1258, 1.0), 200)
        self.assertEqual(rail.fit_below_text(52, 30, 3, 1258, 1.0), 52)
        short = rail.fit_below_text(52, 400, 3, 500, 1.0)
        self.assertLess(short, 400)
        plan = rail.layout([tile("lead"), tile("w1"), tile("w2")], (300, 500), 1.0, top_inset=short)
        self.assertGreaterEqual(plan.panel[1], rail.MARGIN - 1e-6)   # Every tile inside the view.
        shelf = rail.fit_below_text(52, 400, 3, 500, 1.0, bottom=90)   # An asset shelf over the bottom (P1-15 L4).
        plan = rail.layout([tile("lead"), tile("w1"), tile("w2")], (300, 500), 1.0, top_inset=shelf, bottom_inset=90)
        self.assertGreaterEqual(plan.panel[1], 90 + rail.MARGIN - 1e-6)

    def test_approval_shows_a_badge_on_the_picture(self):
        plan = rail.layout([tile("lead"), tile("w1", rail.APPROVAL)], (1600, 900), 1.0)
        self.assertIsNone(plan.tiles[0].badge)
        bx, by, bw, bh = plan.tiles[1].badge
        px, py, pw, ph = plan.tiles[1].picture
        self.assertTrue(px <= bx and bx + bw <= px + pw and py <= by and by + bh <= py + ph)

    def test_the_one_state_a_tile_shows(self):
        self.assertEqual(rail.tile_kind(True, True, False, False), rail.APPROVAL)
        self.assertEqual(rail.tile_kind(True, False, True, False), rail.WORKING)
        self.assertEqual(rail.tile_kind(False, False, True, True), rail.FAILED)
        self.assertEqual(rail.tile_kind(False, False, True, False), rail.ENDED)
        self.assertEqual(rail.tile_kind(False, False, False, False), rail.IDLE)
        self.assertEqual(rail.status(rail.WORKING, "Thinking", "1m 9s"), "Thinking · 1m 9s")
        self.assertEqual(rail.status(rail.APPROVAL), "Waiting for your approval")

    def test_done_only_when_its_plan_is_done_else_finished_its_turn(self):
        """Break-it P1-8 P7: an idle worker that replied but did not report every step done (a question, a part it
        could not build, a stop) never claims a pending merge."""
        self.assertEqual(rail.tile_kind(False, False, False, False, done=True, replied=True), rail.DONE)
        self.assertEqual(rail.tile_kind(False, False, False, False, replied=True), rail.REPLIED)
        self.assertEqual(rail.tile_kind(False, False, False, True, replied=True), rail.FAILED)
        self.assertEqual(rail.tile_kind(True, False, False, False, replied=True), rail.WORKING)
        self.assertEqual(rail.status(rail.REPLIED), "Finished its turn")
        self.assertEqual(rail.status(rail.DONE), "Done, waiting for merge")

    def test_a_tile_with_counts_gives_them_a_line_of_their_own(self):
        plan = rail.layout([rail.Tile("w1", "Room", "Room", "Carving · 3 of 7 steps · 43%", rail.WORKING,
                                      progress=(3, 7), status_head="Carving", status_keep="3 of 7 steps · 43%")],
                           (1600, 900), 1.0)
        placed = plan.tiles[0]
        bottom = placed.rect[1]
        self.assertGreater(placed.title_at[1], placed.status_at[1])
        self.assertGreater(placed.status_at[1], placed.detail_at[1])
        self.assertGreater(placed.detail_at[1], placed.bar[1] + placed.bar[3])
        self.assertGreater(placed.bar[1], bottom)

    def test_the_counts_and_clock_are_the_part_a_narrow_tile_keeps(self):
        """Break-it P1-8 P8: the step title is cut first; "3 of 7 steps · 43%" and the clock stay."""
        head, keep = rail.status_parts(rail.WORKING, "Working in Angelo", "1m 9s", "Carving the mane", (3, 7))
        self.assertEqual((head, keep), ("Carving the mane", "3 of 7 steps · 43% · 1m 9s"))
        self.assertEqual(rail.status(rail.WORKING, "Working in Angelo", "1m 9s", "Carving the mane", (3, 7)),
                         "Carving the mane · 3 of 7 steps · 43% · 1m 9s")
        self.assertEqual(rail.status_parts(rail.DONE), ("Done, waiting for merge", ""))
        self.assertEqual(rail.status_parts(rail.WORKING, "Thinking", ""), ("Thinking", ""))


class OrderTests(unittest.TestCase):
    def test_workers_stand_in_launch_order_whatever_order_they_joined(self):
        """Break-it P1-11 S7: on build4 Mug was ready before Kettle; Worker 1 still stands above Worker 2."""
        numbers = {"mug": 2, "kettle": 1}
        order = rail.ordered(["lead", "mug", "kettle"], lambda s: s != "lead", lambda s: numbers.get(s, 99))
        self.assertEqual(order, ["lead", "kettle", "mug"])
        self.assertEqual(rail.ordered(["lead", "b", "a"], lambda s: s != "lead", lambda s: 99), ["lead", "b", "a"])


class RedrawTests(unittest.TestCase):
    """Break-it P1-8 P1: the rail draws itself again when what it shows changes, and only then, at most RATE/s."""

    def tiles(self, status="Carving · 1 of 3 steps · 33% · 9s"):
        return [tile("lead"), rail.Tile("w1", "Room", "Room", status, rail.WORKING, progress=(1, 3))]

    def test_a_change_on_the_rail_is_drawn_and_an_unchanged_rail_is_not(self):
        drawn = rail_redraw.signature(4, self.tiles())
        self.assertFalse(rail_redraw.due(drawn, rail_redraw.signature(4, self.tiles()), -1e9, 10.0))
        for changed in (rail_redraw.signature(5, self.tiles()),   # A part read again, a picture, a state.
                        rail_redraw.signature(4, self.tiles("Carving · 1 of 3 steps · 33% · 10s")),   # The clock.
                        rail_redraw.signature(4, self.tiles()[:1])):   # A tile closed.
            self.assertTrue(rail_redraw.due(drawn, changed, -1e9, 10.0))
        self.assertTrue(rail_redraw.due(None, rail_redraw.signature(4, self.tiles()), -1e9, 10.0))   # Never drawn.

    def working(self, clock, step="Carving", counts="1 of 3 steps · 33%", head=None, done=1):
        return [tile("lead"), rail.Tile("w1", "Room", "Room", " · ".join([head or step, counts, clock]), rail.WORKING,
                                        progress=(done, 3), status_head=head or step, status_keep=counts + " · " + clock,
                                        step=step)]

    def test_streamed_text_is_paced_like_the_clock(self):
        """Break-it P1-12 T1: a live label a model streams (its phase, a thinking title, a plan's count, a countdown)
        is no committed fact; the step a worker is on, its counts and its state are."""
        drawn = rail_redraw.signature(1, self.working("9s", step="", head="Thinking"))
        streamed = rail_redraw.signature(1, self.working("9s", step="", head="Writing the plan: +3"))
        committed = rail_redraw.signature(1, self.working("9s", step="Legs", done=2))
        replanned = rail_redraw.signature(1, self.working("9s", step="Legs", head="Legs"))   # The step alone.
        self.assertEqual(drawn[0], streamed[0])
        self.assertNotEqual(drawn, streamed)
        self.assertNotEqual(drawn[0], committed[0])
        self.assertNotEqual(rail_redraw.signature(1, self.working("9s", step="Seat"))[0], replanned[0])
        # The tiles the rail draws carry the worker's committed step, never the streamed label (P1-13 U4).
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        states = draw[draw.index("def states("):draw.index("\ndef ", draw.index("def states("))]
        self.assertIn("step = parallel_sessions.current_step(session_id) if worker else \"\"", states)
        self.assertIn("status_keep=keep, step=step,", states)
        self.assertFalse(rail_redraw.due(drawn, streamed, 10.0, 10.5, cost=0.004))
        self.assertTrue(rail_redraw.due(drawn, committed, 10.0, 10.25, cost=0.004))

    def test_the_recheck_is_moved_earlier_for_a_fact_and_never_later(self):
        """Break-it P1-12 T2: a streamed change arms the re-check seconds ahead; a fact due sooner moves it."""
        self.assertTrue(rail_redraw.rearm(None, 12.0))
        self.assertTrue(rail_redraw.rearm(15.0, 10.6))
        self.assertFalse(rail_redraw.rearm(10.6, 15.0))
        self.assertFalse(rail_redraw.rearm(10.6, 10.6))

    def test_a_redraw_the_rail_asked_for_is_costed_from_its_tag(self):
        """Break-it P1-11 S2: the tag-to-draw time counts the sync before PRE_VIEW; capped, and the larger measure."""
        import time as clock
        window, area = 101, 202
        tables = (rail_redraw.view_cost, rail_redraw._view_started, rail_redraw._tag_started, rail_redraw.shown,
                  rail_redraw.drawn_at)
        saved = tuple(dict(table) for table in tables)
        try:
            rail_redraw.view_cost.pop(window, None)
            now = clock.perf_counter()
            rail_redraw._view_started[area], rail_redraw._tag_started[window] = now - 0.004, now - 0.030
            rail_redraw.note_drawn(window, 1, [tile("lead")], area)
            self.assertAlmostEqual(rail_redraw.view_cost[window][-1], 0.030, delta=0.01)
            rail_redraw._tag_started[window] = clock.perf_counter() - 9.0   # A window not drawn for a while.
            rail_redraw.note_drawn(window, 1, [tile("lead")], area)
            self.assertLessEqual(rail_redraw.view_cost[window][-1], rail_redraw.MAX_SAMPLE)
        finally:
            for table, kept in zip(tables, saved):
                table.clear()
                table.update(kept)

    def test_a_clock_alone_never_takes_more_than_five_percent_on_any_view(self):
        """Break-it P1-11 S1: a working tile's clock changes every second; alone, it is redrawn no sooner than a
        second and 20 times the view's redraw cost, uncapped. A committed fact is not held back by it."""
        drawn = rail_redraw.signature(1, self.working("9s"))
        ticked = rail_redraw.signature(1, self.working("10s"))
        stepped = rail_redraw.signature(1, self.working("10s", step="Sanding", counts="2 of 3 steps · 67%"))
        self.assertEqual(drawn[0], ticked[0])   # The clock is no fact.
        self.assertNotEqual(drawn[0], stepped[0])
        self.assertFalse(rail_redraw.due(drawn, ticked, 10.0, 10.9, cost=0.004))
        self.assertTrue(rail_redraw.due(drawn, ticked, 10.0, 11.0, cost=0.004))
        self.assertFalse(rail_redraw.due(drawn, ticked, 10.0, 12.9, cost=0.15))   # A 150 ms view: every 3 s.
        self.assertTrue(rail_redraw.due(drawn, ticked, 10.0, 13.01, cost=0.15))
        self.assertTrue(rail_redraw.due(drawn, stepped, 10.0, 10.0 + rail_redraw.FACT_WAIT, cost=0.15))
        self.assertEqual(rail_redraw.wait(drawn, ticked, 10.0, 10.25, cost=0.004), 0.75)
        self.assertIsNone(rail_redraw.wait(drawn, drawn, 10.0, 99.0))

    def test_tags_are_coalesced_to_the_rate(self):
        drawn, changed = rail_redraw.signature(1, self.tiles()), rail_redraw.signature(2, self.tiles())
        period = 1.0 / rail_redraw.RATE
        self.assertFalse(rail_redraw.due(drawn, changed, 10.0, 10.0 + period * 0.9))
        self.assertTrue(rail_redraw.due(drawn, changed, 10.0, 10.0 + period))

    def test_a_fact_waits_twenty_times_the_view_cost_but_never_more_than_fact_wait(self):
        """Break-it P1-9 Q3: each tag redraws the artist's document; a view whose redraw costs 30 ms waits 0.6 s."""
        drawn, changed = rail_redraw.signature(1, self.tiles()), rail_redraw.signature(2, self.tiles())
        self.assertFalse(rail_redraw.due(drawn, changed, 10.0, 10.5, cost=0.03))
        self.assertTrue(rail_redraw.due(drawn, changed, 10.0, 10.61, cost=0.03))
        self.assertTrue(rail_redraw.due(drawn, changed, 10.0, 10.0 + 1.0 / rail_redraw.RATE, cost=0.004))
        # A committed fact never waits more than FACT_WAIT, however slow the view, and one slow frame among quick ones
        # does not hold the rail back (P1-10 R3).
        self.assertLess(rail_redraw.FACT_WAIT, 1.0)
        self.assertEqual(rail_redraw.spacing(0.3), rail_redraw.FACT_WAIT)
        self.assertTrue(rail_redraw.due(drawn, changed, 10.0, 10.0 + rail_redraw.FACT_WAIT, cost=0.3))
        self.assertEqual(rail_redraw.recent_cost([0.004, 0.3, 0.005, 0.004, 0.006]), 0.005)
        self.assertEqual(rail_redraw.recent_cost([0.004, 2.5]), 0.004)   # Two samples: never the slow one alone.
        self.assertEqual(rail_redraw.recent_cost([]), 0.0)
        source = (ADDON / "rail_redraw.py").read_text(encoding="utf-8")
        tick = source[source.index("def tick("):source.index("def release(")]
        self.assertIn("region.tag_redraw()", tick)   # Its main regions, never the whole area's headers and bars.
        self.assertNotIn("area.tag_redraw()", tick)

    def test_the_rail_draw_records_what_it_showed_and_its_timers_tag(self):
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        self.assertIn("rail_redraw.note_drawn(", draw[draw.index("def draw("):draw.index("def _draw(")])
        for module, function in (("rail_pictures.py", "def _tick("), ("parallel_sessions.py", "def _tick(")):
            source = (ADDON / module).read_text(encoding="utf-8")
            body = source[source.index(function):]
            self.assertIn("rail_redraw.tick()", body[:body.index("\ndef ", 1)], module)


class ScheduleTests(unittest.TestCase):
    def test_waits_for_idle_and_paces_each_session(self):
        choose = live_schedule.choose
        self.assertIsNone(choose({"w1": 0.0}, {}, None, now=0.5, idle_for=0.1, held=False))   # Input, not late yet.
        self.assertEqual(choose({"w1": 0.0}, {}, None, now=0.5, idle_for=1.0, held=False), "w1")
        self.assertIsNone(choose({"w1": 0.0}, {}, None, now=1.0, idle_for=1.0, held=True))
        rail_every = live_schedule.RAIL_INTERVAL
        self.assertIsNone(choose({"w1": 9.0}, {"w1": 9.6 - rail_every}, None, now=9.5, idle_for=5, held=False))
        self.assertEqual(choose({"w1": 9.0}, {"w1": 9.49 - rail_every}, None, now=9.5, idle_for=5, held=False), "w1")
        self.assertEqual(choose({"w1": 9.0}, {"w1": 9.6 - rail_every}, "w1", now=9.5, idle_for=5, held=False),
                         "w1")   # The active session: every ACTIVE_INTERVAL.

    def test_every_committed_edit_is_read_within_its_bound_for_any_input(self):
        """Break-it P1-8 P4: the bound the policy guarantees, over many inputs: four workers saving at random
        times, the artist idle or never idle, each part cheap enough that their reads fit in a frame together. Every
        save is read within ``max(RAIL_INTERVAL, MAX_DEFER) + POLL`` (saves that turn due together are read in one
        tick, ``batch``)."""
        import random
        poll = live_schedule.POLL
        bound = max(live_schedule.RAIL_INTERVAL, live_schedule.MAX_DEFER) + poll + 1e-9
        worst = 0.0
        for seed in range(40):
            rng = random.Random(seed)
            idle_for = 0.0 if seed % 2 else 10.0
            saves = {name: sorted(rng.uniform(0, 20) for _ in range(rng.randrange(5, 80)))
                     for name in ("w1", "w2", "w3", "w4")}
            costs = {name: rng.uniform(0.0, live_schedule.FRAME / 4) for name in saves}
            pending, last, now = {}, {}, 0.0
            while now < 22.0:
                for name, times in saves.items():
                    while times and times[0] <= now:
                        pending.setdefault(name, times.pop(0))
                for chosen in live_schedule.batch(pending, last, "w1", now, idle_for, False, costs):
                    worst = max(worst, now - pending.pop(chosen))
                    last[chosen] = now
                now = round(now + poll, 6)
            self.assertFalse(pending, seed)
        self.assertLessEqual(worst, bound, worst)

    def test_many_workers_saving_at_once_drain_a_frame_of_reads_at_a_time(self):
        """Break-it P1-20 M4: the count follows the machine (up to 16). At the measured ~4 ms a read, a frame holds
        about four, so a tick that read while saves still wait looks again ``BACKLOG`` later (``next_poll``): ten
        workers stay within the bound, sixteen add at most 0.05 s (0.83 s at sixteen with the 0.1 s poll alone)."""
        import random
        bound = max(live_schedule.RAIL_INTERVAL, live_schedule.MAX_DEFER) + live_schedule.POLL + 1e-9
        for count, allowed in ((10, bound), (16, bound + 0.05)):
            worst = 0.0
            for seed in range(40):
                rng = random.Random(seed)
                idle_for = 0.0 if seed % 2 else 10.0
                saves = {"w%d" % n: sorted(rng.uniform(0, 20) for _ in range(rng.randrange(5, 80)))
                         for n in range(1, count + 1)}
                costs = {name: 0.004 for name in saves}
                pending, last, now = {}, {}, 0.0
                while now < 30.0:
                    for name, times in saves.items():
                        while times and times[0] <= now:
                            pending.setdefault(name, times.pop(0))
                    read = live_schedule.batch(pending, last, "w1", now, idle_for, False, costs)
                    for chosen in read:
                        worst = max(worst, now - pending.pop(chosen))
                        last[chosen] = now
                    now = round(now + (live_schedule.next_poll(bool(read), bool(pending)) or live_schedule.POLL), 6)
                self.assertFalse(pending, seed)
            self.assertLessEqual(worst, allowed, (count, worst))
        self.assertEqual(live_schedule.next_poll(True, True), live_schedule.BACKLOG)
        self.assertEqual(live_schedule.next_poll(False, True), live_schedule.POLL)
        self.assertIsNone(live_schedule.next_poll(True, False))
        source = (ADDON / "parallel_sessions.py").read_text(encoding="utf-8")
        self.assertIn("return live_schedule.next_poll(bool(last_pumped), bool(pending))", source)

    def test_save_to_screen_is_within_a_second_on_a_cheap_view(self):
        """The whole path for a cheap part on a cheap view: read within the bound above, its picture in the same tick,
        the rail drawn again within one ``rail_redraw.RATE`` period, then one frame (a heavy view waits up to
        ``rail_redraw.FACT_WAIT`` instead)."""
        read = max(live_schedule.RAIL_INTERVAL, live_schedule.MAX_DEFER) + live_schedule.POLL
        self.assertLessEqual(read + 1.0 / rail_redraw.RATE + live_schedule.FRAME, 1.0)

    def test_a_tick_reads_every_due_part_that_fits_in_a_frame(self):
        due = {"w1": 0.0, "w2": 0.0, "w3": 0.0, "w4": 0.0}
        cheap = {name: 0.003 for name in due}
        self.assertEqual(live_schedule.batch(due, {}, None, now=2.0, idle_for=0.0, held=False, costs=cheap),
                         ["w1", "w2", "w3", "w4"])
        heavy = {"w1": 0.010, "w2": 0.010, "w3": 0.003, "w4": 0.003}
        self.assertEqual(live_schedule.batch(due, {}, None, now=2.0, idle_for=10.0, held=False, costs=heavy),
                         ["w1"])   # 10 ms, then the next 10 ms would pass the frame: the rest wait a tick.
        self.assertEqual(live_schedule.batch(due, {}, None, now=2.0, idle_for=0.0, held=True, costs=cheap), [])

    def test_overdue_saves_run_during_input_oldest_first_but_never_while_held(self):
        pending = {"w1": 0.0, "w2": 1.0}
        self.assertEqual(live_schedule.choose(pending, {}, None, now=7.0, idle_for=0.0, held=False), "w1")
        self.assertIsNone(live_schedule.choose(pending, {}, None, now=600.0, idle_for=600.0, held=True))

    def test_a_heavy_part_is_read_less_often_and_never_forced_during_input(self):
        costs = {"w1": 0.3}   # A heavy part: 0.3 s per read and first draw.
        self.assertIsNone(live_schedule.choose({"w1": 0.0}, {"w1": 1.0}, None, now=6.0, idle_for=1.0, held=False,
                                               costs=costs))   # 20 x 0.3 s: not before 6 s after the last read.
        self.assertEqual(live_schedule.choose({"w1": 0.0}, {"w1": 1.0}, None, now=7.5, idle_for=1.0, held=False,
                                              costs=costs), "w1")
        self.assertIsNone(live_schedule.choose({"w1": 0.0}, {"w1": 1.0}, None, now=60.0, idle_for=0.0, held=False,
                                               costs=costs))   # Late, but too costly to run while the artist works.

    def test_the_active_session_goes_first(self):
        pending = {"w1": 0.0, "w2": 1.0}
        self.assertEqual(live_schedule.choose(pending, {}, "w2", now=2.0, idle_for=1.0, held=False), "w2")


class LiveLayoutTests(unittest.TestCase):
    def test_clean_rejects_what_is_not_a_layout(self):
        self.assertIsNone(live_layout.clean(None))
        self.assertIsNone(live_layout.clean({"revision": "1", "collections": [], "objects": []}))
        self.assertIsNone(live_layout.clean({"revision": 1, "collections": [""], "objects": []}))
        self.assertIsNone(live_layout.clean({"revision": 1, "collections": [], "objects": [], "world": 3}))
        good = live_layout.clean({"revision": 2, "collections": ["A", "A"], "objects": ["Cube"], "world": "W"})
        self.assertEqual(good["collections"], ["A"])

    def test_plan_links_new_names_keeps_order_and_drops_deleted(self):
        to_load, kept = live_layout.plan(["Floor", "Table", "Lamp"], ["Floor", "Chair"], ["Floor", "Table", "Lamp"])
        self.assertEqual(to_load, ["Table", "Lamp"])
        self.assertEqual(kept, ["Floor", "Table", "Lamp"])
        to_load, kept = live_layout.plan(["Floor"], ["Floor", "Chair"], ["Floor"])
        self.assertEqual((to_load, kept), ([], ["Floor"]))
        self.assertEqual(live_layout.plan(["Ghost"], [], ["Floor"]), ([], []))

    def test_the_worker_reports_layout_with_each_saved_edit(self):
        source = (ADDON / "worker_host.py").read_text(encoding="utf-8")
        self.assertIn('"layout": self.layout() if saved else None', source)
        self.assertIn('if op == "layout":', source)


def item(idname, kind, value="PRESS", **mods):
    return nav.Item(idname, kind, value, **mods)


class LiveInputTests(unittest.TestCase):
    """Over a live view only window-wide commands that never edit pass to Blender (break-it P1-2 H1)."""

    ITEMS = (item("wm.save_mainfile", "S", oskey=1), item("wm.quit_blender", "Q", oskey=1),
             item("angelo.toggle_chat", "N", ctrl=1, alt=1))

    def allowed(self, kind, value="PRESS", **mods):
        return nav.allowed(nav.Event(kind, value, **mods), self.ITEMS)

    def test_window_commands_that_never_edit_pass(self):
        self.assertTrue(self.allowed("S", oskey=True))
        self.assertTrue(self.allowed("Q", oskey=True))
        self.assertTrue(self.allowed("N", ctrl=True, alt=True))

    def test_edit_keys_clicks_and_interface_items_never_pass(self):
        for kind, mods in (("X", {}), ("DEL", {}), ("I", {}), ("E", {}), ("K", {}), ("G", {}), ("LEFTMOUSE", {}),
                           ("RIGHTMOUSE", {}), ("Z", {"oskey": True}), ("F3", {}), ("F9", {}), ("S", {})):
            self.assertFalse(self.allowed(kind, **mods), kind)

    def test_only_allowed_commands_are_ever_read_from_the_keymap(self):
        self.assertEqual(nav.GLOBAL_KEYMAPS, ("Window", "Screen"))   # Never Screen Editing or User Interface.
        for name in nav.ALLOWED:
            self.assertFalse(name.startswith(("ui.", "object.", "mesh.", "transform.", "view3d.", "anim.", "ed.")),
                             name)

    def test_the_artists_own_bindings_decide(self):
        custom = (item("wm.save_mainfile", "F2", any=True),)
        self.assertTrue(nav.allowed(nav.Event("F2", "PRESS", ctrl=True), custom))
        inactive = (nav.Item("wm.save_mainfile", "S", "PRESS", oskey=1, active=False),)
        self.assertFalse(nav.allowed(nav.Event("S", "PRESS", oskey=True), inactive))

    def test_the_handler_takes_everything_else_over_a_live_view(self):
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        live = source[source.index("    def _live("):source.index("    def _cursor(")]
        self.assertIn("input_policy.edits_field(event)", live)   # Chat typing: its field keys only (P1-2 M2).
        self.assertIn("live_nav.handle(", live)
        self.assertTrue(live.rstrip().endswith("return {'RUNNING_MODAL'}"))
        self.assertIn("card_host.takes_input(", source)           # P1-2 M1.


class WorkerToolTests(unittest.TestCase):
    START, STATUS = (worker_spec.PREFIX + "start", worker_spec.PREFIX + "status")
    SPECS = {tool["name"]: tool for tool in worker_spec.WORKER_TOOLS}

    def test_start_validates_parts(self):
        good = worker_spec.validate_worker(self.START, {"parts": [{"name": "  Rook ", "brief": "A rook, 6 cm."}]})
        self.assertEqual(good["parts"][0]["name"], "Rook")
        for parts in ([], [{"name": "A", "brief": "x"}] * 5, [{"name": "", "brief": "x"}],
                      [{"name": "A", "brief": "x"}, {"name": "a", "brief": "y"}], [{"name": "A"}],
                      [{"name": "A\x00", "brief": "x"}]):
            with self.assertRaises(ValueError):
                worker_spec.validate_worker(self.START, {"parts": parts})

    def test_start_needs_approval_and_status_does_not(self):
        self.assertFalse(self.SPECS[self.START]["annotations"]["readOnlyHint"])
        self.assertTrue(self.SPECS[self.STATUS]["annotations"]["readOnlyHint"])
        from angelo_agent.tool_specs import BOUNDED_MUTATIONS
        from angelo_agent.external_clients import EDIT_TOOLS
        # Auto starts workers as it applies typed edits (no click inside a run, coordinator 2026-10-24); an Ask chat
        # shows the card; a connected app never gets the family.
        self.assertIn(self.START, BOUNDED_MUTATIONS)
        self.assertNotIn(self.START, EDIT_TOOLS)

    def test_a_build_launched_without_it_lists_none_of_it(self):
        """The host suite runs without ANGELO_ENABLE_PARALLEL_AGENTS: the tool list is the one without phase 1."""
        if parallel_access.enabled():
            self.skipTest("this run enabled parallel agents")
        self.assertFalse(set(TOOL_MAP) & worker_spec.WORKER_NAMES)
        self.assertEqual(parallel_access.hidden_tools(), worker_spec.FAMILY_NAMES)
        source = (ADDON / "tool_specs.py").read_text(encoding="utf-8")
        self.assertIn("if parallel_access_enabled():   # Parallel edit", source)
        self.assertLess(source.index("TOOLS.extend(worker_tools_for_launch(parallel_worker_limit(), "
                                     "parallel_limit_where()) + PART_TOOLS)"),
                        source.index("TOOLS.extend(TEAM_TOOLS)"))

    def test_on_the_lead_gets_it_and_a_worker_never_does(self):
        names = set(TOOL_MAP) | worker_spec.WORKER_NAMES
        with patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "1"}):
            self.assertTrue(parallel_access.enabled())
            self.assertIsNone(parallel_access.restrict(None, names))
            self.assertFalse(parallel_access.restrict(None, names, worker=True) & worker_spec.WORKER_NAMES)
        with patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "0"}):
            self.assertIsNone(parallel_access.restrict(None, names))   # Off: not in the build; nothing to remove.

    def test_apps_and_voice_never_get_it(self):
        source = (ADDON / "external_clients.py").read_text(encoding="utf-8")
        self.assertIn('"angelo_workers_"', source[source.index("NEVER_FOR_APPS"):][:300])
        from angelo_agent.tool_specs import dynamic_tools
        self.assertFalse({t["name"] for t in dynamic_tools()} & worker_spec.WORKER_NAMES)

    def test_the_request_gate_is_the_one_routing_applies(self):
        source = (ADDON / "routing.py").read_text(encoding="utf-8")
        self.assertIn("for_chat(allowed_tools(TOOL_MAP), TOOL_MAP, self.host)", source)


class PrivatePartTests(unittest.TestCase):
    """A worker's part lives in a private Main (``_bpy._angelo_live``), never in Angelo's data (break-it P1-1 H2)."""

    def test_no_part_is_ever_linked_into_angelos_data(self):
        for name in ("live_view.py", "live_view_draw.py", "rail_pictures.py", "parallel_sessions.py"):
            source = (ADDON / name).read_text(encoding="utf-8")
            for forbidden in ("libraries.load", "libraries.remove", ".reload()", "scenes.new", "is_runtime_data"):
                self.assertNotIn(forbidden, source, (name, forbidden))
        self.assertIn("_bpy._angelo_live", (ADDON / "live_view.py").read_text(encoding="utf-8"))

    def test_draw_callbacks_only_read(self):
        """No read, evaluation or data write inside a region's draw (break-it P1-1 M4)."""
        for name, function in (("live_view_draw.py", "draw"), ("live_view_draw.py", "_draw"), ("session_rail_draw.py", "draw"),
                               ("session_rail_draw.py", "_draw")):
            tree = ast.parse((ADDON / name).read_text(encoding="utf-8"))
            body = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function)
            text = ast.unparse(body)
            for forbidden in ("refresh(", "attach(", ".open(", "evaluate(", "view_layer.update"):
                self.assertNotIn(forbidden, text, (name, function, forbidden))

    def test_the_native_module_draws_solid_and_never_runs_handlers(self):
        """Solid, unless it is a view's compile-ahead draw (``view_warm``: EEVEE, its pixels never used)."""
        intern = ADDON.parents[2] / "source/blender/python/intern"
        source = (intern / "bpy_angelo_live.cc").read_text(encoding="utf-8")
        self.assertIn("shading.type = compile_ahead ? OB_MATERIAL : OB_SOLID;",
                      (intern / "bpy_angelo_live_draw.cc").read_text(encoding="utf-8"))
        self.assertIn("DEG_evaluate_on_refresh(part->depsgraph);", source)
        self.assertNotIn("BKE_scene_graph_update_tagged", source)   # That one runs the add-ons' handlers.
        self.assertIn("BLO_read_from_file(", source)

    def test_pictures_and_reads_wait_for_tools_and_jobs(self):
        source = (ADDON / "parallel_sessions.py").read_text(encoding="utf-8")
        self.assertIn("work_guard._render(bpy)", source)
        self.assertIn('getattr(manager, "is_interface_locked", False)', source)
        self.assertIn("parallel_sessions._held_now()", (ADDON / "rail_pictures.py").read_text(encoding="utf-8"))


class BriefAndCardTests(unittest.TestCase):
    def test_a_multi_line_brief_is_fine_other_control_characters_say_which(self):
        start = worker_spec.PREFIX + "start"
        good = worker_spec.validate_worker(start, {"parts": [{"name": "Desk", "brief": "Oak desk.\n\t1.4 m wide."}]})
        self.assertIn("\n", good["parts"][0]["brief"])
        with self.assertRaisesRegex(ValueError, "control character"):
            worker_spec.validate_worker(start, {"parts": [{"name": "Desk", "brief": "Oak\x07 desk."}]})

    def test_the_approval_card_names_parts_count_and_cost(self):
        from angelo_agent import worker_approval
        card = worker_approval.message({"parts": [{"name": "Desk", "brief": "Oak desk " * 40},
                                                  {"name": "Chair", "brief": "Office chair."}]})
        self.assertIn("2", card["title"])
        self.assertIn("Desk:", card["body"])
        self.assertIn("Chair: Office chair.", card["body"])
        self.assertIn("…", card["body"])
        self.assertIn("quota", card["warning"])
        source = (ADDON / "approvals.py").read_text(encoding="utf-8")
        self.assertIn('if name in {"angelo_workers_start", "angelo_workers_merge"}:', source)

    def test_the_merge_card_names_parts_objects_copies_target_and_undo(self):
        from angelo_agent import worker_approval
        args = {"parts": [{"worker": 1}, {"worker": 2, "placements": [{}, {}, {}]}, {"worker": 9}]}
        card = worker_approval.merge_message(args, {"target": "chess.blend", 1: {"name": "Board", "objects": 65},
                                                    2: {"name": "Pawn", "objects": 1}})
        self.assertIn("3", card["title"])
        self.assertIn("Board: 65 objects", card["body"])
        self.assertIn("Pawn: 1 object, 3 copies", card["body"])
        self.assertIn("Worker 9", card["body"])   # A worker Angelo knows nothing of: named by its number.
        self.assertIn("chess.blend", card["warning"])
        self.assertIn("one Undo step", card["warning"])
        self.assertIn("your scene", worker_approval.merge_message({"parts": [{"worker": 1}]}, {})["warning"])

    def test_a_merging_tile_says_so_after_an_approval_and_before_work(self):
        self.assertEqual(rail.tile_kind(busy=True, approval=False, ended=False, failed=False, merging=True),
                         rail.MERGING)
        self.assertEqual(rail.tile_kind(busy=True, approval=True, ended=False, failed=False, merging=True),
                         rail.APPROVAL)
        self.assertEqual(rail.status(rail.MERGING), "Merging into your scene")
        self.assertEqual(rail.KINDS.index(rail.MERGING), 1)

    def test_a_merged_tile_says_so_and_closes_like_an_ended_one(self):
        self.assertEqual(rail.tile_kind(busy=False, approval=False, ended=True, failed=False, merged=True), rail.MERGED)
        self.assertEqual(rail.status(rail.MERGED), "Merged into your scene")
        plan = rail.layout([tile("lead"), tile("w1", rail.MERGED)], (1600, 900), 1.0)
        self.assertIsNotNone(plan.tiles[1].close)
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        self.assertIn("parallel_sessions.ended(session_id) or parallel_sessions.is_merged(session_id)", source)
        remove = source
        body = remove[remove.index("def remove("):remove.index("def _redraw(")]
        for step in ("parallel_sessions.dismiss(session_id)", "rail_pictures.forget(session_id)",
                     "live_view_draw.forget(session_id)", "switch(parallel_sessions.lead, window)"):
            self.assertIn(step, body)

    def test_an_ended_tile_can_be_dismissed(self):
        plan = rail.layout([tile("lead"), tile("w1", rail.ENDED)], (1600, 900), 1.0)
        close = plan.tiles[1].close
        self.assertIsNotNone(close)
        self.assertIsNone(plan.tiles[0].close)
        self.assertEqual(rail.hit(plan, close[0] + 2, close[1] + 2), ("dismiss", "w1"))


if __name__ == "__main__":
    unittest.main()
