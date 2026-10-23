# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The headless child's transport: two anonymous pipes it inherits from the MCP launcher that started it.

There is no socket, no file-system name and no port: only the launcher holds the other ends, so nothing else can
connect, and the child sees end-of-file (and exits) when the launcher closes them or dies. Frames are the CLI's
(``external_cli_protocol``: a 4-byte big-endian length and one JSON object, finite numbers only), each carrying a
sequence number so the launcher's concurrent MCP calls share the pair::

    launcher -> child   {"seq": n, "request": <CLI request>}
    child -> launcher   {"seq": n, "response": <CLI envelope>}     ({"seq": 0, "ready": {...}} once, first)

Pure standard library and no ``bpy``: the launcher runs it in Angelo's bundled Python, the child inside Angelo
(doc/angelo/external_mcp.rst, "Headless").
"""

import json
import os
import struct
import subprocess
import threading
import time

from . import external_cli_protocol as protocol

MAX_HANDLERS = 8          # Requests the child answers at once (the CLI listener's connection limit).
READY_TIMEOUT = 120.0     # Loading a large .blend can take a while; a child that never says ready is stopped.
EXIT_GRACE = 5.0          # After the launcher closes the pipes, how long the child has to exit by itself.


class PipeClosed(Exception):
    """The other end closed the pipe between frames."""


def read_frame(stream, limit):
    """One frame from a binary stream; ``PipeClosed`` at a clean end of file, ``FrameError`` for anything else."""
    head = _read_exact(stream, 4, allow_end=True)
    (size,) = struct.unpack(">I", head)
    if size == 0 or size > limit:
        raise protocol.FrameError(f"The frame length {size} is outside 1-{limit} bytes")
    payload = _read_exact(stream, size)
    try:
        value = json.loads(payload.decode("utf-8"), parse_constant=protocol._reject_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise protocol.FrameError("The frame is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise protocol.FrameError("A frame carries one JSON object")
    return value


def _read_exact(stream, size, allow_end=False):
    chunks, remaining = [], size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            if allow_end and remaining == size:
                raise PipeClosed()
            raise protocol.FrameError("The pipe closed inside a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class Writer:
    """Whole frames to one stream, one writer at a time."""

    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit
        self._lock = threading.Lock()

    def send(self, value):
        data = protocol.encode_frame(value, self.limit)
        with self._lock:
            self.stream.write(data)
            self.stream.flush()


# The child's side ------------------------------------------------------------------------------------------------

class PipeServer:
    """Child: answers each request with ``handler(request, client)`` on a thread of its own (at most MAX_HANDLERS).

    ``on_closed`` runs once when the launcher's end closes or sends something that is not a frame; the host then
    exits. Requests over the limit are answered ``busy``, never queued without bound.
    """

    def __init__(self, read_fd, write_fd, handler, client, on_closed, on_request=None):
        self.reader = os.fdopen(read_fd, "rb", buffering=0)
        self.writer = Writer(os.fdopen(write_fd, "wb", buffering=0), protocol.MAX_RESPONSE_BYTES)
        self.handler, self.client, self.on_closed, self.on_request = handler, client, on_closed, on_request
        self.instance = os.urandom(8).hex()
        self._slots = threading.BoundedSemaphore(MAX_HANDLERS)
        self._thread = None
        self.closed = False

    def ready(self, value):
        self.writer.send({"seq": 0, "ready": value})

    def start(self):
        self._thread = threading.Thread(target=self._read, daemon=True, name="AngeloHeadlessPipe")
        self._thread.start()

    def stop(self):
        self.closed = True

    def _read(self):
        try:
            while not self.closed:
                frame = read_frame(self.reader, protocol.MAX_REQUEST_BYTES)
                seq, request = frame.get("seq"), frame.get("request")
                if type(seq) is not int or seq < 1 or not isinstance(request, dict):
                    raise protocol.FrameError("Expected {seq, request}")
                if self.on_request is not None:
                    self.on_request()
                if not self._slots.acquire(blocking=False):
                    self._send(seq, protocol.envelope("busy", "Too many calls at once; retry shortly"))
                    continue
                threading.Thread(target=self._answer, args=(seq, request), daemon=True,
                                 name="AngeloHeadlessRequest").start()
        except (PipeClosed, protocol.FrameError, OSError, ValueError):
            pass
        finally:
            self.closed = True
            try:
                self.reader.close()
            except OSError:
                pass
            self.on_closed()

    def _answer(self, seq, request):
        try:
            try:
                response = self.handler(request, self.client)
            except Exception as error:  # noqa: BLE001 - every request is answered; the error is the answer.
                response = protocol.envelope("unavailable", "The headless Angelo failed: %s: %s"
                                             % (type(error).__name__, error))
            self._send(seq, response)
        finally:
            self._slots.release()

    def _send(self, seq, response):
        try:
            self.writer.send({"seq": seq, "response": response})
        except protocol.FrameError as error:
            self.writer.send({"seq": seq, "response": protocol.envelope(
                "tool_failed", "The result was larger than the pipe allows (%s); nothing was cut" % error)})
        except OSError:
            self.closed = True


# The launcher's side ---------------------------------------------------------------------------------------------

class ChildProcess:
    """Launcher: one headless child, its two pipes and the answers waiting for it.

    ``command`` is the child's argv with ``{in}``/``{out}`` where its pipe descriptors go. ``request`` returns the
    child's envelope, or an ``unavailable``/``busy`` envelope when it died or did not answer in time.
    """

    def __init__(self, command, environ, popen=subprocess.Popen, stderr=None):
        """``stderr``: a new file (owner-only, never through a link) the child's own output goes to, read back by
        ``last_output`` when it does not start; None: discarded."""
        self.stderr_path = stderr
        error = subprocess.DEVNULL
        if stderr is not None:
            error = os.open(stderr, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        to_child_read, to_child_write = os.pipe()
        from_child_read, from_child_write = os.pipe()
        # Only the arguments that are exactly ``{in}``/``{out}``: a file name holding those letters stays as it is.
        argv = [str(to_child_read) if part == "{in}" else str(from_child_write) if part == "{out}" else part
                for part in command]
        try:
            self.process = popen(argv, env=environ, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=error, pass_fds=(to_child_read, from_child_write),
                                 close_fds=True, start_new_session=True)
        finally:
            os.close(to_child_read)
            os.close(from_child_write)
            if stderr is not None:
                os.close(error)
        self.writer = Writer(os.fdopen(to_child_write, "wb", buffering=0), protocol.MAX_REQUEST_BYTES)
        self.reader = os.fdopen(from_child_read, "rb", buffering=0)
        self._lock = threading.Lock()
        self._waiting = {}          # seq -> [event, envelope]
        self._seq = 0
        self._ready = threading.Event()
        self.ready_value = None
        self.ended = threading.Event()
        threading.Thread(target=self._read, daemon=True, name="AngeloHeadlessChild").start()

    def last_output(self, lines=5, limit=4096):
        """The child's last ``lines`` lines of its own output (at most ``limit`` bytes read), or []."""
        if self.stderr_path is None:
            return []
        try:
            with open(self.stderr_path, "rb") as stream:
                stream.seek(max(0, os.fstat(stream.fileno()).st_size - limit))
                text = stream.read(limit).decode("utf-8", "replace")
        except OSError:
            return []
        return [line.strip()[:240] for line in text.splitlines() if line.strip()][-lines:]

    def wait_ready(self, timeout=READY_TIMEOUT):
        """The child's ready record ({"ok": bool, ...}), or None when it ended or never said ready."""
        deadline = time.monotonic() + timeout
        while not self._ready.wait(0.05):
            if self.ended.is_set() or time.monotonic() >= deadline:
                return None
        return self.ready_value

    def alive(self):
        return not self.ended.is_set() and self.process.poll() is None

    def request(self, request, timeout):
        with self._lock:
            if self.ended.is_set():
                return protocol.envelope("unavailable", "The headless Angelo has ended")
            self._seq += 1
            seq, slot = self._seq, [threading.Event(), None]
            self._waiting[seq] = slot
        try:
            self.writer.send({"seq": seq, "request": request})
        except (OSError, protocol.FrameError) as error:
            with self._lock:
                self._waiting.pop(seq, None)
            return protocol.envelope("unavailable", "Could not reach the headless Angelo (%s)" % error)
        if not slot[0].wait(timeout):
            with self._lock:
                self._waiting.pop(seq, None)
            return protocol.envelope("pending", "Still running in the headless Angelo")
        return slot[1]

    def _read(self):
        try:
            while True:
                frame = read_frame(self.reader, protocol.MAX_RESPONSE_BYTES)
                if frame.get("seq") == 0 and isinstance(frame.get("ready"), dict):
                    self.ready_value = frame["ready"]
                    self._ready.set()
                    continue
                with self._lock:
                    slot = self._waiting.pop(frame.get("seq"), None)
                if slot is not None and isinstance(frame.get("response"), dict):
                    slot[1] = frame["response"]
                    slot[0].set()
        except (PipeClosed, protocol.FrameError, OSError, ValueError):
            pass
        finally:
            self.ended.set()
            with self._lock:
                waiting, self._waiting = list(self._waiting.values()), {}
            for slot in waiting:
                # Sent, unanswered: whether it ran is unknown (cli_client marks a dropped socket the same way).
                slot[1] = protocol.envelope("unavailable", "The headless Angelo ended before it answered",
                                            call={"state": "detached"})
                slot[0].set()

    def close(self, grace=EXIT_GRACE):
        """Close our ends (the child exits on end of file); stop it by its own PID only if it does not."""
        for stream in (self.writer.stream, self.reader):
            try:
                stream.close()
            except OSError:
                pass
        try:
            self.process.wait(grace)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(grace)
