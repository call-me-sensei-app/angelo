# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``RemoteExecutor``: a chat's executor when its document is a worker Blender's (``worker_routing``).

It has ``BlenderExecutor``'s interface, and the chat's ``Controller`` drives it exactly as it drives the window's
executor: admission, approval cards and modes, the journal, results and chat rows all stay in Angelo. Each call is
forwarded to the worker (``worker_process`` over ``worker_channel``), which runs it on its own document with its own
``BlenderExecutor`` (``worker_host``):

* **Reads** run there and return their result, plus the recovery evidence they captured, which joins this thread's
  ``recovery_evidence.collect`` as if read here.
* **Preconditions** come back as exact values (``worker_values``), so the controller's ``==`` checks and the program
  review behave as in one process.
* **An approved edit runs whole in the worker:** ``supports_native_job`` is False, so the controller sends the call
  through ``execute`` once it is approved; the worker stages and commits it through its own controller with its own
  Undo, refuses it when its binding is no longer exactly the one approved here, saves its file, and returns the
  result and the committed program's evidence (``remote_captures``, minted into a completion by ``finish_tool``).
  Nothing is staged in this process and Angelo's own Undo history never gets a step for a worker's edit.
* **Views** (``angelo_view``) are drawn in the worker's Blender, offscreen as the rail's picture is, and their
  pictures come back as bytes, stored in this chat's own references like any view's (the worker capability
  contract, 2026-10-25).
* **Scripts** (``angelo_execute_python``) are approved here as any chat's are, and the approved call runs in the
  worker with its invocation digest, which the worker checks; one that would replace the open file is refused before
  its card (``worker_script``). Never headless: ``external_clients.HEADLESS_TOOLS`` stays without it.
* **Not in a worker:** sculpting, jobs, Studio, flows, team tools, file summaries, previews and their acceptance are
  refused before anything runs (``UNSUPPORTED``); task memory, the chat's metrics and its scripts' code
  (``angelo_script``) are this chat's own state and run here.

A chat's tool work runs on its lane (``worker_dispatch``): while a call waits for its worker, Blender's main thread
draws and handles input. A call made on the main thread outside every lane still waits there, and is counted.
"""

import hashlib
import json
import os

from .refusal_codes import RefusalError
from .view_spec import TOOL_NAME as VIEW_TOOL
from . import worker_refusals as refusals, worker_values as values
from .worker_script import SCRIPT

READ_SECONDS = 60.0
OFFSCREEN_VERDICTS = {}  # Angelo binary -> [usable, why] for offscreen pictures in its background workers.
HEALTH_SECONDS = 0.5     # After a call timed out, a call first checks the worker answers at all, at most this long,
PROBE_INTERVAL = 5.0     # and at most this often; calls between checks are refused at once (break-it N5).
NOT_ANSWERING = "The worker Blender is busy or not answering ({why}); nothing was sent to it. Retry shortly."
NO_ANSWER = ("The worker Blender did not answer in time ({why}); this call changes no scene, so nothing changed. "
             "Retry shortly.")
ENDED = "The worker Blender of this chat has ended ({why}); nothing ran. Start a new chat."
UNREACHABLE = ("The worker Blender could not be reached ({why}); it still runs, and a later call checks it first. "
               "Retry shortly.")
REFUSED = ("This request cannot be sent to the worker Blender ({why}); nothing was sent and nothing changed. Send a "
           "smaller request with finite numbers.")
PNG = b"\x89PNG\r\n\x1a\n"
LEFT_OUT = object()   # A worker's picture left out of its view's answer (``RemoteExecutor._pictures_here``).
EDIT_SECONDS = 300.0     # The worker gives up first (worker_host.EDIT_SECONDS) and says why.
# Called with the executor on Blender's main thread after the worker saved its part with a new layout (live_layout):
# the live view and the session rail reload from it. Each must return quickly; one that raises is printed. A layout
# that arrives on a chat's lane reaches them when the lane hands the baton back (``worker_dispatch.on_main``).
part_listeners = []
UNKNOWN_OUTCOME = ("The worker Blender ended or stopped answering while this edit ran, so whether it was applied is "
                   "unknown. Observe the scene before continuing.")
# What a worker chat runs and refuses, one set for this path and the hosted one (``worker_refusals``).
PREVIEW_IN_WORKER, NOT_IN_WORKER = refusals.PREVIEW_IN_WORKER, refusals.NOT_IN_WORKER
worker_tools, local_tools, _nothing_ran = refusals.worker_tools, refusals.local_tools, refusals.nothing_ran


class RemoteWindow:
    """Stands for the worker's background window where code only asks whether an executor has one."""

    def __init__(self, executor):
        self.executor = executor


