# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The local connection between Angelo and one of its worker Blenders (``worker_process``, ``worker_host``).

Nothing else can reach it:

* **macOS and Linux:** one Unix socket pair (``socket.socketpair``). The worker inherits its end as a descriptor, so
  the connection has no file-system name, port or path anyone could open or squat.
* **Windows:** two one-way named pipes, ``\\\\.\\pipe\\angelo-worker-<128 random bits>-{up,down}``, that Angelo creates
  before it starts the worker: ``FILE_FLAG_FIRST_PIPE_INSTANCE`` with one instance each (a name someone already holds
  fails the start), ``PIPE_REJECT_REMOTE_CLIENTS``, and a protected DACL that grants only the current user's SID.
  Angelo accepts the connection only from the worker's own process ID (``GetNamedPipeClientProcessId``), and the
  worker connects only to a server whose process ID is Angelo's (``GetNamedPipeServerProcessId``). Two one-way pipes,
  because synchronous I/O on one handle serializes a blocked read with a write from another thread.

Frames are the headless child's (``headless_pipe``: a 4-byte length and one JSON object, finite numbers only)::

    Angelo -> worker   {"seq": n, "request": {"op": ..., ...}}
    worker -> Angelo   {"seq": n, "response": {...}}          ({"seq": 0, "ready": {...}} once, first)
    worker -> Angelo   {"event": {"session", "kind", "data", "t"}}   (unsolicited, no seq: a hosted chat's, A′ C3)

A worker's own requests to Angelo (``runtime_service``: a model runtime's launch, probe or kill) are numbered apart::

    worker -> Angelo   {"rseq": n, "request": {"op": ..., ...}}
    Angelo -> worker   {"rseq": n, "response": {...}}

On a socket pair an answer may carry one descriptor (``SCM_RIGHTS``): the worker's end of a new relay pair, which
then has no name either. A descriptor that arrives with any other frame is closed at once.

Standard library only and no ``bpy``.
"""

import array
import os
import secrets
import socket
import sys
import threading

from . import external_cli_protocol as protocol
from .headless_pipe import PipeClosed, Writer, read_frame

REQUEST_LIMIT = protocol.MAX_REQUEST_BYTES     # 1 MiB: a program, its read set and the chat's observed handles
RESPONSE_LIMIT = protocol.MAX_RESPONSE_BYTES   # 8 MiB: a result and its recovery evidence
CONNECT_TIMEOUT = 60.0                         # A worker that never connects is stopped (worker_process).
REVERSE_HANDLERS = 4                           # A worker's requests Angelo answers at once; more are refused busy.


class ChannelError(RuntimeError):
    """The connection could not be made or was refused."""


class Endpoint:
    """One side's two streams: frames in, whole frames out (one writer at a time)."""

    def __init__(self, reader, writer, read_limit, write_limit, closers=()):
        self.reader, self.writer = reader, Writer(writer, write_limit)
        self.read_limit = read_limit
        self._closers = tuple(closers)
        self.closed = False
        self.reverse = None   # The worker's ``ReverseCaller``: it takes the answers to its own requests.

    def send_within(self, value, timeout):
        """``send``, bounded: a frame the other side does not take within ``timeout`` (it stopped reading) ends the
        connection, which unblocks the write; ``ChannelError`` then."""
        done, failure = threading.Event(), []

        def write():
            try:
                self.send(value)
            except BaseException as error:  # noqa: BLE001 - re-raised on the caller's thread.
                failure.append(error)
            finally:
                done.set()
        threading.Thread(target=write, daemon=True, name="AngeloWorkerSend").start()
        if not done.wait(timeout):
            # Closed off this thread: on Windows closing waits on the very write it cuts short (break-it N4); the
            # owner stops the worker, which ends that write.
            self.closed_by_timeout = True
            threading.Thread(target=self.close, daemon=True, name="AngeloWorkerSendClose").start()
            raise ChannelError("The worker did not take the request within %d s" % timeout)
        if failure:
            raise failure[0]

    def receive(self):
        """The next frame; ``PipeClosed`` at a clean end, ``protocol.FrameError`` for anything else.

        With a ``reverse`` caller, the answers to the worker's own requests go to it and are never returned.
        """
        while True:
            try:
                frame = read_frame(self.reader, self.read_limit)
            except BaseException:
                if self.reverse is not None:
                    self.reverse.end()
                raise
            handles = self.reader.take() if hasattr(self.reader, "take") else []
            if self.reverse is not None and "rseq" in frame:
                self.reverse.route(frame, handles)
                continue
            close_handles(handles)
            return frame

    def send(self, value, handle=None):
        """One frame; ``handle``: a descriptor passed with it (socket pairs only), still the caller's to close."""
        if handle is None:
            self.writer.send(value)
        elif isinstance(self.writer, SocketWriter):
            self.writer.send(value, handle)
        else:
            raise ChannelError("This connection cannot pass a descriptor")

    def close(self):
        if self.closed:
            return
        self.closed = True
        for close in (*self._closers[:1], self.writer.stream.close, self.reader.close, *self._closers[1:]):
            try:
                close()
            except OSError:
                pass


