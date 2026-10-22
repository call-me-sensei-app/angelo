# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live row's phase, clock and quiet/stall note, and a thinking row with no summary, without Blender.

Dependency-free: ``live_progress``, the clock scheduling of ``marble_host.clock_frame`` (pinned by source and
by calling it with ``bpy`` stubbed), ``turn_activity``'s keep-alive clock, the providers' ``signs_of_life`` and
Claude Code's ``api_retry`` status. The drawing runs for real in ``test_blender_chat_waiting``.

A real user asked for a sign that a long think is "still going and not stuck"; a 130 s Opus think showed
"Thinking" with nothing moving, and its opened row said only that the runtime supplied no summary.
"""

import ast
import contextlib
import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import live_progress, turn_activity  # noqa: E402
from angelo_agent.claude_stream import STATUS_PLAN, STATUS_THINKING, RoundStream  # noqa: E402
from angelo_agent.codex_retry import RETRYING  # noqa: E402
from angelo_agent.live_progress import Facts, QUIET, STALL  # noqa: E402


def facts(status=STATUS_THINKING, started=100.0, accepted=True, last_model=100.0, last_heard=100.0, thinking=False,
          summarized=False, step=None, watched=True, thinking_age=None):
    return Facts(status, started, accepted, last_model, last_heard, thinking, summarized, step, watched, thinking_age)


def chat(messages, status=STATUS_THINKING, provider=None, anchor=0, started=50.0, generation=3):
    controller = SimpleNamespace(state={"messages": messages}, status=status, provider=provider, busy=True,
                                 turn_started=(generation, started), _request_generation=3)
    controller._request_message_index = anchor
    return controller



def bare_bpy(test):
    """An empty ``bpy`` for imports that only name it, removed after ``test``: left in ``sys.modules`` it would stand
    in for the fuller stand-ins later test modules install with ``setdefault``."""
    if "bpy" not in sys.modules:
        sys.modules["bpy"] = types.ModuleType("bpy")
        test.addCleanup(sys.modules.pop, "bpy", None)

class Clock(unittest.TestCase):
    def test_next_tick_lands_on_whole_seconds(self):
        self.assertAlmostEqual(live_progress.next_tick(10.0, 12.25), 0.75)
        self.assertAlmostEqual(live_progress.next_tick(10.0, 9.5), 0.5)


class StatusLine(unittest.TestCase):
    def test_elapsed_reads_like_a_status_line(self):
        cases = {0: "0s", 9.9: "9s", 69: "1m 9s", 600: "10m 0s", 3720: "1h 2m", -3: "0s", None: "0s"}
        for seconds, text in cases.items():
            self.assertEqual(live_progress.elapsed(seconds), text)

    def test_tokens_come_from_the_usage_meter_or_not_at_all(self):
        meter = types.ModuleType("angelo_agent.usage_meter")
        view = types.ModuleType("angelo_agent.usage_view")
        view.turn_label = lambda n: "%d tokens" % n
        with self._meter_modules(meter, view):
            for count, text in ((562, "562 tokens"), (0, ""), (None, ""), ("9", "")):
                meter.turn_tokens = lambda controller, count=count: count
                self.assertEqual(live_progress.tokens(object()), text)
            meter.turn_tokens = lambda controller: 1 / 0
            self.assertEqual(live_progress.tokens(object()), "", "A failing meter never takes the row down")
        with self._meter_modules(None, None):   # A build without the meter: its import fails.
            self.assertEqual(live_progress.tokens(object()), "", "No meter in this build: no token part")
            self.assertIs(live_progress._meter, False, "Looked up once, not on every frame")

    @staticmethod
    @contextlib.contextmanager
    def _meter_modules(meter, view):
        """``usage_meter`` and ``usage_view`` replaced (None: not importable) for the block, whether or not this
        build has them; the real ones, and live_progress's lookup, come back afterwards."""
        package, names = sys.modules["angelo_agent"], ("usage_meter", "usage_view")
        saved = {name: (sys.modules.get("angelo_agent." + name), getattr(package, name, None)) for name in names}
        try:
            for name, module in zip(names, (meter, view)):
                sys.modules["angelo_agent." + name] = module
                if hasattr(package, name):
                    delattr(package, name)
                if module is not None:
                    setattr(package, name, module)
            live_progress._meter = None
            yield
        finally:
            for name, (module, attribute) in saved.items():
                sys.modules.pop("angelo_agent." + name, None)
                if module is not None:
                    sys.modules["angelo_agent." + name] = module
                if hasattr(package, name):
                    delattr(package, name)
                if attribute is not None:
                    setattr(package, name, attribute)
            live_progress._meter = None

    def test_lead_is_the_drawn_time_and_tokens_and_changes_once_a_second(self):
        controller = chat([{"role": "user"}], started=100.0)
        self.assertEqual(live_progress.lead(controller, 169.4), "1m 9s · ")
        self.assertEqual(live_progress.lead(controller, 169.9), live_progress.lead(controller, 169.4))
        self.assertNotEqual(live_progress.lead(controller, 170.0), live_progress.lead(controller, 169.4))
        controller.busy = False
        self.assertEqual(live_progress.lead(controller, 170.0), "")

    def test_the_kept_picture_is_keyed_on_the_status_line_text(self):
        source = (ADDON / "marble_frames.py").read_text(encoding="utf-8")
        self.assertIn("live_progress.lead(controller, now)", source[source.index("def key("):][:1600])
        row = (ADDON / "live_row.py").read_text(encoding="utf-8")
        painted = row[row.index("def dot(now):"):row.index("marble_draw.paint(state, dot)")]
        self.assertNotIn("view.text", painted, "only the dot is painted per frame")

    def test_the_clocks_refreshes_are_counted_apart_from_other_full_draws(self):
        source = (ADDON / "marble_frames.py").read_text(encoding="utf-8")
        key = source[source.index("def key("):source.index("def _blit(")]
        self.assertTrue(key.rstrip().endswith("live_progress.lead(controller, now))  # Last: reuse() counts a change of it alone as the clock's refresh."))
        reuse = source[source.index("def reuse("):source.index("def _created_in_region(")]
        self.assertIn("current[:-1] == kept[:-1]", reuse)
        self.assertIn("kept is not None", reuse)
        self.assertIn('stats["clock"] += 1', reuse)

    def test_worked_follows_each_finished_turn(self):
        bare_bpy(self)
        from angelo_agent import chat_rows

        def block(role, message):
            return (role, message, (), 10, id(message), 0)
        first, second = {"role": "user", "worked_ms": 118000}, {"role": "user"}
        blocks = [block("user", first), block("assistant", {}), block("user", second), block("assistant", {})]
        out = chat_rows.worked(blocks, live=True)
        self.assertEqual([b[0] for b in out], ["user", "assistant", "worked", "user", "assistant"])
        self.assertEqual(out[2][1]["ms"], 118000)
        second["worked_ms"] = 5000
        self.assertEqual([b[0] for b in chat_rows.worked(blocks, live=False)][-1], "worked")
        self.assertNotEqual([b[0] for b in chat_rows.worked(blocks, live=True)][-1], "worked", "Not while it runs")


