# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The worker Blender's main loop: Angelo's requests, one at a time, on Blender's main thread.

``Angelo --background`` runs no window-manager event loop, so ``bpy.app.timers`` never fire: ``run`` is the loop.
A reader thread only parses frames into ``inbox``; every request is handled here, on the main thread, against the
worker's own document through the same ``BlenderExecutor`` the window uses, bound to the background window record
(``worker_main``), as the headless MCP child does.

Each chat Angelo forwards gets a **shadow controller** here (a ``Controller`` in the worker's private state folder,
registered as the window registers its chats, so the window's approve and commit operators find it). It is set to
Ask, so nothing runs on its own. An edit Angelo approved runs through it exactly as an approved call runs in the
window: admission, the call's own precondition, staging, the native commit with its Undo step (or the Undo operator
of a direct edit). Before approving it, the host compares the shadow's binding with the one Angelo approved and
refuses the edit, with nothing run, when they differ. After the edit the part file is saved, so stopping a worker
never loses committed work. A script runs only as approved, never saving another file as the part (``worker_script``).

With ``ANGELO_WORKER_CHAT_HOST`` on (design A′, the default) the worker hosts the chat itself instead (``chats``,
``worker_chat_host``): ``chat.*`` requests go there, ``run`` pumps its chats between requests, and the shadow's
requests are refused. With it ``0`` everything above is unchanged.
"""

import json
import os
import queue
import threading
import time
import uuid

from . import part_leases, private_roots
from . import worker_refusals as refusals, worker_script, worker_values as values
from .worker_pictures import carry as _carry, pictures_in as _references

EDIT_SECONDS = 240.0     # An approved edit's staging and commit; Angelo waits longer (remote_executor.EDIT_SECONDS).
TURNS_KEPT = 8
SHADOW_OPS = frozenset({"precondition", "precondition_change", "rebind_program", "execute", "recipe_program",
                        "host_program", "prepare_turn"})
STALE_APPROVAL = ("The worker's scene changed after this edit was approved, so its binding is not the approved one; "
                  "nothing ran, and a fresh observation shows the scene as it is now.")
EDIT_TIMED_OUT = "The worker did not finish this edit within {seconds:.0f} s; the scene may hold part of it."


def shadow_class(ui):
    """``Controller`` that keeps the committed program's evidence (``capture_program``) for Angelo's completion."""
    from . import program_completion

    class ShadowController(ui.Controller):
        worker_shadow = True   # A worker chat's calls on the lanes: discovery lists only its set (parallel_access).

        def finish_tool(self, call, result, *, persist=True):
            captures = self.__dict__.setdefault("captures", {})
            if self.pending.get(call.id) is call and captures.get(call.id) is None:
                captures[call.id] = program_completion.capture_program(self.native_jobs.get(call.id))
            return super().finish_tool(call, result, persist=persist)
    return ShadowController


class WorkerHost:
    def __init__(self, ui, bpy, endpoint, part):
        self.ui, self.bpy, self.endpoint, self.part = ui, bpy, endpoint, str(part)
        self.inbox = queue.Queue()
        self.shadows, self.tables, self.turns = {}, {}, {}
        self._shadow_type = shadow_class(ui)
        self.ended = False
        self.revision = 0   # Saves of the part so far: Angelo's live view reloads when it moves (live_layout).
        self.chats = None   # The worker's own chats (worker_chat_host.WorkerChatHost) when it hosts them.

    # Transport -----------------------------------------------------------------------------------------------------

    def start_reader(self):
        threading.Thread(target=self._read, daemon=True, name="AngeloWorkerInbox").start()

    def _read(self):
        from . import external_cli_protocol as protocol
        from .headless_pipe import PipeClosed
        try:
            while True:
                frame = self.endpoint.receive()
                if self.chats is not None and "rseq" in frame:   # A reverse request answered (runtime_relay).
                    self.chats.reverse(frame)
                    continue
                seq, request = frame.get("seq"), frame.get("request")
                if type(seq) is not int or seq < 1 or not isinstance(request, dict):
                    raise protocol.FrameError("Expected {seq, request}")
                if not part_leases.answers(self, seq, request):   # Angelo's read of the part: answered here (C10).
                    # A hosted chat's Stop and consent are marked on arrival; the reader never waits on a lock
                    # another thread holds, nor on the connection's writer (worker_chat_arrival, part_leases).
                    if self.chats is None or not self.chats.arrivals.arrived(seq, request):
                        self.inbox.put((seq, request))
        except (PipeClosed, protocol.FrameError, OSError, ValueError):
            pass
        finally:
            self.inbox.put(None)

    def run(self):
        """Main thread until Angelo closes the connection."""
        from . import external_cli_protocol as protocol
        while not self.ended:
            item = self.inbox.get() if self.chats is None else self.chats.next_request()
            if item is None:
                break
            seq, request = item
            started = time.perf_counter()
            try:
                response = self.handle(request)
            except Exception as error:  # noqa: BLE001 - every request is answered; the error is the answer.
                response = {"error": values.encode_error(error)}
            finally:
                if self.chats is not None:
                    self.chats.arrivals.handled(seq)   # A Stop's or queued consent's barrier ends once it ran.
            if self.chats is not None and self.chats.defer(seq, response):
                continue   # A merge hold: answered once it holds (worker_chat_host).
            response["document"] = list(self.ui.document_key())
            response["worker_ms"] = round((time.perf_counter() - started) * 1000, 3)   # Its own time, for the cost.
            try:
                self.endpoint.send({"seq": seq, "response": response})
            except protocol.FrameError as error:
                self.endpoint.send({"seq": seq, "response": {"error": values.encode_error(RuntimeError(
                    "The worker's answer was larger than the connection allows (%s); nothing was cut" % error)),
                    "document": list(self.ui.document_key())}})
            except OSError:
                break

    # Chats ---------------------------------------------------------------------------------------------------------

    def hosted_sessions(self):
        """The chats this worker hosts (design A′), open until Angelo closes them; none with the switch off."""
        return list(self.chats.chats) if self.chats is not None else []

    def shadow(self, session):
        if not isinstance(session, str) or not session:
            session = str(uuid.uuid4())
        if self.chats is not None and self.chats.hosted(session) is not None:
            return self.chats.hosted(session)   # Its thumbnails use the hosted chat's own references.
        shadow = self.shadows.get(session)
        if shadow is None:
            shadow = self._shadow_type(self.ui.state_directory(), session_id=session)
            shadow.set_document(self.ui.document_key())
            shadow.checkpoint_factory = None   # Angelo takes the request snapshot first (op "checkpoint").
            shadow.state["options"] = dict(shadow.state.get("options") or {}, approval="ASK")
            self.shadows[session] = self.ui._controllers[session] = shadow
        return shadow

    def executor(self, shadow):
        from .blender_tools import BlenderExecutor
        executor = BlenderExecutor(self.ui.main_window())
        executor.studio_context = shadow
        return executor

    def bind_identifiers(self, shadow, request):
        """The chat's observed handles (``remote_executor._identifiers``); False when this worker lacks them."""
        from .identifiers import IdentifierTable
        digest = request.get("identifiers_digest")
        if "identifiers" in request:
            value = request["identifiers"]
            table = IdentifierTable.load(value, "worker") if value is not None else None
            if value is not None and table is None:
                error = ValueError("The chat's observed handles are not a valid table; nothing ran")
                error.partial_changes_possible = False
                raise error
            self.tables[shadow.state["id"]] = (digest, table)
        known = self.tables.get(shadow.state["id"])
        if known is None or known[0] != digest:
            return False
        shadow._identifiers = known[1]
        return True

    # Requests ------------------------------------------------------------------------------------------------------

    def handle(self, request):
        op = request.get("op")
        if op == "hello":
            return {"value": True}
        if op == "playback_running":
            from . import native_programs
            return {"value": bool(native_programs.playback_running(self.ui.main_window()))}
        if op == "commit_turn":
            turn = self.turns.pop(request.get("token"), None)
            if turn is not None:
                turn.commit()
            return {"value": turn is not None}
        if op == "save":
            if request.get("if_dirty") and not self.bpy.data.is_dirty:
                return {"value": None}   # Nothing unsaved: the part file is the document (a done worker stops).
            return {"value": self.save(), "layout": self.layout(), "revision": self.revision}
        if op == "layout":
            return {"layout": self.layout()}
        if op == "switch_part":   # Another part for this warm worker (worker_switch).
            from .worker_switch import switch_part
            return switch_part(self, request.get("part"))
        if self.chats is not None:
            if str(op).startswith("chat."):
                return self.chats.handle(request)
            if op in SHADOW_OPS:   # The chat runs here: no lead-side controller asks a shadow (worker_chat_host).
                raise _refused("This worker hosts its chat; %s is not asked of it" % op)
        shadow = self.shadow(request.get("session"))
        executor = self.executor(shadow)
        reads, edits = refusals.worker_tools()
        name = request.get("name")
        if op in ("precondition", "precondition_change", "rebind_program", "execute"):
            # Angelo refuses these first (``worker_refusals``, the hosted path's set too); never run one anyway.
            refusals.admit(name, request.get("arguments"), refusals.in_worker)
            if name in edits and op != "precondition_change" and not self.bind_identifiers(shadow, request):
                return {"resend_identifiers": True}
        if op == "precondition":
            return {"value": values.encode(executor.precondition(request["name"], request["arguments"]))}
        if op == "precondition_change":
            change = executor.precondition_change(request["name"], values.decode(request.get("before")),
                                                  values.decode(request.get("after")))
            return {"value": values.encode(change)}
        if op == "rebind_program":
            outcome = executor.rebind_program(request["name"], request["arguments"], values.decode(request["approved"]))
            return {"value": values.encode(outcome)}
        if op == "recipe_program":
            return {"value": executor.recipe_program(request["arguments"])}
        if op == "host_program":
            used = []
            program = executor.host_program(request["arguments"], used)
            return {"value": program, "used": json.loads(json.dumps(used))}
        if op == "execute":
            if name in edits:
                return self.edit(shadow, executor, request)
            from .recovery_evidence import collect
            from .view_spec import TOOL_NAME as VIEW
            if refusals.draws(name):   # Drawn offscreen as the rail's picture is (the hosted path's views too).
                self.gpu(request)
                executor.observation_store = shadow.references
            with collect() as records:
                result = executor.execute(request["name"], request["arguments"])
            if name == VIEW:
                _carry(result, shadow.references)
            return {"result": result, "evidence": records, "gpu_verdict": [True, ""] if name == VIEW else None}
        if op == "prepare_turn":
            from . import turn_context
            turn = turn_context.prepare(self.ui.main_window(), shadow.state["id"], message=request.get("message", ""))
            token = uuid.uuid4().hex
            self.turns[token] = turn
            while len(self.turns) > TURNS_KEPT:
                self.turns.pop(next(iter(self.turns)))
            return {"token": token, "observation": turn.observation, "recovery_evidence": turn.recovery_evidence,
                    "stats": turn.stats}
        if op == "checkpoint":
            return {"record": self.checkpoint(request)}
        if op == "thumbnail":
            return self.thumbnail(shadow, executor, request)
        raise ValueError("Unknown worker request")

    def edit(self, shadow, executor, request):
        """One edit Angelo approved, run whole through the shadow controller (module docstring)."""
        arguments = request.get("arguments")
        refusals.admit(request.get("name"), arguments, refusals.in_worker)   # Angelo refuses it before its card first.
        script, serial = request.get("name") == worker_script.SCRIPT, worker_script.serial(self.ui)
        approved = values.decode(request.get("approved"))
        fence = shadow.open_external_read()
        call_id = "worker/" + uuid.uuid4().hex
        key = shadow.state["id"] + "/" + call_id
        answers, approved_once = [], False
        bpy, ui = self.bpy, self.ui
        from .controller import pump_controller_tools

        def apply_mutation(target, call):
            with bpy.context.temp_override(window=ui.main_window()):
                bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])

        def commit_mutation(target, call):
            with bpy.context.temp_override(window=ui.main_window()):
                return bpy.ops.angelo.commit_native_job('EXEC_DEFAULT', True, call_id=call.id,
                                                         session_id=target.state["id"])
        try:
            shadow.request_tool(call_id, request["name"], request["arguments"], answers.append)
            deadline = time.monotonic() + EDIT_SECONDS
            while not answers and time.monotonic() < deadline:
                shadow.pump_events()
                pump_controller_tools([shadow], 0, executor, ui.document_key(), apply_mutation,
                                      commit_mutation=commit_mutation)
                call = shadow.pending.get(key)
                if (call is not None and call.approval and not approved_once and not call.executing
                        and not call.authorized and not call.recovery_review):
                    if call.precondition != approved:
                        shadow.finish_tool(call, {"error_code": "STALE_REVISION", "error": STALE_APPROVAL,
                                                  "scene_changed": False})
                    elif script and not worker_script.approved(call, request):
                        shadow.finish_tool(call, dict(worker_script.NOT_APPROVED))
                    else:
                        approved_once = True
                        if script:   # Approved in Angelo as this exact text: the shadow's exact-code review.
                            shadow.review_script(call)
                        apply_mutation(shadow, call)
                elif not shadow.has_dispatchable_tools and not answers:
                    time.sleep(0.001)
            if not answers:
                call = shadow.pending.get(key)
                if call is not None and not (call.executing or call.committing):
                    shadow.finish_tool(call, {"error_code": "EXPIRED", "scene_changed": False,
                                              "error": EDIT_TIMED_OUT.format(seconds=EDIT_SECONDS)})
            result = answers[0] if answers else {"error_code": "UNKNOWN", "partial_changes_possible": True,
                                                 "error": EDIT_TIMED_OUT.format(seconds=EDIT_SECONDS)}
        finally:
            shadow.close_external_read(fence)
        captured = shadow.__dict__.get("captures", {}).pop(key, None)
        reloaded = worker_script.after(self, result, serial) if script else None
        if reloaded is not None:   # Another file's data is never saved as the part.
            return reloaded
        saved = self.save() if self.bpy.data.is_dirty or script and not worker_script.on_part(self) else None
        return {"result": result, "captured": values.encode_capture(captured), "saved": saved,
                "layout": self.layout() if saved else None, "revision": self.revision if saved else None}

    def save(self):
        """The part file as it is now (no ``.blend1``, paths as they are); its path."""
        bpy = self.bpy
        paths = bpy.context.preferences.filepaths
        backups = paths.save_version
        try:
            paths.save_version = 0
            with part_leases.leases_of(self).saving(self.part), bpy.context.temp_override(window=self.ui.main_window()):
                result = bpy.ops.wm.save_as_mainfile(filepath=self.part, check_existing=False, relative_remap=False)
        finally:
            paths.save_version = backups
        if "FINISHED" not in result:
            raise RuntimeError("The worker could not save its part file")
        self.revision += 1
        return self.part

    def layout(self):
        """What the saved part shows at its top level, for Angelo's live view (``live_layout``), or None when it
        cannot be read: the edit it follows already ran and saved, so it is never reported as failed for this, and
        a worker still starts (break-it P1-5 L2)."""
        from .live_layout import layout
        from .worker_merge import view_state
        try:
            window = self.ui.main_window()
            with self.bpy.context.temp_override(window=window):
                depsgraph = self.bpy.context.evaluated_depsgraph_get()
            # The view layer's visibility too: a merge appends the part's data, never its view layer (worker_merge).
            return dict(layout(window.scene, self.revision, depsgraph),
                        view=view_state(window.scene, window.view_layer))
        except Exception as error:   # noqa: BLE001 - the live view waits for the next save instead.
            print("Angelo worker: layout:", type(error).__name__, error)
            return None

    def thumbnail(self, shadow, executor, request):
        """A picture of the part as it is now, for Angelo's session rail: ``angelo_view``'s own offscreen viewport
        draw (the same code the model's views use), never a render. PNG bytes, and how long the draw took."""
        import base64
        from .view_spec import RESOLUTIONS
        size = request.get("size") if request.get("size") in RESOLUTIONS else RESOLUTIONS[0]
        shading = request.get("shading") if request.get("shading") in ("SOLID", "MATERIAL") else "SOLID"
        scene = self.ui.main_window().scene
        if not any(obj.visible_get() for obj in scene.objects):   # An empty part has no picture yet: nothing to frame.
            return {"pictures": [], "draw_ms": 0.0, "size": size, "shading": shading, "empty": True}
        self.gpu(request)
        executor.observation_store = shadow.references
        if request.get("fresh"):   # Measure a real draw: an unchanged scene is otherwise answered from view_cache.
            from . import view_cache
            view_cache.clear()
        started = time.perf_counter()
        result = executor.execute("angelo_view", {"views": ["ISO"], "shading": shading, "resolution": size})
        seconds = time.perf_counter() - started
        pictures = []
        for reference in _references(result):
            with open(reference["path"], "rb") as stream:
                pictures.append(base64.b64encode(stream.read()).decode("ascii"))
            shadow.references.discard_observation(reference)
        answer = {"pictures": pictures, "draw_ms": round(seconds * 1000, 3), "size": size, "shading": shading,
                  "gpu_verdict": [True, ""]}
        if self.__dict__.pop("probe_ms", None) is not None:   # Once: what this worker's first picture spent on it.
            answer["probe_ms"] = self.__dict__.get("last_probe_ms")
        return answer

    def gpu(self, request):
        """This worker draws offscreen, or the call fails with why (the worker and its chat go on): learnt once per
        worker, or from Angelo's verdict for an earlier worker on this machine and binary (break-it L4-1)."""
        known = request.get("gpu_verdict")
        if "gpu_verdict" not in self.__dict__ and isinstance(known, list) and len(known) == 2 and type(known[0]) is bool:
            self.gpu_verdict = (known[0], str(known[1]))
        if not self.__dict__.get("gpu_ready"):
            if "gpu_verdict" not in self.__dict__:   # A failed probe is never run again.
                probe_started = time.perf_counter()
                self.gpu_verdict = offscreen_draw_usable(self.bpy)
                self.probe_ms = self.last_probe_ms = round((time.perf_counter() - probe_started) * 1000, 3)
            usable, why = self.gpu_verdict
            if not usable:
                error = _refused(NO_GPU.format(why=why))
                error.details = {"gpu_verdict": [False, why]}   # Angelo keeps it: no later worker probes again.
                raise error
            import gpu
            gpu.init()   # Views draw offscreen through the background window's 3D Viewport (headless_main does too).
            self.gpu_ready = True

    def checkpoint(self, request):
        """A request snapshot of this document into Angelo's store for the chat (``remote_executor``)."""
        from . import native_programs
        from .checkpoints import CheckpointStore
        from .document_checkpoint import capture
        staging = getattr(native_programs, "runtime_is_staging", native_programs.has_active_programs)
        if staging():
            raise RuntimeError("A program is still staging in the worker; its stage cannot be in a snapshot")
        store = CheckpointStore(request["root"], request["session"])
        return capture(self.bpy, store, self.ui.main_window(), self.part, capture_mode=request["capture_mode"],
                       purpose=request.get("purpose", "request"))


