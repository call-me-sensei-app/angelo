# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The background-job card, its approval and the status-bar item in the running app.

Real chat canvas and real synthetic input (``--enable-event-simulate``): the
approval for ``angelo_job_start`` shows the host's facts; a running card shows
progress, frames and an ETA; its buttons have hover, pressed, keyboard focus
(Tab from an empty composer, Enter, Escape) and disabled looks; clicking Cancel
stops the job; a finished card's Open opens the published output; the endings
explain themselves; a finished job adds a host-only notice row; the status bar
shows the running job with Cancel, then the finished one with Open.

Jobs are driven by stand-in runners inside the real ``JobManager`` (its timer,
redraws and notices), so every state can be held for a screenshot; real
Blender jobs are covered by ``test_blender_long_jobs.py``. No provider,
account or network. Look at every PNG: ``job-card.json`` lists them.

In Arabic (full build) the card mirrors with the rest of the canvas and the
pointer at a button's drawn place reaches it; Japanese then shows it translated
left to right, and the suite returns to English.

Run through ``run_native_suites.py --gui --include test_blender_job_card``;
check ``job-card.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import time
import traceback
from array import array
from pathlib import Path

import addon_utils
import bpy

assert "--enable-event-simulate" in sys.argv and not bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-job-card-qa"))
OUT.mkdir(parents=True, exist_ok=True)
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
ui.connection_available = lambda: True
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

from angelo_agent import chat_view, console, job_chat, job_host, job_ui  # noqa: E402
from angelo_agent.approvals import approval_message, review_arguments  # noqa: E402
from angelo_agent.controller import ToolCall  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

REPORT = OUT / "job-card.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [],
         "fixture": "Stand-in job runners in the real manager; real chat canvas and events; no provider"}
ctx = {"opened": []}
started = time.monotonic()
job_ui.open_output = lambda record: ctx["opened"].append(record["detail"].get("output_path"))


class HeldRunner:
    """A job whose progress the suite sets; it ends when ``outcome`` is set."""

    def __init__(self, facts, published=None):
        self.identity, self.facts, self.published, self.staging = None, facts, published, None
        self.state, self.outcome, self.stops = {"progress": 0.0}, None, 0

    locks_interface = False

    def describe(self):
        return dict(self.facts)

    def start(self):
        pass

    def poll(self):
        return None if self.outcome else dict(self.state)

    def stop(self):
        self.stops += 1  # Ends when the suite sets ``outcome``, so the stopping state can be seen.

    def finish(self, stopped):
        return self.outcome

    def abandon(self):
        pass


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view():
    return chat_view._views.get(chat()[0].as_pointer(), {})


def check(name, value):
    state["checks"][name] = bool(value)
    if not value:
        print("ANGELO_JOB_CARD check failed:", name, flush=True)


def hit(action, value):
    return next((rect for rect, a, v in view().get("hits", []) if a == action and v == value), None)


def point(rect):
    _, region = chat()
    return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)


def simulate(kind, value, x, y, **keys):
    window().event_simulate(type=kind, value=value, x=x, y=y, **keys)


def redraw():
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)


def capture(name, whole_window=False):
    """A screenshot of the chat area, so the card fills the picture; the status bar needs the whole window."""
    redraw()
    if whole_window:
        with bpy.context.temp_override(window=window()):
            bpy.ops.screen.screenshot(filepath=str(OUT / name))
    else:
        with bpy.context.temp_override(window=window(), area=chat()[0]):
            bpy.ops.screen.screenshot_area(filepath=str(OUT / name))
    state["screenshots"].append(name)
    return OUT / name


def mean_color(path, rect):
    """Average RGB of a region-relative rectangle in a chat-area screenshot."""
    area, region = chat()
    image = bpy.data.images.load(str(path))
    try:
        width, height = image.size
        values = array('f', [0.0]) * (width * height * 4)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    left, bottom = region.x - area.x + int(rect[0]) + 6, region.y - area.y + int(rect[1]) + 4
    samples = [(x, y) for x in range(left, left + 12) for y in range(bottom, bottom + 4)]
    total = [sum(values[(y * width + x) * 4 + c] for x, y in samples) / len(samples) for c in range(3)]
    return tuple(round(v, 3) for v in total)