class Phase(unittest.TestCase):
    def test_before_the_first_api_event_it_waits_for_the_model(self):
        self.assertEqual(live_progress.phase(facts(accepted=False)), "Waiting for the model")
        self.assertEqual(live_progress.phase(facts(status="Connecting", accepted=False)), "Waiting for the model")

    def test_unknown_acceptance_keeps_the_status(self):
        self.assertEqual(live_progress.phase(facts(status="Connecting", accepted=None)), "Connecting")

    def test_a_running_step_names_the_hosts_work(self):
        cases = {"angelo_execute_python": "Working in Blender", "angelo_scene_program": "Working in Blender",
                 "WebSearch": "Searching the web", "web_search": "Searching the web",
                 "angelo_delegate": "Consulting another model", "angelo_studio_generate": "Generating media"}
        for tool, label in cases.items():
            step = {"role": "activity", "tool": tool, "status": "running"}
            self.assertEqual(live_progress.phase(facts(step=step)), label, tool)

    def test_host_statuses_never_read_thinking(self):
        for status, label in {"Staging native program": "Working in Blender", "Generating media": "Generating media",
                              "Waiting for animation playback to stop": "Waiting for animation playback to stop",
                              RETRYING: RETRYING}.items():
            self.assertEqual(live_progress.phase(facts(status=status)), label)

    def test_a_plan_being_written_keeps_its_label(self):
        self.assertEqual(live_progress.phase(facts(status=STATUS_PLAN)), "Preparing the next steps")


