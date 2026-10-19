# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's marble wait in the real app: carved while Claude plans, revealed when the plan arrives.

Real default startup and real turns through the stand-in ``claude`` CLI (``chat_waiting_fixtures``): thinking
summary sentences, then a castle plan at Opus 5.5's recorded pace, accepted, and a verify round. The provider,
``claude_stream``, the controller's pump, ``marble_host``'s timer and ``marble_draw`` are the app's own; every
marble draw is recorded where it is drawn (``marble_draw._note``), with window screenshots and enlarged crops of
the hero and the live row's icon at the moments checked. No account, network or inference.

Covered:

1. Carving: the hero (112 px) under the request and the 22 px raven on the live row instead of the spinner,
   chips falling before any event, a tap for each thinking line and for the plan's rows, never past the cap.
2. The reveal on the plan's arrival (the accepted plan call's row), never before: all stone gone, the eyes up and
   a blink, the hero at 56 px; while the turn builds, the live row's raven is tilted and blinks, with sparse frames.
3. Frames: never more than 24 a second; after the turn the timer is gone, the hero is gone and nothing redraws.
4. Reduce Motion (Blender's preference): the stone fades in place (no dust, no shake), no blink, the hero's size
   steps once, and no frames while steps run.
5. Cost: the marble's own draw time per frame and the chat region's whole draw time, during the wait and after.
6. Pixels: pale stone in the hero and icon while carving; the teal mark and no stone once revealed.

Run through ``run_native_suites.py --gui --include test_blender_chat_waiting``; check ``chat-waiting.json``
for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-waiting-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
import chat_waiting_fixtures as rounds  # noqa: E402
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.
ROUNDS = OUT / "rounds"
ROUNDS.mkdir(exist_ok=True)
fake_provider_cli.sign_in(os.environ["HOME"], "claude")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "chat-waiting.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "observed": {}, "moments": {},
         "fixture": "Stand-in claude CLI streaming scripted thinking and plans; no browser, account or inference"}
started = time.monotonic()
draws, frames, marks = [], [], {}  # Marble draws (seconds since sent, kind, record); (seconds, timer frames).


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def click(action, value=None):
    area, region = chat()
    rect = next(r for r, a, v in view_state()["hits"] if a == action and (value is None or v == value))
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def send(text):
    area, region = chat()
    area.spaces.active.history[-1].body = text
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='RET', value='PRESS', x=x, y=y)
    window().event_simulate(type='RET', value='RELEASE', x=x, y=y)


def since():
    return time.monotonic() - state.get("sent", started)


def install_recorder():
    from angelo_agent import marble_draw
    original = marble_draw._note

    def note(drawn_state, kind, wait, block, size, now, rect):
        original(drawn_state, kind, wait, block, size, now, rect)
        if "sent" in state and len(draws) < 40000:
            draws.append((round(since(), 3), state["phase"], kind, dict(drawn_state["marble_drawn"][kind])))
    marble_draw._note = note


def texts():
    """Redraw once and return the chat's text runs."""
    from angelo_agent import chat_view
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    try:
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        chat_view.text = original
    return " ".join(runs)


def chat_draw_ms(iterations=20):
    """The chat region's whole draw, in milliseconds per draw."""
    area, region = chat()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        begin = time.perf_counter()
        bpy.ops.wm.redraw_timer(type='DRAW', iterations=iterations)
        return round((time.perf_counter() - begin) * 1000 / iterations, 3)


def snap(name, crops=True):
    """A window screenshot with enlarged crops of the hero and the icon as last drawn; what each held."""
    from angelo_agent import marble_draw, marble_frames
    text = texts()
    path = OUT / (name + ".png")
    marble_frames.trace = []
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    trace, marble_frames.trace = marble_frames.trace, None
    state["screenshots"].append(str(path))
    drawn = {kind: dict(record) for kind, record in view_state().get("marble_drawn", {}).items()}
    moment = {"at": round(since(), 2), "drawn": drawn, "text": text[:600], "screenshot": str(path),
              "marble_ms": round(marble_draw.draw_seconds * 1000 / max(1, marble_draw.draw_calls), 4), "trace": trace}
    region = chat()[1]
    for kind, zoom in (("hero", 2), ("icon", 6)):
        record = drawn.get(kind)
        if crops and record and time.monotonic() - record["at"] < 1.0:
            x, y, w, h = record["rect"]
            crop = OUT / ("%s-%s.png" % (name, kind))
            moment[kind + "_pixels"] = rounds.crop(path, (region.x + x, region.y + y, w, h), crop, zoom)
            state["screenshots"].append(str(crop))
    state["moments"][name] = moment
    return moment


