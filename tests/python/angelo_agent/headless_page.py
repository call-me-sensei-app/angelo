# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One page open in a headless browser, for GUI scenarios whose check needs a page and no person.

``gui_web_preview.py`` uses it for the live web preview: the page connects to Angelo's event stream, compiles the
web shaders, reports back and sends captures exactly as it does in a person's browser, so every page-side check runs
unattended. The browser is a Chromium-family one (Chrome, Chromium, Edge) in its new headless mode: no window, no
Dock activation, never the keyboard; a throw-away profile (never the person's), a mock keychain (no macOS prompt),
no background throttling, and WebGL through SwiftShader, on the CPU, so the result depends neither on the machine's
graphics nor on another run's GPU.

``ANGELO_QA_BROWSER`` names the browser's executable; ``none`` turns it off, so a person opens the page instead.
Unset, the usual install locations are tried (``find``).

``evaluate`` runs a line of JavaScript in the open page through the browser's own DevTools protocol, so a scenario
presses the page's real controls (``click``) as a person would; nothing is added to the page for it. The channel is
two private pipes the browser inherits as descriptors 3 and 4 (``--remote-debugging-pipe``, as the Fab browser's
``fab_browser_process``), never a port another process could reach. POSIX only: on Windows ``open`` starts the page
without the channel and ``evaluate`` refuses.
"""

import itertools
import json
import os
import shutil
import time
import signal
import subprocess
import sys
import tempfile
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

ENV = "ANGELO_QA_BROWSER"
OFF = "none"
SIZE = (1280, 800)
CANDIDATES = {
    "darwin": ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Chromium.app/Contents/MacOS/Chromium",
               "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    "win32": (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
}
NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge")


def find(environ=None, platform=None, exists=os.path.isfile, which=shutil.which):
    """The browser executable to use, or None (``ANGELO_QA_BROWSER=none``, or none installed)."""
    environ = os.environ if environ is None else environ
    named = environ.get(ENV, "").strip()
    if named:
        return None if named.lower() == OFF else named
    for path in CANDIDATES.get(sys.platform if platform is None else platform, ()):
        if exists(path):
            return path
    return next((found for found in map(which, NAMES) if found), None)


def command(executable, url, profile, size=SIZE):
    """The browser's command line for ``url`` alone, with its own ``profile`` folder."""
    return [executable, "--headless=new", "--user-data-dir=" + str(profile), "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--disable-component-update",
            "--disable-background-networking", "--disable-default-apps", "--use-mock-keychain",
            "--password-store=basic", "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--mute-audio", "--hide-scrollbars", "--window-size=%d,%d" % size, url]


class Page:
    """``open(url)`` starts the browser on the page; ``close()`` ends it and its whole process group."""

    def __init__(self, log_path, executable=None):
        self.executable = find() if executable is None else executable
        self.log_path = log_path
        self.process = None
        self._profile = None
        self._channel = None   # (write to the browser, read from it): the DevTools pipe, or None.
        self._ids = itertools.count(1)
        self._buffer = b""
        self._session = None

    def open(self, url):
        if self.executable is None:
            return False
        self._profile = tempfile.mkdtemp(prefix="angelo-qa-browser-")
        if sys.platform == "win32":
            log = open(self.log_path, "wb")
            try:
                self.process = subprocess.Popen(command(self.executable, url, self._profile),
                                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            finally:
                log.close()
            return True
        self.process = _Spawned(*self._spawn(url))
        return True

    def _spawn(self, url):
        """The browser with the DevTools pipe on descriptors 3 (it reads) and 4 (it writes), its own process group,
        output to the log; returns (pid,)."""
        incoming, to_browser = _pipe()
        from_browser, outgoing = _pipe()
        log = _high(os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        null = _high(os.open(os.devnull, os.O_RDONLY))
        line = command(self.executable, url, self._profile)
        line = line[:-1] + ["--remote-debugging-pipe", line[-1]]
        try:
            pid = os.posix_spawn(self.executable, line, dict(os.environ), setpgroup=0, file_actions=[
                (os.POSIX_SPAWN_DUP2, null, 0), (os.POSIX_SPAWN_DUP2, log, 1), (os.POSIX_SPAWN_DUP2, log, 2),
                (os.POSIX_SPAWN_DUP2, incoming, 3), (os.POSIX_SPAWN_DUP2, outgoing, 4)])
        finally:
            for descriptor in (incoming, outgoing, log, null):
                os.close(descriptor)
        self._channel = (to_browser, from_browser)
        return (pid,)

    def _call(self, method, params=None, session=None, deadline=None):
        to_browser, from_browser = self._channel
        ident = next(self._ids)
        message = {"id": ident, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        os.write(to_browser, json.dumps(message).encode("utf-8") + b"\0")
        import select
        while True:
            while b"\0" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\0", 1)
                answer = json.loads(raw)
                if answer.get("id") == ident:
                    if "error" in answer:
                        raise RuntimeError("DevTools refused %s: %s" % (method, answer["error"]))
                    return answer.get("result", {})
            left = (deadline - time.monotonic()) if deadline else CAP_S
            if left <= 0 or not select.select([from_browser], [], [], left)[0]:
                raise TimeoutError("the browser did not answer %s" % method)
            chunk = os.read(from_browser, 65536)
            if not chunk:
                raise OSError("the browser closed the DevTools pipe")
            self._buffer += chunk

    def evaluate(self, expression, timeout=10.0):
        """The value of ``expression`` (JavaScript, a promise awaited) in the open page, through DevTools."""
        if self.process is None or self._channel is None:
            raise OSError("no page open with a DevTools channel")
        deadline = time.monotonic() + timeout
        if self._session is None:
            pages = []
            while not pages and time.monotonic() < deadline:
                pages = [t for t in self._call("Target.getTargets", deadline=deadline)["targetInfos"]
                         if t.get("type") == "page"]
                if not pages:
                    time.sleep(0.1)
            if not pages:
                raise OSError("the browser shows no page")
            self._session = self._call("Target.attachToTarget", {"targetId": pages[0]["targetId"], "flatten": True},
                                       deadline=deadline)["sessionId"]
        result = self._call("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                                 "returnByValue": True}, self._session, deadline)
        if "exceptionDetails" in result:
            raise RuntimeError("the page refused %r: %s" % (expression, result["exceptionDetails"]))
        return result["result"].get("value")

    def click(self, element_id):
        """Press the page's control ``element_id`` as a person's click does (its own handler); False when the control
        is missing or disabled."""
        return bool(self.evaluate("(() => { const control = document.getElementById(%s); if (!control || "
                                  "control.disabled) return false; control.click(); return true; })()"
                                  % json.dumps(element_id)))

    def close(self):
        channel, self._channel, self._session, self._buffer = self._channel, None, None, b""
        for descriptor in channel or ():
            try:
                os.close(descriptor)
            except OSError:
                pass
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                if sys.platform == "win32":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=CAP_S)
            except (OSError, subprocess.TimeoutExpired):
                if sys.platform == "win32":
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
        if self._profile:
            shutil.rmtree(self._profile, ignore_errors=True)
            self._profile = None


def _high(fd):
    """``fd`` moved to 10 or above, close-on-exec: clear of the child's fixed descriptors 0-4 (as the Fab browser's
    ``fab_browser_process._pipe``), so each dup2 below really copies onto its target."""
    import fcntl
    try:
        return fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)
    finally:
        os.close(fd)


def _pipe():
    read, write = os.pipe()
    return _high(read), _high(write)


class _Spawned:
    """The ``subprocess.Popen`` surface ``close`` uses (pid, poll, wait), for a browser started by ``posix_spawn``."""

    def __init__(self, pid):
        self.pid, self.returncode = pid, None

    def poll(self):
        if self.returncode is None:
            done, status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() > deadline:
                raise subprocess.TimeoutExpired("browser", timeout)
            time.sleep(0.05)
        return self.returncode
