# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One model runtime child Angelo runs for a worker chat, and the relay that carries its stdio (``runtime_service``).

The child is Angelo's: its command, environment and credentials never leave this process, it runs in its own
process group (a Job Object on Windows, ``child_process``) and ``kill`` ends its whole tree. The relay is a
connection of its own to the worker (a socket pair, or a pair of named pipes on Windows: ``worker_channel``) whose
frames are::

    Angelo -> worker   {"line": "<one line of the child's stdout>"} ... then {"exit": <return code>} once
    worker -> Angelo   {"line": "<one line for the child's stdin>"}, {"close": true} (its stdin ends)

Every worker line passes ``runtime_filter.Filter.inbound`` first; a refused one is answered on the relay and logged,
never written (a refused answer to the child's own request is answered to the child by Angelo instead). Every child
line passes ``outbound``: a request Angelo answers itself (``runtime_requests``: approvals declined) is answered here
and never reaches the worker; any other line goes with its secrets replaced. The child's stderr stays here: the last
``STDERR_LINES`` lines, as ``protocol.JsonRpcProcess`` keeps them. The relay closing (the worker ended or let go)
ends the child; the child ending sends ``exit`` and closes the relay.

Standard library only and no ``bpy``.
"""

import json
import os
import signal
import subprocess
import threading

from . import external_cli_protocol as protocol
from .headless_pipe import PipeClosed
from .runtime_filter import DENIED

STDERR_LINES = 12
EARLY_LINES = 1000      # Lines the child may write before its relay opens (a prewarm, a sign-in); more ends it.
HANDSHAKE_SECONDS = 60.0


class ChildFailed(RuntimeError):
    """The child ended, or did not answer Angelo's own request, before its relay opened."""


class RuntimeChild:
    def __init__(self, process, flt, session, key=None, log=None, group=None):
        """``group``: True when the child leads its own POSIX process group (``start_new_session``)."""
        self.process, self.filter, self.session, self.key = process, flt, session, key
        self.binding = None   # ``runtime_bindings.binding`` of the chat it started for, set by its service.
        self.log = log or (lambda text: None)
        self.group = (os.name != "nt") if group is None else group
        self.stderr_tail = []
        self.ended = threading.Event()       # The child's stdout ended (it exited, or was killed).
        self.relay = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._early = []                     # Child lines before the relay opened.
        self._waiters = {}                   # Angelo's own request ids -> [event, message]
        self._next = 0
        self._exit_sent = False
        threading.Thread(target=self._read_stdout, daemon=True, name="AngeloRelayOut").start()
        threading.Thread(target=self._read_stderr, daemon=True, name="AngeloRelayLogs").start()

    @property
    def pid(self):
        return self.process.pid

    def alive(self):
        return not self.ended.is_set() and self.process.poll() is None

    # Angelo's own requests, before the relay opens (Codex: initialize and an API-key sign-in) ------------------

    def request(self, method, params=None, timeout=HANDSHAKE_SECONDS):
        with self._lock:
            self._next += 1
            identity = "angelo-lead-%d" % self._next
            slot = self._waiters[identity] = [threading.Event(), None]
        try:
            self._write(json.dumps({"id": identity, "method": method, "params": params or {}}, ensure_ascii=False))
            if not slot[0].wait(timeout) or slot[1] is None:
                raise ChildFailed("The model runtime did not answer %s" % method)
        finally:
            with self._lock:
                self._waiters.pop(identity, None)
        if "error" in slot[1]:
            error = slot[1]["error"]
            raise ChildFailed(str(error.get("message", "%s failed" % method)) if isinstance(error, dict) else method)
        return slot[1].get("result")

    def notify(self, method, params=None):
        self._write(json.dumps({"method": method, "params": params or {}}))

    # The relay ---------------------------------------------------------------------------------------------------

    def attach(self, endpoint):
        """Open the relay: the lines written so far, then every next one, go to the worker."""
        with self._lock:   # Held while the early lines go: a later line, or the exit, follows them.
            self.relay = endpoint
            for line in self._early:
                self._forward(line)
            self._early = []
        threading.Thread(target=self._read_relay, daemon=True, name="AngeloRelayIn").start()
        if self.ended.is_set():
            self._send_exit()

    def _forward(self, line):
        routed = self.filter.outbound(line)
        if routed.answer is not None:   # Angelo's own answer (an approval declined): never relayed.
            self._write(routed.answer)
            return
        try:
            self.relay.send({"line": routed.line})
        except (OSError, ValueError, protocol.FrameError) as error:
            self.log("Angelo: a model runtime line could not reach its worker (%s); the runtime is stopped" % error)
            self.kill()

    def _read_relay(self):
        try:
            while True:
                frame = self.relay.receive()
                if frame.get("close") is True:
                    self._close_stdin()
                    continue
                line = frame.get("line")
                if not isinstance(line, str):
                    raise protocol.FrameError("Expected {line} or {close}")
                verdict = self.filter.inbound(line)
                if verdict.passed:
                    self._write(verdict.line)
                    continue
                if verdict.child is not None:   # The worker's answer did not fit: the child gets Angelo's refusal.
                    self._write(verdict.child)
                if verdict.reply is not None:
                    self.relay.send({"line": verdict.reply})
                if verdict.reason != "initialized here":
                    self.log("Angelo: refused a worker's line to its model runtime (%s)" % verdict.reason)
                    self.relay.send({"notice": "%s (%s)" % (DENIED, verdict.reason)})
        except (PipeClosed, protocol.FrameError, OSError, ValueError):
            pass
        finally:
            self.kill()   # The relay closed (the worker ended or let go) or broke: the child ends with it.

    # The child's streams -----------------------------------------------------------------------------------------

    def _write(self, line):
        with self._write_lock:
            try:
                self.process.stdin.write(line.rstrip("\n") + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass   # It ended: its stdout's end says so.

    def _close_stdin(self):
        with self._write_lock:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                self._route(line.rstrip("\n"))
        except (OSError, ValueError):
            pass
        finally:
            self.ended.set()
            with self._lock:
                waiting = list(self._waiters.values())
            for slot in waiting:
                slot[0].set()
            with self._lock:
                attached = self.relay is not None
            if attached:
                self._send_exit()

    def _route(self, line):
        with self._lock:
            if self._waiters:
                try:
                    message = json.loads(line)
                except ValueError:
                    message = None
                slot = self._waiters.get(message.get("id")) if isinstance(message, dict) else None
                if slot is not None:
                    slot[1] = message
                    slot[0].set()
                    return
            if self.relay is None:
                self._early.append(line)
                overflow = len(self._early) > EARLY_LINES
            else:
                overflow = None
        if overflow is None:
            self._forward(line)
        elif overflow:
            self.log("Angelo: a model runtime wrote too much before its worker connected; it is stopped")
            self.kill()

    def _read_stderr(self):
        try:
            for line in self.process.stderr:   # Never forwarded: prompts, errors and paths stay in Angelo.
                self.stderr_tail.append(line.rstrip()[:1000])
                self.stderr_tail[:] = self.stderr_tail[-STDERR_LINES:]
        except (OSError, ValueError):
            pass

    def _send_exit(self):
        with self._lock:
            if self._exit_sent:
                return
            self._exit_sent = True
        try:
            code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.kill()
            code = self.process.wait()
        try:
            self.relay.send({"exit": code})
        except (OSError, ValueError, protocol.FrameError):
            pass
        self.relay.close()

    # Ending ------------------------------------------------------------------------------------------------------

    def kill(self):
        """End the child's whole tree at once (its group on POSIX, its job on Windows) and close its relay."""
        if self.process.poll() is None:
            try:
                if self.group:
                    os.killpg(self.process.pid, signal.SIGKILL)
                else:
                    self.process.kill()
            except (OSError, ProcessLookupError):
                pass
        relay = self.relay
        if relay is not None:   # ``_send_exit`` closes it after the exit; one whose child never ends closes anyway.
            timer = threading.Timer(10.0, relay.close)
            timer.daemon = True
            timer.start()