def played():
    counter = ROUNDS / "next"
    return int(counter.read_text()) if counter.is_file() else 0


def begin(phase, scripted, message):
    base_round = played()
    for offset, events in enumerate(scripted):
        (ROUNDS / ("round-%d.json" % (base_round + offset))).write_text(json.dumps(events), encoding="utf-8")
    draws.clear()
    frames.clear()
    marks.clear()
    state.update(phase=phase, base=base_round, rounds=len(scripted), sent=time.monotonic())
    send(message)
    return .05


def request_done(controller):
    return not controller.busy and played() == state["base"] + state["rounds"]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    bpy.context.preferences.view.use_reduce_motion = False
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_CHAT_WAITING", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait_for(condition, limit=60):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def track(controller):
    """Per tick: the timer's frame count, and when the plan call's accepted row first appeared."""
    from angelo_agent import marble_host
    frames.append((round(since(), 3), marble_host.frames))
    if "plan_row" not in marks and any(m.get("tool") == "StructuredOutput" and m.get("status") == "completed"
                                       for m in controller.state["messages"][-40:]):
        marks["plan_row"] = since()


def most_frames_in_a_second(window_from=0.0, window_to=1e9):
    """The most timer frames within any one second of the samples between the two times."""
    best = 0
    samples = [(t, n) for t, n in frames if window_from <= t <= window_to]
    for t0, n0 in samples:
        later = [n for t, n in samples if t0 <= t <= t0 + 1.0]
        best = max(best, max(later) - n0)
    return best


def recorded(kind, phase=None, test=lambda record: True):
    return [(t, r) for t, _, k, r in draws if k == kind and (phase is None or r["phase"] == phase) and test(r)]


def boot(controller, checks):
    from angelo_agent import director, ui
    if not director.is_director(window()) or director._timer is not None or ui._layout_timer is not None:
        return .2
    import angelo_agent
    state["observed"]["add_on"] = str(Path(angelo_agent.__file__).resolve().parent)
    checks["the_add_on_is_this_checkouts"] = (state["observed"]["add_on"]
                                              == str((ROOT / "scripts/addons_core/angelo_agent").resolve()))
    assert checks["the_add_on_is_this_checkouts"], "The bundled add-on loaded instead of this checkout's"
    if not wait_for(lambda: not ui._authentication.running and ("welcome_connect", "claude_subscription") in [
            (action, value) for _, action, value in view_state().get("hits", [])]):
        return .2
    click("welcome_connect", "claude_subscription")
    state["phase"] = "connected"
    return .3


def connected(controller, checks):
    from angelo_agent import quick_connect, ui
    if not wait_for(lambda: quick_connect.flow("claude_subscription") is None and controller.models
                    and ui.connection_available()):
        return .2
    controller.set_options(model="claude-opus-5-5")
    install_recorder()
    bpy.context.preferences.view.use_reduce_motion = False
    state["observed"]["idle_chat_draw_ms"] = chat_draw_ms()
    return begin("castle", rounds.castle_rounds(), "Build a small castle on a hill.")


def ended(controller):
    """The turn is over: wait 0.4 s, then return the frames the timer asked for since (None until then)."""
    from angelo_agent import marble_host
    if not wait_for(lambda: request_done(controller)):
        return None
    if "ended" not in marks:
        marks.update(ended=since(), frames_at_end=marble_host.frames)
        return None
    if since() - marks["ended"] < 0.4:
        return None
    return marble_host.frames - marks["frames_at_end"]


