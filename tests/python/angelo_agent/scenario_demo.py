# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Demo recording of the Angelo side of a scenario run (``--demo-record``).

The recording feeds the welcome screen's demo tiles and the website's demo
pages. It has two halves:

* ``Recorder``, inside the trial's Angelo process: it first switches the
  trial window to Director Mode (the app's default for a new person: the 3D
  viewport and the chat showing the trial's conversation) and the first
  message waits for that layout. It points the viewport at the scenario's
  demo view in Material Preview without overlays, and switches the preview to
  the scene's own lights and world once the scene has a Sun. Then a timer
  grabs the window's own pixels every ``INTERVAL_S`` with
  ``screen.screenshot`` (Blender reads its window framebuffer, so no
  operating-system screen-recording permission is involved and other windows
  covering it do not matter). ``frames.json`` lists every frame with its time
  and the turn markers.
* ``encode``, on the host after the run: stock Blender in the background
  (``scenario_demo_job.py``) turns the frames into a 1920 x 1080 H.264 MP4
  (letterboxed when the window is not 16:9) and ``poster-1080p.png``, the last
  frame, which shows the finished scene.

Recording costs main-thread time in the trial (each grab redraws the window
and writes a PNG; ``frames.json`` reports the measured cost), so a recorded
run's timings are not comparable with unrecorded runs. The bench marks such
runs in its plan and report.
"""

import json
import time
from pathlib import Path

INTERVAL_S = 0.5
LAYOUT_TIMEOUT_S = 20.0
# After the last turn the recording continues until the viewport has compiled its shaders (the
# last turn may have changed the render settings), at least TAIL_S and at most TAIL_MAX_S.
TAIL_S, TAIL_MAX_S = 2.0, 20.0
VIDEO_SIZE = (1920, 1080)
VIDEO_FPS = 30
# Window size of a recorded trial, in Blender's window-geometry units: 16:9 and small enough for a
# laptop screen; a Retina display doubles it to 2880 x 1620 pixels, scaled down to 1080p.
WINDOW_GEOMETRY = ("0", "0", "1440", "810")
# Per scenario: the viewport view of the recording (else the scenario's hero camera).
DEMO_VIEWS = {"bridge": {"location": (-13.0, -24.0, 7.0), "target": (1.0, 0.0, -0.8), "lens": 28.0},
              # The island is recorded through its own hero camera: the stages read as one fixed shot.
              "island": {"location": (-11.5, -128.0, 18.0), "target": (-9.0, 0.0, 2.5), "lens": 50.0}}


def demo_view(name):
    import scenario_specs
    return DEMO_VIEWS.get(name) or scenario_specs.module(name).CAMERAS["hero"]


class Recorder:
    def __init__(self, folder, scenario, interval=INTERVAL_S):
        self.folder, self.scenario, self.interval = Path(folder), scenario, interval
        self.frames, self.markers, self.capture_ms = [], [], []
        self.window = self.started = self.bpy = self.framed = self.deadline = self.tail = None
        self.stopped, self.scene_lights, self.settled, self.errors = False, False, False, []
        self.layout = "unchanged"

    # ------------------------------------------------------------ in the app

    def start(self, bpy, window):
        self.bpy, self.window = bpy, window
        (self.folder / "frames").mkdir(parents=True, exist_ok=True)
        self.started, self.deadline = time.monotonic(), time.monotonic() + LAYOUT_TIMEOUT_S
        try:
            from angelo_agent import director
            director.request_mode(window, "DIRECTOR", persist=False)
            self.layout = "director"
        except (ImportError, RuntimeError, ValueError) as error:
            self.errors.append("Director Mode: %s" % error)
        bpy.app.timers.register(self.tick, first_interval=0.1, persistent=True)

    def ready(self):
        """Whether the recorded layout has settled (or the wait gave up), so the first message may go out."""
        if self.started is None or self.settled:
            return True
        from angelo_agent import director, ui
        busy = director._timer is not None or ui._layout_timer is not None
        if busy and time.monotonic() < self.deadline:
            return False
        if busy or director.last_error:
            self.errors.append("layout: %s" % (director.last_error or "did not settle in %g s" % LAYOUT_TIMEOUT_S))
        self.settled = True
        self.mark("layout ready")
        return True

    def drained(self):
        """After the last turn: whether the recording has its finished-scene tail and may stop."""
        if self.started is None or self.stopped:
            return True
        now = time.monotonic()
        if self.tail is None:
            self.tail = now
            self.mark("tail")
        compiling = self.bpy.app.is_job_running("SHADER_COMPILATION")
        return now - self.tail >= TAIL_MAX_S or (now - self.tail >= TAIL_S and not compiling)

    def view_area(self):
        area = next((a for a in self.window.screen.areas if a.type == "VIEW_3D"), None)
        region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
        return area, region

    def frame_view(self, area):
        """The 3D viewport looks from the demo view, in Material Preview without overlays."""
        import scenario_build_kit as kit
        from mathutils import Vector
        self.framed = area.as_pointer()
        spec = demo_view(self.scenario)
        space = area.spaces.active
        view = space.region_3d
        view.view_perspective = "PERSP"
        view.view_location = spec["target"]
        view.view_rotation = kit.look_at_rotation(spec["location"], spec["target"]).to_quaternion()
        view.view_distance = (Vector(spec["location"]) - Vector(spec["target"])).length
        space.lens, space.clip_end = spec.get("lens", 35.0), 1000.0
        space.shading.type = "MATERIAL"
        space.shading.use_scene_lights = space.shading.use_scene_world = self.scene_lights
        space.overlay.show_overlays = False
        space.show_gizmo = False

    def follow_lighting(self):
        """Once the scene has a Sun, the preview shows the scene's own lights and world (golden hour)."""
        if self.scene_lights:
            return
        if any(obj.type == "LIGHT" and obj.data.type == "SUN" for obj in self.window.scene.objects):
            area, _region = self.view_area()
            if area is not None:
                shading = area.spaces.active.shading
                shading.use_scene_lights, shading.use_scene_world = True, True
                self.scene_lights = True
                self.mark("scene lighting")

    def mark(self, label):
        if self.started is not None:
            self.markers.append({"frame": len(self.frames), "t_s": round(time.monotonic() - self.started, 3),
                                 "label": label})

    def tick(self):
        if self.stopped:
            return None
        if not self.ready():
            return 0.1
        self.capture()
        return self.interval

    def capture(self):
        bpy = self.bpy
        try:
            area, region = self.view_area()
            if area is not None and area.as_pointer() != self.framed:
                self.frame_view(area)   # The first grab, or a layout change replaced the viewport.
            self.follow_lighting()
            path = self.folder / "frames" / ("frame-%06d.png" % (len(self.frames) + 1))
            started = time.perf_counter()
            with bpy.context.temp_override(window=self.window, area=area, region=region):
                bpy.ops.screen.screenshot(filepath=str(path), check_existing=False)
            self.capture_ms.append((time.perf_counter() - started) * 1000)
            if path.is_file():
                # Relative to frames.json, so the folder can be moved or archived as it is.
                self.frames.append({"file": "frames/" + path.name, "t_s": round(time.monotonic() - self.started, 3)})
        except Exception as error:  # A failed grab never stops the trial; it is reported.
            if len(self.errors) < 20:
                self.errors.append("%s: %s" % (type(error).__name__, str(error)[:200]))

    def stop(self, bpy):
        """One last frame (the finished turn), then ``frames.json``; returns its summary."""
        self.stopped = True
        if self.started is None:
            return {"frames": 0, "errors": self.errors or ["the recorder never started"]}
        self.capture()
        ordered = sorted(self.capture_ms)
        summary = {"scenario": self.scenario, "interval_s": self.interval, "frames": len(self.frames),
                   "layout": self.layout,
                   "duration_s": round(time.monotonic() - self.started, 3),
                   "capture_ms": {"p50": round(ordered[len(ordered) // 2], 2) if ordered else None,
                                  "max": round(ordered[-1], 2) if ordered else None,
                                  "total": round(sum(ordered), 1)},
                   "errors": self.errors, "folder": str(self.folder)}
        (self.folder / "frames.json").write_text(json.dumps(dict(summary, frames_index=self.frames,
                                                                 markers=self.markers), indent=2))
        return summary


# ------------------------------------------------------------ on the host

def encode(frames_folder, out_folder, name, blender=None, speed=None, fps=VIDEO_FPS):
    """``{"video", "poster", ...}`` for a recorded trial, encoded in stock Blender; reused when present.

    ``speed``: playback speed against real time. By default every grabbed frame
    is one video frame (a time-lapse at ``fps * interval`` times real time).
    """
    import scenario_references as references
    frames_folder, out_folder = Path(frames_folder), Path(out_folder)
    index = json.loads((frames_folder / "frames.json").read_text())
    if not index.get("frames_index"):
        return {"error": "no frames were recorded", "errors": index.get("errors")}
    hold = 1 if not speed else max(1, round(fps * index["interval_s"] / speed))
    video, poster = out_folder / ("%s-angelo-1080p.mp4" % name), out_folder / "poster-1080p.png"
    result = {"video": str(video), "poster": str(poster), "fps": fps, "hold": hold,
              "speed": round(fps * index["interval_s"] / hold, 2), "frames": len(index["frames_index"]),
              "size": list(VIDEO_SIZE)}
    if video.is_file() and poster.is_file():
        return result
    out_folder.mkdir(parents=True, exist_ok=True)
    code = references.run_job(blender or references.STOCK_BLENDER,
                              ["--frames", str(frames_folder), "--video", str(video), "--poster", str(poster),
                               "--fps", str(fps), "--hold", str(hold)],
                              out_folder / ".scratch", out_folder / "encode.log",
                              script=Path(__file__).resolve().parent / "scenario_demo_job.py")
    if code != 0 or not video.is_file():
        return {"error": "encoding failed (%s); see %s" % (code, out_folder / "encode.log")}
    import shutil
    shutil.rmtree(out_folder / ".scratch", ignore_errors=True)
    return result
