# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's side of one worker Blender: start it on a part file, ask it things, stop it.

A worker is Angelo itself in background mode, started as::

    <Angelo> --background --factory-startup --disable-autoexec --offline-mode --python-exit-code 1
             --python <scripts>/angelo/angelo_headless.py -- --worker --channel CHANNEL --file PART
             --private FOLDER

with ``ANGELO_HEADLESS=1`` and its state in an owner-only private folder this process makes (``angw-*``)
and removes after the worker ends. Factory settings keep workers small and the same for everyone: no add-ons of the
person's run in them. ``CHANNEL`` is its end of the local connection (``worker_channel``). The worker exits when
that connection closes, so it never outlives the Angelo that started it. Standard library only and no ``bpy``.

A running worker can take another part (``start`` after ``part`` changed): it switches its document to the new
part file (``worker_host.switch_part``) instead of a new launch, so one process builds several parts one after
another (``worker_pool``: warm reuse and workers launched ahead).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import external_cli_protocol as protocol, worker_channel
from .headless_session import KEEP_ENVIRONMENT
from . import private_roots

PRIVATE_PREFIX = private_roots.WORKER_PREFIX
READY_TIMEOUT = 120.0
SWITCH_TIMEOUT = 30.0   # A switch reloads an empty part file: tens of milliseconds measured (Mac 2).
STOP_GRACE = 5.0
WATCH_SECONDS = 0.5   # How often a hosting worker's process is checked while its connection stays open (``watch``).
# What a Windows process needs besides KEEP_ENVIRONMENT: the system, temporary and profile folders.
WINDOWS_ENVIRONMENT = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE",
                       "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "USERNAME", "NUMBER_OF_PROCESSORS",
                       "PROCESSOR_ARCHITECTURE")


# QA switches a worker honours when the Angelo that starts it has them set (suites only; never set in a release).
QA_ENVIRONMENT = ("ANGELO_QA_GPU_PROBE_CRASH",)


class WorkerUnavailable(RuntimeError):
    """The worker did not start, ended, or did not answer in time (``timed_out``: it still runs; ``sent``: the
    request may have reached it)."""

    def __init__(self, message, timed_out=False, sent=True):
        super().__init__(message)
        self.timed_out, self.sent = timed_out, sent


STATE = "s"   # The worker's own state folder in its private folder (ANGELO_STATE_DIR there); short (worker_paths).


def references_of(private, session):
    """The folder of chat ``session``'s references in the worker Blender started with ``private``: where its views
    resolve an image's ``reference`` (``ReferenceStore`` of its state folder)."""
    import uuid
    return Path(private) / STATE / "references" / str(uuid.UUID(session))