class EveryStatusHasALabel(unittest.TestCase):
    """A real Opus turn showed "Waiting for a render" as "Thinking" (thinking-ux real-turn, 2026-10-21): every status
    the add-on sets is either labelled for the live row or listed as one no running turn shows."""

    STATUS = re.compile(r'(?:status_text\s*=\s*|\.status\s*=\s*|emit\("status", \{"text": )"([A-Z][^"]{3,60})"')

    def test_every_literal_status_is_labelled_or_never_live(self):
        from angelo_agent.chat_titles import LIVE, NOT_LIVE
        found = {match for path in ADDON.glob("*.py")
                 for match in self.STATUS.findall(path.read_text(encoding="utf-8"))}
        self.assertGreater(len(found), 10, "the scan finds the statuses")
        missing = sorted(found - set(LIVE) - NOT_LIVE - {STATUS_THINKING})
        self.assertEqual(missing, [], "label these in chat_titles.LIVE or list them in NOT_LIVE")

    def test_labels_are_what_the_live_row_shows(self):
        for status in ("Waiting for a render", "Rendering a look", "Approval required"):
            self.assertNotEqual(live_progress.phase(facts(status=status)), "Thinking", status)


class Note(unittest.TestCase):
    def test_a_quiet_think_without_a_summary_says_so_after_the_threshold(self):
        quiet = facts(thinking=True, last_model=100.0, last_heard=100.0)
        self.assertIsNone(live_progress.note(quiet, 100.0 + live_progress.QUIET_SECONDS - 1))
        kind, text = live_progress.note(quiet, 100.0 + live_progress.QUIET_SECONDS)
        self.assertEqual(kind, QUIET)
        self.assertIn("without sharing its thoughts", text)

    def test_quiet_is_the_thinking_rows_own_age_not_the_model_clock(self):
        # Claude Code's default display streams empty thinking updates: model events keep arriving while nothing
        # shows. A row open 25 s without text is quiet even though the last model event was a second ago.
        updating = facts(thinking=True, last_model=159.0, last_heard=159.0, thinking_age=25.0)
        self.assertEqual(live_progress.note(updating, 160.0)[0], QUIET)
        self.assertIsNone(live_progress.note(facts(thinking=True, last_model=100.0, thinking_age=5.0), 160.0))

    def test_the_age_comes_from_the_controllers_own_thinking_start(self):
        import time
        messages = [{"role": "user"}, {"role": "thinking", "text": "", "status": "running"}]
        controller = chat(messages, anchor=0)
        controller._thinking_messages, controller._thinking_times = {"t1": 1}, {"t1": time.perf_counter() - 30}
        self.assertGreaterEqual(live_progress.facts(controller).thinking_age, 30)
        controller._thinking_messages = {"other": 1}
        self.assertIsNone(live_progress._thinking_age(controller, 0))

    def test_a_think_that_streams_its_summary_has_no_note(self):
        self.assertIsNone(live_progress.note(facts(thinking=True, summarized=True), 160.0))

    def test_keepalives_hold_off_a_stall_but_not_the_quiet_note(self):
        alive = facts(thinking=True, last_model=100.0, last_heard=299.0)
        self.assertEqual(live_progress.note(alive, 300.0)[0], QUIET)

    def test_nothing_at_all_past_the_threshold_is_a_stall_with_its_time(self):
        silent = facts(thinking=True, last_model=100.0, last_heard=100.0)
        kind, text = live_progress.note(silent, 100.0 + live_progress.STALL_SECONDS + 5)
        self.assertEqual(kind, STALL)
        self.assertIn(live_progress.elapsed(live_progress.STALL_SECONDS + 5), text)
        self.assertIn("Stop", text)

    def test_a_runtime_without_keepalives_stalls_only_with_no_reasoning_open(self):
        self.assertEqual(live_progress.note(facts(last_model=10.0, last_heard=None, started=0.0), 200.0)[0], STALL)
        quiet = facts(last_model=10.0, last_heard=None, started=0.0, thinking=True)  # Codex, unsummarized reasoning.
        self.assertEqual(live_progress.note(quiet, 400.0)[0], QUIET, "A quiet model is never called a stall")

    def test_no_round_in_progress_is_never_judged(self):
        # Between rounds, between a plan's operations or while the runtime relaunches: the host is working.
        for now in (150.0, 290.0, 3600.0):
            self.assertIsNone(live_progress.note(facts(started=100.0, last_model=None, last_heard=None), now))

    def test_a_running_step_or_an_unwatched_provider_never_notes(self):
        step = {"role": "activity", "tool": "angelo_execute_python", "status": "running"}
        self.assertIsNone(live_progress.note(facts(step=step, last_heard=0.0, last_model=0.0), 1000.0))
        self.assertIsNone(live_progress.note(facts(watched=False, last_heard=None, last_model=None), 1000.0))

    def test_the_clock_belongs_to_the_request_that_started_it(self):
        controller = chat([{"role": "user"}], started=42.0)
        self.assertEqual(live_progress.started(controller), 42.0)
        controller._request_generation = 4  # An External CLI read or work opened since: not this clock.
        self.assertIsNone(live_progress.started(controller))
        self.assertIsNone(live_progress.facts(controller).started)
        controller.turn_started = 42.0  # Not a (generation, time) pair.
        self.assertIsNone(live_progress.started(controller))
        controller = chat([{"role": "user"}])
        controller.busy = False
        self.assertIsNone(live_progress.facts(controller).started, "No clock once the turn is over")

    def test_external_requests_start_a_new_generation_and_sends_set_the_clock_there(self):
        source = (ADDON / "controller.py").read_text(encoding="utf-8")
        begin = source[source.index("def _begin_metrics"):][:600]
        self.assertIn("self.turn_started = (self._request_generation, time.monotonic())", begin)
        external = (ADDON / "controller_external.py").read_text(encoding="utf-8")
        read = external[external.index("def open_external_read"):external.index("def close_external_read")]
        work = external[external.index("def open_external_work"):external.index("def send_external_task")]
        self.assertIn("self._request_generation += 1", read)
        self.assertIn("self.open_external_read()", work)

    def test_live_puts_it_together(self):
        shown = live_progress.live(facts(thinking=True), 137.4)
        self.assertEqual((shown.label, shown.clock, shown.kind), ("Thinking", "37s", QUIET))


