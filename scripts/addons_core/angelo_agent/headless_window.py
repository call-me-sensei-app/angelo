# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The launcher's side of a hidden-window session (doc/angelo/mcp_headless_window.rst): the real Angelo app on a
working copy of the file, its window kept off screen, reached as the person's window is (its CLI endpoint), its life
owned through one pipe. No bpy.

* ``HiddenWindow.open``: refused while the person's own Angelo window runs (the app uses that window); starts the app
  on a working copy beside the source with the session's environment (``hidden_environment``); waits until the app's
  endpoint answers as this session's (``ANGELO_HIDDEN_SESSION``).
* The pipe: the app reads it; its end (the launcher exited or died, or ``close``) makes the app save beside the source
  and quit. ``show`` travels on it too: the app brings its window up for the person (a waiting approval is theirs to
  answer) and is from then on the person's window; the launcher lets it go.
* ``close``: asks the app to save and quit, waits, then ends it. A session past its time cap is ended here too
  (``overdue``), whatever the app does.
"""

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


START_SECONDS = 120.0   # A cold start of the app (the person's add-ons included) answers well within this.
QUIT_SECONDS = 45.0     # A stop that settles (STOP_WAIT), a save beside the source and a clean quit.
TERMINATE_SECONDS = 20.0   # After SIGTERM the app still saves and quits (its handler); then it is killed.
DEFAULT_CAPS = {"minutes": 60, "usd": 10.0, "tokens": None}
ENV_FLAG, ENV_PIPE, ENV_SESSION, ENV_SOURCE, ENV_CAPS, ENV_CLIENT = (
    "ANGELO_HIDDEN_AGENT", "ANGELO_HIDDEN_PIPE", "ANGELO_HIDDEN_SESSION", "ANGELO_HIDDEN_SOURCE", "ANGELO_HIDDEN_CAPS",
    "ANGELO_HIDDEN_CLIENT")
WINDOW_RUNNING = ("Another Angelo is running on this computer (the person's window, or another app's session without "
                  "it), so none starts here: work through the open Angelo instead, under the person's approvals, or "
                  "ask the person to close it to work on a file without its window.")
ENDED_AT_START = ("Angelo ended as it started without its window. Either access for external apps is off in Angelo "
                  "(the person turns on Preferences > AI Providers > External AI apps > \"Allow apps on this computer "
                  "to reach Angelo\"), or another Angelo started meanwhile and holds its folder; then work through "
                  "that one.")
# Never Python a .blend file brings: the app chose this file, not the person (break-it review S6).
APP_FLAGS = ("--disable-autoexec",)


class HeadlessUnavailable(RuntimeError):
    """A hidden-window session that can't start or act; ``code`` is the MCP-facing code."""

    def __init__(self, message, code="unavailable"):
        super().__init__(message)
        self.code = code


def supported(platform=None):
    """Whether Angelo can work without its window on this platform (macOS only: AppKit hides it, POSIX signals and a
    flock hand it over). Elsewhere neither the launcher nor the add-on ever starts a hidden session."""
    return (platform or sys.platform) == "darwin"


def angelo_binary(executable=None, platform=None):
    """Angelo's own executable, found from the bundled Python that runs the launcher (``<app>/.../python/bin``)."""
    executable = Path(executable or sys.executable).resolve()
    platform = platform or sys.platform
    if platform == "darwin":
        contents = next((parent for parent in executable.parents if parent.name == "Contents"), None)
        folder, names = (contents / "MacOS" if contents else None), ("Angelo",)
    else:
        folder, names = (executable.parents[3] if len(executable.parents) > 3 else None), ("angelo", "Angelo")
    for name in names:
        candidate = folder / name if folder else None
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise HeadlessUnavailable("Angelo's executable was not found next to this launcher's Python")


def hidden_environment(environ, pipe, session, source, caps, client):
    """The app's environment: the person's own (their state folder, connections and preferences: it is the window's
    app), plus the session's facts. Never a credential."""
    environment = dict(environ)
    environment.update({ENV_FLAG: "1", ENV_PIPE: str(pipe), ENV_SESSION: session, ENV_SOURCE: str(source),
                        ENV_CAPS: json.dumps(caps, sort_keys=True), ENV_CLIENT: client})
    return environment


def caps_from(value):
    """The session's caps: the defaults, narrowed by what the app asked (never widened past them)."""
    caps = dict(DEFAULT_CAPS)
    value = value if isinstance(value, dict) else {}
    for key in ("minutes", "usd", "tokens"):
        asked = value.get(key)
        if type(asked) in (int, float) and asked > 0 and (caps[key] is None or asked <= caps[key]):
            caps[key] = asked
    return caps