def castle(controller, checks):
    from angelo_agent import marble_host, marble_wait as mw
    track(controller)
    wait, moments, t = marble_host.current(controller), state["moments"], since()
    if played() == state["base"] + 2 and "round_two" not in marks:
        marks["round_two"] = t
    if wait and wait.phase == mw.CARVING and t >= 2.4 and wait.taps and "1-waiting-hero" not in moments:
        snap("1-waiting-hero")
        state["observed"]["waiting_chat_draw_ms"] = chat_draw_ms()
    elif wait and wait.phase == mw.CARVING and wait.seen.rows >= 2 and "2-waiting-plan-rows" not in moments:
        snap("2-waiting-plan-rows")
    elif (wait and wait.phase == mw.BUILDING and wait.wake(time.monotonic()) >= 1.6 and "round_two" not in marks
          and "3-revealed-hero" not in moments):
        snap("3-revealed-hero")
    elif wait and "round_two" in marks and t - marks["round_two"] >= 0.9 and "4-building" not in moments:
        snap("4-building")
    after = ended(controller)
    if after is None:
        return .03
    snap("5-after-turn", crops=False)
    state["observed"].update(after_chat_draw_ms=chat_draw_ms(), plan_row_at=marks.get("plan_row"),
                             turn_seconds=round(marks["ended"], 2), frames_after_end=after,
                             timer_frames=frames[-1][1] - frames[0][1] if frames else 0,
                             most_frames_in_a_second=most_frames_in_a_second(),
                             building_frames=most_frames_in_a_second(marks.get("round_two", 0),
                                                                     marks.get("round_two", 0) + 2.2))
    first, rows_moment = moments.get("1-waiting-hero") or {}, moments.get("2-waiting-plan-rows") or {}
    revealed, building = moments.get("3-revealed-hero") or {}, moments.get("4-building") or {}
    carving = recorded("hero", "carving") + recorded("icon", "carving")
    shown = [t for t, r in recorded("hero") if r["phase"] != mw.CARVING]
    plan = marks.get("plan_row", 1e9)
    hero = lambda moment: (moment.get("drawn") or {}).get("hero") or {}  # noqa: E731
    checks["1_the_hero_is_drawn_at_112_px_while_carving"] = bool(carving) and all(
        r["size"] == mw.HERO for _, r in recorded("hero", "carving"))
    checks["1_the_live_row_draws_the_raven_not_the_spinner"] = bool(recorded("icon", "carving"))
    checks["1_chips_fall_before_any_event"] = any(r["removed"] and not r["taps"] for _, r in carving)
    checks["1_a_thinking_line_taps_and_names_the_live_row"] = hero(first).get("taps", 0) >= 1 and any(
        line[:24] in first.get("text", "") for line in rounds.LINES)
    checks["1_the_plans_rows_tap_again"] = hero(rows_moment).get("taps", 0) > hero(first).get("taps", 0)
    checks["1_the_stone_never_runs_out_while_carving"] = bool(carving) and max(
        r["removed"] / r["total"] for _, r in carving) <= 0.8
    checks["2_the_reveal_follows_the_plans_arrival"] = bool(shown) and plan - 0.15 <= shown[0] <= plan + 0.6
    checks["2_never_before_it"] = bool(shown) and all(t >= plan - 0.15 for t in shown)
    checks["2_all_stone_gone_and_the_hero_at_56_px"] = (hero(revealed).get("size") == mw.HERO_COMPACT
                                                        and hero(revealed).get("removed") == 128)
    checks["2_the_raven_looks_up_and_blinks"] = hero(revealed).get("look") == mw.LOOK_UNITS and any(
        r["eye_open"] < 0.5 for _, r in recorded("hero") if r["phase"] != mw.CARVING)
    checks["2_while_building_the_icon_is_tilted"] = abs(
        ((building.get("drawn") or {}).get("icon") or {}).get("tilt", 0) - mw.ICON_TILT) < 0.5
    checks["2_while_building_frames_are_sparse"] = state["observed"]["building_frames"] <= 8  # One blink: ~6.
    checks["3_never_more_than_24_frames_a_second"] = 0 < state["observed"]["most_frames_in_a_second"] <= 25
    checks["3_after_the_turn_the_timer_stops"] = not bpy.app.timers.is_registered(marble_host._tick) and after <= 1
    checks["3_and_nothing_is_drawn_after_it"] = not any(t > marks["ended"] + 0.15 for t, _, _, _ in draws)
    checks["6_stone_pixels_while_carving"] = (first.get("hero_pixels", {}).get("stone", 0) >= 0.3
                                              and first.get("icon_pixels", {}).get("stone", 0) >= 0.15)
    checks["6_teal_and_no_stone_once_revealed"] = (revealed.get("hero_pixels", {}).get("teal", 0) >= 0.08
                                                   and revealed.get("hero_pixels", {}).get("stone", 1) <= 0.03)
    checks["6_the_building_icon_is_teal"] = building.get("icon_pixels", {}).get("teal", 0) >= 0.05
    checks["the_castle_was_built"] = all(bpy.data.objects.get(name) for name in rounds.CASTLE_OBJECTS)
    state["observed"]["marble_draws"] = len(draws)
    return begin("measure", rounds.measure_rounds(), "Add a measured keep.")


