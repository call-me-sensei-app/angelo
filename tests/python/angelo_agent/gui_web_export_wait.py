# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replayable GUI scenario: an explicit web export waits for its World's picture and Angelo stays responsive.

doc/angelo/web_export.rst ("The World") has the contract. Run it inside a windowed Angelo whose bundled add-on is
this checkout's, with isolated state folders and the window flags of ``gui_launch.window_flags()``::

    Angelo.app/Contents/MacOS/Angelo --factory-startup --no-window-focus --enable-event-simulate \\
        --python tests/python/angelo_agent/gui_web_export_wait.py

``ANGELO_QA_DIR`` names the output folder. The scene's World is a Sky texture, so its picture is baked in a
background Blender. A main-thread timer ticks every 50 ms throughout and records the longest gap between ticks:
the time the main thread was held. Four runs:

* stalled: the bake's worker is replaced by a thread that never ends. File > Export > Web keeps running as a modal
  operator, nothing is written, the ticks keep coming, and a simulated Esc cancels it (the Info log says so);
* the agent's ``angelo_web_export`` on the same stalled worker returns a pending call that polls at once and,
  stopped, leaves nothing in the workspace;
* real: with Blender's own bake, the person's export finishes by itself with the World's picture in the file;
* real, the agent's: its pending call delivers the file with the picture;
* settling: the live preview of a World the key can't read (a Texture Coordinate's object, so the key holds the
  edit count), in a scene whose objects are animated (the exporter steps through their frames), bakes once, exports
  again when the picture lands, and then stays still: an export's own updates are no edit, so it never starts
  another bake by itself.
