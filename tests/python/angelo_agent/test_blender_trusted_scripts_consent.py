# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Consent in the compiled app's window: the trusted-scripts dialog, then a hosted worker chat's approval cards.

Synthetic events, Angelo's default chat startup (Director Mode, the default view), isolated state; no provider,
account, network or paid call. The ``angelo.trusted_scripts`` dialog: no consent at first and ``EXEC_DEFAULT`` refuses
(before, after a cancel, after a revoke); Escape leaves it off, Enter grants it, invoking again revokes it, a New
Chat starts without it. Hosted (design A′): a worker chat made as the lead makes one (``worker_sessions._new_chat``: a
mirror, in Ask), started in a real worker Blender (``worker_routing.start``, the installed headless script) and played
by the stand-in ``codex`` the lead's runtime service relays. The person clicks the worker's tile on the session rail,
types in the composer and presses Return, and clicks the drawn buttons; every card is drawn by the chat's real draw
(``chat_guard.shown_approvals`` records each drawn Approve; nothing here calls ``drew``). Checked from the mirror, its
journal, the stand-in's log, the drawn rows and the window's offscreen pixels (``live_view.window_pixels``):

* a typed edit's card names its object, targets the worker's part and shows the worker's digest; Approve runs it there;
* a script's card: Decline runs nothing (``DECLINED``); each answered card clears, in state and on screen;
* stale: a hosted call never changes while its card waits (one chat per worker), so the suite plays the re-sent card a
  changed call would send, through the mirror's own ``receive``; Approve of the redrawn card reaches a worker whose
  call does not match: nothing runs, and the window shows the refusal row with its code (WRK-CHANGED).