def measure(controller, checks):
    """Main-thread share (loop rate), orbit frame time, one frame's cost and the kept picture, in a silent wait."""
    import chat_waiting_measure as gauge
    from angelo_agent import marble_frames, marble_host
    t, observed = since(), state["observed"]
    if t < 1.5:
        return .05
    if "loop" not in marks:
        marks.update(loop=gauge.LoopRate(), stats=dict(marble_frames.stats))
        marks["loop"].start()
    reading = lambda: (marks["loop"].count, marble_host.frames, marble_host.tick_seconds)  # noqa: E731
    if not gauge.alternate(marks, "rate", reading, 1.5, 10, t):
        return .02
    if "orbit" not in marks:
        marks["loop"].stop()
        marble_host.enabled = True
        marks.update(orbit=gauge.Orbit(window()), pauses=marble_host.pauses, reused=dict(marble_frames.stats))
        marks["orbit"].start()
        marks["orbit_from"] = t
    if not gauge.alternate(marks, "orbiting", lambda: (marble_host.frames,), marks["orbit_from"], 4, t):
        return .02
    if "rotated" not in marks:
        marks.update(rotated=marks["orbit"].stop(), pause_reason=marble_host.last_pause,
                     paused=marble_host.pauses - marks["pauses"])
        marble_host.enabled = True
        return .4
    if "frame" not in marks:
        if not marble_host.current(controller):
            return .05
        area, region = chat()
        marks["frame"] = {"marble": gauge.frame_cost(window(), area, region, True)}
        marble_host.enabled = False
        marks["frame"]["stock_chat"] = gauge.frame_cost(window(), area, region, False)
        marble_host.enabled = True
        return .3
    if "picture" not in marks:
        if not marble_host.current(controller):
            return .05
        marks["picture"] = gauge.same_picture(window(), chat()[1], view_state(), OUT, rounds.differing,
                                              state["observed"].setdefault("picture_paths", []))
        state["screenshots"] += [str(OUT / ("picture-%s.png" % name)) for name in ("kept", "direct", "kept-again")]
    if ended(controller) is None:
        return .05
    on, off = gauge.rates(marks["rate"].samples[True]), gauge.rates(marks["rate"].samples[False])
    shares = gauge.paired_shares(marks["rate"].samples)
    share = gauge.median(shares) if shares else None
    # The same share composed from its parts: frames a second in the "on" windows times one frame's CPU time,
    # plus the timer's own ticks (ticks run in "on" windows only).
    seconds_on = sum(end[0] - begin[0] for begin, end in marks["rate"].samples[True])
    frames_on = sum(end[1][1] - begin[1][1] for begin, end in marks["rate"].samples[True])
    ticks_on = sum(end[1][2] - begin[1][2] for begin, end in marks["rate"].samples[True])
    composed = (frames_on * marks["frame"]["marble"]["cpu_ms"] / 1000 + ticks_on) / max(1e-9, seconds_on)
    orbit = {flag: [ms for begin, end in marks["orbiting"].samples[flag]
                    for ms in marks["orbit"].frame_times(begin[0], end[0])] for flag in (True, False)}
    orbit_frames_on = sum(end[1][0] - begin[1][0] for begin, end in marks["orbiting"].samples[True])
    stats = {key: marble_frames.stats[key] - marks["stats"][key] for key in marble_frames.stats}
    observed.update(
        loop_rates={"on": [round(r) for r in on], "off": [round(r) for r in off]},
        main_thread_share_by_loop_rate={"median": None if share is None else round(share, 4),
                                        "pairs": [round(value, 4) for value in shares]},
        main_thread_share_composed={"share": round(composed, 4), "frames_per_second": round(
            frames_on / max(1e-9, seconds_on), 1), "tick_ms_per_second": round(
            ticks_on * 1000 / max(1e-9, seconds_on), 3)},
        orbit_frame_ms={flag and "on" or "off": {"median": gauge.median(values), "p95": gauge.percentile(values, .95),
                                                  "frames": len(values)} for flag, values in orbit.items()},
        orbit_marble_frames_on=orbit_frames_on, orbit_pauses=marks["paused"], orbit_pause_reason=marks["pause_reason"],
        frame_cost=marks["frame"], kept_picture_differs=marks["picture"], frames_kept_vs_full=stats)
    medians = observed["orbit_frame_ms"]
    checks["5_the_wait_takes_at_most_2_percent_of_the_main_thread"] = composed <= 0.02
    checks["5_and_the_loop_rate_agrees_within_its_noise"] = share is not None and share <= 0.03
    own = stats["reused_seconds"] * 1000 / max(1, stats["reused"])  # The frame's own draw: the picture and marks.
    observed["marble_frame_own_draw_ms"] = round(own, 4)
    checks["5_a_marble_frame_draws_in_under_half_a_millisecond"] = 0 < own <= 0.5
    checks["5_and_less_than_the_chats_full_draw"] = (marks["frame"]["marble"]["cpu_ms"] or 9) < (
        marks["frame"]["stock_chat"]["cpu_ms"] or 0)
    checks["5_marble_frames_reuse_the_kept_picture"] = stats["reused"] >= 5 * max(1, stats["full"])
    checks["5_the_kept_picture_is_the_chats_own"] = max(marks["picture"].values()) <= 0.002
    traced = [t for shot in observed.get("picture_paths", []) for t in shot["trace"]] + [
        t for moment in state["moments"].values() for t in moment.get("trace") or ()]
    checks["5_every_kept_draw_lands_in_the_regions_own_framebuffer"] = bool(traced) and all(
        t["before"][2:] == t["size"] and t["after"][2:] == t["size"] for t in traced)
    checks["5_orbiting_pauses_the_animation"] = (marks["rotated"] and orbit_frames_on <= 1 and marks["paused"] > 0
                                                 and marks["pause_reason"] == "modal")
    checks["5_and_orbit_frames_stay_within_noise_of_off"] = bool(orbit[True] and orbit[False]) and (
        medians["on"]["median"] <= medians["off"]["median"] * 1.05 + 0.05)
    bpy.context.preferences.view.use_reduce_motion = True
    return begin("reduced", rounds.quiet_rounds(), "Add a quiet keep beside it.")