class RequestRefused(ValueError):
    """Angelo could not encode the request (too large for a frame, or not finite JSON): nothing was sent, and the worker
    is as it was (break-it E2E-15 M-1: this was taken for the worker's death and stopped a healthy worker). ``code``:
    ``VALIDATION`` for a value JSON cannot carry (a NaN, an infinity), ``LIMIT_EXCEEDED`` for a frame too large."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def worker_environment(environ, private, platform=None):
    kept = {name: environ[name] for name in KEEP_ENVIRONMENT if name in environ}
    if (platform or sys.platform) == "win32":
        upper = {key.upper(): value for key, value in environ.items()}
        kept.update({name: upper[name] for name in WINDOWS_ENVIRONMENT if name in upper})
    kept.update({name: value for name, value in environ.items() if name.startswith("LC_")})
    kept.update({name: environ[name] for name in QA_ENVIRONMENT if name in environ})
    # The test slot's non-secret owner survives a worker's separate session so
    # bounded QA cleanup can identify it even after an intermediate parent exits.
    owner = environ.get("ANGELO_QA_PROCESS_OWNER", "")
    if isinstance(owner, str) and len(owner) == 32 and all(char in "0123456789abcdef" for char in owner):
        kept["ANGELO_QA_PROCESS_OWNER"] = owner
    from .worker_chat_seams import enabled
    kept["ANGELO_WORKER_CHAT_HOST"] = "1" if enabled(environ) else "0"   # The worker hosts its chat as Angelo decides.
    if enabled(environ):   # Its chats are workers' own: their tools and briefing (parallel_access.in_worker).
        kept["ANGELO_PARALLEL_WORKER"] = "1"
    kept.update(ANGELO_HEADLESS="1", ANGELO_STATE_DIR=str(Path(private) / STATE),
                ANGELO_CONNECTIONS_DIR=str(Path(private) / "connections"), ANGELO_DISABLE_DEFAULT_CHAT="1",
                ANGELO_DISABLE_LOCAL_CLI="1", ANGELO_DISABLE_KEYCHAIN="1")
    return kept


def worker_command(binary, script, part, channel, private):
    return [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
            "--python-exit-code", "1", "--python", str(script), "--", "--worker", "--channel", channel,
            "--file", str(part), "--private", str(private)]


def default_binary():
    import bpy
    return Path(bpy.app.binary_path)


def default_script():
    return Path(__file__).resolve().parents[2] / "angelo" / "angelo_headless.py"


_live = set()   # Workers whose start began and whose process has not been seen to exit (``live``).
_live_lock = threading.Lock()


def live():
    """Every worker this Angelo started whose start began and whose process has not exited: launching, switching,
    ready, idle ahead or stopping, each a place in use (``worker_places``); those that exited leave here."""
    with _live_lock:
        _live.difference_update([worker for worker in _live if not worker.holding()])
        return set(_live)


class WorkerProcess:
    """One worker Blender and its connection. ``request`` is called from Blender's main thread only."""

    def __init__(self, part, *, binary=None, script=None, environ=None, platform=None, popen=None,
                 listener=None, temporary=None, killpg=None):
        self.part = Path(part)
        self.binary, self.script = binary, script
        self.environ = os.environ if environ is None else environ
        self.platform = platform or sys.platform
        self._popen, self._listener = popen, listener
        # posix: a worker runs in a session of its own; its scripts' processes share its group (signalled on stop).
        self._killpg = killpg if killpg is not None else getattr(os, "killpg", None) if popen is None else None
        self._group = None
        self.temporary = temporary
        self.process = self.client = self.private = None
        self.ready = None
        self.started_at = self.ready_seconds = None
        self.ended = threading.Event()
        self.serving = None     # The part file the running worker has open (``part`` once ready).
        self.parts = 0          # Parts this process took after its first (``switch``).
        self.how = None         # How the last ``start`` made it ready: "launched", "switched" or "ready".
        self.moving = []        # Chats still bound to it across a switch: they open again on the new part (A').
        self.scratch = None     # A folder of this worker's own to remove once it leaves it (``worker_pool``).
        self._starting = threading.Lock()
        self._beginning = False
        # One stop per process (``stop``): whoever comes second waits for the first and returns, so the ended path
        # (hand back, crash-mark, turn ended, consent and runtime released) runs exactly once.
        self._stop_lock = threading.RLock()
        self._stopping = self._stopped = None   # The process whose stop began, and the one whose stop ended.
        self.ended_on_its_own = None   # Why its last process ended without a stop of Angelo's (``watch``), or None.

    def holding(self):
        """Whether this worker holds a place: its start is under way, or its process runs (until it has exited)."""
        return self._beginning or (self.process is not None and self.process.poll() is None)

    def start(self, timeout=READY_TIMEOUT):
        """Make this worker ready on ``part``: launch it, or, when it already runs on another part file (a warm
        worker, ``worker_pool``), switch it to ``part``; a worker whose switch fails is stopped and launched afresh,
        so a part never fails because a warm worker could not take it. ``WorkerUnavailable`` with its last output when
        it cannot start. A second ``start`` waits for one under way (a part taking a worker still launching)."""
        with self._starting:
            self._beginning = True
            with _live_lock:
                _live.add(self)
            try:
                return self._start(timeout)
            finally:
                self._beginning = False

    def _start(self, timeout):
        begun, self.moving = time.perf_counter(), []
        if self.alive() and self.serving is not None:
            if Path(self.serving) == self.part:
                self.how, self.ready_seconds = "ready", 0.0
                return self.ready
            try:
                ready = self._switch(SWITCH_TIMEOUT)
                self.how, self.ready_seconds = "switched", time.perf_counter() - begun
            except (WorkerUnavailable, RequestRefused) as error:
                print("Angelo: a warm worker could not take its new part (%s); launching one" % error)
            else:
                return self._moved(ready)
        if self.process is not None:
            self.stop()
            self.process = self.client = self.private = self.ready = self.serving = None
            self.ended = threading.Event()
        self.how = "launched"
        return self._moved(self._launch(timeout))

    def _moved(self, ready):
        """A chat that stays bound to this worker across a new part opens there again (A', C10), or the start
        fails."""
        from . import worker_chat_lifecycle as lifecycle
        try:
            moving, self.moving = self.moving, []
            lifecycle.after_switch(self, moving)
        except lifecycle.ChatOpFailed as error:
            raise WorkerUnavailable("its chat could not move to the new part: %s" % error) from error
        return ready

    def _switch(self, timeout):
        """The running worker takes ``part`` (``worker_host.switch_part``): its earlier part file stays as its last
        save left it. With A' every chat it hosts closes first (C10; those still bound to it are kept in ``moving``
        for ``_moved``), and one that cannot refuses the switch."""
        from . import worker_chat_lifecycle as lifecycle
        try:
            self.moving = lifecycle.before_switch(self)
        except lifecycle.ChatOpFailed as error:
            raise WorkerUnavailable("a chat it hosts could not close: %s" % error) from error
        answer = self.request("switch_part", timeout, part=str(self.part))
        if answer.get("error") is not None:
            raise WorkerUnavailable("it refused the switch: %s" % str(answer["error"])[:200], sent=True)
        self.ready = dict(self.ready or {}, layout=answer.get("layout"), document=answer.get("document"))
        self.serving, self.parts = self.part, self.parts + 1
        self._remove_scratch()
        return self.ready

    def _launch(self, timeout):
        part, self.ended_on_its_own = self.part, None
        from .worker_paths import PathTooLong, check   # Windows: never a worker whose own files cannot be written.
        try:
            check(self.temporary or private_roots.scratch_root(), part, platform=self.platform)
        except PathTooLong as error:
            raise WorkerUnavailable(str(error), sent=False) from error
        self.private = private_roots.allocate(PRIVATE_PREFIX, self.temporary)   # Private to file tools at once.
        os.chmod(self.private, 0o700)
        (self.private / STATE).mkdir(mode=0o700)
        listener = self._listener() if self._listener else worker_channel.listener(self.platform)
        log = self.private / "worker.log"
        error = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        self.started_at = time.perf_counter()
        try:
            command = worker_command(self.binary or default_binary(), self.script or default_script(), part,
                                     listener.child_argument(), self.private)
            options = dict(env=worker_environment(self.environ, self.private, self.platform),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=error)
            if self.platform == "win32":
                from .child_process import popen as job_popen
                popen = self._popen or job_popen   # A Job Object: stopping ends the worker's whole tree.
            else:
                popen = self._popen or subprocess.Popen
                options.update(pass_fds=listener.pass_fds(), close_fds=True, start_new_session=True)
            self.process = popen(command, **options)
            self._group = self.process.pid if options.get("start_new_session") else None
        except BaseException:
            listener.close()
            self._remove_private()
            raise
        finally:
            os.close(error)
        listener.started()
        try:
            endpoint = listener.accept(self.process.pid, alive=lambda: self.process.poll() is None)
        except Exception as failure:
            listener.close()
            self.stop()
            raise WorkerUnavailable("The worker did not connect: %s" % failure) from failure
        channel = self._chat_channel()
        self.client = worker_channel.Client(endpoint, on_ended=self._connection_ended, **channel)
        ready = self.client.wait_ready(timeout)
        if not isinstance(ready, dict) or not ready.get("ok"):
            lines = self.last_output()
            self.stop()
            why = (ready or {}).get("error") if isinstance(ready, dict) else "it never said ready"
            raise WorkerUnavailable("The worker did not start (%s)%s" % (why, ": " + " | ".join(lines) if lines
                                                                       else ""))
        self.ready, self.ready_seconds, self.serving = ready, time.perf_counter() - self.started_at, part
        if channel:   # A': it hosts chats, so its own end is acted on at once (``watch``).
            self.watch()
        return ready

    def _chat_channel(self):
        """A': this worker's chats' events and model runtimes (``worker_chat_wiring``); off: neither."""
        from . import worker_chat_lifecycle
        if not worker_chat_lifecycle.enabled():
            return {}
        from . import worker_chat_wiring
        return worker_chat_wiring.attach(self)

    def _connection_ended(self):
        self.ended.set()
        runtime = getattr(self, "runtime", None)
        if runtime is not None:   # A': every model child Angelo runs for this worker ends with it (C5).
            runtime.close()

    def watch(self):
        """A': once this worker ends on its own (its connection ends or its process exits, never a stop of Angelo's),
        the path ``stop`` takes runs for it at once, on a thread of its own, exactly once: every chat it hosts is
        handed back (its file reopened as after a crash, its admitted calls ``interrupted``, its mirror's turn ended),
        its binding is crash-marked (``ended_on_its_own``: ``worker_chat_wiring.handed_back``), its consent and model
        runtimes are released. Without it a dead worker kept its mirror busy until Angelo next asked it something
        (FE D5). A process that exits while something else still holds its connection open counts too."""
        process, client = self.process, self.client
        threading.Thread(target=self._watch, args=(process, client), daemon=True, name="AngeloWorkerWatch").start()

    def _watch(self, process, client):
        while not client.ended.wait(WATCH_SECONDS) and process.poll() is None:
            pass
        with self._stop_lock:   # Decided under the stop's own lock: a stop of Angelo's that began first wins.
            if self.process is not process or self._stopping is process:
                return   # Angelo stopped it (or relaunched it): that stop runs the ended path.
            code = process.poll()
            self.ended_on_its_own = ("its process exited (code %s)" % code if code is not None
                                     else "its connection ended while its process still ran")
            print("Angelo: a worker ended on its own:", self.ended_on_its_own)
            self.stop()

    def alive(self):
        return (self.client is not None and not self.client.ended.is_set() and self.process is not None
                and self.process.poll() is None)

    takes_late = True   # ``request(late=...)``: ``worker_chat_lifecycle.send_op`` routes a late grant to its release.

    def request(self, op, timeout, late=None, **fields):
        """The worker's answer to one request; ``WorkerUnavailable`` when it ended or did not answer in time (``late``:
        ``worker_channel.Client.request``)."""
        if not self.alive():
            raise WorkerUnavailable("The worker has ended", sent=False)
        if op == "chat.stop" and type(fields.get("lead_seq")) is not int:   # Every Stop is ordered, whoever sends it.
            from .worker_mirror_ops import order
            fields["lead_seq"] = order()   # After everything the person did before it (``worker_chat_arrival``).
        try:
            request = dict(fields, op=op)
            answer = self.client.request(request, timeout) if late is None else self.client.request(request, timeout,
                                                                                                      late=late)
        except protocol.FrameError as error:   # Encoded before any byte is written.
            raise RequestRefused(str(error), "VALIDATION" if error.__cause__ is not None else "LIMIT_EXCEEDED") \
                from error
        except (OSError, ValueError) as error:
            raise WorkerUnavailable("Could not reach the worker (%s)" % error) from error
        if answer is None:
            if self.client.ended.is_set() and self.process.poll() is None:
                # The connection ended under a live worker (a request it did not take in time, break-it N4): it can
                # never answer again, so it is stopped, off this thread, which a blocked write may still hold.
                threading.Thread(target=self.stop, daemon=True, name="AngeloWorkerStop").start()
                raise WorkerUnavailable("its connection ended while it still ran, so Angelo is stopping it")
            if self.alive():
                raise WorkerUnavailable("no answer within %g s" % timeout, timed_out=True)
            raise WorkerUnavailable("The worker ended before it answered")
        return answer

    def last_output(self, lines=5, limit=4096):
        try:
            with open(self.private / "worker.log", "rb") as stream:
                stream.seek(max(0, os.fstat(stream.fileno()).st_size - limit))
                text = stream.read(limit).decode("utf-8", "replace")
        except (OSError, TypeError):
            return []
        return [line.strip()[:240] for line in text.splitlines() if line.strip()][-lines:]

    def stop(self, grace=STOP_GRACE):
        """Close the connection (the worker exits on its end), then stop it by its own process only if it lingers. With
        A' each chat it hosts closes first, bounded, and is handed back once the process has ended (C10). Once per
        process: a second stop (or the watcher's, ``watch``) waits for the first and returns. The ended path runs
        whatever the stop raised (``_stop``)."""
        with self._stop_lock:
            process = self.process
            if process is not None and self._stopped is process:
                return
            self._stopping = process
            self._stop(grace, process)

    def _stop(self, grace, process):
        from . import worker_chat_lifecycle as lifecycle
        reaped = process is None
        try:
            lifecycle.closing(self)
            if self.client is not None:   # Never waits on a stuck read or write: the kill below ends those too.
                closing = threading.Thread(target=self.client.close, daemon=True, name="AngeloWorkerClose")
                closing.start()
                closing.join(2.0)
            if process is not None:
                try:
                    process.wait(grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(grace)
                reaped = True   # Its wait returned: the process has ended.
            self._end_group(grace)
        finally:   # A step that raised (a kill not reaped in time) never keeps its chats from being handed back.
            if reaped or process.poll() is not None:
                self._ended_path(process)
            else:   # Until its process has ended its chats' files stay its own (C7): handed back once it has.
                threading.Thread(target=self._reaped, args=(process,), daemon=True, name="AngeloWorkerReap").start()

    def _reaped(self, process):
        process.wait()
        with self._stop_lock:   # A later stop that found it ended ran the path already; never a newer process's.
            if self.process is process and self._stopped is not process:
                self._ended_path(process)

    def _ended_path(self, process):
        """Exactly once per process, under the stop's lock: every chat it hosts handed back, its folders removed."""
        from . import worker_chat_lifecycle as lifecycle
        try:
            lifecycle.ended(self)
            self._remove_private()
            self._remove_scratch()
        finally:
            self._stopped = process
            self.ended.set()

    def _end_group(self, grace):
        """posix: processes the worker's scripts started share its process group and outlive it unless signalled
        (Windows ends the whole tree by its Job Object). SIGTERM, then SIGKILL to whatever is left after a short grace;
        a process that left the group itself (its own ``setsid``) is outside reach. Break-it E2E-19A L-3."""
        import signal
        group, self._group = self._group, None
        if group is None or self._killpg is None:
            return
        deadline = time.monotonic() + min(grace, 1.0)
        for sig in (signal.SIGTERM, 0, signal.SIGKILL):
            try:
                self._killpg(group, sig)
                while sig == 0 and time.monotonic() < deadline:   # Anything left after the SIGTERM?
                    time.sleep(0.05)
                    self._killpg(group, 0)
            except (ProcessLookupError, PermissionError):
                return   # Nothing of the group is left.

    def _remove_private(self):
        if self.private is not None:
            shutil.rmtree(self.private, ignore_errors=True)

    def _remove_scratch(self):
        scratch, self.scratch = self.scratch, None
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