``hosted-cards-scenario.json`` keeps the scripted turns and every click. The lanes variant records the hosted cases as
skipped, with why; every platform runs them in the hosted variant. Run through ``run_native_suites.py --gui --include
test_blender_trusted_scripts_consent``; check ``trusted-consent-gui.json`` for ``success`` and ``phase == "complete"``.
"""

import copy
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert not bpy.app.background, "The consent dialog needs a window"
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # The QA helpers beside this file.
import qa_boot  # noqa: E402
import qa_frames  # noqa: E402
import qa_input  # noqa: E402
import qa_waits  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')  # The dialog belongs to the default chat.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-trusted-consent-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {}, "screenshots": [], "details": {}, "timings": {},
          "scope": "The real native trust dialog, then a hosted worker chat's cards approved, declined and stale "
                   "with real clicks; no Undo or recovery clearance"}
STARTED = time.monotonic()
LIMIT = 105.0 * qa_waits.timeout_scale()   # Under the manifest's 120 s (both scale with --timeout-scale).
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False


def parts():
    from angelo_agent import console, ui
    window = ui.main_window()
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return {"window": window, "area": area, "region": next(r for r in area.regions if r.type == 'WINDOW')}


def invoke():
    with bpy.context.temp_override(**parts()):
        return bpy.ops.angelo.trusted_scripts('INVOKE_DEFAULT')


def execute_direct():
    """The operator without its dialog: it must refuse, never grant consent."""
    with bpy.context.temp_override(**parts()):
        try:
            return bpy.ops.angelo.trusted_scripts('EXEC_DEFAULT')
        except RuntimeError as error:  # Blender raises an ERROR report for a Python caller.
            if "Open the trust dialog for the current chat first" not in str(error):
                raise
            return {'CANCELLED'}


def key(kind):
    for value in ('PRESS', 'RELEASE'):
        parts()["window"].event_simulate(type=kind, value=value)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=parts()["window"]):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    REPORT["screenshots"].append(str(path))


def check(name, condition, detail=None):
    """Record ``name``; a failed check ends the run with what it saw."""
    REPORT["checks"][name] = bool(condition)
    if not condition and detail is not None:
        REPORT["details"][name] = str(detail)[:1500]
    assert condition, name


# The hosted worker chat ---------------------------------------------------------------------------------------------

HOSTED = {"chat": None, "mirror": None, "frames": [], "actions": [], "waiting": None}
DRAWN = {"cards": [], "notices": []}   # What the chat's real draw drew, newest last (``probe_draws``).
SCRIPT = {"description": "Add an empty named QA Declined",
          "code": "import bpy\nempty = bpy.data.objects.new('QA Declined', None)\n"
                  "bpy.context.scene.collection.objects.link(empty)\nresult = {'made': empty.name}"}
# Each case: the person's message, the stand-in's turn (a call that asks, then a read the worker runs at once: Ask
# asks only for edits), the button the person clicks and the words its card must show.
CASES = (("typed", "Add a one metre cube named QA Box.", ("qa-1", "angelo_create_cube", {
              "name": "QA Box", "size": 1.0, "location": [0.0, 0.0, 0.5]}), "qa-2", "approve", ("QA Box",)),
         ("script", "Add an empty named QA Declined with a script.", ("qa-3", "angelo_execute_python", SCRIPT), "qa-4",
          "decline", ("QA Declined",)),
         ("stale", "Move QA Box to x 2.", ("qa-5", "angelo_set_transform", {
              "object": "QA Box", "location": [2.0, 0.0, 0.5]}), "qa-6", "approve", ("QA Box",)))
TURNS = {name: [{"call": {"id": ask[0], "tool": ask[1], "arguments": ask[2]}},
                {"call": {"id": read, "tool": "angelo_inspect_scene", "arguments": {}}}]
         for name, _text, ask, read, _button, _words in CASES}
CHANGED = [3.0, 0.0, 0.5]   # Where the stale case's re-sent card says the move now goes.


def skip_reason():
    """Why the hosted cases do not run here, or None. Every platform runs them: the stand-in codex is the kind the
    lead launches there (``hosted_codex_stand_in.install``: a shell wrapper, or a launcher exe on Windows)."""
    from angelo_agent import worker_chat_lifecycle
    return ("lanes variant: no chat is hosted in its worker (the other worker suites' lanes variants cover it)"
            if not worker_chat_lifecycle.enabled() else None)


def probe_draws():
    """Record each approval card and notice card the chat's draw really drew (rows, rectangle), then draw as before."""
    from angelo_agent import chat_card_draw, chat_notices
    card_draw, notice_draw = chat_card_draw.draw, chat_notices.draw

    def card(role, message, rows, key_, top, bottom, ctx):
        card_draw(role, message, rows, key_, top, bottom, ctx)   # Recorded once it drew (a raise: never).
        if role == "approval":
            DRAWN["cards"] = DRAWN["cards"][-40:] + [{"key": key_, "at": time.monotonic(), "rows": [
                str(row[0]).rstrip() for row in rows], "rect": (16, bottom, ctx["width"] - 32, top - bottom)}]

    def notice(state, ui, controller, message, key_, top, bottom, width, *rest):
        notice_draw(state, ui, controller, message, key_, top, bottom, width, *rest)
        title = str((message.get("_notice_card") or {}).get("title"))
        DRAWN["notices"] = DRAWN["notices"][-40:] + [{"rect": (16, bottom, width - 32, top - bottom), "title": title,
                                                      "at": time.monotonic()}]
    chat_card_draw.draw, chat_notices.draw = card, notice


def start_worker():
    """A worker chat made as the lead makes one (inheriting Ask), started in a real worker Blender on its part."""
    import hosted_worker_chat
    from angelo_agent import ui, worker_mirror_events, worker_routing, worker_sessions
    os.environ["ANGELO_ENABLE_PARALLEL_AGENTS"] = "1"   # The rail's tiles, whatever this machine's capability.
    hosted_worker_chat.install_stand_in(OUT / "stand-in")
    lead = ui.get_controller()
    record = hosted_worker_chat.connect(lead)   # ChatGPT, signed in on this computer (the stand-in says so),
    bpy.ops.angelo.connection_check(connection_id=record)   # checked as its "Refresh status" does.
    options = dict(lead.state.get("options") or {})
    lead.state["options"] = dict(options, approval="ASK")   # The new chat inherits the lead's mode, as it does.
    try:
        mirror = worker_sessions._new_chat(lead)
    finally:
        lead.state["options"] = options
    receive = mirror.receive

    def recording(kind, data):   # The worker's re-sent cards, kept for the stale case (any thread).
        if kind == worker_mirror_events.PENDING:
            HOSTED["frames"] = HOSTED["frames"][-6:] + [copy.deepcopy(data)]
        return receive(kind, data)
    mirror.receive = recording
    started = time.monotonic()
    worker_routing.start(mirror, OUT / "parts" / "hosted-cards.blend")
    REPORT["timings"]["worker_start_s"] = round(time.monotonic() - started, 3)
    HOSTED.update(mirror=mirror, chat=hosted_worker_chat.HostedWorkerChat.of(mirror), lead_document=ui.document_key())


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(parts()["area"].as_pointer(), {})