def still_frame(wait, now):
    """Whether the hero's chips in flight sit where they stood, with no dust and no shake (Reduce Motion)."""
    from angelo_agent import marble_wait as mw
    drawn = mw.frame(wait, wait.hero, now, False)
    rest = [p for flight in wait.hero.flights if 0 <= now - flight.start < 0.6
            for p in wait.hero.chips[flight.chip].mesh.positions]
    return bool(rest) and list(drawn.flying.positions) == rest and not drawn.dust.indices and drawn.offset == (0, 0)


def reduced(controller, checks):
    from angelo_agent import marble_host, marble_wait as mw
    track(controller)
    wait, moments = marble_host.current(controller), state["moments"]
    if wait and wait.phase == mw.CARVING and since() >= 1.3 and "6-reduced-waiting" not in moments:
        state["observed"]["reduced_still"] = still_frame(wait, time.monotonic())
        snap("6-reduced-waiting")
    elif (wait and wait.phase == mw.BUILDING and wait.wake(time.monotonic()) >= 1.2
          and "7-reduced-revealed" not in moments):
        marks["reduced_building"] = since()
        snap("7-reduced-revealed")
    after = ended(controller)
    if after is None:
        return .03
    building = most_frames_in_a_second(marks.get("reduced_building", 1e9), marks["ended"] - 0.05)
    state["observed"].update(reduced_frames_while_building=building, reduced_frames_after_end=after)
    records = [r for _, _, _, r in draws]
    checks["4_reduce_motion_reaches_the_wait"] = bool(records) and all(r["reduced"] for r in records)
    checks["4_the_stone_fades_in_place_without_dust_or_shake"] = state["observed"].get("reduced_still") is True
    checks["4_no_blink"] = all(r["eye_open"] == 1.0 for r in records)
    checks["4_the_hero_steps_once"] = {r["size"] for _, r in recorded("hero")} <= {mw.HERO, mw.HERO_COMPACT}
    checks["4_no_frames_while_steps_run"] = building <= 1
    checks["4_and_the_timer_stops_with_the_turn"] = not bpy.app.timers.is_registered(marble_host._tick)
    from angelo_agent import marble_draw
    mean = marble_draw.draw_seconds * 1000 / max(1, marble_draw.draw_calls)
    state["observed"].update(marble_draw_ms_mean=round(mean, 4), marble_draw_calls=marble_draw.draw_calls,
                             marble_tick_ms_total=round(marble_host.tick_seconds * 1000, 2),
                             marble_last_error=marble_host.last_error)
    checks["5_no_tick_failed"] = marble_host.last_error is None
    finish()
    return None


PHASES = {"boot": boot, "connected": connected, "castle": castle, "measure": measure, "reduced": reduced}


def tick():
    try:
        assert time.monotonic() - started < 290, "Chat-waiting fixture timed out in " + state["phase"]
        from angelo_agent import ui
        return PHASES[state["phase"]](ui.get_controller(), state["checks"])
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=.5)