# macOS and Linux --------------------------------------------------------------------------------------------------

class SocketPairListener:
    """Angelo's side before the worker starts: the pair, and the worker's end to pass as a descriptor."""

    def __init__(self):
        # Neither end is inheritable: ``pass_fds`` hands the worker's end to the worker alone, so no other child this
        # process starts meanwhile can hold a copy (break-it L2).
        self._ours, self._theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self._ours.set_inheritable(False)
        self._theirs.set_inheritable(False)

    def child_argument(self):
        return "fd:%d" % self._theirs.fileno()

    def pass_fds(self):
        return (self._theirs.fileno(),)

    def started(self):
        """After the worker started: Angelo keeps only its own end."""
        self._theirs.close()

    def accept(self, process_id, timeout=CONNECT_TIMEOUT, alive=None):
        del process_id, timeout, alive   # Connected from birth; only the started worker holds the other end.
        return socket_endpoint(self._ours, RESPONSE_LIMIT, REQUEST_LIMIT)

    def close(self):
        for sock in (self._ours, self._theirs):
            try:
                sock.close()
            except OSError:
                pass


def connect_socket(argument):
    """The worker's side of ``fd:N``: its inherited end of the pair."""
    sock = socket.socket(fileno=int(argument[3:]))
    sock.set_inheritable(False)
    return socket_endpoint(sock, REQUEST_LIMIT, RESPONSE_LIMIT)


def socket_endpoint(sock, read_limit, write_limit):
    def shut():   # Unblocks a read or write another thread is in, before the close.
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    endpoint = Endpoint(SocketReader(sock), sock.makefile("wb", buffering=0), read_limit, write_limit,
                        closers=(shut, sock.close))
    endpoint.writer = SocketWriter(sock, endpoint.writer.stream, write_limit)
    return endpoint


class SocketReader:
    """A socket's bytes, keeping the descriptors that arrive with them until the frame they came in is read."""

    HANDLES = 4   # At most this many descriptors per read; any more are cut off by the kernel (and closed).

    def __init__(self, sock):
        self.sock, self._handles = sock, []

    def read(self, size):
        data, ancillary, _flags, _address = self.sock.recvmsg(size, socket.CMSG_SPACE(self.HANDLES * 4))
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                found = array.array("i")
                found.frombytes(payload[:len(payload) - len(payload) % found.itemsize])
                self._handles.extend(found)
        return data

    def take(self):
        handles, self._handles = self._handles, []
        return handles

    def close(self):
        close_handles(self.take())   # The socket itself closes with the endpoint.


class SocketWriter(Writer):
    """``Writer`` that can pass one descriptor with a frame (``SCM_RIGHTS`` on its first bytes)."""

    def __init__(self, sock, stream, limit):
        super().__init__(stream, limit)
        self.sock = sock

    def send(self, value, handle=None):
        if handle is None:
            return super().send(value)
        data = protocol.encode_frame(value, self.limit)
        with self._lock:
            sent = self.sock.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [handle]))])
            if sent < len(data):
                self.sock.sendall(data[sent:])
        return None


def close_handles(handles):
    for handle in handles:
        try:
            os.close(handle)
        except OSError:
            pass


# Windows ----------------------------------------------------------------------------------------------------------

PIPE_PREFIX = "\\\\.\\pipe\\angelo-worker-"


class PipeListener:
    """Angelo's side on Windows: both pipes exist, with their security, before the worker starts."""

    def __init__(self, win32=None):
        from . import worker_pipes_windows
        self.win32 = win32 or worker_pipes_windows
        self.name = PIPE_PREFIX + secrets.token_hex(16)
        self._up = self.win32.create_server(self.name + "-up", inbound=True)        # worker -> Angelo
        try:
            self._down = self.win32.create_server(self.name + "-down", inbound=False)  # Angelo -> worker
        except BaseException:
            self.win32.close_handle(self._up)
            raise

    def child_argument(self):
        return "pipe:%s:%d" % (self.name, os.getpid())

    def pass_fds(self):
        return ()

    def started(self):
        pass

    def accept(self, process_id, timeout=CONNECT_TIMEOUT, alive=None, limits=(RESPONSE_LIMIT, REQUEST_LIMIT)):
        for handle in (self._up, self._down):
            self.win32.accept(handle, process_id, timeout, alive=alive)
        return Endpoint(self.win32.stream(self._up, "rb"), self.win32.stream(self._down, "wb"), *limits)

    def close(self):
        for handle in (self._up, self._down):
            self.win32.close_handle(handle)