def actions():
    return [(action, value) for _rect, action, value in view_state().get("hits", ())]


def to_window(rect):
    """A canvas rectangle (unscaled, region-relative) in window pixels: (x, y, width, height)."""
    from angelo_agent import chat_view
    region, scale = parts()["region"], chat_view.ui_scale()
    return (region.x + rect[0] * scale, region.y + rect[1] * scale, rect[2] * scale, rect[3] * scale)


def control(action, value):
    rect = next((r for r, a, v in view_state().get("hits", ()) if a == action and v == value), None)
    return to_window(rect) if rect is not None else None


def press(action, value):
    """The person's click on a drawn control (``qa_input``: the pointer arrives, the press follows a pass later)."""
    x, y, w, h = control(action, value)
    point = (x + w / 2, y + h / 2)
    HOSTED["actions"].append({"phase": REPORT["phase"], "click": action, "call": value, "at": [int(v) for v in point]})
    qa_input.press(parts()["window"], *point)


def pixels():
    import numpy
    from angelo_agent import live_view
    width, height, data = live_view.window_pixels(window := parts()["window"])
    return numpy.frombuffer(bytes(data), dtype=numpy.uint8).reshape(height, width, 4), width / max(1, window.width)


def crop(image, factor, rect):
    x, y, w, h = (int(round(v * factor)) for v in rect)
    return image[max(0, y):y + h, max(0, x):x + w]


def shot(name, rect=None):
    """The window drawn now (offscreen), saved; with ``rect`` (window pixels) that part too, returned as an array."""
    from angelo_agent import view_png
    image, factor = pixels()
    REPORT["screenshots"].append(str(OUT / name))
    for path, part in ((OUT / name, image), (OUT / name.replace(".png", "-crop.png"), None if rect is None else
                                             crop(image, factor, rect).copy())):
        if part is not None:
            path.write_bytes(view_png.encode_png(part.tobytes(), part.shape[1], part.shape[0], bottom_up=True))
    return part


def keyed(table, suffix):
    return next((value for key_, value in list(table.items()) if key_ == suffix or key_.endswith("/" + suffix)), None)


def drawn_card(suffix, digest=None, since=0.0):
    """The mirror's card for ``suffix`` as the chat last drew it, once its Approve recorded the digest it shows."""
    from angelo_agent import worker_consent_lead
    call = keyed(HOSTED["mirror"].pending, suffix)
    if call is None or not call.approval or not getattr(call, "needs_person", False):
        return None
    shown = worker_consent_lead._shown.get(HOSTED["mirror"].state["id"], {}).get(call.id)
    drawn = [card for card in DRAWN["cards"] if card["key"] == call.id and card["at"] > since]
    if not drawn or ("approve", call.id) not in actions() or shown != call.approval_digest \
            or (digest is not None and shown != digest):
        return None
    return call, drawn[-1]


def results(*suffixes):
    """What the stand-in model got for each call (its log), or None while any is unanswered."""
    rows = {row.get("id"): row for row in HOSTED["chat"].model_log("tool_result")}
    if not all(suffix in rows for suffix in suffixes):
        return None
    return {suffix: json.loads(rows[suffix].get("text") or "null") or {} for suffix in suffixes}


def say(case):
    """The person types the case's message in the worker chat's composer and presses Return; the stand-in plays it."""
    chat, area, region, window = HOSTED["chat"], parts()["area"], parts()["region"], parts()["window"]
    chat.queue_turn(TURNS[case[0]])
    area.spaces.active.history[-1].body = "%s [chat:%s] %s" % (case[1], chat.session, case[0])
    x, y = region.x + region.width // 2, region.y + 60
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('RET', 'PRESS'), ('RET', 'RELEASE')):
        window.event_simulate(type=kind, value=value, x=x, y=y)
    HOSTED["actions"].append({"phase": REPORT["phase"], "message": case[0]})
    REPORT["phase"] = "card:" + case[0]


