# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Helpers for the worker-Blender suites (``test_blender_parallel_workers.py``, ``bench_parallel_workers.py``).

``WorkerChat`` is one chat in this (background) Angelo bound to its own worker Blender, driven the way a provider
turn drives a chat: ``Controller.request_tool``, then the UI pump's routing (``worker_routing.pump``) until the call
is answered. That is the lanes (``ANGELO_WORKER_CHAT_HOST=0``); with design A′ the chat runs in its worker and
``hosted_worker_chat.HostedWorkerChat`` drives it through a stand-in model. ``worker_chat`` picks by the switch, and
``WorkerChat`` refuses to start under A′ (its chat is no mirror, so the worker never opens it): a suite that drives
worker chats declares both variants (``native_suite_manifest.WORKER_CHAT_HOST``) and picks its harness by the switch.
``CASES`` are parts built from typed programs only, unrelated to each other on purpose: a chess piece,
a furnished room, a vehicle with repeated wheels and a character with clothing parts.
"""

import math
import time
import uuid

import bpy

from angelo_agent import ui, worker_routing
from angelo_agent.controller import Controller, ToolDispatchBudget, pump_controller_tools


class WorkerChat:
    def __init__(self, state, part, root, approval="AUTO_BOUNDED", environ=None):
        from hosted_worker_chat import hosted
        if hosted():   # Else the worker refuses its chat.open far from here (NotMirrored); say why at once.
            raise AssertionError("WorkerChat drives the lanes (ANGELO_WORKER_CHAT_HOST=0); with the switch unset or 1 "
                                 "use parallel_workers_qa.worker_chat and declare the suite's WORKER_CHAT_HOST variants")
        self.controller = Controller(str(state), session_id=str(uuid.uuid4()))   # A new chat each.
        self.controller.state["options"] = dict(self.controller.state.get("options") or {}, approval=approval)
        self.session = self.controller.state["id"]
        started = time.perf_counter()
        self.executor = worker_routing.start(self.controller, part, binary=bpy.app.binary_path,
                                             script=root / "scripts/angelo/angelo_headless.py", environ=environ)
        self.start_seconds = time.perf_counter() - started
        self.worker = worker_routing._workers[self.session]
        ui._controllers[self.session] = self.controller   # The approve operator finds it as the window's would.
        self.fence = self.controller.open_external_read()
        self.timings = []
        self.pumps = []   # (seconds, the chat's lane was waiting on its worker when the pump began)
        self._count = 0

    def request(self, name, arguments):
        self._count += 1
        answers = []
        call_id = "qa-%d" % self._count
        self.controller.request_tool(call_id, name, arguments, answers.append)
        return self.session + "/" + call_id, answers

    def pump(self):
        """One UI pump of this chat as the window pumps it: its executor and document come from its binding, and an
        Auto edit goes through Angelo's own approve operator (registered by the suite)."""
        from angelo_agent import worker_dispatch
        waiting, started = worker_dispatch.waiting(self.controller), time.perf_counter()
        self.controller.pump_events()

        def apply_mutation(target, call):
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
        pump_controller_tools([self.controller], 0, None, None, apply_mutation, budget=ToolDispatchBudget(seconds=60))
        self.pumps.append((time.perf_counter() - started, waiting))
        if waiting:
            time.sleep(0.001)   # As the UI timer comes back while a lane waits, not a busy spin.

    def call(self, name, arguments, timeout=180.0):
        started = time.perf_counter()
        _key, answers = self.request(name, arguments)
        while not answers and time.perf_counter() - started < timeout:
            self.pump()
        self.timings.append((name, time.perf_counter() - started))
        if not answers:
            raise AssertionError("%s was not answered within %d s" % (name, timeout))
        return answers[0]

    def wait_for_approval(self, key, answers=None, timeout=60.0):
        started = time.perf_counter()
        while time.perf_counter() - started < timeout:
            self.pump()
            call = self.controller.pending.get(key)
            if call is not None and call.approval and not call.executing:
                return call
            if answers:
                raise AssertionError("answered without an approval card: %r (chat document %r, worker %r)" % (
                    answers[0], self.controller.document, self.executor.document_key))
        raise AssertionError("no approval card for " + key)

    def wait(self, answers, timeout=180.0):
        started = time.perf_counter()
        while not answers and time.perf_counter() - started < timeout:
            self.pump()
        if not answers:
            raise AssertionError("not answered within %d s" % timeout)
        return answers[0]

    def new_request(self):
        """The next request, as a person's next message opens one (a refused edit's recovery hold ends with it)."""
        self.controller.close_external_read(self.fence)
        self.fence = self.controller.open_external_read()

    def objects(self):
        return {row["name"]: row for row in self.call("angelo_inspect_scene", {})["objects"]}

    def stop(self):
        try:
            self.controller.close_external_read(self.fence)
        finally:
            worker_routing.unbind(self.controller, worker_routing.END_CLOSING)
            ui._controllers.pop(self.session, None)


def worker_chat(state, part, root, **options):
    """One worker chat as the running switch makes it: ``HostedWorkerChat`` (design A′, ``ANGELO_WORKER_CHAT_HOST``
    unset or ``1``: the chat runs in its worker, played by the stand-in model) or ``WorkerChat`` (``0``: the lanes)."""
    from hosted_worker_chat import HostedWorkerChat, hosted
    return (HostedWorkerChat if hosted() else WorkerChat)(state, part, root, **options)


