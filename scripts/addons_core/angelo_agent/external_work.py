# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connected MCP app's tool calls, run as Angelo's own direct-tools calls are (tool level).

An app's calls go through exactly the per-call host code that Claude's
direct-tools route uses (``claude_direct.DirectRequest``): exact validation with
the schemas back on a rejection, ``run_operations`` (visual evidence,
completions, self-check notes), kept views refreshed after an edit, the plan
route's result presentation and short identifiers. Approval, Undo, journaling,
invocation guards, receipts and script consent are ``Controller.request_tool``'s,
unchanged. Only the caller differs: a connected app instead of a provider turn.

One ``ExternalWork`` is one request in the app's External conversation
(``Controller.open_external_work``): it opens at the app's first call, takes the
person's snapshot before its first approved edit, and closes once the app has
been quiet for ``IDLE_SECONDS`` with nothing in flight, when the person presses
Stop, when a call ends it (a decline, a failed edit that may have changed the
scene: ``DirectRequest``'s rules) or when the document changes. The next call
opens a new request. At the close, the transcript gets the host's verified
account of the edits that succeeded, as a reply-less direct-tools turn does.

The calls themselves run on worker threads (``run_operations`` waits for the
main thread to dispatch each native call); opening, closing and every Blender
effect stay on the main thread through ``request_tool`` and ``pump_tools``.
"""

import threading
import time
import uuid

from . import identifiers, script_files
from .claude_direct import DirectRequest, _error
from .external_clients import SCRIPT_FILE_TOOL
from .claude_transport import ClaudeRequest
from .self_checks import SelfChecks
from .tool_deadlines import await_native
from .protocol import RpcError
from .tool_specs import SCRIPT_DESCRIPTION_LIMIT

# A model thinks between calls (tens of seconds on a hard step); a request stays open across that.
IDLE_SECONDS = 120.0
STOPPED = "The person stopped this in Angelo; this call did not run. Ask them before continuing."
DOCUMENT_CHANGED = ("The open Blender file changed in Angelo; this call did not run. Observe the scene again: handles "
                    "from before do not work in the new file.")


class ExternalWork:
    """One open request of an app's calls; the provider stand-in ``DirectRequest`` calls into (module docstring)."""

    def __init__(self, controller, label, app_name, allowed):
        self.controller, self.label = controller, label
        if getattr(controller, "_identifiers_route", None) != "external":
            # The app sees short handles (o<n>), as Angelo's own agent does: the tool schemas it reads are those of
            # the direct-tools route. In memory only; after a restart old handles are unknown and the app observes.
            controller._identifiers, controller._identifiers_route = identifiers.IdentifierTable(), "external"
        self.fence = controller.open_external_work(app_name + " is using Angelo's tools.", label)
        context = controller.runtime_context()
        context.allowed_tools = frozenset(allowed)
        self.context, self.owner = context, ClaudeRequest(context)
        self.direct = DirectRequest(self, context, self.owner, SelfChecks())
        self.last_activity = time.monotonic()
        self.closed = False
        self._script_file = threading.local()   # The ScriptFile an angelo_run_script_file call is running, per worker.

    # What DirectRequest calls on its provider ----------------------------------------------------------

    def _current(self, owner, transport=None):
        return not self.closed and owner.owns_request()

    def _native_call(self, name, arguments, *, owner):
        """One native call through the request's own context, waited for as a provider worker waits."""
        unavailable = {"error": "This request no longer owns native dispatch; inspect before retrying"}
        if not self._current(owner):
            return unavailable
        done, answer = threading.Event(), []

        def receive(result):
            answer.append(result if self._current(owner) else unavailable)
            done.set()
        call_id = "external-call/" + str(uuid.uuid4())
        script = getattr(self._script_file, "current", None)
        if (script is not None and name == "angelo_execute_python" and isinstance(arguments, dict)
                and arguments.get("code") == script.text):
            # Host-only: the person's approval of this file is checked against what Angelo read (trusted_scripts).
            owner.controller.request_tool(call_id, name, arguments, receive, script_file=script)
        else:
            owner.controller.request_tool(call_id, name, arguments, receive)
        if not await_native(done, owner.controller, call_id, name):
            raise RpcError("Native Blender approval/dispatch timed out")
        return answer[0] if self._current(owner) else unavailable

    def _emit(self, owner, kind, data):
        if self._current(owner):
            owner.controller.emit(kind, data)

    # The host's side (main thread) -------------------------------------------------------------------

    def usable(self):
        """Whether a new call may join this request (else the host closes it and opens another)."""
        controller = self.controller
        return (not self.closed and self.direct.closed is None and self.fence.matches(controller)
                and not controller.cancelled and not controller.document_invalid)

    def submit(self, key, name, arguments, respond):
        """Run one call (main thread admits, a worker runs it); ``respond`` gets its MCP result exactly once.

        ``key`` identifies the call: a delivery of a call this request has had already gets that call's result and
        never runs it again (``DirectRequest.admit``).
        """
        self.last_activity = time.monotonic()
        if not self.direct.admit(key, respond):
            return
        ticket = self.direct.arrived()

        def work():
            try:
                if name == SCRIPT_FILE_TOOL:
                    value = self._script_file_call(arguments, ticket)
                else:
                    value = self.direct.call(name, arguments, ticket)
            except Exception as error:  # noqa: BLE001 - every call is answered; the error is the result.
                value = _error(self.direct.why(error))
            finally:
                self.direct.left()
                self.last_activity = time.monotonic()
            self.direct.answered(key, self._explained(value))
        threading.Thread(target=work, daemon=True, name="AngeloExternalCall").start()

    def _script_file_call(self, arguments, ticket):
        """``angelo_run_script_file``: read the file once (``script_files.read_script``) and run exactly that text as
        an ``angelo_execute_python`` call through the same host code; the call carries what was read, so the
        person's approval of the file is checked against it at admission and again at dispatch."""
        if (not isinstance(arguments, dict) or set(arguments) - {"file", "description"}
                or not isinstance(arguments.get("file"), str)):
            return _error('angelo_run_script_file takes {"file": the absolute path, "description": optional text}; '
                          "nothing ran.")
        description = arguments.get("description")
        if description is not None and (not isinstance(description, str) or not description.strip()):
            return _error("description must be non-empty text; nothing ran.")
        try:
            script = script_files.read_script(arguments["file"])
        except script_files.ScriptFileError as error:
            return _error("%s; nothing ran." % str(error).rstrip("."))
        label = (description or script.name).strip()[:SCRIPT_DESCRIPTION_LIMIT]
        self._script_file.current = script
        try:
            return self.direct.call("angelo_execute_python", {"description": label, "code": script.text}, ticket)
        finally:
            self._script_file.current = None

    def _explained(self, value):
        """A call that did not run because of the person or the file says so in the app's words."""
        controller = self.controller
        if isinstance(value, dict) and value.get("isError") and not self.owner.owns_request():
            if controller.document_invalid:
                return _error(DOCUMENT_CHANGED)
            if controller.cancelled:
                return _error(STOPPED)
        return value

    def idle(self, now=None):
        now = time.monotonic() if now is None else now
        return not self.direct.busy() and now - self.last_activity >= IDLE_SECONDS

    def finishable(self):
        """Nothing of this request is still running or waiting in Angelo."""
        return not self.direct.busy() and not self.context.tools_pending()

    def close(self, why="the app finished"):
        """Main thread: end the request; the transcript gets the verified account of its edits."""
        if self.closed:
            return
        from .codex_plan import emit_summary
        calls, completions = self.direct.edits()
        if calls and self.owner.owns_request():
            emit_summary(self.context, "", calls, emit_uncertified=True, completions=completions)
        self.direct.close(why)
        self.closed = True
        self.controller.close_external_read(self.fence)