def until(condition, label, limit=30.0):
    """``condition()`` once true (the wait rearms), else None; a wait over ``limit`` x the scale fails, naming it."""
    value = condition()
    if value:
        HOSTED["waiting"] = None
        return value
    HOSTED["waiting"] = HOSTED["waiting"] or time.monotonic()
    assert time.monotonic() - HOSTED["waiting"] < limit * qa_waits.timeout_scale(), "Timed out waiting: " + label
    return None


def card_checks(name, call, card, words):
    """State and pixels of a drawn card: the worker's digest as drawn, its rows, its target and its drawn Approve."""
    from angelo_agent import worker_consent_lead
    mirror = HOSTED["mirror"]
    check(name + "_card_is_the_workers_pending_call", call.approval_digest and not call.approved and not results(
        call.id.rsplit("/", 1)[-1]))
    check(name + "_drawn_digest_is_the_workers", (call.name, call.approval_digest) == tuple(
        worker_consent_lead.drawn(mirror.state["id"], call.id) or ()))
    check(name + "_card_names_its_target", all(word in " ".join(card["rows"]) for word in words), card["rows"])
    check(name + "_targets_the_workers_part", call.document is not None and call.document == mirror.document
          and call.document != HOSTED["lead_document"], (call.document, HOSTED["lead_document"]))
    image = shot("hosted-%s-card.png" % name, to_window(card["rect"]))
    check(name + "_card_pixels_drawn", qa_frames.drawn(image[..., :3]), qa_frames.content_share(image[..., :3]))
    button = crop(*pixels(), control("approve", call.id))
    check(name + "_approve_button_drawn", qa_frames.drawn(button[..., :3]), qa_frames.content_share(button[..., :3]))
    HOSTED.update(card=(to_window(card["rect"]), image), call=call.id)


def answered(name, answers, ask, read):
    """The worker's outcome of the person's answer, then the card gone from the chat and from its region on screen."""
    found = {row.get("name"): row for row in answers[read].get("objects") or ()}
    if name == "typed":
        record = keyed(HOSTED["mirror"].state.get("results") or {}, ask) or {}
        check("approve_ran_it_in_the_worker", answers[ask].get("success") is True, answers[ask])
        check("approve_journal_row", record.get("phase") == "completed"
              and (record.get("result") or {}).get("success") is True, record)
        check("approve_changed_the_part", "QA Box" in found, answers[read])
        check("lead_scene_unchanged", "QA Box" not in bpy.data.objects)
        HOSTED["box"] = found["QA Box"].get("location")
    elif name == "script":
        check("decline_runs_nothing", answers[ask].get("error_code") == "DECLINED", answers[ask])
        check("declined_left_no_object", "QA Declined" not in found, answers[read])
    else:
        row = HOSTED["row"]
        check("stale_refusal_row_says_nothing_ran", "WRK-CHANGED" in row["title"] and "Nothing ran" in row["title"],
              row["title"])
        REPORT["hosted_cards"] = {"refusal_row": row["title"]}
        since = qa_frames.redraw(parts()["window"])   # First: each draw replaces DRAWN's lists.
        now = qa_frames.drawn_in(DRAWN["notices"], row["title"], since, "title")
        check("stale_refusal_row_pixels_drawn", now and qa_frames.drawn(shot(
            "hosted-stale-refused.png", to_window(now["rect"]))[..., :3]), now or DRAWN["notices"][-3:])
        check("stale_approval_refused_by_the_worker", answers[ask].get("error_code") == "STALE_REVISION", answers[ask])
        check("stale_approval_ran_nothing", (found.get("QA Box") or {}).get("location") == HOSTED["box"], found)
    rect, before = HOSTED.pop("card")
    after = shot("hosted-%s-after.png" % name, rect)
    check(name + "_card_cleared", keyed(HOSTED["mirror"].pending, ask) is None
          and not any(value == HOSTED["call"] for _action, value in actions()), actions())
    same = before.shape == after.shape and float(abs(before.astype(int) - after.astype(int)).mean()) < 2.0
    check(name + "_card_gone_from_the_window", not same)