NO_GPU = ("This machine cannot draw offscreen pictures in a background Blender ({why}), so this worker draws none; "
          "nothing ran, and the worker goes on.")
PROBE_SECONDS = 60.0
# One offscreen draw exactly as a picture is drawn (``angelo_view`` on the background window), in a child Blender of
# its own: a draw that crashes the GPU driver, or has no GPU to use, ends that child, never the worker.
PROBE = r"""
import os, sys
sys.path.insert(0, sys.argv[-1])
if os.environ.get("ANGELO_QA_GPU_PROBE_CRASH") == "1":
    # QA: a draw that kills the process, as on a machine whose driver crashes. A kill, never abort(): on macOS an abort
    # shows "quit unexpectedly", a system dialog that stops every later windowed or timed run on the machine (Mac 1,
    # 2026-10-24/25). SIGTERM where SIGKILL does not exist: on Windows os.kill ends the process (TerminateProcess).
    import signal
    os.kill(os.getpid(), signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
import gpu, shutil, uuid
gpu.init()
from angelo_agent import live_windows, private_roots
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.references import ReferenceStore
import bpy
executor = BlenderExecutor(live_windows.first(bpy.context.window_manager))
folder = private_roots.mkdtemp("angelo-observations-")
executor.observation_store = ReferenceStore(folder, str(uuid.uuid4()))
for shading in ("SOLID", "MATERIAL"):   # Every shading and the largest size a picture may be drawn at.
    executor.execute("angelo_view", {"views": ["ISO"], "shading": shading, "resolution": 1024})
shutil.rmtree(folder, ignore_errors=True)
print("ANGELO-OFFSCREEN-OK")
"""


