# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's marble wait without Blender: the mark's triangles, the carving schedule, the events and the reveal.

Dependency-free: ``marble_mark`` (the raven from its SVG), ``marble_carving`` (chips, schedule, flights),
``marble_wait`` (events, state machine, frame scheduling), ``motion_preference`` and the summary-line hook in
``plan_progress``. The drawing (``marble_draw``) and the timer (``marble_host``) are pinned by source checks
here and run for real in ``test_blender_chat_waiting``.
"""

import ast
import math
import re
import sys
import unittest

from host_test_policy import environment_skip
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import marble_carving, marble_mark, marble_wait, motion_preference, plan_progress  # noqa: E402
from angelo_agent import plan_steps  # noqa: E402
from angelo_agent.claude_stream import STATUS_PLAN, STATUS_THINKING  # noqa: E402
from angelo_agent.plan_progress import PlanProgress  # noqa: E402

SVG = ROOT / "release/branding/angelo/angelo-mark-color.svg"
BRIDGE = ('{"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": ['
          '{"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Deck"}}, '
          '{"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Tower"}}]}}]}')


def area(points, triangles):
    return sum(((points[b][0] - points[a][0]) * (points[c][1] - points[a][1])
                - (points[b][1] - points[a][1]) * (points[c][0] - points[a][0])) / 2 for a, b, c in triangles)


def chat(status=STATUS_THINKING, messages=None, anchor=0, busy=True, voice=None, session="s1"):
    messages = [{"role": "user", "text": "Build a castle"}] if messages is None else messages
    return SimpleNamespace(status=status, busy=busy, voice=voice, state={"id": session, "messages": messages},
                           _request_message_index=anchor)


def plan_status(rows):
    progress = PlanProgress()
    progress.feed(BRIDGE if rows > 1 else BRIDGE.split(', {"kind"')[0])
    return plan_progress.status(STATUS_PLAN, progress.detail(), plan_steps.encode(progress))


class MarkTests(unittest.TestCase):
    def test_paths_and_paints_are_the_branding_svgs(self):
        source = SVG.read_text(encoding="utf-8")
        if source.startswith("version https://git-lfs"):
            self.skipTest("The mark is a Git LFS pointer in this checkout")
        drawn = re.findall(r'<path fill="([^"]+)" d="([^"]+)"', source)
        self.assertEqual([(" ".join(d.split()), fill) for fill, d in drawn],
                         [(" ".join(data.split()), paint if paint.startswith("#") else "url(#%s)" % paint)
                          for _, data, paint in marble_mark.PARTS])
        self.assertIn('transform="translate(-313 -306) scale(1.5)"', source)
        for name, ((x1, y1, x2, y2), stops) in marble_mark.GRADIENTS.items():
            with self.subTest(gradient=name):
                block = re.search(r'<linearGradient id="%s"(.*?)</linearGradient>' % name, source, re.S).group(1)
                self.assertIn('x1="%g" y1="%g" x2="%g" y2="%g"' % (x1, y1, x2, y2), block)
                found = re.findall(r'<stop(?: offset="([^"]+)")? stop-color="([^"]+)"', block)
                self.assertEqual([(float(offset or 0), colour) for offset, colour in found], list(stops))

    def test_every_part_is_covered_exactly_and_wound_counter_clockwise(self):
        for name, data, _ in marble_mark.PARTS:
            with self.subTest(part=name):
                positions, colours, triangles = marble_mark.mesh(name)
                outline = sum(abs(marble_mark.signed_area(shape)) for shape in marble_mark.outlines(data))
                self.assertAlmostEqual(area(positions, triangles), outline * 1.5 ** 2, delta=outline * 1e-6)
                self.assertTrue(all(area(positions, [t]) > -1e-6 for t in triangles))
                self.assertEqual(len(positions), len(colours))
                self.assertTrue(all(0 <= x <= marble_mark.SIZE and 0 <= y <= marble_mark.SIZE for x, y in positions))
        self.assertIs(marble_mark.mesh("helmet"), marble_mark.mesh("helmet"), "Triangulated once, then cached")

    def test_a_gradient_is_exact_per_vertex(self):
        positions, colours, _ = marble_mark.mesh("helmet")
        measure = marble_mark._gradient("helmet")[0]
        for point, colour in list(zip(positions, colours))[::17]:
            x, y = point
            path = ((x + 313) / 1.5, (marble_mark.SIZE - y + 306) / 1.5)
            t = min(1, max(0, measure(path)))
            expected = marble_mark._gradient("helmet")[2](path)
            for got, want in zip(colour, expected):
                self.assertAlmostEqual(got, want, places=9)
            if t <= 0.55:
                self.assertLessEqual(colour[2], marble_mark.hex_colour("#27bdbf")[2] + 1e-9)
        middle = marble_mark.hex_colour("#27bdbf")
        self.assertTrue(any(max(abs(a - b) for a, b in zip(c, middle)) < 1e-9 for c in colours),
                        "Triangles are split on the middle stop, so its colour is a vertex colour")

    def test_ear_clipping_handles_concave_and_collinear_outlines(self):
        shape = [(0, 0), (4, 0), (4, 1), (2, 1), (1, 1), (1, 4), (0, 4)]  # An L with a collinear vertex.
        triangles = marble_mark.triangulate(shape)
        self.assertAlmostEqual(area(shape, triangles), 7.0)
        self.assertAlmostEqual(area(list(reversed(shape)), marble_mark.triangulate(list(reversed(shape)))), 7.0)

    def test_a_split_keeps_area_and_winding(self):
        triangle = ((0.0, 0.0), (10.0, 0.0), (0.0, 10.0))
        for level in (-1, 0, 2.5, 5, 9.99, 10, 11):
            pieces = marble_mark._split(triangle, lambda p: p[0], level)
            with self.subTest(level=level):
                self.assertAlmostEqual(sum(area(piece, [(0, 1, 2)]) for piece in pieces), 50.0)
                self.assertTrue(all(area(piece, [(0, 1, 2)]) >= 0 for piece in pieces))
        through = marble_mark._split(triangle, lambda p: p[0] - p[1], 0)
        self.assertEqual(len(through), 2, "Through a vertex: two")
        self.assertAlmostEqual(sum(area(piece, [(0, 1, 2)]) for piece in through), 50.0)

    def test_unsupported_path_commands_are_refused(self):
        with self.assertRaises(ValueError):
            marble_mark.outlines("m0 0 l10 0 l0 10 z")


class CarvingTests(unittest.TestCase):
    def test_the_hero_and_icon_grids(self):
        hero, icon = marble_wait.hero_block(), marble_wait.icon_block()
        self.assertEqual((hero.total, icon.total), (128, 32))
        self.assertIs(hero.chips, marble_wait.hero_block().chips, "Chips are built once and shared")
        inner = marble_carving.CENTRE
        first = sum(math.dist(chip.centre, inner) for chip in hero.chips[:32]) / 32
        last = sum(math.dist(chip.centre, inner) for chip in hero.chips[-32:]) / 32
        self.assertGreater(first, last * 1.5, "Carving works from the outside in")
        hero_triangles = sum(len(chip.mesh.indices) for chip in hero.chips)
        self.assertGreater(hero_triangles, 2 * hero.total, "The hero's chips carry edges and veins")
        self.assertEqual(sum(len(chip.mesh.indices) for chip in icon.chips), icon.total, "The icon is plain facets")

    def test_edges_are_darker_than_faces_and_the_stone_is_pale(self):
        chip = marble_wait.hero_block().chips[0]
        edge, face = chip.mesh.colours[0], chip.mesh.colours[3]
        self.assertLess(sum(edge[:3]), sum(face[:3]))
        self.assertGreater(min(face[:3]), 0.75, "Pale stone")
        veins = [c for c in (c for chip in marble_wait.hero_block().chips for c in chip.mesh.colours)
                 if c == marble_carving.VEIN]
        self.assertTrue(veins, "A few grey veins cross the hero's chips")

    def test_the_steady_schedule_slows_and_never_runs_out(self):
        counts = [marble_carving.steady_count(128, t) for t in range(0, 3600, 5)]
        self.assertEqual(counts, sorted(counts))
        self.assertLessEqual(max(counts), int(128 * marble_carving.ASYMPTOTE))
        gaps = [marble_carving.steady_time(128, k + 1) - marble_carving.steady_time(128, k) for k in range(1, 80)]
        self.assertEqual(gaps, sorted(gaps), "Each chip waits longer than the one before")
        self.assertIsNone(marble_carving.steady_time(128, 93), "Past 72% the steady schedule stops")
        for k in (1, 10, 50, 92):
            self.assertEqual(marble_carving.steady_count(128, marble_carving.steady_time(128, k) + 1e-9), k)

    def test_taps_burst_but_stop_at_the_cap(self):
        block = marble_wait.hero_block()
        self.assertEqual(block.tap(0.0), round(128 * marble_carving.TAP_SHARE))
        for second in range(1, 200):
            block.tap(float(second))
        self.assertEqual(block.removed, int(128 * marble_carving.CAP), "A long wait never runs out of stone")
        self.assertEqual(block.tap(500.0), 0)
        self.assertEqual(block.fall_all(600.0), 128 - int(128 * marble_carving.CAP))
        self.assertEqual(block.removed, 128)

    def test_flights_are_analytic_and_fade(self):
        block = marble_wait.icon_block()
        block.drop(1, 10.0)
        flight = block.flights[0]
        chip = block.chips[flight.chip]
        moved = block.flying(10.5, False)
        self.assertEqual(len(moved.positions), len(chip.mesh.positions))
        self.assertTrue(all(abs(c[3] - (1 - 0.5 / marble_carving.FALL_SECONDS)) < 1e-9 for c in moved.colours))
        self.assertNotEqual(moved.positions[0][:2], chip.mesh.positions[0][:2])
        self.assertEqual(block.flying(10.5, False), block.flying(10.5, False), "A frame depends only on its clock")
        self.assertFalse(block.flying(10.0 + marble_carving.FALL_SECONDS, False).indices)
        self.assertTrue(block.moving(10.2, False))
        self.assertFalse(block.moving(11.5, False))

    def test_reduced_motion_fades_in_place_without_dust_or_shake(self):
        block = marble_wait.hero_block()
        block.tap(5.0)
        faded = block.flying(5.3, True)
        originals = [p for flight in block.flights for p in block.chips[flight.chip].mesh.positions]
        self.assertEqual(faded.positions, originals, "No chip moves")
        self.assertFalse(block.dust_mesh(5.1, True).indices)
        self.assertEqual(block.offset(5.01, True), (0.0, 0.0))
        self.assertTrue(block.dust_mesh(5.1, False).indices)
        self.assertNotEqual(block.offset(5.01, False), (0.0, 0.0))
        self.assertFalse(block.moving(5.0 + marble_carving.FADE_SECONDS + 1e-6, True))

    def test_the_standing_stone_is_rebuilt_only_when_a_chip_falls(self):
        block = marble_wait.icon_block()
        whole = block.solid()
        self.assertIs(block.solid(), whole)
        block.drop(1, 0.0)
        self.assertIsNot(block.solid(), whole)
        self.assertEqual(len(block.solid().indices), len(whole.indices) - 1)


class WaitTests(unittest.TestCase):
    def test_taps_follow_real_progress_only(self):
        wait = marble_wait.Wait(0.0, marble_wait.progress(chat()))
        self.assertEqual(wait.observe(1.0, marble_wait.progress(chat())), [], "No change, no tap")
        self.assertEqual(wait.observe(2.0, marble_wait.progress(chat(STATUS_PLAN))), ["tap"], "The plan call began")
        self.assertEqual(wait.observe(3.0, marble_wait.progress(chat(plan_status(1)))), ["tap"], "A plan row")
        self.assertEqual(wait.observe(3.2, marble_wait.progress(chat(plan_status(2)))), [], "Within TAP_GAP")
        self.assertEqual(wait.observe(4.0, marble_wait.progress(chat(plan_status(2)))), [], "Same rows")
        line = plan_progress.status(STATUS_THINKING, "Laying out the curtain wall.")
        self.assertEqual(wait.observe(5.0, marble_wait.progress(chat(line))), ["tap"], "A new thinking line")
        self.assertEqual(wait.observe(6.0, marble_wait.progress(chat(line))), [])
        self.assertEqual(wait.taps, 3)

    def test_the_reveal_is_the_turns_first_step(self):
        refused = {"role": "activity", "tool": "StructuredOutput", "status": "failed"}
        running = {"role": "activity", "tool": "StructuredOutput", "status": "running"}
        earlier = {"role": "activity", "tool": "angelo_scene_program", "status": "completed"}
        rows = [earlier, {"role": "user", "text": "Castle"}, {"role": "thinking", "text": ""}, refused, running]
        wait = marble_wait.Wait(0.0, marble_wait.progress(chat(messages=rows, anchor=1)))
        self.assertEqual(wait.observe(1.0, marble_wait.progress(chat(messages=rows, anchor=1))), [],
                         "An earlier turn's step, a thinking row and a refused or unsettled plan are no step")
        running["status"] = "completed"
        self.assertEqual(wait.observe(2.0, marble_wait.progress(chat(messages=rows, anchor=1))), ["reveal"])
        self.assertEqual((wait.phase, wait.hero.removed, wait.icon.removed), (marble_wait.REVEALING, 128, 32))
        self.assertIsNone(marble_wait.progress(chat(messages=rows, anchor=0)).request, "The anchor is a request row")
        self.assertIs(wait.request, rows[1])

    def test_the_wake_shrinks_the_hero_raises_the_eyes_and_blinks(self):
        wait = marble_wait.Wait(0.0)
        self.assertEqual(wait.hero_size(50.0), marble_wait.HERO)
        wait.reveal(100.0)
        self.assertEqual(wait.hero_size(100.5), marble_wait.HERO)
        self.assertEqual(wait.hero_size(100.0 + marble_wait.SHRINK_END), marble_wait.HERO_COMPACT)
        self.assertTrue(marble_wait.HERO_COMPACT < wait.hero_size(101.15) < marble_wait.HERO)
        self.assertEqual(wait.pose(101.0, False).eye_dy, marble_wait.LOOK_UNITS)
        self.assertLess(wait.pose(100.0 + marble_wait.FIRST_BLINK + 0.07, False).eye_open, 0.2, "It blinks")
        self.assertEqual(wait.pose(102.0, False).eye_open, 1.0)
        later = 100.0 + marble_wait.FIRST_BLINK + marble_wait.BLINK_PERIOD + 0.07
        self.assertLess(wait.pose(later, False).eye_open, 0.2, "And keeps blinking")
        self.assertAlmostEqual(wait.pose(110.0, True).tilt, marble_wait.ICON_TILT - 4 * math.sin(1.2 * 2.6))
        wait.advance(100.0 + marble_wait.WAKE_SECONDS + 0.01)
        self.assertEqual(wait.phase, marble_wait.BUILDING)

    def test_frames_are_asked_for_only_when_something_changes(self):
        wait = marble_wait.Wait(0.0)
        wait.advance(0.0)
        delay = wait.next_frame(0.0)
        self.assertGreater(delay, 0, "Nothing has fallen yet: sleep until the first chip")
        wait.advance(delay)
        self.assertEqual(wait.next_frame(delay + 0.01), 0.0, "A chip in flight: animate")
        wait.advance(4000.0)
        self.assertIsNone(wait.next_frame(4000.0), "At the asymptote nothing more changes")
        wait.reveal(5000.0)
        self.assertEqual(wait.next_frame(5000.5), 0.0)
        wait.advance(5000.0 + marble_wait.WAKE_SECONDS + 0.01)
        between = 5000.0 + marble_wait.FIRST_BLINK + 1.0
        self.assertAlmostEqual(wait.next_frame(between), marble_wait.BLINK_PERIOD - 1.0, places=6)
        self.assertEqual(wait.next_frame(5000.0 + marble_wait.FIRST_BLINK + marble_wait.BLINK_PERIOD), 0.0)

    def test_reduced_motion_asks_for_almost_no_frames(self):
        wait = marble_wait.Wait(0.0, reduced=True)
        wait.reveal(10.0)
        self.assertEqual(wait.next_frame(10.1), 0.0, "The stone fades")
        self.assertAlmostEqual(wait.next_frame(10.7), marble_wait.SHRINK_START - 0.7)
        self.assertEqual(wait.hero_size(10.0 + marble_wait.SHRINK_START), marble_wait.HERO_COMPACT, "One step")
        self.assertIsNone(wait.next_frame(12.0), "No blinking: no frames while steps run")
        self.assertEqual(wait.pose(12.0, False).eye_open, 1.0)

    def test_frames_do_not_depend_on_how_often_the_timer_ran(self):
        fine, coarse = marble_wait.Wait(0.0), marble_wait.Wait(0.0)
        for step in range(1, 301):
            fine.advance(step / 30)
        for step in range(1, 101):
            coarse.advance(step / 10)
        self.assertEqual(fine.hero.removed, coarse.hero.removed)
        self.assertEqual(fine.hero.flying(10.0, False), coarse.hero.flying(10.0, False))

    def test_redraws_keep_to_a_share_of_the_main_thread(self):
        self.assertEqual(marble_wait.MAX_SHARE, 0.02)
        self.assertEqual(marble_wait.frame_interval(0.0005), 1 / 12, "A cheap draw still limits native redraw work")
        self.assertAlmostEqual(marble_wait.frame_interval(0.002), 0.002 / marble_wait.MAX_SHARE)
        self.assertEqual(marble_wait.frame_interval(5.0), 1.0, "At least a frame a second")
        for draw in (0.0002, 0.0005, 0.001, 0.004, 0.01, 0.02):
            self.assertLessEqual(draw / marble_wait.frame_interval(draw), marble_wait.MAX_SHARE + 1e-9)

    def test_a_chat_opened_mid_turn_is_already_revealed(self):
        rows = [{"role": "user", "text": "Castle"}, {"role": "activity", "tool": "angelo_view", "status": "running"}]
        wait = marble_wait.Wait(7.0, marble_wait.progress(chat(messages=rows)))
        self.assertEqual((wait.phase, wait.hero.removed, wait.hero.flights), (marble_wait.BUILDING, 128, []))
        self.assertEqual(wait.hero_size(7.0), marble_wait.HERO_COMPACT)


class TrackerTests(unittest.TestCase):
    def test_a_wait_lives_exactly_as_long_as_the_turn(self):
        tracker, controller = marble_wait.Tracker(), chat()
        asked = []
        wait = tracker.update(controller, 1.0, lambda: asked.append(1) or True)
        self.assertTrue(wait.reduced)
        self.assertIs(tracker.update(controller, 1.1, lambda: asked.append(1)), wait)
        self.assertEqual(asked, [1], "Reduced motion is asked once per wait")
        self.assertIs(tracker.current(controller), wait)
        controller.busy = False
        self.assertIsNone(tracker.current(controller))
        self.assertIsNone(tracker.update(controller, 2.0))
        self.assertEqual(tracker.waits, {})

    def test_voice_and_a_new_request_row(self):
        tracker = marble_wait.Tracker()
        self.assertIsNone(tracker.update(chat(voice=object()), 1.0))
        rows = [{"role": "user", "text": "One"}, {"role": "assistant", "text": "Done"}, {"role": "user", "text": "Two"}]
        first = tracker.update(chat(messages=rows, anchor=0), 1.0)
        second = tracker.update(chat(messages=rows, anchor=2), 1.1)
        self.assertIsNot(first, second)
        self.assertIs(second.request, rows[2])
        rows.insert(0, {"role": "system", "text": "A notice"})  # The request row moves; it is the same turn.
        self.assertIs(tracker.update(chat(messages=rows, anchor=3), 1.2), second)
        tracker.end(["other"])
        self.assertEqual(tracker.waits, {})


class SummaryLineTests(unittest.TestCase):
    def test_a_finished_sentence_names_the_live_row(self):
        line = plan_progress.summary_line
        self.assertEqual(line("The user wants a castle. I will lay out the walls"), "The user wants a castle.")
        self.assertEqual(line("Towers are 1.5 m wide. Next"), "Towers are 1.5 m wide.")
        self.assertEqual(line("Towers are 1."), "", "A sentence still streaming is not shown")
        self.assertEqual(line("Planning the **gatehouse** arch.\n"), "Planning the gatehouse arch.")
        long = "A sentence about " + "the towers and " * 8 + "the gate. "
        self.assertTrue(line(long).endswith("…") and len(line(long)) <= plan_progress.MAX_TITLE)
        self.assertEqual(line("sk-" + "x" * 90 + ". "), "", "A long run without a space is never cut")

    def test_only_claude_prose_takes_it_and_a_title_still_wins(self):
        text = "Reading the brief. More"
        self.assertEqual(plan_progress.thinking_status(STATUS_THINKING, text), STATUS_THINKING, "GPT path as before")
        self.assertEqual(plan_progress.thinking_status(STATUS_THINKING, text, prose=True),
                         plan_progress.status(STATUS_THINKING, "Reading the brief."))
        self.assertEqual(plan_progress.thinking_status(STATUS_THINKING, "**Castle**\n" + text, prose=True),
                         plan_progress.status(STATUS_THINKING, "Castle"))
        self.assertEqual(plan_progress.thinking_status(STATUS_PLAN, text, prose=True), STATUS_PLAN)

    def test_the_controller_passes_prose_for_claude_thinking_only(self):
        source = (ADDON / "controller.py").read_text(encoding="utf-8")
        self.assertIn('prose=item.startswith("claude-thinking/")', source)
        self.assertIn('"claude-thinking/%s/%s"', (ADDON / "claude_stream.py").read_text(encoding="utf-8"))


class MotionPreferenceTests(unittest.TestCase):
    def test_platforms(self):
        self.assertFalse(motion_preference.os_reduces_motion("linux"))
        with patch.object(motion_preference, "_macos_query", return_value=True):
            self.assertTrue(motion_preference.os_reduces_motion("darwin"))
        with patch.object(motion_preference, "_windows_query", return_value=True):
            self.assertTrue(motion_preference.os_reduces_motion("win32"))

    def test_windows_animations_off_means_reduce(self):
        def answer(value):
            def call(action, _, pointer, __):
                self.assertEqual(action, motion_preference.SPI_GETCLIENTAREAANIMATION)
                pointer._obj.value = value
                return 1
            return SimpleNamespace(user32=SimpleNamespace(SystemParametersInfoW=call))
        for value, expected in ((0, True), (1, False)):
            with patch.object(motion_preference.ctypes, "windll", answer(value), create=True):
                self.assertIs(motion_preference._windows_query(), expected)

    @unittest.skipUnless(sys.platform == "darwin", environment_skip("AppKit is macOS-only"))
    def test_the_macos_query_answers(self):
        self.assertIsInstance(motion_preference._macos_query(), bool)


class HygieneTests(unittest.TestCase):
    CORE = ("marble_mark.py", "marble_carving.py", "marble_wait.py", "motion_preference.py")

    def test_modules_are_small_licensed_and_the_logic_is_bpy_free(self):
        for name in self.CORE + ("marble_draw.py", "marble_host.py", "marble_frames.py"):
            source = (ADDON / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertLessEqual(len(source.splitlines()), 500)
                self.assertIn("SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.", source)
                self.assertIn("SPDX-License-Identifier: GPL-2.0-or-later", source)
                tree = ast.parse(source)
                imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                            for alias in node.names}
                self.assertNotIn("open", {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call)
                                          and isinstance(node.func, ast.Name)}, "No file I/O near drawing")
                if name in self.CORE:
                    self.assertFalse(imported & {"bpy", "gpu", "blf"}, name)

    def test_the_chat_draws_it_and_only_its_region_redraws(self):
        view = (ADDON / "chat_view.py").read_text(encoding="utf-8")
        rows = (ADDON / "chat_rows.py").read_text(encoding="utf-8")
        host = (ADDON / "marble_host.py").read_text(encoding="utf-8")
        self.assertIn("marble_draw.with_hero(chat_rows.group_blocks(", view)
        self.assertIn("marble_host.poll()", view)
        self.assertIn("marble_host.stop()", view)
        self.assertIn('"marble_hero"', rows)
        self.assertLess(rows.index("marble_draw.draw_icon("), rows.index("_spinner(x + 7, centre, TEAL, 5)"))
        self.assertIn("console.is_chat(area)", host)
        self.assertIn("region.type == 'WINDOW'", host)
        self.assertEqual(host.count("tag_redraw()"), 1, "One redraw site: the chat's region")
        self.assertEqual(marble_wait.FPS, 24)
        self.assertIn("interval = marble_wait.frame_interval(marble_frames.frame_cost)", host)
        self.assertIn("marble_host.note_event(event.type, event.value)", view)
        self.assertIn('state["input_serial"] = state.get("input_serial", 0) + 1', view)
        for reason in ('"unfocused"', '"navigating"', '"modal"', '"playback"', '"hidden"'):
            self.assertIn(reason, host)
        self.assertIn("input_policy.tool_running(window)", host)
        self.assertIn("return None", host)

    def test_a_marble_frame_redraws_only_the_marks_over_the_kept_picture(self):
        view = (ADDON / "chat_view.py").read_text(encoding="utf-8")
        frames = (ADDON / "marble_frames.py").read_text(encoding="utf-8")
        draw = view[view.index("def draw():"):view.index("def _draw_chat():")]
        self.assertLess(draw.index("marble_frames.reuse(area, region)"), draw.index("_draw_chat()"))
        self.assertIn("with marble_frames.keep(area, region):", draw)
        reuse = frames[frames.index("def reuse("):frames.index("class keep")]
        self.assertIn("if not asked or entry is None:", reuse, "Only a frame the marble timer asked for")
        self.assertIn("now - entry[\"at\"] > REFRESH", reuse)
        self.assertIn('state.get("popover")', frames)
        self.assertIn("call.approval", frames)
        self.assertNotIn("_draw_chat", reuse, "A marble frame never lays the chat out again")


if __name__ == "__main__":
    unittest.main()
