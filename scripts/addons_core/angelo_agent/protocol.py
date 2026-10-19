# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, dependency-free JSON-lines RPC transport. No Blender or auth logic."""

import json
import subprocess
import threading
from concurrent.futures import Future

from . import child_process


class RpcError(RuntimeError):
    pass


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
            raise RpcError("Agent process has disconnected")
        with self._write_lock:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

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
                        future.set_exception(RpcError("Agent process exited"))
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