def connect_pipe(argument, win32=None, limits=(REQUEST_LIMIT, RESPONSE_LIMIT)):
    """The worker's side of ``pipe:NAME:PID``: both pipes, only when Angelo's own process serves them."""
    from . import worker_pipes_windows
    win32 = win32 or worker_pipes_windows
    _prefix, rest = argument.split(":", 1)
    name, _colon, parent = rest.rpartition(":")
    if not name.startswith(PIPE_PREFIX) or not parent.isdigit():
        raise ChannelError("Not a worker pipe")
    up = win32.connect(name + "-up", int(parent), write=True)
    try:
        down = win32.connect(name + "-down", int(parent), write=False)
    except BaseException:
        win32.close_handle(up)
        raise
    return Endpoint(win32.stream(down, "rb"), win32.stream(up, "wb"), *limits)


# Both -------------------------------------------------------------------------------------------------------------

def listener(platform=None):
    return PipeListener() if (platform or sys.platform) == "win32" else SocketPairListener()


def connect(argument):
    """The worker's endpoint for its ``--channel`` argument."""
    if argument.startswith("fd:") and argument[3:].isdigit():
        return connect_socket(argument)
    if argument.startswith("pipe:"):
        return connect_pipe(argument)
    raise ChannelError("Unknown worker channel")


class Client:
    """Angelo's side once connected: numbered requests, each answered once; a reader thread matches answers.

    ``request`` blocks the calling thread (Blender's main thread) for at most ``timeout`` seconds. An answer that
    never comes, or a worker that ended, gives ``None``: whether the request ran is then unknown.

    ``on_reverse(request)`` answers the worker's own requests (``runtime_service``), each on a thread of its own
    (at most ``REVERSE_HANDLERS``), never on the reader: it returns the response, or ``(response, handle)`` to pass
    a descriptor with it, which is closed here once sent. Without it every such request is refused.

    ``on_event(body)`` gets each unsolicited ``{"event": body}`` frame on the reader thread, in the order they came
    (``worker_mirror.EventRouter``, which only queues them for the main thread). Without it they are dropped.
    """

    def __init__(self, endpoint, on_ended=None, on_reverse=None, on_event=None):
        self.endpoint, self.on_ended, self.on_reverse, self.on_event = endpoint, on_ended, on_reverse, on_event
        self._reverse_slots = threading.BoundedSemaphore(REVERSE_HANDLERS)
        self._lock = threading.Lock()
        self._waiting = {}
        self._seq = 0
        self._ready = threading.Event()
        self.ready_value = None
        self.ended = threading.Event()
        threading.Thread(target=self._read, daemon=True, name="AngeloWorkerReader").start()

    def wait_ready(self, timeout):
        if not self._ready.wait(timeout):
            return None
        return self.ready_value

    def request(self, request, timeout, late=None):
        """The answer, or None; ``late(answer)``, when given, gets an answer that arrives after ``timeout``, on a
        thread of its own (a grant the caller gave up on is released, never dropped: audit #4)."""
        with self._lock:
            if self.ended.is_set():
                return None
            self._seq += 1
            seq, slot = self._seq, [threading.Event(), None]
            self._waiting[seq] = slot
        try:
            self.endpoint.send_within({"seq": seq, "request": request}, timeout)
        except ChannelError:   # It stopped reading: the connection is closing, so no answer can come any more.
            with self._lock:
                self._waiting.pop(seq, None)
            self.ended.set()
            return None
        except (OSError, protocol.FrameError):
            with self._lock:
                self._waiting.pop(seq, None)
            raise
        slot[0].wait(timeout)
        with self._lock:   # The reader answers under this lock, so who gets the answer is decided here, once: the
            if slot[0].is_set() or late is None or self.ended.is_set():   # caller, or else ``late`` (audit2 #2).
                self._waiting.pop(seq, None)
                return slot[1] if slot[0].is_set() else None
            slot.append(late)
            return None

    def _read(self):
        try:
            while True:
                frame = self.endpoint.receive()
                if frame.get("seq") == 0 and isinstance(frame.get("ready"), dict):
                    self.ready_value = frame["ready"]
                    self._ready.set()
                    continue
                if "rseq" in frame:
                    self._reverse(frame)
                    continue
                if "seq" not in frame and isinstance(frame.get("event"), dict):
                    self._event(frame["event"])
                    continue
                late = None
                with self._lock:
                    slot = self._waiting.get(frame.get("seq"))
                    if slot is not None and isinstance(frame.get("response"), dict):
                        slot[1] = frame["response"]
                        slot[0].set()
                        if len(slot) > 2:   # Its request gave up waiting: the answer goes to its ``late``.
                            late = self._waiting.pop(frame["seq"])[2]
                if late is not None:
                    threading.Thread(target=late, args=(frame["response"],), daemon=True,
                                     name="AngeloLateAnswer").start()
        except (PipeClosed, protocol.FrameError, OSError, ValueError):
            pass
        finally:
            self.ended.set()
            self._ready.set()
            with self._lock:
                waiting = list(self._waiting.values())
            for slot in waiting:
                slot[0].set()
            if self.on_ended is not None:
                self.on_ended()

    def _reverse(self, frame):
        rseq, request = frame.get("rseq"), frame.get("request")
        if type(rseq) is not int or rseq < 1 or not isinstance(request, dict):
            raise protocol.FrameError("Expected {rseq, request}")
        if self.on_reverse is None or not self._reverse_slots.acquire(blocking=False):
            refusal = "Angelo serves no requests from this worker" if self.on_reverse is None else "Busy; retry"
            self._answer_reverse(rseq, {"error": {"type": "RuntimeError", "message": refusal}})
            return
        threading.Thread(target=self._serve_reverse, args=(rseq, request), daemon=True,
                         name="AngeloWorkerReverse").start()

    def _serve_reverse(self, rseq, request):
        try:
            try:
                answer = self.on_reverse(request)
            except Exception as error:  # noqa: BLE001 - every request is answered; the error is the answer.
                answer = {"error": {"type": type(error).__name__, "message": str(error)[:4000]}}
                support = getattr(error, "support", None)   # A WRK-* support code (runtime_service.Refused).
                if isinstance(support, str) and support.startswith("WRK-") and len(support) <= 40:
                    answer["error"]["support"] = support
            response, handle = answer if isinstance(answer, tuple) else (answer, None)
            self._answer_reverse(rseq, response, handle)
        finally:
            self._reverse_slots.release()

    def _answer_reverse(self, rseq, response, handle=None):
        try:
            self.endpoint.send({"rseq": rseq, "response": response}, handle)
        except (OSError, ValueError, ChannelError, protocol.FrameError):
            pass   # The worker ended or the answer could not cross: its request times out there.
        finally:
            if handle is not None:
                close_handles([handle])

    def _event(self, body):
        if self.on_event is None:
            return
        try:
            self.on_event(body)
        except Exception as error:   # noqa: BLE001 - one bad frame never ends the connection's answers.
            print("Angelo: a worker event could not be routed:", type(error).__name__, error)

    def close(self):
        self.endpoint.close()