class RemoteTurn:
    """A turn observation the worker prepared; ``commit`` acknowledges its change feed there."""

    def __init__(self, executor, answer):
        self.executor, self.token = executor, answer.get("token")
        self.observation = answer.get("observation")
        from .recovery_evidence import collect, merge
        with collect() as records:   # Bounded again here, as a read's evidence is (break-it L7).
            merge(answer.get("recovery_evidence"))
        self.recovery_evidence = records
        self.stats = answer.get("stats") or {}

    def commit(self):
        if self.token is not None:
            token, self.token = self.token, None
            self.executor.ask("commit_turn", token=token)


class RemoteExecutor:
    def __init__(self, worker, worker_id):
        self.worker, self.worker_id = worker, worker_id
        self.window = RemoteWindow(self)
        self.observation_store = None
        self.studio_context = None
        self.script_file = None
        self.document_key = ("angelo-worker", worker_id, None)
        self._sent_identifiers = None
        self.unresponsive = False   # A call timed out while the worker still ran (never "ended": break-it M2).
        self._next_probe = 0.0
        self.last_worker_ms = None
        self.reads, self.edits = worker_tools()
        self.local = local_tools()
        from .live_layout import clean
        ready = getattr(worker, "ready", None)
        self.part_layout = clean(ready.get("layout")) if isinstance(ready, dict) else None

    def note_layout(self, value):
        """A layout the worker reported (``live_layout``): kept when newer, and the listeners told."""
        from .live_layout import clean
        layout = clean(value)
        if layout is None or (self.part_layout is not None and layout["revision"] <= self.part_layout["revision"]):
            return
        self.part_layout = layout
        from .worker_dispatch import on_main

        def tell():
            for listener in list(part_listeners):
                try:
                    listener(self)
                except Exception as error:   # noqa: BLE001 - a listener never fails the edit that already ran.
                    print("Angelo: part listener:", error)
        on_main(tell)

    # The connection --------------------------------------------------------------------------------------------

    def ask(self, op, timeout=None, effects=False, **fields):
        """The worker's answer. A worker that ended changes this chat's document (its calls are refused from then);
        one that only did not answer in time keeps it: later calls first check it answers again (``hello``) and are
        refused, sending nothing, while it does not (break-it M2, M3, N5).

        A call that did not get its answer is a classified refusal that changed nothing (``WRITER_BUSY`` while the
        worker runs, ``CANCELLED`` once it ended; break-it N1), except an edit that may have reached the worker
        (``effects``): that one raises ``WorkerUnavailable`` for its caller to report as an unknown outcome."""
        import time
        from .worker_process import RequestRefused, WorkerUnavailable
        from .worker_dispatch import ask as off_main   # On a chat's lane, the main thread never waits for this.
        try:
            if self.unresponsive:
                if time.monotonic() < self._next_probe:
                    raise WorkerUnavailable("it did not answer the last check", timed_out=True, sent=False)
                self._next_probe = time.monotonic() + PROBE_INTERVAL
                try:
                    off_main(lambda: self.worker.request("hello", HEALTH_SECONDS))
                except WorkerUnavailable as error:
                    error.sent = False   # Only the check went out: the request itself was never sent.
                    raise
                self.unresponsive = False
            answer = off_main(lambda: self.worker.request(op, READ_SECONDS if timeout is None else timeout, **fields),
                              effects=effects)
        except RequestRefused as error:   # Angelo's own request, never the worker's state (break-it E2E-15 M-1).
            raise _nothing_ran(RefusalError(REFUSED.format(why=error), code=error.code)) from error
        except WorkerUnavailable as error:
            # Dead only when it is gone: its process or connection ended, never a send that failed under a live one.
            dead = not error.timed_out and not getattr(self.worker, "alive", lambda: False)()
            if dead:
                self.document_key = ("angelo-worker", self.worker_id, "ended")
                self._died(error)
            else:
                self.unresponsive = True   # Not dead: it may answer again (a later call checks first).
            if effects and error.sent:
                raise
            words = (ENDED if dead else NOT_ANSWERING if not error.sent else NO_ANSWER if error.timed_out
                     else UNREACHABLE)   # A send that failed under a live worker is no timeout (E2E-17 L-4).
            refusal = RefusalError(words.format(why=error), code="CANCELLED" if dead else "WRITER_BUSY")
            raise _nothing_ran(refusal) from error
        self.last_worker_ms = answer.get("worker_ms")   # The worker's own handling time (bench_parallel_workers).
        document = answer.get("document")
        if isinstance(document, list) and len(document) == 2:
            self.document_key = ("angelo-worker", self.worker_id, tuple(document))
        if answer.get("error") is not None:
            raise values.decode_error(answer["error"])
        return answer

    def greet(self):
        """The worker's ``hello`` as its launch answered it (``WorkerProcess.start``, off the main thread), taken once;
        asked now when it has none (a worker started another way)."""
        answer = getattr(self.worker, "__dict__", {}).pop("hello", None)
        if not isinstance(answer, dict):
            return self.ask("hello")
        document = answer.get("document")
        if isinstance(document, list) and len(document) == 2:
            self.document_key = ("angelo-worker", self.worker_id, tuple(document))
        if answer.get("error") is not None:
            raise values.decode_error(answer["error"])
        return answer

    def _died(self, why=None):
        """The worker is gone (not a timeout): its chat leaves it on the main thread, as when it is stopped, so its
        parallel session ends, its slot frees and its tile can close, while its saved part and the chat's receipts
        stay (audit P2: a crashed worker was counted as running, and a replacement was refused). The death is recorded
        as a failure, never a finish: the lead's status says so and its merge warns (break-it E2E-15 M-2)."""
        controller = self.studio_context
        if controller is None or getattr(controller, "remote_executor", None) is not self:
            return
        from . import worker_routing, worker_sessions
        from .worker_dispatch import on_main
        worker_sessions.died.setdefault(controller.state["id"], str(why or "its worker ended")[:200])

        def leave():
            if getattr(controller, "remote_executor", None) is self:   # Not already left (a stop, a merge).
                worker_routing.unbind(controller, worker_routing.END_CRASHED)
        on_main(leave)

    def _share_references(self, arguments):
        """The chat's images a view names as its ``reference``, in the worker Blender's own store (``worker_share``)."""
        from .worker_share import share
        share(self.worker, self.studio_context, self._session(), arguments)

    def _pictures_here(self, value):
        """A view's pictures (PNG bytes in the worker's answer) as files of this chat's own references, the records
        rewritten to them: the controller attaches them to the model as any view's (``ReferenceStore.tool_images``).
        Only a view's answer is read, and only a PNG within the image limit is written: anything else is left out of
        its list, counted for the answer's note, never a half-written file (break-it E2E-16 L-1, E2E-17 L-1)."""
        import base64
        import binascii
        if isinstance(value, dict):
            if "png_b64" in value:
                store = self.studio_context.references
                try:
                    data = base64.b64decode(value.pop("png_b64"), validate=True)
                except (binascii.Error, TypeError, ValueError):
                    data = b""
                if not data.startswith(PNG) or len(data) > store.image_limit:
                    self._dropped = getattr(self, "_dropped", 0) + 1
                    return LEFT_OUT   # Its parent list leaves it out.
                reference = store.new_observation(str(value.get("name") or "view")[:120])
                with open(reference["path"], "wb") as stream:
                    stream.write(data)
                value.update(reference, bytes=len(data))
                return value
            for item in value.values():
                self._pictures_here(item)
        elif isinstance(value, list):
            value[:] = [item for item in value if self._pictures_here(item) is not LEFT_OUT]
        return value

    def _session(self):
        state = getattr(self.studio_context, "state", None)
        return state.get("id") if isinstance(state, dict) else None

    def _identifiers(self):
        """The chat's observed handles and revisions (programs bind undeclared dependencies to them); sent again only
        when they changed since the last call."""
        table = getattr(self.studio_context, "_identifiers", None)
        value = table.to_dict("worker") if table is not None else None
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()
        if digest == self._sent_identifiers:
            return {"identifiers_digest": digest}
        self._sent_identifiers = digest
        return {"identifiers": value, "identifiers_digest": digest}

    def _ask_bound(self, op, timeout=None, effects=False, **fields):
        """``ask`` with the chat's observed handles; a worker that lacks them (it never got them, or started again)
        says so before doing anything, and gets them once in full."""
        for _attempt in range(2):
            answer = self.ask(op, timeout, effects, **fields, **self._identifiers())
            if not answer.get("resend_identifiers"):
                return answer
            self._sent_identifiers = None
        raise _nothing_ran(RuntimeError("The worker could not take this chat's observed handles; nothing ran"))

    def _admit(self, name, arguments=None):
        refusals.admit(name, arguments)   # Before anything is sent; the worker checks again (``worker_host``).

    def _local_executor(self):
        from .blender_tools import BlenderExecutor
        executor = BlenderExecutor(None)   # Memory and metrics read the chat itself, never a window or scene.
        executor.studio_context = self.studio_context
        return executor

    # BlenderExecutor's interface -------------------------------------------------------------------------------

    def precondition(self, name, arguments):
        if name in self.local:
            return self._local_executor().precondition(name, arguments)
        self._admit(name, arguments)
        answer = self._ask_bound("precondition", name=name, arguments=arguments, session=self._session())
        return values.decode(answer["value"])

    def precondition_change(self, name, before, after):
        if name not in {"angelo_scene_program", "angelo_apply_recipe"}:
            return None
        answer = self.ask("precondition_change", name=name, before=values.encode(before), after=values.encode(after))
        return values.decode(answer["value"])

    def rebind_program(self, name, arguments, approved):
        if name != "angelo_scene_program":
            return None
        answer = self._ask_bound("rebind_program", name=name, arguments=arguments, approved=values.encode(approved),
                                 session=self._session())
        return values.decode(answer["value"])

    def playback_running(self):
        return bool(self.ask("playback_running")["value"])

    def usage_label(self, name, arguments, precondition=None):
        return ""   # Only Studio and flow calls have one, and neither runs in a worker.

    def execute(self, name, arguments):
        if name in self.local:
            return self._local_executor().execute(name, arguments)
        self._admit(name, arguments)
        if name in self.reads:
            key = str(getattr(self.worker, "binary", None) or "")
            if name == VIEW_TOOL:
                self._share_references(arguments)
            try:
                answer = self.ask("execute", name=name, arguments=arguments, session=self._session(),
                                  gpu_verdict=OFFSCREEN_VERDICTS.get(key))
            except RefusalError as error:   # A view on a machine that cannot draw offscreen: learnt once, as a picture.
                verdict = (getattr(error, "details", None) or {}).get("gpu_verdict")
                if verdict is not None:
                    OFFSCREEN_VERDICTS[key] = list(verdict)
                raise
            if answer.get("gpu_verdict") is not None:
                OFFSCREEN_VERDICTS[key] = list(answer["gpu_verdict"])
            from .recovery_evidence import merge
            merge(answer.get("evidence"))
            if name != VIEW_TOOL:
                return answer["result"]
            self._dropped = 0
            result = self._pictures_here(answer["result"])
            result = {} if result is LEFT_OUT else result
            if self._dropped and isinstance(result, dict):   # Left out, never failing the view (E2E-17 L-1).
                result["pictures_left_out"] = "%d of the worker's pictures were not a PNG within the image limit" % (
                    self._dropped)
            return result
        call = getattr(self.studio_context, "executing_call", None)
        approved = getattr(call, "precondition", None) if call is not None else None
        from .worker_process import WorkerUnavailable
        if name == SCRIPT:   # Sent, it may run: a part's Python can leave state behind, so this Blender is never reused.
            self.worker.ran_python = True
        try:
            answer = self._ask_bound("execute", EDIT_SECONDS, effects=True, name=name, arguments=arguments,
                                     session=self._session(), approved=values.encode(approved),
                                     **({"digest": getattr(call, "invocation_digest", None)} if name == SCRIPT else {}))
        except WorkerUnavailable as error:   # Sent: it may have run (an unsent one is a refusal from ``ask``).
            failure = RuntimeError(UNKNOWN_OUTCOME + " (" + str(error) + ")")
            failure.partial_changes_possible = True
            raise failure from error
        layout = answer.get("layout")
        if layout is None and answer.get("saved") and type(answer.get("revision")) is int and self.part_layout:
            # The edit saved its part but the worker could not read its layout: the last known tree at the new
            # revision, so the part is still read again and its tile never freezes on that save (break-it P1-9 P9).
            layout = dict(self.part_layout, revision=answer["revision"])
        self.note_layout(layout)
        if call is not None and answer.get("captured") is not None:
            program = call.program_arguments if call.program_arguments is not None else call.arguments
            captured = values.decode_capture(answer["captured"], program)
            captures = getattr(self.studio_context, "remote_captures", None)
            if captured is not None and isinstance(captures, dict):
                captures[call.id] = captured
        return answer["result"]

    @staticmethod
    def supports_native_job(name, arguments=None):
        return False   # The worker stages and commits an approved edit itself (module docstring).

    def recipe_program(self, arguments):
        return self.ask("recipe_program", arguments=arguments, session=self._session())["value"]

    def host_program(self, arguments, used=None):
        answer = self.ask("host_program", arguments=arguments, session=self._session())
        if used is not None:
            used.extend(answer.get("used") or ())
        return answer["value"]

    orbit_program = host_program

    def start_native_job(self, name, arguments, *, approved_obligations=None):
        raise RefusalError("A worker's edit is staged and committed in the worker; nothing was staged here",
                           code="UNSUPPORTED")

    def thumbnail(self, size=256, shading="SOLID", fresh=False):
        """The worker's picture of its part for the session rail (``worker_host.thumbnail``). Whether this machine
        can draw offscreen in a background Blender is learnt from the first worker that tried, and every later
        worker is told, so no other one runs the probe again (break-it L4-1)."""
        key = str(getattr(self.worker, "binary", None) or "")
        try:
            answer = self.ask("thumbnail", size=size, shading=shading, fresh=fresh, session=self._session(),
                              gpu_verdict=OFFSCREEN_VERDICTS.get(key))
        except RefusalError as error:
            verdict = (getattr(error, "details", None) or {}).get("gpu_verdict")
            if verdict is not None:
                OFFSCREEN_VERDICTS[key] = list(verdict)
            raise
        if answer.get("gpu_verdict") is not None:
            OFFSCREEN_VERDICTS[key] = list(answer["gpu_verdict"])
        return answer

    # Beyond BlenderExecutor: what the window's helpers do with ui.main_window() -------------------------------

    def prepare_turn(self, window=None, chat_id=None, clock=None, message=""):
        """``turn_context.prepare`` on the worker's document (its change feed lives there)."""
        del window, clock
        return RemoteTurn(self, self.ask("prepare_turn", session=chat_id, message=message or ""))

    def capture_checkpoint(self, controller, purpose="request"):
        """A request snapshot of the worker's document, written by the worker into this chat's snapshot store."""
        from .checkpoint_policy import clean_policy
        from . import snapshot_service
        policy = clean_policy(getattr(controller, "_turn_checkpoint_policy", None)) if purpose == "request" else (
            clean_policy())
        answer = self.ask("checkpoint", EDIT_SECONDS, root=str(controller.checkpoints.root),
                          session=controller.checkpoints.session_id, capture_mode=policy["capture_mode"],
                          purpose=purpose)
        record = answer["record"]
        from .worker_dispatch import on_main
        on_main(lambda: snapshot_service.after_capture(controller, record))   # Its bookkeeping may touch bpy.
        return record


class EndedWorker:
    """The executor of a worker chat whose worker is gone (Angelo restarted, or the chat was unbound): every call is
    refused with nothing run, so the chat never edits Angelo's own document (break-it N2)."""

    def __init__(self, session_id, part):
        self.window = RemoteWindow(self)
        self.observation_store = self.studio_context = self.script_file = None
        self.document_key = ("angelo-worker", session_id, "ended")
        self.part = part

    def _refuse(self, *args, **kwargs):
        raise _nothing_ran(RefusalError(ENDED.format(why="it is not running: this chat worked on %s"
                                                      % os.path.basename(self.part)), code="CANCELLED"))

    precondition = precondition_change = rebind_program = execute = recipe_program = host_program = _refuse
    start_native_job = prepare_turn = capture_checkpoint = _refuse

    def playback_running(self):
        return False

    def usage_label(self, name, arguments, precondition=None):
        return ""

    @staticmethod
    def supports_native_job(name, arguments=None):
        return False
