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
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile

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

    def open(self, url):
        if self.executable is None:
            return False
        self._profile = tempfile.mkdtemp(prefix="angelo-qa-browser-")
        log = open(self.log_path, "wb")
        try:
            group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else \
                {"start_new_session": True}
            self.process = subprocess.Popen(command(self.executable, url, self._profile), stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, **group)
        finally:
            log.close()
        return True

    def close(self):
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                if sys.platform == "win32":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
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