def change_card(call):
    """The re-sent card a changed call would send (new digest, new arguments), through the mirror's own receive."""
    from angelo_agent import worker_mirror_events
    frame = copy.deepcopy(next(f for f in reversed(HOSTED["frames"]) if any(
        e.get("id") == call.id and e.get("needs_person") for e in f.get("calls") or ())))
    entry = next(e for e in frame["calls"] if e["id"] == call.id)
    entry["digest"] = hashlib.sha256((entry["digest"] + "|changed").encode()).hexdigest()
    entry["summary"]["arguments"] = dict(entry["summary"]["arguments"], location=CHANGED)
    HOSTED.update(stale=entry["digest"], since=time.monotonic())
    HOSTED["actions"].append({"phase": REPORT["phase"], "changed_card": call.id, "location": CHANGED})
    HOSTED["mirror"].receive(worker_mirror_events.PENDING, frame)


def hosted_tick(phase):
    from angelo_agent import director, parallel_sessions, session_rail_draw, ui, worker_consent_lead
    mirror, chat, window = HOSTED["mirror"], HOSTED["chat"], parts()["window"]
    stage, _, name = phase.partition(":")
    index = next((i for i, case in enumerate(CASES) if case[0] == name), None)
    if phase == "hosted_start":
        reason = skip_reason()
        if reason:
            REPORT["hosted_cards"] = {"skipped": reason}
            return finish()
        check("hosted_runs_in_director_mode", director.is_director(window))
        probe_draws()
        start_worker()
        REPORT["phase"] = "hosted_tile"
    elif phase == "hosted_tile":
        placed = session_rail_draw.last.get(window.as_pointer())
        tile = until(lambda: next((t for t in (placed[1].tiles if placed else ()) if t.session == chat.session
                                   and t.rect[2] > 0), None), "the worker's tile on the session rail")
        if tile is None:
            return 0.2
        check("worker_tile_on_the_rail", chat.session in parallel_sessions.sessions())
        region = next(r for a in window.screen.areas if a.as_pointer() == placed[0] for r in a.regions
                      if r.type == 'WINDOW')
        HOSTED["actions"].append({"phase": phase, "click": "tile", "session": chat.session})
        qa_input.press(window, region.x + tile.rect[0] + tile.rect[2] / 2, region.y + tile.rect[1] + tile.rect[3] / 2)
        REPORT["phase"] = "hosted_shown"
    elif phase == "hosted_shown":
        seen = REPORT["details"]["hosted_shown"] = {
            "shown": ui.get_controller() is mirror, "active": ui._sessions.active == chat.session,
            "connected": ui.connection_available(), "composer": not view_state().get("composer_hidden")}
        if not until(lambda: all(seen.values()), "the worker chat shown in the chat, ready to type in"):
            return 0.2
        check("tile_click_shows_the_worker_chat", worker_consent_lead.hosted(mirror))
        say(CASES[0])
    elif stage == "card":
        _name, _text, ask, read, button, words = CASES[index]
        found = until(lambda: drawn_card(ask[0]), "the %s card drawn" % name, 45)
        if not found:
            return 0.2
        REPORT["timings"].setdefault("first_card_s", round(time.monotonic() - STARTED, 2))
        check(name + "_asks_in_ask", found[0].name == ask[1])
        card_checks(name, *found, words)
        if name == "stale":
            change_card(found[0])
            REPORT["phase"] = "redrawn:" + name
        else:
            press(button, found[0].id)
            REPORT["phase"] = "answered:" + name
    elif stage == "redrawn":
        found = until(lambda: drawn_card(CASES[index][2][0], HOSTED["stale"], HOSTED["since"]), "the changed card")
        if not found:
            return 0.2
        check("changed_card_redrawn_with_its_new_digest", found[0].arguments.get("location") == CHANGED)
        shot("hosted-stale-changed-card.png", to_window(found[1]["rect"]))
        HOSTED["since"] = time.monotonic()
        press("approve", found[0].id)
        REPORT["phase"] = "answered:" + name
    elif stage == "answered":
        ask, read = CASES[index][2][0], CASES[index][3]
        if name == "stale":   # The refusal row, drawn after the click.
            HOSTED["row"] = next((n for n in DRAWN["notices"] if n["at"] > HOSTED["since"] and "WRK-" in n["title"]),
                                 None)
        answers = until(lambda: not mirror.busy and (name != "stale" or HOSTED["row"]) and results(ask, read),
                        "the %s turn answered" % name)
        if not answers:
            return 0.2
        answered(name, answers, ask, read)
        if index + 1 == len(CASES):
            return finish()
        say(CASES[index + 1])
    return 0.2