def offscreen_draw_usable(bpy, run=None):
    """(usable, why): whether a picture can be drawn in this process, learnt once from a child Blender that draws one.

    The draw itself cannot be guarded in process: with no GPU (a Windows machine without a display adapter) the
    first one ended the worker. So the first picture a worker is asked for is drawn by a short-lived child first,
    with this worker's own environment; only when that child drew it does the worker draw. ``run`` stands in for
    ``child_process.run`` in tests."""
    from pathlib import Path
    command = [bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
               "--python-exit-code", "1", "--python-expr", PROBE, "--", str(Path(__file__).resolve().parents[1])]
    try:
        if run is None and os.name != "nt":
            done = _run_group(command, PROBE_SECONDS)
        else:   # Windows: a Job Object ends the probe's whole tree (child_process).
            from .child_process import run as job_run
            done = (run or job_run)(command, capture_output=True, text=True, timeout=PROBE_SECONDS,
                                    env=dict(os.environ))
    except Exception as error:  # noqa: BLE001 - the probe's own failure is a reason, never the worker's.
        return False, "the probe did not run: %s" % type(error).__name__
    if done.returncode == 0 and "ANGELO-OFFSCREEN-OK" in (done.stdout or ""):
        return True, ""
    return False, "a test draw ended with exit status %s" % done.returncode


def _run_group(command, timeout):
    """macOS and Linux: the probe in a process group of its own, all of which a timeout ends (break-it L4-4)."""
    import signal
    import subprocess
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace", env=dict(os.environ),
                               start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, "")


def _refused(message):
    """A refusal before any edit started here: nothing ran (``worker_values`` carries the certainty to Angelo)."""
    from .refusal_codes import RefusalError
    error = RefusalError(message, code="UNSUPPORTED")
    error.partial_changes_possible = False
    return error