class FactsFromAController(unittest.TestCase):
    def test_reads_this_turns_open_thinking_and_running_step_only(self):
        old_step = {"role": "activity", "tool": "angelo_view", "status": "running"}
        messages = [old_step, {"role": "user", "text": "Build"},
                    {"role": "thinking", "text": "", "status": "running"},
                    {"role": "activity", "tool": "WebSearch", "status": "running"}]
        activity = turn_activity.TurnActivity(clock=lambda: 120.0)
        provider = SimpleNamespace(_activity=activity, _input_accepted=True)  # Codex's shape.
        found = live_progress.facts(chat(messages, provider=provider, anchor=1))
        self.assertTrue(found.thinking)
        self.assertFalse(found.summarized)
        self.assertIs(found.step, messages[3])
        self.assertEqual((found.accepted, found.last_model, found.last_heard, found.started), (True, 120.0, None, 50.0))  # Codex sends no keep-alives.
        self.assertTrue(found.watched)

    def test_claude_reads_its_transports_turn_and_none_between_turns(self):
        activity = turn_activity.TurnActivity(clock=lambda: 7.0)
        turn = SimpleNamespace(accepted=False, activity=activity, done=SimpleNamespace(is_set=lambda: False))
        claude = SimpleNamespace(_transport=SimpleNamespace(turn=turn))
        self.assertEqual(turn_activity.signs(claude), (True, (False, 7.0, 7.0)))
        claude._transport = None  # Not started yet: watched, nothing heard, the turn's own start counts.
        self.assertEqual(turn_activity.signs(claude), (True, None))
        turn.done = SimpleNamespace(is_set=lambda: True)
        self.assertEqual(turn_activity.signs(SimpleNamespace(_transport=SimpleNamespace(turn=turn))), (True, None))

    def test_an_unknown_provider_or_a_failing_read_is_unwatched(self):
        for provider in (None, SimpleNamespace(), SimpleNamespace(_activity="not one")):
            found = live_progress.facts(chat([{"role": "user"}], provider=provider))
            self.assertEqual((found.accepted, found.last_model, found.last_heard, found.watched),
                             (None, None, None, False))

        class Broken:  # A transport torn down between the check and the read.
            def __init__(self):
                self._transport = self

            @property
            def turn(self):
                raise RuntimeError("closed")
        self.assertFalse(live_progress.facts(chat([{"role": "user"}], provider=Broken())).watched)