def no_worker_done_event():
    """A suite that drives its lead by hand, idle between its own steps, gets no worker-done event: Angelo would
    otherwise start the lead's next request itself once its workers end (``worker_resume``, covered by
    ``test_blender_parallel_resume``)."""
    from angelo_agent import worker_resume
    worker_resume.started = lambda *arguments, **keywords: None   # Whatever its signature: no batch opens.


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def program(operations):
    return {"mode": "APPLY", "read_set": [], "write_set": [], "operations": operations}


def primitive(kind, name, alias, **fields):
    return op("create_primitive", primitive=kind, name=name, alias=alias, **fields)


def paint(alias, name, colour, **inputs):
    return op("material_principled", "$" + alias, name=name, inputs=dict({"Base Color": colour}, **inputs))


def chess_knight():
    return [program([
        primitive("CYLINDER", "Knight Base", "base", radius=0.4, depth=0.18, location=[0, 0, 0.09]),
        primitive("CYLINDER", "Knight Neck", "neck", radius=0.22, depth=0.6, location=[0, 0, 0.48]),
        primitive("UV_SPHERE", "Knight Head", "head", radius=0.26, location=[0.08, 0, 0.9], scale=[1.4, 0.8, 1.0]),
        paint("base", "Ebony", [0.05, 0.04, 0.04, 1], Roughness=0.35),
        op("material_assign", "$neck", material="Ebony"),
        op("material_assign", "$head", material="Ebony"),
        op("parent_set", "$neck", parent="$base"), op("parent_set", "$head", parent="$neck"),
        op("modifier_add", "$base", type="BEVEL", name="Soft Edge"),
    ])], {"Knight Base", "Knight Neck", "Knight Head"}


def furnished_room():
    legs = [[x, y, 0.35] for x in (-0.55, 0.55) for y in (-0.3, 0.3)]
    return [program([
        primitive("PLANE", "Floor", "floor", size=6),
        primitive("CUBE", "Back Wall", "wall", size=1, location=[0, 3, 1.4], scale=[6, 0.1, 2.8]),
        paint("floor", "Oak Floor", [0.42, 0.28, 0.16, 1], Roughness=0.6),
        paint("wall", "Plaster", [0.86, 0.84, 0.8, 1]),
    ]), program([
        primitive("CUBE", "Table Top", "top", size=1, location=[0, 0, 0.74], scale=[1.4, 0.8, 0.06]),
        primitive("CYLINDER", "Table Leg", "leg", radius=0.04, depth=0.7, location=legs[0]),
        op("batch", sources=["$leg"], at=legs[1:]),
        primitive("CUBE", "Chair Seat", "seat", size=0.45, location=[0, -0.75, 0.45], scale=[1, 1, 0.12]),
        op("object_duplicate", source="$seat", alias="seat2", linked=True),
        op("transform", "$seat2", location=[0, 0.75, 0.45], rotation=[0, 0, math.pi]),
    ])], {"Floor", "Back Wall", "Table Top", "Table Leg", "Chair Seat"}


def vehicle():
    return [program([
        primitive("CUBE", "Car Body", "body", size=1, location=[0, 0, 0.7], scale=[2.0, 0.9, 0.5]),
        primitive("CYLINDER", "Wheel", "wheel", radius=0.35, depth=0.25, location=[1.25, 0.8, 0.35],
                  rotation=[math.pi / 2, 0, 0]),
        paint("body", "Car Paint", [0.7, 0.05, 0.05, 1], Metallic=0.6),
        paint("wheel", "Tyre", [0.02, 0.02, 0.02, 1], Roughness=0.9),   # Before the copies: they share its data.
        op("batch", sources=["$wheel"], array=[{"step": [-2.5, 0, 0], "count": 2}], mirror="Y"),
    ])], {"Car Body", "Wheel"}


def character():
    return [program([
        primitive("UV_SPHERE", "Body", "body", radius=0.5, location=[0, 0, 1.0], scale=[0.7, 0.5, 1.4]),
        primitive("UV_SPHERE", "Head", "head", radius=0.3, location=[0, 0, 2.0]),
        paint("body", "Skin", [0.8, 0.6, 0.5, 1]),
        op("material_assign", "$head", material="Skin"),
    ]), program([
        primitive("CYLINDER", "Shirt", "shirt", radius=0.38, depth=0.7, location=[0, 0, 1.25]),
        primitive("CONE", "Hat", "hat", radius=0.32, depth=0.4, location=[0, 0, 2.35]),
        paint("shirt", "Shirt Cloth", [0.1, 0.25, 0.7, 1], Roughness=0.8),
        paint("hat", "Felt", [0.3, 0.2, 0.1, 1]),
        op("modifier_add", "$shirt", type="SOLIDIFY", name="Cloth Thickness"),
    ])], {"Body", "Head", "Shirt", "Hat"}


CASES = {"chess": chess_knight, "room": furnished_room, "vehicle": vehicle, "character": character}


def percentile(values, share):
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, math.ceil(share * len(ordered)) - 1))
    return ordered[index]