class ReverseCaller:
    """The worker's side of its own requests to Angelo: numbered, each answered once or never (``None``).

    Installed on the worker's endpoint, whose reader hands it every ``{rseq, response}``; when the connection ends
    every waiting request ends without an answer.
    """

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self._lock = threading.Lock()
        self._waiting = {}
        self._seq = 0
        self.ended = threading.Event()
        endpoint.reverse = self

    def request(self, request, timeout):
        """``(response, handles)``; ``(None, [])`` when Angelo did not answer in time or the connection ended.

        The caller owns (and must close) every descriptor in ``handles``.
        """
        with self._lock:
            if self.ended.is_set():
                return None, []
            self._seq += 1
            rseq, slot = self._seq, [threading.Event(), None, []]
            self._waiting[rseq] = slot
        try:
            self.endpoint.send({"rseq": rseq, "request": request})
        except (OSError, ValueError, protocol.FrameError):
            with self._lock:
                self._waiting.pop(rseq, None)
            return None, []
        slot[0].wait(timeout)
        with self._lock:   # Popped under the lock ``route`` takes: an answer after this keeps nothing.
            self._waiting.pop(rseq, None)
            response, handles = slot[1], slot[2]
        if response is None:
            close_handles(handles)
            return None, []
        return response, handles

    def route(self, frame, handles):
        with self._lock:
            slot = self._waiting.get(frame.get("rseq"))
            if slot is not None and isinstance(frame.get("response"), dict) and not slot[0].is_set():
                slot[1], slot[2], handles = frame["response"], list(handles), []
                slot[0].set()
        close_handles(handles)   # A late, unknown or second answer keeps nothing.

    def end(self):
        with self._lock:
            self.ended.set()
            waiting = list(self._waiting.values())
        for slot in waiting:
            slot[0].set()