class ThinkingDetail(unittest.TestCase):
    def test_a_summary_is_shown_with_home_folders_hidden(self):
        message = {"role": "thinking", "text": "Reading /Users/ana/Desktop/castle.blend and C:\\Users\\Bo\\x.png",
                   "status": "completed"}
        self.assertEqual(live_progress.thinking_detail(message), "Reading ~/Desktop/castle.blend and ~\\x.png")

    def test_no_summary_says_how_long_and_what_it_led_to(self):
        message = {"role": "thinking", "text": "", "status": "completed", "duration_ms": 130400}
        plan = {"role": "activity", "tool": "StructuredOutput", "status": "completed",
                "arguments": {"operations": [{}, {}, {}]}}
        text = live_progress.thinking_detail(message, [plan])
        self.assertIn("2m 10s", text)
        self.assertIn("wrote a plan with 3 steps", text)
        self.assertNotIn("runtime did not supply", text)
        step = {"role": "activity", "tool": "angelo_execute_python", "status": "completed"}
        self.assertIn("then started this step: Ran Blender Python.",
                      live_progress.thinking_detail(message, [{"role": "thinking"}, step]))
        answer = {"role": "assistant", "text": "Done."}
        self.assertEqual(live_progress.thinking_detail(dict(message, duration_ms=8000), [answer]),
                         "The model worked for 8s without sharing its thoughts, then answered.")
        self.assertEqual(live_progress.thinking_detail(dict(message, duration_ms=60000)),
                         "The model worked for 1m 0s without sharing its thoughts.")

    def test_an_open_row_without_a_summary_says_it_is_still_working(self):
        self.assertIn("working", live_progress.thinking_detail({"role": "thinking", "text": "", "status": "running"}))

    def test_personal_keeps_ordinary_paths_words_and_web_addresses(self):
        for text in ("users/home are words", "/tmp/render.png", "the Users folder", "C:/Program Files/x", "/rooted/x",
                     "see https://example.com/Users/ana/profile", "Users/ana is the folder", "~/x"):
            self.assertEqual(live_progress.personal(text), text)

    def test_personal_hides_every_home_folder_form(self):
        # The break-it review's leaks (breakit/r3_personal.out), as a class: spaces, encodings, mounts, ~user, root.
        cases = {"/home/kim": "~", '"/Users/ana"': '"~"', "/Users/Ana Lee/Desktop/x.blend": "~/Desktop/x.blend",
                 "Saved to C:\\Users\\John Smith\\x.blend": "Saved to ~\\x.blend",
                 "C:\\Documents and Settings\\ana\\x": "~\\x", "%2FUsers%2Fana%2Fx.blend": "~%2Fx.blend",
                 "~ana/x.blend": "~/x.blend", "/root/x.blend": "~/x.blend", "file:///Users/ana/x": "file://~/x",
                 "Look in /Users/ana. Then in /home/bo, ok": "Look in ~. Then in ~, ok", "(/Users/ana)": "(~)",
                 "/Users/ana then /home/bo/x": "~ then ~/x"}
        for text, shown in cases.items():
            self.assertEqual(live_progress.personal(text), shown, text)
        self.assertNotIn("ana", live_progress.personal("/Volumes/Macintosh HD/Users/ana/x"))