def _others_running(binary):
    """Whether an Angelo this launcher did not start runs now (a list that can't be read counts as one)."""
    from . import carry_over_processes
    try:
        return carry_over_processes.others_of(carry_over_processes.process_table(), str(binary), os.getpid())
    except (OSError, subprocess.SubprocessError):
        return True


class HiddenWindow:
    """One hidden app per launcher session."""

    def __init__(self, client, state_directory, *, status=None, binary=None, environ=None, platform=None,
                 popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep, others_running=None):
        self.client, self.state = client, Path(state_directory)
        self.status = status   # status() -> the endpoint's status dict, or None when nothing answers.
        self.binary, self.environ = binary, dict(os.environ if environ is None else environ)
        self.platform = platform or sys.platform
        self.popen, self.clock, self.sleep = popen, clock, sleep
        self.others_running = others_running or _others_running
        self.child = self.pipe = self.session = self.source = self.working = None
        self.started, self.caps = None, dict(DEFAULT_CAPS)
        self.released = []   # Windows ``show`` gave the person: still this user's Angelo while they run.
        self.binary_used = binary
        self.writing, self.timer = threading.Lock(), None   # The pipe's writers; the time cap's own timer.

    @property
    def opened(self):
        return self.child is not None and self.child.poll() is None

    def _send(self, message):
        with self.writing:
            if self.pipe is None:
                raise HeadlessUnavailable("No file is open without Angelo's window.", code="no_file")
            self.pipe.write(json.dumps(message) + "\n")
            self.pipe.flush()

    def ours(self, status):
        """Whether an endpoint's ``status`` is this session's hidden app, not the person's window."""
        return bool(self.session) and isinstance(status, dict) and status.get("hidden_session") == self.session

    def open(self, path, caps=None):
        """Start the hidden app on ``path`` (an absolute ``.blend``); refused while the person's window runs."""
        if not supported(self.platform):
            raise HeadlessUnavailable("The agent without Angelo's window is available on macOS first.")
        from . import hidden_copy as working_copy
        source = working_copy.check_source(path)
        if self.opened:
            if str(source) == str(self.source):
                return {"ok": True, "reused": True, "file": str(source)}
            self.close()
        binary = self.binary or angelo_binary(platform=self.platform)
        self.binary_used = binary
        # The person's Angelo, whether or not it listens to apps: one process writes their state folder at a time.
        # A window ``show`` released is the person's now, though it is still this launcher's child.
        self.released = [child for child in self.released if child.poll() is None]   # Reaps the ended ones.
        if self.released or self.others_running(binary):
            raise HeadlessUnavailable(WINDOW_RUNNING, code="window_running")
        copy, identity = working_copy.claim(source)
        if copy is None:   # A read-only folder: the app would open the source itself, and its saves go beside it.
            raise HeadlessUnavailable("The file's folder can't be written, so Angelo can't work on it without its "
                                      "window (its changes are saved next to it).", code="read_only")
        self.session, self.source, self.working = secrets.token_hex(16), source, (copy, identity)
        self.caps, self.started = caps_from(caps), self.clock()
        read, write = os.pipe()
        # ANGELO_HIDDEN_APP_LOG: where the hidden app's own output goes, for a proof or a support case.
        log = self.environ.get("ANGELO_HIDDEN_APP_LOG")
        output = open(log, "ab") if log else subprocess.DEVNULL
        try:
            self.child = self.popen([str(binary), *APP_FLAGS, str(copy)], stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=output, pass_fds=(read,),
                                    env=hidden_environment(self.environ, read, self.session, source, self.caps,
                                                           self.client),
                                    start_new_session=True)
        except OSError as error:
            os.close(write)
            working_copy.discard(copy, source, identity)
            self.session = None
            raise HeadlessUnavailable("Angelo could not start without its window (%s)" % error) from None
        finally:
            os.close(read)
            if output is not subprocess.DEVNULL:
                output.close()   # The child holds its own descriptor.
        self.pipe = os.fdopen(write, "w", buffering=1, encoding="utf-8")
        self._send({"session": self.session})   # The app acts only on a pipe whose first line names its session.
        # The time cap on a timer of its own (break-it review 2, S4): whatever the app or the app's calls do.
        self.timer = threading.Timer(self.caps["minutes"] * 60 + QUIT_SECONDS, self.close)
        self.timer.daemon = True
        self.timer.start()
        deadline = self.clock() + START_SECONDS
        while self.clock() < deadline:
            if self.child.poll() is not None:
                break
            if self.ours(self.status() if self.status else None):
                return {"ok": True, "file": str(source), "hidden": True, "caps": self.caps}
            self.sleep(0.5)
        ended = self.child is not None and self.child.poll() is not None
        self.close()
        if ended:
            raise HeadlessUnavailable(ENDED_AT_START, code="unavailable")
        raise HeadlessUnavailable("Angelo did not start without its window in time.", code="unavailable")

    def overdue(self):
        """Past the session's time cap and the app's own quit: the launcher's backstop (break-it review S8)."""
        return (self.opened and self.started is not None
                and self.clock() - self.started > self.caps["minutes"] * 60 + QUIT_SECONDS)

    def decline(self, card):
        """The app's decline of the card ``card`` (``headless_window_relay.card_id``): the one answer an app gives."""
        if not self.opened:
            raise HeadlessUnavailable("No file is open without Angelo's window.", code="no_file")
        if type(card) is not str or len(card) != 16 or any(c not in "0123456789abcdef" for c in card):
            raise ValueError("card is the 16-character card from angelo_read's state.approval")
        self._send({"decline": card})

    def show(self):
        """Bring the app's window up for the person (``angelo_show``) and let it go: from now on it is the person's
        window, their approvals and their file; nothing here ends it or removes its file."""
        if not self.opened or self.pipe is None:
            raise HeadlessUnavailable("No file is open without Angelo's window.", code="no_file")
        source = self.source
        with self.writing:
            try:
                self.pipe.write(json.dumps({"show": True}) + "\n")
                self.pipe.close()   # After ``show`` the app no longer ends with the pipe.
            except OSError:
                pass
            self.pipe = None
        if self.timer is not None:
            self.timer.cancel()   # The person's window now: no cap of the app's ends it.
        self.released.append(self.child)
        self.child = self.pipe = self.working = self.session = None
        return source

    def live(self, session, child):
        """Whether ``child`` is still this hidden session, by the one authority: the state folder's lock record names
        ``session`` and the child's pid, verified (``headless_window_lock.live_session``). The person's window (after
        show), an unreadable record or an unverified pid are not: never signalled (review 4, M-A)."""
        from . import headless_window_lock
        try:
            return headless_window_lock.live_session(self.state, session, child.pid, self.binary_used)
        except Exception:   # noqa: BLE001 - unknown is never a hidden session.
            return False

    def close(self):
        """Ask the app to stop, save beside the source and quit (``close`` on the pipe); while the lock record still
        names this session, wait for it and end it if it hangs, re-checking before each signal. Anything else (the
        person took it over, or it can't be told) is let go, never signalled."""
        with self.writing:
            child, pipe, session = self.child, self.pipe, self.session
            if child is None:   # Another close() has this session (the cap's timer, say): it ends and discards it.
                return
            self.child = self.pipe = None
            if pipe is not None:
                try:
                    pipe.write(json.dumps({"close": True}) + "\n")
                    pipe.close()
                except OSError:
                    pass
        if self.timer is not None and self.timer is not threading.current_thread():
            self.timer.cancel()
        ended = child is None or child.poll() is not None
        if child is not None and not ended and self.live(session, child):
            try:
                child.wait(timeout=QUIT_SECONDS)
                ended = True
            except subprocess.TimeoutExpired:
                hard = getattr(signal, "SIGKILL", signal.SIGTERM)   # POSIX; a hidden session runs on macOS only.
                for number, seconds in ((signal.SIGTERM, TERMINATE_SECONDS), (hard, TERMINATE_SECONDS)):
                    if not self.live(session, child):
                        break   # It became the person's window meanwhile, or can't be told: let go.
                    try:
                        child.send_signal(number)   # SIGTERM: its handler still stops, saves and quits.
                        child.wait(timeout=seconds)
                        ended = True
                        break
                    except (subprocess.TimeoutExpired, ProcessLookupError):
                        continue
        if child is not None and not ended:
            self.released.append(child)   # Still running and not ours to end: the person's, or unknown.
        if self.working is not None and ended:
            from . import hidden_copy as working_copy
            copy, identity = self.working
            working_copy.discard(copy, self.source, identity)   # Only while it is still the copy the launcher made.
        self.working, self.session = None, None
