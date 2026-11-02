# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, dependency-free JSON-lines RPC transport. No Blender or auth logic."""

import json
import subprocess
import sys
import threading
from concurrent.futures import Future

from . import child_process


class RpcError(RuntimeError):
    pass


class TransportClosed(RpcError):
    """A write found the agent runtime's connection already ended (its process exited, its pipe or relay closed):
    nothing of that write reached the runtime. While no model round of a request has been written, the request has
    sent nothing, so ``Controller._run`` starts it once more on a fresh runtime (``unsent_loss``)."""


DISCONNECTED = "Agent process has disconnected"   # TransportClosed's words (cache_affinity matches them).


def ended(rpc):
    """Whether ``rpc``'s process has ended (its reader saw the end, or a write found it): read before any ``close``."""
    closed = getattr(rpc, "closed", None)
    return closed is not None and closed.is_set()


EXITED = "Agent process exited"   # A request whose answer never came: its process ended (``_read``).


def lost_runtime(error):
    """Whether ``error`` is the runtime's end itself (a write that found it ended, an answer it never gave, a check it
    exited during), never an answer of its own such as a refused sign-in: only such a failure starts again."""
    return (isinstance(error, TransportClosed) or str(error) in (DISCONNECTED, EXITED)
            or getattr(error, "verdict", None) == "exited")   # ``cache_affinity.CheckFailed(EXITED)``.


def transport_ended(rpc):
    """``ended`` for a provider's process or a lease of a shared one (``codex_runtime.Lease``: its runtime's)."""
    return ended(rpc) or ended(getattr(getattr(rpc, "runtime", None), "rpc", None))


def log(text):
    print(text, file=sys.stderr)   # English log lines, never shown.


class JsonRpcProcess:
    def __init__(self, command, *, env=None, cwd=None, on_event=None, on_request=None, spawn=child_process.popen):
        """``spawn``: ``cli_runtime.launch`` for a provider CLI (it also resolves Windows npm shims)."""
        self.process = spawn(
            command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
        )
        self.on_event = on_event or (lambda method, params: None)
        self.on_request = on_request or self._reject_request
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending = {}
        self._next_id = 0
        self.closed = threading.Event()
        self.stderr_tail = []
        self._reader = threading.Thread(target=self._read, daemon=True, name="AngeloRPC")
        self._logger = threading.Thread(target=self._read_stderr, daemon=True, name="AngeloRPCLogs")
        self._reader.start()
        self._logger.start()

    def write(self, message):
        if self.closed.is_set():
            raise TransportClosed(DISCONNECTED)
        line = json.dumps(message, ensure_ascii=False) + "\n"   # A content error is the caller's, raised as it is.
        with self._write_lock:
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
            except UnicodeError:
                raise   # Text the pipe cannot encode (a lone surrogate): the runtime is fine, nothing was written.
            except (OSError, ValueError) as error:
                if isinstance(error, ValueError) and not getattr(self.process.stdin, "closed", False):
                    raise
                raise TransportClosed(DISCONNECTED) from error   # Its pipe (or a worker's relay) ended under the write.

    def request(self, method, params=None, timeout=60):
        future = Future()
        with self._pending_lock:
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = future
        try:
            self.write({"id": request_id, "method": method, "params": params or {}})
            return future.result(timeout=timeout)
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notify(self, method, params=None):
        self.write({"method": method, "params": params or {}})

    def respond(self, request_id, result):
        self.write({"id": request_id, "result": result})

    def _reject_request(self, request_id, method, params):
        self.write({"id": request_id, "error": {"code": -32601, "message": "Unsupported host request"}})

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    try:
                        if "id" in message:
                            self.on_request(message["id"], message["method"], message.get("params", {}))
                        else:
                            self.on_event(message["method"], message.get("params", {}))
                    except Exception:
                        if "id" in message:
                            self._reject_request(message["id"], message["method"], {})
                elif "id" in message:
                    with self._pending_lock:
                        future = self._pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RpcError(str(message["error"].get("message", "RPC failed"))))
                        else:
                            future.set_result(message.get("result"))
        finally:
            self.closed.set()
            with self._pending_lock:
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(RpcError(EXITED))
            self.on_event("host/disconnected", {})

    def _read_stderr(self):
        # Never forward arbitrary process logs to the UI or persisted transcript.
        for line in self.process.stderr:
            self.stderr_tail.append(line.rstrip()[:1000])
            self.stderr_tail[:] = self.stderr_tail[-12:]

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        self.closed.set()
        # Reap pipes after EOF. Never join the callback's own reader thread.
        for reader in (self._reader, self._logger):
            if reader is not threading.current_thread():
                reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream and not stream.closed:
                stream.close()
