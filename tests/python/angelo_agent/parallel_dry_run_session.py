# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo side of the unbilled parallel-agents dry run, in a disposable GUI process (``parallel_dry_run.py``).

The real trial driver (``eval_live_agent_session.py``) runs unchanged behind a scripted runtime that plays every
chat of the chess test (``scenario_oneshot_chess``) with typed scene programs only:

* the parallel arm's **lead** starts one worker per unique design (the board and six pieces) through
  ``angelo_workers_start`` (the tester approves it), waits with ``angelo_workers_status`` ``wait``, merges the parts
  with ``angelo_workers_merge`` (each piece placed on its squares, Black's a per-placement material variant), adds
  the hero camera and replies;
* each **worker** builds the part its brief names, at the origin, from primitives at the brief's heights;
* the **single** arm's chat builds the same parts in its own scene, the copies with ``batch``.

So the whole path runs in the real app (lanes, the rail, approvals, merge, Undo, save, the end-of-session checks)
and the lead window's main-loop lateness is measured with no model in the way: the mechanics' own cost. Two checks
are a model's own work, which this script does not script in either arm: removing the start-up file's Cube, Light
and Camera ("tidy file") and making the Hero Camera the scene's camera ("... and active"); they fail alike in both
arms, and every other check passes in both.
Nothing starts a provider process, reads a credential or opens a socket.
"""

import math
import os
from pathlib import Path
import runpy
import sys
import threading
import uuid

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use the dry-run launcher's disposable GUI process"
assert os.environ.get("ANGELO_PARALLEL_DRY_RUN") == "1", "Started only by parallel_dry_run.py"
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]

import parallel_kits as chess  # noqa: E402

STARTED = __import__("time").monotonic()   # The profile's clock (profile_main_thread starts its own at the same point).
DESIGNS = ("King", "Queen", "Bishop", "Knight", "Rook", "Pawn")


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def program(operations):
    return {"mode": "APPLY", "read_set": [], "write_set": [], "operations": operations}


def piece_ops(piece, prefix, colour, at=(0.0, 0.0), material=None):
    """A piece from primitives at its brief height, standing at ``at``: a base, a body and a top, in its colour's
    material (``material`` names it: one scene makes each name once; a worker's own file has only its piece)."""
    h, x, y = chess.HEIGHTS[piece], at[0], at[1]
    base = 0.019 if piece == "King" else 0.014 if piece == "Pawn" else 0.016
    top = {"King": ("CUBE", 0.012), "Queen": ("UV_SPHERE", 0.006), "Bishop": ("UV_SPHERE", 0.005),
           "Knight": ("CUBE", 0.016), "Rook": ("CYLINDER", 0.012), "Pawn": ("UV_SPHERE", 0.0075)}[piece]
    body_top = h - (top[1] if top[0] == "CUBE" else 2 * top[1] if top[0] == "UV_SPHERE" else 0.008)
    ops = [op("create_primitive", primitive="CYLINDER", name=prefix + " Base", alias="base", radius=base,
              depth=0.008, location=[x, y, 0.004]),
           op("create_primitive", primitive="CONE", name=prefix + " Body", alias="body", radius=base * 0.7,
              radius_top=base * 0.35, depth=body_top - 0.008, location=[x, y, 0.008 + (body_top - 0.008) / 2])]
    if top[0] == "CUBE":
        ops.append(op("create_primitive", primitive="CUBE", name=prefix + " Top", alias="top", size=top[1],
                      location=[x, y, h - top[1] / 2]))
    elif top[0] == "UV_SPHERE":
        ops.append(op("create_primitive", primitive="UV_SPHERE", name=prefix + " Top", alias="top", radius=top[1],
                      location=[x, y, h - top[1]]))
    else:
        ops.append(op("create_primitive", primitive="CYLINDER", name=prefix + " Top", alias="top", radius=top[1],
                      depth=0.008, location=[x, y, h - 0.004]))
    colour_value = list(chess.IVORY if colour == "White" else chess.EBONY) + [1.0]
    name = material or ("Ivory" if colour == "White" else "Ebony")
    ops.append(op("material_principled", "$base", name=name, inputs={"Base Color": colour_value, "Roughness": 0.3},
                  also=["$body", "$top"]))
    return ops


def board_ops():
    size = 8 * chess.SQUARE
    return [op("create_primitive", primitive="CUBE", name="Chess Board", alias="board", size=1.0,
               location=[0.0, 0.0, -chess.BOARD_THICKNESS / 2], scale=[size, size, chess.BOARD_THICKNESS]),
            op("material_principled", "$board", name="Board Wood", inputs={"Base Color": list(chess.LIGHT_SQUARE) + [1.0]})]


def squares(piece, colour):
    return [chess.square_centre(square) for square, (c, p) in sorted(chess.starting_position().items())
            if c == colour and p == piece]


def camera_ops():
    import scenario_oneshot_chess as test
    return [op("camera_create", name="Hero Camera", location=list(test.HERO["location"]),
               look_at=list(test.HERO["target"]), settings={"lens": test.HERO["lens"]})]


class ScriptedRuntime:
    """Every chat of the dry run (module docstring). It never starts a process or opens a socket."""

    def __init__(self, context, settings):
        self.controller, self.warm = context, False

    def connect(self):
        self.warm = True
        self.controller.emit("native_session", {"id": "parallel-dry-run"})
        return "parallel-dry-run"

    def has_warm_runtime(self):
        return self.warm

    def bind_request(self, context):
        self.controller = context

    def stop(self):
        pass

    def stop_request(self, fence):
        pass

    def close(self):
        pass

    def call(self, context, name, arguments, wait=900):
        done, box = threading.Event(), {}
        context.request_tool("dry-" + uuid.uuid4().hex, name, arguments,
                             lambda result: (box.update(result=result), done.set()))
        if not done.wait(wait):
            raise RuntimeError("Scripted tool call timed out: " + name)
        return box["result"]

    def say(self, context, text):
        context.emit("delta", {"item": "parallel-dry-run", "text": text})
        context.emit("message_complete", {"item": "parallel-dry-run", "text": text})

    def ok(self, context, name, arguments):
        import time
        began = time.monotonic()
        result = self.call(context, name, arguments)
        print("DRYPROF tool %s from %.3f s to %.3f s" % (name, began - STARTED, time.monotonic() - STARTED), flush=True)
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError("%s failed: %s" % (name, str(result)[:400]))
        return result

    def run_request(self, context, native_id, text, observation=None):
        context.record_provider_turn()
        context.record_provider_accepted()
        import eval_warm_up
        if eval_warm_up.PROMPT in text:
            self.say(context, "OK")
            return "completed"
        try:
            from angelo_agent import worker_routing
            if worker_routing.worker_snapshots(context.host):   # A worker's chat, whatever its brief's wording says.
                return self.worker(context, text)
            import eval_parallel
            return self.lead(context) if eval_parallel.ENABLED else self.single(context)
        except RuntimeError as error:
            self.say(context, "The scripted build failed: %s" % error)
            return "failed"

    def worker(self, context, text):
        name = text.split("Part: ", 1)[1].split(".", 1)[0].strip()
        self.ok(context, "angelo_scene_program", program(board_ops() if name == "Board" else
                                                         piece_ops(name, "White " + name, "White")))
        self.say(context, "Built the %s at the origin; it stands %.1f cm tall." % (
            name, 100 * (chess.BOARD_THICKNESS if name == "Board" else chess.HEIGHTS[name])))
        return "completed"

    def lead(self, context):
        parts = [{"name": "Board", "brief": "The board: 8 x 8 squares of 5 cm, its playing surface at z = 0."}]
        parts += [{"name": design, "brief": "One %s, Staunton style, %.1f cm tall, standing at the origin, in ivory."
                   % (design.lower(), 100 * chess.HEIGHTS[design])} for design in DESIGNS]
        started = []
        for group in (parts[:4], parts[4:]):   # Four at a time (within any capable machine's count): then the rest.
            self.ok(context, "angelo_workers_start", {"parts": group})
            status = self.ok(context, "angelo_workers_status", {"wait": True, "timeout_seconds": 600})
            started += [row["worker"] for row in status["workers"] if row.get("name") in {p["name"] for p in group}]
            numbers = {row["name"]: row["worker"] for row in status["workers"]}
            merge = []
            for part in group:
                if part["name"] == "Board":
                    merge.append({"worker": numbers["Board"]})
                    continue
                placements = [{"location": [x, y, 0.0]} for x, y in squares(part["name"], "White")]
                placements += [{"location": [x, y, 0.0], "rotation": [0.0, 0.0, math.pi],
                                "materials": {"Ivory": {"name": "Ebony", "base_color": list(chess.EBONY)}}}
                               for x, y in squares(part["name"], "Black")]
                merge.append({"worker": numbers[part["name"]], "placements": placements})
            self.ok(context, "angelo_workers_merge", {"parts": merge})
        self.ok(context, "angelo_scene_program", program(camera_ops()))
        self.say(context, "The set is built, merged and placed.")
        return "completed"

    def single(self, context):
        self.ok(context, "angelo_scene_program", program(board_ops()))
        for design in DESIGNS:
            for colour in ("White", "Black"):
                spots = squares(design, colour)
                ops = piece_ops(design, "%s %s" % (colour, design), colour, spots[0],
                                material="%s %s" % ("Ivory" if colour == "White" else "Ebony", design))
                if len(spots) > 1:   # Every square, the unit's own first: batch counts the unit as its first copy.
                    ops.append(op("batch", sources=["$base", "$body", "$top"],
                                  at=[[x, y, 0.004] for x, y in spots]))
                self.ok(context, "angelo_scene_program", program(ops))
        self.ok(context, "angelo_scene_program", program(camera_ops()))
        self.say(context, "The set is built and placed.")
        return "completed"


def profile_main_thread(slow=0.015, late=0.033):
    """Where the lead window's main loop stalls: every call of parallel edit's main-thread entry points that takes
    longer than ``slow``, and every poll of the trial's lateness timer that ran later than ``late``, printed with its
    time since this process started ("DRYPROF"), so a stall names its cause. Timing only: nothing else changes."""
    import functools
    import time
    import eval_receipts
    from angelo_agent import (live_view, live_view_draw, parallel_sessions, rail_pictures, rail_redraw,
                              session_rail_draw, worker_dispatch, worker_routing, worker_sessions)
    started_at = STARTED

    def wrap(owner, name):
        original = getattr(owner, name, None)
        if not callable(original):
            return

        @functools.wraps(original)
        def timed(*args, **kwargs):
            began = time.monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                spent = time.monotonic() - began
                if spent > slow:
                    print("DRYPROF call %s.%s at %.3f s took %.1f ms" % (getattr(owner, "__name__", owner), name,
                                                                        began - started_at, spent * 1000), flush=True)
        setattr(owner, name, timed)
    for owner, names in ((worker_sessions, ("_tick", "_adopt", "_merge", "_new_chat", "_send")),
                         (worker_routing, ("bind",)), (ui, ("prepare_controller", "pump_controller_tools")),
                         (parallel_sessions, ("pump", "reload")), (live_view, ("refresh", "attach")),
                         (live_view_draw, ("draw",)), (session_rail_draw, ("draw", "states")),
                         (rail_pictures, ("refresh", "_tick", "draw")), (rail_redraw, ("tick",)),
                         (worker_dispatch.Lane, ("step",))):
        for name in names:
            wrap(owner, name)
    poll = eval_receipts.ProgressWatch._poll

    def timed_poll(self):
        before = self._last_poll
        result = poll(self)
        if before is not None and self._last_poll - before - self.EVERY > late:
            print("DRYPROF late poll at %.3f s by %.1f ms" % (before - started_at,
                                                             (self._last_poll - before - self.EVERY) * 1000), flush=True)
        return result
    eval_receipts.ProgressWatch._poll = timed_poll


from angelo_agent import controller as controller_module, ui  # noqa: E402

profile_main_thread()
controller_module.CodexProvider = controller_module.ClaudeProvider = ScriptedRuntime
ui._authentication.start = lambda *args, **kwargs: None  # No native sign-in status process.
runpy.run_path(str(HERE / "eval_live_agent_session.py"), run_name="parallel_dry_run")