def stop_worker(error):
    if HOSTED["mirror"] is None:
        return
    from angelo_agent import ui, worker_routing
    try:
        if error:
            shot("hosted-failure.png")
        REPORT.setdefault("hosted_cards", {})["model_log"] = HOSTED["chat"].model_log()
        (OUT / "hosted-cards-scenario.json").write_text(json.dumps(
            {"turns": TURNS, "actions": HOSTED["actions"], "session": HOSTED["chat"].session}, indent=2))
        worker_routing.unbind(HOSTED["mirror"], worker_routing.END_CLOSING)
    finally:
        ui._controllers.pop(HOSTED["chat"].session, None)
        HOSTED["mirror"] = None


def finish(error=None):
    try:
        stop_worker(error)
    except Exception:   # noqa: BLE001 - the report says what failed; the worker ends with Angelo.
        error = (error or "") + "\nstopping the worker: " + traceback.format_exc()
    REPORT.update(success=error is None and bool(REPORT["checks"]) and all(REPORT["checks"].values()),
                  phase="complete" if error is None else "failed", elapsed_seconds=round(time.monotonic() - STARTED, 2))
    if error:
        REPORT["error"] = error
    (OUT / "trusted-consent-gui.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_TRUSTED_CONSENT_GUI", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    from angelo_agent import console, director, ui
    try:
        assert time.monotonic() - STARTED < LIMIT, "The consent fixture timed out in " + REPORT["phase"]
        phase = REPORT["phase"]
        if phase == "boot":
            if not ui.main_window() or not any(console.is_chat(a) for a in ui.main_window().screen.areas) \
                    or not qa_boot.started():
                return 0.2
            REPORT["checks"]["starts_in_director_mode"] = director.is_director(ui.main_window())
            controller = ui.get_controller()
            controller.set_document(ui.document_key())
            REPORT["checks"]["default_off"] = not controller.busy and not controller.trusted_scripts
            REPORT["checks"]["direct_execute_before_dialog_refused"] = (execute_direct() == {'CANCELLED'}
                                                                         and not controller.trusted_scripts)
            assert invoke() == {'RUNNING_MODAL'}
            REPORT["phase"] = "cancel_dialog"
        elif phase == "cancel_dialog":
            capture("trusted-consent-cancel-warning.png")
            key('ESC')
            REPORT["phase"] = "after_cancel"
        elif phase == "after_cancel":
            controller = ui.get_controller()
            REPORT["checks"]["escape_leaves_consent_off"] = not controller.trusted_scripts
            REPORT["checks"]["direct_execute_after_cancel_refused"] = (execute_direct() == {'CANCELLED'}
                                                                        and not controller.trusted_scripts)
            assert invoke() == {'RUNNING_MODAL'}
            REPORT["phase"] = "confirm_dialog"
        elif phase == "confirm_dialog":
            capture("trusted-consent-confirm-warning.png")
            key('RET')
            REPORT["phase"] = "after_confirm"
        elif phase == "after_confirm":
            controller = ui.get_controller()
            REPORT["checks"]["enter_grants_consent"] = controller.trusted_scripts
            assert controller.trusted_scripts, "Enter in the dialog did not grant consent"
            capture("trusted-consent-enabled.png")
            assert invoke() == {'FINISHED'}
            REPORT["checks"]["invoking_again_revokes"] = not controller.trusted_scripts
            REPORT["checks"]["direct_execute_after_revoke_refused"] = (execute_direct() == {'CANCELLED'}
                                                                        and not controller.trusted_scripts)
            controller.new_chat(ui.document_key())
            REPORT["checks"]["new_chat_starts_without_consent"] = not ui.get_controller().trusted_scripts
            REPORT["phase"] = "hosted_start"
        else:
            return hosted_tick(phase)
        return 0.5
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.0)
