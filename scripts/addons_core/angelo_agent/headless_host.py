# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The headless child's host: an app's calls on a .blend with no Angelo window (doc/angelo/external_mcp.rst).

It is ``ExternalHost`` with three differences, so a call runs the code it runs in the window (``RequestService``
admission, ``ExternalWork``, ``DirectRequest``, ``Controller.request_tool`` under Approve for me):

* **Transport and loop.** Requests arrive over the launcher's pipes (``headless_pipe.PipeServer``), not a socket,
  and background Blender runs no timers, so ``run`` is the main-thread loop ``ui.pump`` would be: drain, the apps'
  requests, the shared tool pump with the background window's executor. It wakes as soon as work arrives.
* **Nothing waits for a person.** Only reads and bounded typed edits are admitted (``tools_for(headless=True)``), and
  ``angelo_run_script_file`` for a file the person always allows while it is unchanged (``script_files``, read from
  the person's state folder, never written; keyed by the opened source); a call that still reaches an approval card
  (an unapproved or changed script file, an Ask policy, a recovery review) is finished at once as ``needs_gui``.
* **Edits go to a new file.** After a call that committed an edit, the session is saved to an output file claimed
  next to the source (``headless_files``); the first save moves the session's file path to it, so nothing can ever
  save over the source. An edit that ended its request (it failed and may have changed the scene) reloads the last
  saved state, so a partial change is never saved. Every call first checks that neither file changed on disk.
"""

import json
import os
import queue
import shutil
import threading
import time
from pathlib import Path

from . import private_roots
from . import headless_files
from . import headless_working_copy as working_copy
from .external_cli import ClientInfo, Peer
from .external_clients import EDIT_TOOLS, SCRIPT_FILE_TOOL
from .external_host import LISTENING, ClientRegistry, ExternalHost, UiEnvironment
from .external_requests import WorkQueue
from .external_service import RequestService
from . import program_undo

IDLE_EXIT = 600.0          # Seconds without a call before the child exits; the launcher restarts it on the next call.
NOTES_KEPT = 1024          # Call notes remembered for a delivery asked for again (external_apps.CALLS_KEPT).
IDLE_WAIT, BUSY_WAIT = 0.25, 0.005
WAITS_FOR_PERSON = ("needs_gui: this call would wait for the person's approval in Angelo's window, and no window is "
                    "open here; nothing ran. Without the window only reads, views and bounded typed edits run.")
PREVIEW_NEEDS_GUI = ("needs_gui: a PREVIEW is accepted by the person in Angelo's window. Without the window use "
                     "mode APPLY; the edit is saved to a new file and the source stays unchanged.")
RELOADED = ("Without Angelo's window this ends differently: the edit failed and may have changed the scene, so Angelo "
            "reloaded {name} as last saved. Nothing of it remains or was saved; observe the scene again, then "
            "continue.")
SAVED = "Saved as {path} (a new file; {source} is unchanged)."
SOURCE_CHANGED = ("{source} changed on disk during this session: an approved script or one of your add-ons wrote it, "
                  "so it is no longer the file this session opened; open it again with angelo_open_file before more "
                  "calls.")
SOURCE_WRITTEN = ("Saved as {path}. {source} changed on disk during this call (an approved script or one of your "
                  "add-ons saved it), so it is no longer the file this session opened; open it again with "
                  "angelo_open_file before more calls.")
FOREIGN_FILE = ("This run opened or saved as another file ({opened}), which this session does not own: nothing of the run "
                "was saved, and Angelo loaded {name} again. Observe the scene before continuing.")
NOTHING_RAN = "nothing ran"   # What the refusals below say to the app (the decision itself is structured).


def nothing_ran(value):
    """Whether an MCP result is a refusal before anything ran: its host result block says ``NEEDS_GUI`` and
    ``scene_changed`` false. Decided from those fields, never from words an error could echo (review N2)."""
    for block in (value.get("content") or []) if isinstance(value, dict) else ():
        try:
            result = json.loads(block.get("text", "")) if isinstance(block, dict) else None
        except ValueError:
            continue
        if isinstance(result, dict) and result.get("error_code") == "NEEDS_GUI" and result.get("scene_changed") is False:
            return True
    return False
# Headless Python (maintainer decision 2026-10-23): only a script file the person always allows while it is unchanged.
SCRIPT_FILE_NEEDS_APPROVAL = (
    "needs_gui: {file} is not approved for this app in {project}, or it changed since it was approved; nothing ran. "
    "Without Angelo's window only a script file the person always allows while it is unchanged runs. In Angelo's "
    "window, open {project}, run it once with angelo_run_script_file and choose \"Always allow this file while it's "
    "unchanged\"; headless runs then use that approval until the file changes.")
# Why else a script file did not run headless (TrustedScripts.script_file_refusal); each says "nothing ran".
SCRIPT_FILE_REFUSALS = {
    "excluded": ("needs_gui: {file} refers to preferences or Angelo's own add-on, so it always needs the person's "
                 "review in Angelo's window and can never be allowed to run unasked; nothing ran."),
    "mode": ("needs_gui: this app's conversation in Angelo is set to Ask or Read only (by the person, or by a policy "
             "that requires asking for edits), so no script runs without the person; nothing ran."),
    "policy": ("needs_gui: the organization's policy requires the person to approve every script (AllowAutoApproval "
               "is off), so no script file runs without Angelo's window; nothing ran."),
    "levels": "needs_gui: the person has not allowed this app to run Python scripts; nothing ran.",
    "project": ("needs_gui: the file open here is not the one this session opened, so no script file approval applies; "
                "nothing ran."),
}
SAVES = EDIT_TOOLS | {SCRIPT_FILE_TOOL}   # Calls whose success is saved to the output (a script can change anything).


class HeadlessEnvironment(UiEnvironment):
    """``UiEnvironment`` over the background ``ui`` module: no access switch, no snapshots, no tasks. ``approval``
    is the person's choice for this app's External conversation in the window (``ASK`` or ``READ_ONLY`` hold here
    too: an edit then needs the window); ``AUTO_BOUNDED`` or None is the chat default."""

    def __init__(self, ui, approval=None, source=None, levels=None):
        super().__init__(ui)
        self.approval = approval if approval in {"ASK", "READ_ONLY"} else None
        self.source, self.levels = source, tuple(levels) if levels is not None else None

    def sessions(self):
        if self.ui._sessions is None:
            from .sessions import SessionCollection
            self.ui._sessions = SessionCollection(self.state_directory())
        return self.ui._sessions

    def access_requested(self):
        return True

    def prepare_task(self, controller):
        raise RuntimeError("needs_gui: Angelo's agent runs tasks only in Angelo's window")

    def make_controller(self, session_id):
        controller = self.ui.Controller(self.state_directory(), session_id=session_id)
        controller.set_document(self.document_key())
        # No request snapshot: the source file is never written, so it is the state before every edit.
        controller.checkpoint_factory = None
        if self.source is not None:
            host = getattr(self, "host", None)
            bind_script_files(controller, self.source, self.levels,
                              session_open=host.session_file_open if host is not None else (lambda: True))
        return apply_window_approval(controller, self.approval)

    def private_roots(self):
        host = getattr(self, "host", None)
        return [root for root in super().private_roots() if root[1] != "<project>"] + working_copy.named_roots(
            self.source, getattr(host, "named_scripts", ()))


def bind_script_files(controller, source, levels, session_open=lambda: True):
    """A script file approval in this child is keyed by the .blend the person opened (the session's source), never
    the output its edits are saved to, and by the levels the launcher read for this child (``trusted_scripts``).
    ``session_open()`` says whether the file Blender has open is still this session's own (the source or its
    output); while it is not (a script opened or saved as another file), no approval applies."""
    project = os.path.realpath(source)
    controller.script_project = lambda: project if session_open() else ""
    controller.external_levels = tuple(levels)
    return controller


def apply_window_approval(controller, approval):
    """The person's stricter choice for the app in the window (``ASK``, ``READ_ONLY``) holds headless too."""
    if approval in {"ASK", "READ_ONLY"}:
        controller.state["options"] = dict(controller.state.get("options") or {}, approval=approval)
    return controller


class WakingQueue(WorkQueue):
    """The work queue that also wakes the main loop."""

    def __init__(self, wake):
        super().__init__()
        self.wake = wake

    def submit(self, work):
        accepted = super().submit(work)
        self.wake.set()
        return accepted


class HeadlessHost(ExternalHost):
    def __init__(self, environment, *, source, label, app, levels, bpy, source_identity=None, working=None):
        super().__init__(environment, server_factory=None, environ={})
        self.bpy = bpy
        self.source = Path(source)
        # The launcher's identity of the source from before the child started (``--source-identity``), so a write
        # during the open (an add-on's handler) is a change, never the baseline (break-it review, 2026-10-23).
        self.source_identity = (tuple(source_identity) if source_identity is not None
                                else headless_files.identity(self.source))
        self.output = self.output_identity = None
        # The file Blender has open (headless_working_copy): a copy of the source next to it, never the source
        # unless its folder is read-only; it becomes the output with the first saved edit.
        self.working = Path(working) if working is not None else self.source
        self.working_identity = headless_files.identity(self.working)
        self.file_note = None   # headless_main: the file the app opened, the copy and the preferences, in words.
        self.app, self.levels = app, tuple(levels)
        self.client = ClientInfo(label, Peer(uid=os.getuid(), pid=os.getppid(), checked=True), frozenset(levels),
                                 headless=True)
        self.channel = None
        self.wake = threading.Event()
        self.last_call = time.monotonic()
        self.ended = False
        self._answers = queue.Queue()     # (key, tool, replayed, request, value, respond) awaiting the main thread
        self.staging = None               # Whether a native program is staging (native_programs; tests replace it)
        self._notes = {}

    # ExternalHost, for a pipe instead of a listener ----------------------------------------------------------------

    def wanted(self):
        return True, ""

    def record_access(self):
        """Nothing to record: the launcher already checked the person's switch for this call; the child's state
        folder is its own private one."""

    def reload_apps(self, force=False):
        self._apps = {self.client.label: {"app": self.app, "levels": self.levels, "connected_at": None}}
        self._app_keys = {}

    def start(self):
        self.mapping = ClientRegistry(self.env.state_directory()).load()
        self.service = RequestService(lambda: self._snapshot, work=WakingQueue(self.wake))
        self.reload_apps()
        self.server, self.status = self.channel, LISTENING
        self.publish()
        return True

    def handle_request(self, request, client):
        """Pipe thread: one launcher request, answered as the listener's service answers it, plus the file record."""
        self.last_call = time.monotonic()
        if request.get("command") == "headless":
            from . import external_cli_protocol as protocol
            return protocol.envelope("ok", "Headless file", headless_file=self.file_record())
        service = self.service
        if service is None:
            from . import external_cli_protocol as protocol
            return protocol.envelope("unavailable", "The headless Angelo is starting")
        envelope = service.handle(request, client)
        envelope["headless_file"] = self.file_record()
        return envelope

    def file_record(self):
        """What the launcher shows and restarts from (attribute reads only: any thread)."""
        output = self.output
        identity = self.output_identity
        working, working_identity = self.working, self.working_identity
        return {"source": str(self.source), "output": str(output) if output else None,
                "output_identity": list(identity) if output and identity else None,
                "working": str(working) if working != self.source else None,
                "working_identity": list(working_identity) if working != self.source and working_identity else None,
                "changed_on_disk": self.disk_problem() is not None}

    # Calls ---------------------------------------------------------------------------------------------------------

    def session_file_open(self):
        """Whether Blender's open file is this session's own: the source, or the output its edits are saved to
        (a resumed child opens the output). A script that opens, reads home or saves as another file changes it."""
        current = getattr(self.bpy.data, "filepath", "") or ""
        own = {os.path.realpath(self.working)} | ({os.path.realpath(self.output)} if self.output else set())
        return bool(current) and os.path.realpath(current) in own

    def disk_problem(self):
        if headless_files.identity(self.source) != self.source_identity:
            return ("stale: {name} changed on disk since it was opened. Call angelo_open_file again to load it"
                    .format(name=self.source.name) + (" (edits so far are in %s)" % self.output if self.output else "")
                    + ".")
        if self.output is not None and headless_files.identity(self.output) != self.output_identity:
            return ("stale: {name}, where this session's edits are saved, changed on disk. Open it (or the source) "
                    "again with angelo_open_file.".format(name=self.output.name))
        return None

    def handle_app_call(self, work):
        payload = work.payload
        if not self.session_file_open():
            self.reload()   # Main thread: the host owns the document, its own file again before anything runs.
        problem = self.disk_problem()
        if problem:
            return self._reply(work, None, "stale", problem)
        if payload["tool"] in SAVES:
            arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
            if payload["tool"] == SCRIPT_FILE_TOOL and isinstance(arguments.get("file"), str):   # Before admission
                self.__dict__.setdefault("named_scripts", set()).add(arguments["file"])   # captures roots.
            if arguments.get("mode") == "PREVIEW":
                return self._reply(work, None, "refused", PREVIEW_NEEDS_GUI, result={"excluded": True,
                                   "reason": PREVIEW_NEEDS_GUI, "needs_gui": True})
            if self.output is None and not headless_files.folder_writable(self.source):
                text = ("output_unwritable: Angelo cannot write a new file in {folder}, so edits cannot be saved; "
                        "nothing ran.".format(folder=self.source.parent))
                return self._reply(work, None, "refused", text, result={"excluded": True, "reason": text,
                                                                        "code": "output_unwritable"})
        return super().handle_app_call(work)

    def responder(self, work, fields, replayed=False, current=None, tool=None):
        respond = super().responder(work, fields, replayed=replayed)
        key = (work.client.label, work.payload.get("id"))

        def deliver(value):   # The call's worker thread: saving waits for the main thread.
            self._answers.put((key, tool, replayed, current, value, respond))
            self.wake.set()
        return deliver

    def finish_answers(self):
        """Main thread: save (or reload) after each answered edit, then send its answer."""
        while True:
            try:
                key, tool, replayed, current, value, respond = self._answers.get_nowait()
            except queue.Empty:
                return
            if replayed:
                note = self._notes.get(key)
            else:
                note = self.after_call(tool, current, value)
                if note:
                    self._notes[key] = note
                    while len(self._notes) > NOTES_KEPT:
                        self._notes.pop(next(iter(self._notes)))
            respond(_with_note(value, note))

    def after_call(self, tool, current, value):
        if tool not in SAVES:
            # Any call: a write to the source meanwhile is said, never left for the next call to find.
            if headless_files.identity(self.source) != self.source_identity:
                return SOURCE_CHANGED.format(source=self.source.name)
            return self.file_note if tool == "angelo_blend_file" else None   # Which file and preferences (L3).
        if not self.session_file_open():
            # A script opened or saved as another file (break-it review M2): nothing of it is saved as this
            # session's output, and the session's own file is loaded again.
            opened = getattr(self.bpy.data, "filepath", "") or "an empty file"
            self.reload()
            return FOREIGN_FILE.format(opened=opened, name=(self.output or self.source).name)
        if isinstance(value, dict) and value.get("isError"):
            if nothing_ran(value):
                return None   # Refused before anything ran (needs_gui): nothing to reload.
            # A failed script may leave a change Undo could not restore (review N2): always the last saved state
            # again, so a partial change is never saved by a later edit.
            if tool == SCRIPT_FILE_TOOL or (current is not None and current.direct.closed is not None):
                self.reload()
                return RELOADED.format(name=(self.output or self.source).name)
            return None
        staging = self.staging
        if staging is None:
            from . import native_programs
            staging = getattr(native_programs, "runtime_is_staging", native_programs.has_active_programs)
        if staging():
            return "Staged, not saved: a program is still staging; it is saved once it commits."
        try:
            path = self.save()
        except (headless_files.FileProblem, RuntimeError, OSError) as error:
            code = getattr(error, "code", "output_unwritable")
            self.reload()
            return ("%s: the edit could not be saved (%s), so Angelo reloaded the last saved state. Observe the scene "
                    "again." % (code, error))
        if headless_files.identity(self.source) != self.source_identity:
            # The run itself wrote the source (a script's save_mainfile): say so, never "unchanged" (review L1).
            return SOURCE_WRITTEN.format(path=path, source=self.source.name)
        return SAVED.format(path=path, source=self.source.name)

    def save(self):
        """Save the session to its output: a newly claimed file the first time (or after it changed on disk).

        Blender writes ``<target>@`` and renames it over the target, following any link at that predictable name;
        in a folder others can write, that could rewrite the source (found by the break-it review). So Blender only
        ever writes into a private folder in this user's own temporary folder, and ``headless_files.place`` puts
        the finished bytes next to the source through a file it creates exclusively, then a rename. Paths are saved
        as they are (``relative_remap=False``): the output sits in the source's folder, so its ``//`` paths stay
        right, and the session's own file path stays the source's.
        """
        bpy = self.bpy
        target = self.output
        if target is None and self.working != self.source and (headless_files.identity(self.working)
                                                                == self.working_identity):
            target, self.output_identity = self.working, self.working_identity   # The open copy becomes the output.
        elif target is None or headless_files.identity(target) != self.output_identity:
            target = headless_files.claim_output(self.source)
        private = private_roots.allocate("angelo-save-")
        try:
            staged = private / target.name
            paths = getattr(getattr(getattr(bpy, "context", None), "preferences", None), "filepaths", None)
            backups = getattr(paths, "save_version", None)
            try:
                if backups is not None:   # No .blend1 of the host's own copy; the person's value for other saves.
                    paths.save_version = 0
                result = bpy.ops.wm.save_as_mainfile(filepath=str(staged), copy=True, relative_remap=False,
                                                     check_existing=False)
            except RuntimeError:
                result = {"CANCELLED"}
            finally:
                if backups is not None:
                    paths.save_version = backups
            if "FINISHED" not in result or not staged.is_file():
                raise RuntimeError("Blender could not save " + target.name)
            if target == self.output and headless_files.identity(target) != self.output_identity:
                target = headless_files.claim_output(self.source)   # Changed meanwhile: never replaced.
            if target.resolve() == self.source.resolve():
                raise RuntimeError("Refusing to save over the source")
            headless_files.place(staged, target)
        except BaseException:
            if target != self.output:
                headless_files.remove_empty(target)
            raise
        finally:
            shutil.rmtree(private, ignore_errors=True)
        self.output, self.output_identity = target, headless_files.identity(target)
        if target == self.working:
            self.working_identity = self.output_identity
        # The document is now the session's saved state (a reload loads this output), so it reads as saved
        # (angelo_blend_file's unsaved_changes), as after a window's save; Blender's own save was a copy.
        program_undo.background_document_saved()
        self.publish()
        return self.output

    def track_in_process_save(self):
        """Angelo's own ``save_post`` handler: a plain save of the open copy in this process (one of the person's
        add-ons, or an approved script) is the session's own state, never a change on disk by someone else."""
        current = getattr(self.bpy.data, "filepath", "") or ""
        if self.working != self.source and current and os.path.realpath(current) == os.path.realpath(self.working):
            self.working_identity = headless_files.identity(self.working)
            if self.output == self.working:
                self.output_identity = self.working_identity

    def discard_working(self):
        """At the end: an unedited working copy is removed, so a read-only session leaves nothing behind."""
        if self.output is None and working_copy.discard(self.working, self.source, self.working_identity):
            self.working, self.working_identity = self.source, headless_files.identity(self.source)

    def reload(self):
        """Load the last saved state: the output while it is the file this session wrote, else the source."""
        if self.output is not None and headless_files.identity(self.output) == self.output_identity:
            self.bpy.ops.wm.open_mainfile(filepath=str(self.output), load_ui=False, use_scripts=False)
            self.working, self.working_identity = self.output, self.output_identity
            return
        self.output = self.output_identity = None
        self.discard_working()
        # A fresh copy of the source, never the source itself (headless_working_copy).
        self.working = working_copy.open_copy(self.bpy, self.source)
        self.working_identity = headless_files.identity(self.working)

    def refusal_text(self, controller, call):
        """Why ``call`` cannot run here, in the app's words (a script file names its reason and how to approve)."""
        script = getattr(call, "script_file", None)
        if script is None:
            return WAITS_FOR_PERSON
        reason = controller.script_file_refusal(call)
        return SCRIPT_FILE_REFUSALS.get(reason, SCRIPT_FILE_NEEDS_APPROVAL).format(file=script.path,
                                                                                   project=self.source)

    def refuse_waiting(self, controllers):
        """Finish at once every call that reached an approval card: no person can decide it here."""
        for controller in controllers:
            for call in list(controller.pending.values()):
                if call.approval and not call.automatic and not call.executing and not call.authorized:
                    controller.finish_tool(call, {"error_code": "NEEDS_GUI", "error": self.refusal_text(controller, call),
                                                  "scene_changed": False})

    def refuse_unauthorized(self, controller, call):
        """Before ``angelo.approve`` runs a script here: one its consent no longer covers (the person forgot the file,
        or changed a policy or mode, between the pump's check and now) is finished as ``needs_gui``. In the window,
        approve would open the review (``angelo.review_tool``), which only the window has; True when refused."""
        if call.name != "angelo_execute_python" or controller.script_authorized(call):
            return False
        controller.finish_tool(call, {"error_code": "NEEDS_GUI", "error": self.refusal_text(controller, call),
                                      "scene_changed": False})
        return True

    # The loop ------------------------------------------------------------------------------------------------------

    def run(self, ui, idle_exit=IDLE_EXIT):
        """Main thread until the launcher's pipe closes or the session is idle for ``idle_exit`` seconds."""
        from .blender_tools import BlenderExecutor
        from .controller import pump_controller_tools
        bpy, cursor = self.bpy, 0

        def apply_mutation(target, call):
            if self.refuse_unauthorized(target, call):
                return
            with bpy.context.temp_override(window=ui.main_window()):
                bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])

        def commit_mutation(target, call):
            with bpy.context.temp_override(window=ui.main_window()):
                return bpy.ops.angelo.commit_native_job('EXEC_DEFAULT', True, call_id=call.id,
                                                         session_id=target.state["id"])
        while not self.ended:
            self.pump()
            controllers = list(self.env.controllers().values())
            for controller in controllers:
                controller.pump_events()
            if controllers:
                cursor = pump_controller_tools(controllers, cursor, BlenderExecutor(ui.main_window()),
                                               ui.document_key(), apply_mutation, commit_mutation=commit_mutation)
                self.refuse_waiting(controllers)
            self.finish_answers()
            working = any(c.busy or c.pending for c in controllers) or not self._answers.empty()
            if not working and not self._work_in_flight() and time.monotonic() - self.last_call >= idle_exit:
                break
            if any(c.has_dispatchable_tools for c in controllers):
                continue
            self.wake.wait(BUSY_WAIT if working or self._work_in_flight() else IDLE_WAIT)
            self.wake.clear()
        self.close_apps("The headless session ended")
        self.discard_working()

    def _work_in_flight(self):
        return any(not work.finishable() for work in self._work.values() if not work.closed)

    def end(self):
        self.ended = True
        self.wake.set()


def _with_note(value, note):
    if not note or not isinstance(value, dict) or not isinstance(value.get("content"), list):
        return value
    return dict(value, content=[{"type": "text", "text": note}, *value["content"]])