def bottom_strip(path, rows=30):
    """The status bar's pixels in a window screenshot (its bottom rows)."""
    image = bpy.data.images.load(str(path))
    try:
        values = array('f', [0.0]) * len(image.pixels)
        image.pixels.foreach_get(values)
        return values[:image.size[0] * rows * 4].tobytes()  # Rows run bottom-up.
    finally:
        bpy.data.images.remove(image)


def add_row(record, arguments):
    controller = ui.get_controller()
    controller.state["messages"].append({"role": "activity", "call_id": "call-" + record["id"], "text": "",
                                         "tool": "angelo_job_start", "arguments": arguments, "status": "completed",
                                         "result": {"job": record["id"], "status": record["status"]}})
    controller.version += 1


def submit(arguments, runner):
    from angelo_agent.job_spec import validate_job
    request = validate_job("angelo_job_start", arguments)
    record = job_ui.manager().submit(ui.get_controller().state["id"], "call", request, runner)
    add_row(record, arguments)
    return record


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_JOB_CARD", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def step():
    assert time.monotonic() - started < 180, "Job-card fixture timed out in " + state["phase"]
    phase = state["phase"]
    if phase == "boot":
        if not qa_boot.started():   # Never dock or switch while Angelo's start-up still moves the layout.
            return .1
        bpy.ops.angelo.open_chat(side='RIGHT')
        state["phase"] = "layout"
        return 0.5
    if not qa_boot.started():
        return 0.05
    controller = ui.get_controller()
    if phase == "layout":
        controller.refresh_models = lambda *args, **kwargs: None
        controller.state["messages"] = [{"role": "user", "text": "Render the walk cycle, frames 1 to 24."}]
        scene = bpy.context.scene
        scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1280, 720, 100
        arguments = {"kind": "RENDER_ANIMATION", "output": "renders/walk", "frame_start": 1, "frame_end": 24}
        call = ToolCall("approval-job", "angelo_job_start", arguments, ui.document_key(), approval=True)
        call.precondition = job_host.precondition(controller, window(), "angelo_job_start", arguments)
        controller.pending[call.id] = call
        message = approval_message("angelo_job_start", review_arguments(call))
        check("approval_names_frames_resolution_output_and_budget",
              message["title"] == "Render frames 1–24" and "1280×720 pixels" in message["body"]
              and "Writes renders/walk" in message["body"] and "Time budget: 4 h 0 min" in message["body"])
        chat()[0].tag_redraw()
        state["phase"] = "approval"
        return 0.3
    if phase == "approval":
        capture("job-card-0-approval.png")
        check("approval_card_has_approve_and_decline", hit("approve", "approval-job") and hit("decline", "approval-job"))
        controller.pending.clear()
        # A stereo scene whose compositor has a File Output node: the approval lists every file it will write.
        scene = bpy.context.scene
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import production_jobs_fixtures as pfx
        pfx.stereo_scene(scene)
        pfx.compositor(scene, OUT / "the persons own folder")
        arguments = {"kind": "RENDER_STILL", "output": "renders/stereo"}
        call = ToolCall("approval-outputs", "angelo_job_start", arguments, ui.document_key(), approval=True)
        call.precondition = job_host.precondition(controller, window(), "angelo_job_start", arguments)
        controller.pending[call.id] = call
        message = approval_message("angelo_job_start", review_arguments(call))
        state["approval_with_outputs"] = message["body"]
        check("approval_lists_each_view_and_file_output_item",
              "frame_0001_L.png · View left" in message["body"]
              and "Passes/Mask0001_R.png · File Output Passes: Mask · View right" in message["body"]
              and "Writes renders/stereo in this chat's file workspace" in message["body"])
        controller.version += 1
        chat()[0].tag_redraw()
        state["phase"] = "approval_outputs"
        return 0.3
    if phase == "approval_outputs":
        capture("job-card-0b-approval-outputs.png")
        check("approval_with_outputs_keeps_its_buttons", hit("approve", "approval-outputs") and hit("decline", "approval-outputs"))
        controller.pending.clear()
        scene = bpy.context.scene
        scene.render.use_multiview, scene.compositing_node_group = False, None
        ctx["running"] = HeldRunner({"engine": "CYCLES", "resolution": [1280, 720], "samples": 64, "frames": [1, 24]})
        ctx["record"] = submit({"kind": "RENDER_ANIMATION", "output": "renders/walk", "frame_start": 1, "frame_end": 24},
                               ctx["running"])
        ctx["queued_runner"] = HeldRunner({"engine": "CYCLES", "resolution": [1280, 720], "samples": 64, "frames": [12, 12]})
        ctx["queued"] = submit({"kind": "RENDER_STILL", "output": "renders/hero.png", "frame_start": 12, "queue": True},
                               ctx["queued_runner"])
        ctx["record"]["started"] = time.time() - 120  # Two minutes in: an ETA is meaningful.
        # The device its Cycles session used, as render_device.shown reports it (job_manager._device_used).
        ctx["record"]["detail"]["device_shown"] = {"gpu": True, "backend": "METAL", "names": ["Apple M4 Pro (GPU - 20 cores)"],
                                                   "cpu": False}
        ctx["running"].state = {"progress": 0.4583, "frames_done": 11, "frames_total": 24}
        state["phase"], ctx["polled_by"] = "running", time.monotonic() + 10
        return 0.6
    if phase == "running":
        record = ctx["record"]
        # A timer's next call is counted from when its callback began: after a slow screenshot this phase can
        # follow at once, before the job timer (0.25 s) has polled the runner's new progress. Wait for that poll.
        if record["eta_s"] is None and time.monotonic() < ctx["polled_by"]:
            return 0.1
        check("running_card_shows_progress_frames_and_eta", record["eta_s"] is not None
              and "11 of 24 frames · 45% · about" in job_chat.progress_text(record))
        check("queued_card_waits", ctx["queued"]["status"] == "queued")
        check("running_card_names_its_device", job_chat.device_text(record)[0]
              == "On the GPU: Apple M4 Pro (GPU - 20 cores) (Metal)")
        plain = capture("job-card-1-running-and-queued.png")
        cancel = hit("job_cancel", "j1")
        check("running_card_has_cancel_and_details", cancel is not None and hit("job_details", "j1") is not None)
        ctx["plain_color"] = mean_color(plain, cancel)
        simulate('MOUSEMOVE', 'NOTHING', *point(cancel))
        state["phase"] = "hover"
        return 0.3
    if phase == "hover":
        check("pointer_over_cancel_is_hover", view().get("hover") == ("job_cancel", "j1"))
        ctx["record"]["detail"]["device_shown"] = {"gpu": False, "reason": "no_gpu"}   # A CPU fallback says why.
        check("cpu_fallback_says_why_as_a_warning", job_chat.device_text(ctx["record"])
              == ("On the CPU: no GPU that Cycles supports was found", job_chat.STATE_COLORS["stopping"]))
        hovered = capture("job-card-2-cancel-hover.png")
        ctx["hover_color"] = mean_color(hovered, hit("job_cancel", "j1"))
        check("hover_looks_different", ctx["hover_color"] != ctx["plain_color"])
        simulate('LEFTMOUSE', 'PRESS', *point(hit("job_cancel", "j1")))
        state["phase"] = "pressed"
        return 0.3
    if phase == "pressed":
        check("held_press_is_pressed", view().get("pressed") == ("job_cancel", "j1"))
        pressed = capture("job-card-3-cancel-pressed.png")
        check("pressed_looks_different", mean_color(pressed, hit("job_cancel", "j1")) not in {ctx["hover_color"], ctx["plain_color"]})
        simulate('LEFTMOUSE', 'RELEASE', *point(hit("job_cancel", "j1")))
        state["phase"] = "stopping"
        return 0.3
    if phase == "stopping":
        record = ctx["record"]
        check("click_cancels_the_job", record["status"] in {"stopping", "cancelled"} and record["reason"] == "user"
              and ctx["running"].stops == 1 and not view().get("pressed"))
        check("stopping_button_is_disabled", record["status"] == "stopping" and hit("job_cancel", "j1") is None
              and hit("disabled", job_chat.STOPPING_REASON) is not None)
        simulate('MOUSEMOVE', 'NOTHING', *point(hit("disabled", job_chat.STOPPING_REASON)))
        state["phase"] = "stopping_reason"
        return 0.3
    if phase == "stopping_reason":
        check("disabled_button_shows_its_reason_on_hover", view().get("hover") == ("disabled", job_chat.STOPPING_REASON)
              and view().get("cursor") == 'STOP')
        capture("job-card-4-stopping-reason.png")
        ctx["running"].outcome = ("cancelled", "", {"files": 3, "kept": "3/24 frames", "frames_total": 24,
                                                    "note": "Kept the frames that finished; each is a whole frame."})
        state["phase"] = "cancelled"
        return 0.5
    if phase == "cancelled":
        record = ctx["record"]
        if record["status"] != "cancelled" or ctx["queued"]["status"] != "running":
            return 0.2
        check("cancel_keeps_finished_frames", record["receipt"]["kept"] == "3/24 frames")
        notices = [m for m in controller.state["messages"] if m.get("notice") and m.get("job") == "j1"]
        check("finished_job_adds_a_host_only_notice", len(notices) == 1
              and notices[0]["text"] == "Job j1 (RENDER_ANIMATION) cancelled · renders/walk"
              and console.display_text(notices[0]).startswith("Job j1 · Rendering an animation: Cancelled"))
        ctx["queued_runner"].state = {"progress": 0.5}
        chat()[0].spaces.active.history[-1].body = ""
        composer = view()["composer"]
        x, y = point(composer)
        simulate('MOUSEMOVE', 'NOTHING', x, y)
        simulate('LEFTMOUSE', 'PRESS', x, y)  # Focus the empty composer, as a person would.
        simulate('LEFTMOUSE', 'RELEASE', x, y)
        simulate('TAB', 'PRESS', x, y)
        simulate('TAB', 'RELEASE', x, y)
        state["phase"] = "focus"
        return 0.3
    if phase == "focus":
        focus = view().get("focus")
        tabs = state.setdefault("tab_order", [])
        tabs.append(focus)
        if focus is not None and focus[0].startswith("job_") and "job_focus_capture" not in state:
            state["job_focus_capture"] = focus
            capture("job-card-5-keyboard-focus.png")
        x, y = point(view()["composer"])
        if focus != ("job_details", "j2") and len(tabs) < 24:
            simulate('TAB', 'PRESS', x, y)  # Reading order over every control; never Enter on Cancel.
            simulate('TAB', 'RELEASE', x, y)
            return 0.3
        check("tab_focuses_job_buttons_and_skips_disabled_ones", "job_focus_capture" in state
              and ("disabled", job_chat.MOVED_REASON) not in tabs)
        check("tab_reaches_the_running_jobs_details", focus == ("job_details", "j2"))
        simulate('RET', 'PRESS', x, y)
        simulate('RET', 'RELEASE', x, y)
        state["phase"] = "details"
        return 0.3
    if phase == "details":
        check("enter_activates_the_focused_button", ("job", "j2") in view().get("expanded", set()))
        capture("job-card-6-details-expanded.png")
        x, y = point(view()["composer"])
        simulate('ESC', 'PRESS', x, y)
        simulate('ESC', 'RELEASE', x, y)
        state["phase"] = "status_running"
        return 0.3
    if phase == "status_running":
        check("escape_returns_to_the_composer", view().get("focus") is None)
        item = job_ui.status_item(job_ui.manager(), time.monotonic())
        check("status_bar_shows_the_running_job_with_cancel", item is not None and item[2] == "cancel" and item[3] == "j2"
              and item[0].startswith("Rendering a still · 50%"))
        capture("job-card-7-statusbar-running.png", whole_window=True)
        published = OUT / "workspace/renders/hero.png"
        published.parent.mkdir(parents=True, exist_ok=True)
        published.write_bytes(b"\x89PNG fixture")
        ctx["queued_runner"].published = published
        ctx["queued_runner"].outcome = ("done", "", {"files": 1})
        state["phase"] = "done"
        return 0.5
    if phase == "done":
        if ctx["queued"]["status"] != "done":
            return 0.2
        item = job_ui.status_item(job_ui.manager(), time.monotonic())
        check("status_bar_offers_open_after_it_ends", item is not None and item[2] == "open" and item[3] == "j2")
        capture("job-card-8-statusbar-done.png", whole_window=True)
        check("status_bar_redraws_when_the_job_ends",
              bottom_strip(OUT / "job-card-7-statusbar-running.png") != bottom_strip(OUT / "job-card-8-statusbar-done.png"))
        controller.state["messages"] = [m for m in controller.state["messages"] if m.get("result", {}).get("job") != "j1"]
        controller.version += 1
        chat()[0].tag_redraw()
        redraw()
        simulate('MOUSEMOVE', 'NOTHING', *point(hit("job_open", "j2")))
        state["phase"] = "open"
        return 0.3
    if phase == "open":
        check("open_is_a_primary_button_with_hover", view().get("hover") == ("job_open", "j2"))
        capture("job-card-9-done-open-hover.png")
        rect = hit("job_open", "j2")
        simulate('LEFTMOUSE', 'PRESS', *point(rect))
        simulate('LEFTMOUSE', 'RELEASE', *point(rect))
        state["phase"] = "opened"
        return 0.3
    if phase == "opened":
        check("open_opens_the_published_output", ctx["opened"] == [str(OUT / "workspace/renders/hero.png")])
        controller.state["messages"] = [controller.state["messages"][0]]
        for identity, (status, reason, receipt) in {
                "j3": ("failed", "Blender ended the render early; see its Info log", {"note": "Nothing was written."}),
                "j4": ("interrupted", "app_closed", {"note": "Angelo closed while this job ran, which stopped it. "
                                                              "Nothing was published."}),
                "j5": ("cancelled", "time_budget", {"files": 7, "kept": "7/240 frames", "frames_total": 240})}.items():
            runner = HeldRunner({"engine": "CYCLES", "resolution": [1920, 1080], "samples": 128, "frames": [1, 240]})
            record = submit({"kind": "RENDER_ANIMATION", "output": "renders/" + identity, "frame_start": 1,
                             "frame_end": 240}, runner)
            runner.outcome = (status, reason, receipt)
            job_ui.manager().poll()
            if status == "interrupted":
                record.update(reason=reason)
        state["phase"] = "endings"
        return 0.5
    if phase == "endings":
        statuses = [job_ui.manager().record(i)["status"] for i in ("j3", "j4", "j5")]
        check("endings_recorded", statuses == ["failed", "interrupted", "cancelled"])
        capture("job-card-10-endings.png")
        texts = {identity: job_chat.progress_text(job_ui.manager().record(identity)) for identity in ("j3", "j4", "j5")}
        check("interrupted_card_explains_instead_of_showing_a_code", "app_closed" not in texts["j4"])
        check("endings_offer_open_only_for_published_files", all(hit("job_open", i) is None for i in ("j3", "j4", "j5"))
              and hit("disabled", job_chat.MOVED_REASON) is not None)
        # A render Angelo closed during, whose 12 finished frames a start kept (job_recovery): the card says how much
        # exists and offers the rest (VFX audit A1).
        controller = ui.get_controller()
        controller.state["messages"] = [controller.state["messages"][0]]
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import job_frame_fixtures
        from angelo_agent import job_recovery
        folder = OUT / "workspace/renders/kept"
        folder.mkdir(parents=True, exist_ok=True)
        for frame in range(1, 13):
            (folder / ("frame_%04d.png" % frame)).write_bytes(job_frame_fixtures.png())
        runner = HeldRunner({"engine": "CYCLES", "resolution": [1920, 1080], "samples": 128, "frames": [1, 120]})
        kept = submit({"kind": "RENDER_ANIMATION", "output": "renders/kept", "frame_start": 1, "frame_end": 120},
                      runner)
        runner.outcome = ("interrupted", "app_closed", {})
        job_ui.manager().poll()
        kept.update(reason="app_closed", receipt={"kept": "12/120 frames", "frames_total": 120, "frames_kept": 12,
                                                   "files": 12, "note": job_recovery.KEPT})
        kept["detail"].update(output_path=str(folder), recovery={
            "root": str(OUT / "workspace"), "output": str(folder), "skip": False, "frames": [1, 120],
            "blend": bpy.data.filepath, "scene": bpy.context.window.scene.name,
            "plan": [{"prefix": "frame_", "suffix": "", "extension": ".png", "main": True, "labels": {}}]})
        ctx["kept"] = kept
        chat()[0].tag_redraw()
        redraw()
        state["phase"] = "kept"
        return 0.5
    if phase == "kept":
        kept = ctx["kept"]
        text = job_chat.progress_text(kept)
        capture("job-card-10b-kept-frames.png")
        check("kept_frames_card_says_how_much_was_rendered", "12" in text and "120" in text)
        from angelo_agent import job_resume
        # The suite's document is unsaved, so the rest is offered disabled, with the reason on hover.
        offered = (hit("job_resume", kept["id"]) if bpy.data.filepath
                   else hit("disabled", job_resume.RESUME_UNSAVED))
        check("kept_frames_card_offers_the_rest_and_open", offered is not None
              and hit("job_open", kept["id"]) is not None)
        runner = HeldRunner({"engine": "CYCLES", "resolution": [1920, 1080], "samples": 128, "frames": [1, 240]})
        ctx["arabic_job"] = submit({"kind": "RENDER_ANIMATION", "output": "renders/arabic", "frame_start": 1,
                                    "frame_end": 240}, runner)
        ctx["arabic_job"]["started"] = time.time() - 60
        runner.state = {"progress": 0.3, "frames_done": 72, "frames_total": 240}
        if not bpy.app.build_options.international:
            state["arabic"] = "WITH_INTERNATIONAL is off in this build: the Arabic layout was not checked"
            finish()
            return None
        view_prefs = bpy.context.preferences.view
        view_prefs.use_translate_interface = True
        view_prefs.language = 'ar_EG'
        state["phase"] = "arabic"
        return 0.8
    if phase == "arabic":
        from angelo_agent.ui_text import is_rtl
        check("arabic_interface_is_right_to_left", is_rtl())
        capture("job-card-11-arabic.png")
        # Mirrored: a button laid out at x is drawn at width - x, and the pointer there must reach it.
        _, region = chat()
        rect = hit("job_cancel", ctx["arabic_job"]["id"])
        x = region.x + region.width - int(rect[0] + rect[2] / 2)
        simulate('MOUSEMOVE', 'NOTHING', x, region.y + int(rect[1] + rect[3] / 2))
        state["phase"] = "arabic_hover"
        return 0.4
    if phase == "arabic_hover":
        check("mirrored_pointer_reaches_the_job_card_button", view().get("hover") == ("job_cancel", ctx["arabic_job"]["id"]))
        capture("job-card-12-arabic-cancel-hover.png")
        simulate('MOUSEMOVE', 'NOTHING', 4, 4)
        bpy.context.preferences.view.language = 'ja_JP'   # A left-to-right language with wide characters.
        state["phase"] = "japanese"
        return 0.6
    if phase == "japanese":
        from angelo_agent.ui_text import is_rtl
        check("japanese_card_is_translated_left_to_right",
              not is_rtl() and job_chat.title(ctx["arabic_job"]) != "Rendering an animation")
        capture("job-card-13-japanese.png")
        bpy.context.preferences.view.language = 'en_US'
        state["phase"] = "english_again"
        return 0.6
    if phase == "english_again":
        from angelo_agent.ui_text import is_rtl
        check("english_again_is_left_to_right", not is_rtl() and job_chat.title(ctx["arabic_job"]) == "Rendering an animation")
        finish()
        return None
    return 0.1


def tick():
    try:
        return step()
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