class SignsOfLife(unittest.TestCase):
    def test_keepalives_move_heard_only_and_rest_moves_both(self):
        now = [0.0]
        activity = turn_activity.TurnActivity(clock=lambda: now[0])
        now[0] = 5.0
        activity.hear()
        self.assertEqual((activity.last, activity.heard), (0.0, 5.0))
        now[0] = 9.0
        activity.touch()
        self.assertEqual((activity.last, activity.heard), (9.0, 9.0))
        now[0] = 12.0
        activity.rest()
        self.assertEqual((activity.last, activity.heard, activity.stretch), (12.0, 12.0, 12.0))

    def test_claude_hears_every_event_and_touches_only_on_model_events(self):
        source = (ADDON / "providers.py").read_text(encoding="utf-8")
        self.assertIn("(turn.activity.touch if answering(event) else turn.activity.hear)()", source)
        tree = ast.parse(source)  # Both providers keep what signs() reads.
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertTrue({"_transport", "_activity", "_input_accepted"} <= names)

    def test_claude_api_retry_shows_the_retrying_status(self):
        events = RoundStream().events({"type": "system", "subtype": "api_retry", "error_status": 529}, {})
        self.assertIn(("status", {"text": RETRYING}), events)


class ClockFrames(unittest.TestCase):
    def setUp(self):
        bare_bpy(self)
        from angelo_agent import marble_host
        self.host = marble_host

    def test_a_new_second_is_a_tick_and_otherwise_the_pulse_paces_frames(self):
        frame = self.host.clock_frame
        self.assertEqual(frame(None, False, 5.0, 0.0), (None, None))
        self.assertEqual(frame(0.0, False, 3.01, 2.99)[0], self.host.TICK)
        kind, delay = frame(0.0, False, 3.02, 3.01)
        self.assertIsNone(kind)
        self.assertAlmostEqual(delay, 1 / self.host.PULSE_FPS - 0.01)
        self.assertEqual(frame(0.0, False, 3.0 + 2 / self.host.PULSE_FPS, 3.01)[0], self.host.PULSE)

    def test_reduce_motion_keeps_only_the_seconds(self):
        kind, delay = self.host.clock_frame(0.0, True, 3.5, 3.1)
        self.assertIsNone(kind)
        self.assertAlmostEqual(delay, 0.5)

    def test_only_an_unfocused_window_lets_the_seconds_through(self):
        self.assertEqual(self.host.TICK_THROUGH, frozenset({"unfocused"}))

    def test_a_failing_painter_loses_its_frame_never_the_marble(self):
        source = (ADDON / "marble_draw.py").read_text(encoding="utf-8")
        handler = source[source.index("except Exception as error:  # Never let decoration"):][:400]
        self.assertLess(handler.index("if painter:"), handler.index("_failed(error)"))
        self.assertIn("paint_errors += 1", handler)


if __name__ == "__main__":
    unittest.main()
