# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's model runtimes, run by Angelo and reached over a relay (design A', ``runtime_service``).

In a worker Blender that hosts a chat, ``install`` makes ``cli_runtime.launch`` (the one factory every provider
launch goes through: Codex's ``JsonRpcProcess``, its prewarm and opening, every Claude session) ask Angelo to start
the runtime instead (``runtime.launch``) and return a ``RelayedProcess``: what ``protocol.JsonRpcProcess``,
``claude_transport.close_process`` and the Claude provider use of ``subprocess.Popen``, over the relay:

* ``stdin.write`` / ``flush`` / ``close`` (each whole line one ``{"line"}`` frame; ``close`` ends the child's stdin);
* ``stdout`` lines by iteration or ``readline``, ending when the child ends or the relay closes;
* ``stderr``: only Angelo's notices (a refused line); the child's own stderr stays in Angelo;
* ``poll``, ``wait(timeout)`` (``subprocess.TimeoutExpired``), ``returncode`` and ``pid`` (Angelo's child);
* ``kill`` / ``terminate``: ``runtime.kill`` to Angelo, then the relay closes (which ends the child too).

The worker-built command and environment carry no credential (the worker has none) and are not run: Angelo
rebuilds both from its own records and accepts from them only the launch values its own builders take
(``runtime_service``). ``probe`` asks Angelo for the checks that need the key. Standard library only and no ``bpy``.
"""

import queue
import socket
import subprocess
import threading

from . import worker_channel
from .headless_pipe import PipeClosed
from .protocol import RpcError
from .ui_text import n_

LAUNCH_SECONDS = 150.0   # Angelo's start of the child, an API-key sign-in (60 s) and its version check included.
PROBE_SECONDS = 60.0
KILL_SECONDS = 5.0
LOST = -9                # The return code of a child whose relay closed before Angelo said how it ended.
RELAY_LIMIT = 64 * 1024 * 1024
NOT_RELAYED = "In a worker chat Angelo runs only the chat's own model runtime"


class _Lines:
    """A text stream read line by line from a queue that ends once (``None``)."""

    def __init__(self):
        self._queue, self.closed, self._ended = queue.Queue(), False, False

    def put(self, line):
        self._queue.put(line)

    def end(self):
        self._queue.put(None)

    def readline(self):
        if self._ended or self.closed:
            return ""
        line = self._queue.get()
        if line is None:
            self._ended = True
            return ""
        return line

    def __iter__(self):
        while True:
            line = self.readline()
            if not line:
                return
            yield line

    def close(self):
        self.closed = True


class _Stdin:
    def __init__(self, process):
        self.process, self.closed = process, False
        self._buffer, self._lock = "", threading.Lock()

    def write(self, text):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        with self._lock:
            self._buffer += text
            *lines, self._buffer = self._buffer.split("\n")
            for line in lines:
                self.process._send({"line": line})
        return len(text)

    def flush(self):
        if self.closed:
            raise ValueError("I/O operation on closed file")

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process._send({"close": True})
        except OSError:
            pass


class RelayedProcess:
    """``subprocess.Popen``'s surface for a child Angelo runs (module docstring)."""

    def __init__(self, endpoint, pid, args=None, kill=None, runtime=None):
        self.endpoint, self.pid, self.args, self._kill = endpoint, pid, args, kill
        self.runtime = runtime   # A Codex launch: ``{kind, version}`` as Angelo resolved them (``launched``).
        self.returncode = None
        self._exit = None
        self._done = threading.Event()
        self.stdin, self.stdout, self.stderr = _Stdin(self), _Lines(), _Lines()
        threading.Thread(target=self._read, daemon=True, name="AngeloRelay").start()

    def _send(self, frame):
        if self._done.is_set():
            raise BrokenPipeError("The model runtime has ended")
        try:
            self.endpoint.send(frame)
        except (OSError, ValueError) as error:
            raise BrokenPipeError("The model runtime's relay closed") from error

    def _read(self):
        try:
            while True:
                frame = self.endpoint.receive()
                if isinstance(frame.get("line"), str):
                    self.stdout.put(frame["line"] + "\n")
                elif type(frame.get("exit")) is int:
                    self._exit = frame["exit"]
                elif isinstance(frame.get("notice"), str):
                    self.stderr.put(frame["notice"] + "\n")
        except (PipeClosed, OSError, ValueError):
            pass
        finally:
            self.returncode = self._exit if self._exit is not None else LOST
            self._done.set()
            self.stdout.end()
            self.stderr.end()
            self.endpoint.close()

    def poll(self):
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def kill(self):
        """Angelo ends the child (``runtime.kill``), even while this worker's relay is wedged; the relay closes."""
        if self._done.is_set():
            return
        if self._kill is not None:
            try:
                self._kill(self.pid)
            except Exception:  # noqa: BLE001 - the relay's close below ends the child in any case.
                pass
        self.endpoint.close()

    terminate = kill

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.kill()
        return False


# A model runtime the lead would not start for this worker's chat (``runtime_service.Refused``): why
# (``worker_reasons``) and its support code, in a sentence whose template the chat's error row keeps.
NOT_STARTED = n_("This chat's model did not start ({why}; support code {code}).")


def refusal(support):
    """``(English text, i18n recipe)`` of a runtime the lead refused with ``support`` (a ``WRK-*`` code), or None for
    a code ``worker_reasons`` does not define: the lead's own words are kept then."""
    from .display_text import Text
    from .ui_text import notice
    from .worker_reasons import BY_SUPPORT
    chosen = BY_SUPPORT.get(support) if isinstance(support, str) else None
    if chosen is None:
        return None
    row = notice(NOT_STARTED, why=Text(chosen), code=support)
    return row["text"], row.get("i18n")


class Launcher:
    """``cli_runtime.relay`` in a worker: each launch is Angelo's (``runtime.launch``), relayed back here.

    ``caller`` is the worker channel's ``worker_channel.ReverseCaller``; ``sessions()`` the chats this worker hosts.
    """

    def __init__(self, caller, sessions, platform=None):
        self.caller, self.sessions, self.platform = caller, sessions, platform

    def session(self):
        hosted = [session for session in self.sessions() if isinstance(session, str)]
        if len(hosted) != 1:   # Which chat a launch is for must be certain: with none or several, none starts.
            raise RpcError(NOT_RELAYED)
        return hosted[0]

    def ask(self, request, timeout):
        response, handles = self.caller.request(request, timeout)
        if response is None:
            raise RpcError("Angelo did not answer this worker's request for its model runtime")
        if "error" in response or response.get("ok") is not True:
            worker_channel.close_handles(handles)
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
            known = refusal(error.get("support"))   # Said again from its own template: the row translates.
            failure = RpcError(known[0] if known else error.get("message", "Angelo refused"))
            failure.i18n = known[1] if known else None
            raise failure
        return response, handles

    def __call__(self, command, *, env, cwd=None, **kwargs):
        if kwargs.get("text") is not True:
            raise RpcError(NOT_RELAYED)
        session = self.session()
        request = {"op": "runtime.launch", "session": session, "workspace": str(cwd) if cwd is not None else None}
        request.update(launch_request(list(command), env))
        response, handles = self.ask(request, LAUNCH_SECONDS)
        runtime = lead_runtime(response.get("runtime")) if request["provider_kind"] == "codex" else None
        if request["provider_kind"] == "codex" and runtime is None:   # Its checks need Angelo's word: none run.
            worker_channel.close_handles(handles)
            self._kill(session, response.get("pid"))
            raise RpcError("Angelo's answer did not say which Codex it launched for this chat")
        try:
            endpoint = self.open(response.get("relay"), handles)
        except Exception:
            self._kill(session, response.get("pid"))
            raise
        return RelayedProcess(endpoint, response.get("pid"), list(command),
                              kill=lambda pid: self._kill(session, pid), runtime=runtime)

    def open(self, argument, handles):
        handles = list(handles)
        try:
            if argument == "fd:passed" and handles:
                sock = socket.socket(fileno=handles.pop(0))
                sock.set_inheritable(False)
                return worker_channel.socket_endpoint(sock, RELAY_LIMIT, RELAY_LIMIT)
            if isinstance(argument, str) and argument.startswith("pipe:"):
                return worker_channel.connect_pipe(argument, limits=(RELAY_LIMIT, RELAY_LIMIT))
            raise RpcError("Angelo's answer named no relay for the model runtime")
        finally:
            worker_channel.close_handles(handles)

    def _kill(self, session, pid):
        if type(pid) is int:
            self.caller.request({"op": "runtime.kill", "session": session, "pid": pid}, KILL_SECONDS)

    def probe(self, what, **fields):
        """A check that needs the chat's key, run in Angelo: its non-secret answer (``runtime_service.probe``)."""
        request = dict(fields, op="runtime.probe", session=self.session(), what=what)
        return self.ask(request, PROBE_SECONDS)[0]


def lead_runtime(value):
    """A launch answer's ``runtime`` (``agent_runtime.launched``) when well formed, else None (fail closed)."""
    from .agent_runtime import KINDS
    if type(value) is not dict or value.get("kind") not in KINDS:
        return None
    version = value.get("version")
    if (version is None) != (value["kind"] == "own") or version is not None and (type(version) is not str
                                                                                  or not version):
        return None
    return {"kind": value["kind"], "version": version}


def launched(rpc):
    """``lead_runtime`` of the process behind ``rpc`` (a ``JsonRpcProcess`` or a shared runtime's ``Lease``) when
    Angelo launched it for this worker chat, else None: the Codex checks then use Angelo's word (FE D1)."""
    inner = getattr(getattr(rpc, "runtime", None), "rpc", None) or rpc   # A lease: its shared runtime's process.
    process = getattr(inner, "process", None)
    return process.runtime if isinstance(process, RelayedProcess) else None


def launch_request(command, env):
    """What a worker-built launch asks Angelo for: the runtime kind and the launch values Angelo's builders take."""
    from . import claude_direct
    if "app-server" in command[1:2]:
        return {"provider_kind": "codex", "text_only": command[-2:] == ["-c", 'web_search="disabled"']}
    if command[1:2] == ["-p"]:
        direct = all(env.get(name) == value for name, value in claude_direct.SESSION_ENVIRONMENT.items())
        return {"provider_kind": "claude", "arguments": command[1:], "session_options": {
            "prompt_cache_ttl": env.get("CLAUDE_CODE_PROMPT_CACHE_TTL") or None, "claude_direct_tools": direct,
            "single_model": env.get("CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK") == "1"}}
    raise RpcError(NOT_RELAYED)


# The worker process -----------------------------------------------------------------------------------------------

_installed = None


def install(endpoint, sessions):
    """Route this worker's model runtime launches to Angelo over ``endpoint`` (its channel); the launcher."""
    global _installed
    from . import cli_runtime
    caller = endpoint.reverse if isinstance(endpoint.reverse, worker_channel.ReverseCaller) else \
        worker_channel.ReverseCaller(endpoint)
    _installed = cli_runtime.relay = Launcher(caller, sessions)
    return _installed


def uninstall():
    global _installed
    from . import cli_runtime
    _installed = cli_runtime.relay = None


def relayed():
    """Whether this process's model runtimes run in Angelo (a worker chat): voice and collaborators are refused."""
    return _installed is not None


def probe(what, **fields):
    if _installed is None:
        raise RpcError(NOT_RELAYED)
    return _installed.probe(what, **fields)