"""

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.view.use_save_prompt = False
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-gui-web-export-wait"))
OUT.mkdir(parents=True, exist_ok=True)
TICK = 0.05
state = {"phase": "boot", "checks": {}, "ticks": {}}
ticks = {"last": None, "gap": 0.0, "count": 0}


def record():
    (OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))


def check(name, value, detail=None):
    state["checks"][name] = {"ok": bool(value), "detail": detail}
    print(("PASS " if value else "FAIL ") + name + ("" if value else ": %s" % (detail,)), flush=True)


def tick():
    now = time.perf_counter()
    if ticks["last"] is not None:
        ticks["gap"] = max(ticks["gap"], now - ticks["last"])
    ticks["last"], ticks["count"] = now, ticks["count"] + 1
    return TICK


def measure(label):
    """Start a span of ticks: ``measure(label)()`` ends it and stores its count and longest gap."""
    ticks.update(last=None, gap=0.0, count=0)
    return lambda: state["ticks"].__setitem__(label, {"count": ticks["count"], "longest_gap_ms":
                                                      round(ticks["gap"] * 1000.0, 1)})


def sky_world():
    world = bpy.data.worlds.new("Sky")
    tree = world.node_tree
    background = next(node for node in tree.nodes if node.type == "BACKGROUND")
    sky = tree.nodes.new("ShaderNodeTexSky")
    tree.links.new(sky.outputs[0], background.inputs["Color"])
    bpy.context.scene.world = world
    return sky


def wait_for(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        yield 0.1


def scenario():
    from angelo_agent import web_budget, web_environment, web_export, web_host
    from angelo_agent.files import FileWorkspace
    window = bpy.context.window_manager.windows[0]
    sky = sky_world()
    bpy.app.timers.register(tick, first_interval=TICK)
    real_start = web_environment._start_bake
    release = threading.Event()

    def stalled(job, scene):   # A worker that never finishes, as a hung bake would be, and its scratch folder.
        return (threading.Thread(target=release.wait, name="Angelo stalled bake", daemon=True),
                tempfile.mkdtemp(prefix="angelo-web-world-"))

    # 1. The person's export on a stalled worker: modal, responsive, Esc cancels.
    web_environment._start_bake = stalled
    made, real_pending = [], web_export.pending_export

    def recorded(*args):   # The operator's wait, so the scenario can see Esc end it.
        made.append(real_pending(*args))
        return made[-1]

    web_export.pending_export = recorded
    path = OUT / "stalled.glb"
    done = measure("person_stalled")
    with bpy.context.temp_override(window=window):
        started = bpy.ops.angelo.web_export('EXEC_DEFAULT', filepath=str(path))
    check("person_export_waits_modally", started == {'RUNNING_MODAL'}, started)
    yield 3.0
    done()
    span = state["ticks"]["person_stalled"]
    check("main_thread_free_while_stalled", span["count"] >= 30 and span["longest_gap_ms"] < 500, span)
    check("nothing_written_while_waiting", not path.exists())
    window.event_simulate(type='ESC', value='PRESS')
    window.event_simulate(type='ESC', value='RELEASE')
    yield 0.5
    web_export.pending_export = real_pending
    check("esc_cancels", made and made[-1].cancelled and not path.exists(),
          {"waits": len(made), "cancelled": bool(made) and made[-1].cancelled})

    # 2. The agent's export on the stalled worker: a pending call, stopped with nothing left.
    workspace = Path(tempfile.mkdtemp(prefix="angelo-web-wait-workspace-"))
    controller = SimpleNamespace(files=FileWorkspace(str(workspace)))
    options = {"meshes": "AUTO", "textures": "AUTO", "merge": True, "selection_only": False, "output": "web/sky.glb"}
    sky.sun_elevation += 0.1   # A new World state: a new picture, also stalled.
    done = measure("agent_stalled")
    pending = web_host.execute(controller, window, "angelo_web_export", dict(options))
    polled = time.perf_counter()
    answers = [pending.poll() for _ in range(20)] if hasattr(pending, "poll") else [pending]
    polled = (time.perf_counter() - polled) * 1000.0
    yield 1.0
    done()
    check("agent_export_is_pending", hasattr(pending, "poll") and answers == [None] * 20,
          {"type": type(pending).__name__, "poll_ms_20": round(polled, 1)})
    pending.cancel()
    left = [str(item.relative_to(workspace)) for item in workspace.rglob("*") if item.is_file()]
    check("stopped_agent_export_leaves_nothing", left == [] and pending.stopped_result["error_code"] == "CANCELLED",
          left)
    release.set()
    web_environment._start_bake = real_start
    web_environment.stop()   # Forget the stalled jobs: the next exports bake for real.

    # 3. The person's export with Blender's own bake: it finishes by itself, with the picture.
    sky.sun_elevation += 0.1
    path = OUT / "sky.glb"
    done = measure("person_real")
    with bpy.context.temp_override(window=window):
        started = bpy.ops.angelo.web_export('EXEC_DEFAULT', filepath=str(path))
    yield from wait_for(path.exists, 120)
    yield 0.3
    done()
    budget = web_budget.measure(str(path)) if path.exists() else {}
    check("person_export_finishes_with_the_picture", started == {'RUNNING_MODAL'} and
          budget.get("environment_bytes", 0) > 0, {"started": started, "environment": budget.get("environment_bytes")})
    span = state["ticks"]["person_real"]
    check("main_thread_free_while_baking", span["longest_gap_ms"] < 500, span)

    # 4. The agent's, for real.
    sky.sun_elevation += 0.1
    done = measure("agent_real")
    pending = web_host.execute(controller, window, "angelo_web_export", dict(options))
    result = None
    deadline = time.monotonic() + 120
    while result is None and time.monotonic() < deadline:
        result = pending.poll() if hasattr(pending, "poll") else pending
        yield 0.1
    done()
    written = workspace / result["output"] if result and "output" in result else None
    environment = web_budget.measure(str(written))["environment_bytes"] if written and written.is_file() else 0
    check("agent_export_delivers_the_picture", environment > 0, result and {k: result.get(k) for k in
                                                                            ("output", "error", "waited_ms")})

    # 5. A World the key can't read, under the live preview: one bake, then still.
    from angelo_agent import web_preview
    coordinates = bpy.context.scene.world.node_tree.nodes.new("ShaderNodeTexCoord")
    coordinates.object = mover = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    for frame, height in ((1, 0.0), (24, 2.0)):   # Animated: the exporter samples it, frame by frame.
        mover.location.z = height
        mover.keyframe_insert("location", frame=frame)
    bpy.context.scene.frame_set(1)
    tree = bpy.context.scene.world.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")   # The object's coordinates light the World through a noise.
    tree.links.new(coordinates.outputs["Object"], noise.inputs["Vector"])
    tree.links.new(noise.outputs["Fac"], next(node for node in tree.nodes if node.type == "BACKGROUND")
                   .inputs["Strength"])
    landed = web_environment.landed()
    web_preview.open_page = lambda address: None   # Never the person's browser in a scripted check.
    with bpy.context.temp_override(window=window):
        bpy.ops.angelo.web_preview()
    yield from wait_for(lambda: web_environment.landed() > landed, 120)
    yield 2.0
    settled_exports, settled_landed = web_preview._state["exports"], web_environment.landed()
    yield 8.0
    check("unreadable_world_settles_under_the_preview", web_environment.landed() - landed == 1 and
          web_preview._state["exports"] == settled_exports and web_environment.landed() == settled_landed,
          {"pictures": web_environment.landed() - landed, "exports": web_preview._state["exports"],
           "exports_when_settled": settled_exports})
    with bpy.context.temp_override(window=window):
        bpy.ops.angelo.web_preview(stop=True)
    bpy.app.timers.unregister(tick)
    state["passed"] = all(item["ok"] for item in state["checks"].values())
    state["phase"] = "done"
    record()


def driver():
    try:
        return next(driver.steps)
    except StopIteration:
        bpy.ops.wm.quit_blender()
        return None
    except Exception:
        state["phase"] = "error"
        state["error"] = traceback.format_exc()
        record()
        bpy.ops.wm.quit_blender()
        return None


driver.steps = scenario()
bpy.app.timers.register(driver, first_interval=3.0)
sys.stdout.write("gui_web_export_wait: output in %s\n" % OUT)
