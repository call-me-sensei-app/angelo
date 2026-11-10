# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run providers' own sign-in commands. Never read or intermediate OAuth tokens."""

import json
import queue
import subprocess
import threading
import uuid
from typing import NamedTuple
from pathlib import Path

from .cli_runtime import launch, with_runtime_path
from .providers import child_environment, find_executable, profile_environment


def native_auth_command(mode, settings, state_dir, login=False, api_key=False):
    if mode not in {"codex_subscription", "codex_api", "claude_subscription"}:
        raise ValueError("API keys are configured separately from subscription sign-in")
    if login and mode.endswith("_api") and not api_key:
        raise ValueError("Use the API-key field for an API connection")
    name = "codex" if mode.startswith("codex_") else "claude"
    executable = find_executable(name, settings.get(name + "_path", ""))
    env = with_runtime_path(profile_environment(child_environment(mode), mode, settings.get("auth_directory", state_dir),
                            settings.get("reuse_codex_login", True), settings.get("connection_id"), settings.get("native_profile")), executable)
    if name == "codex":
        command = [executable]
        if mode == "codex_api":
            command += ["-c", 'cli_auth_credentials_store="keyring"']
        return command + ["login"] + (["--with-api-key"] if api_key else [] if login else ["status"]), env
    return [executable, "auth", "login", "--claudeai"] if login else [executable, "auth", "status"], env


def metadata_status(mode, code, output):
    """Whitelist status text; URLs, codes, emails and raw CLI output stay private."""
    if mode.startswith("codex_"):
        if code != 0:
            return "Not connected"
        lowered = output.lower()
        if mode == "codex_subscription":
            return "Connected to ChatGPT" if "chatgpt" in lowered else "Different native billing mode; sign in with ChatGPT"
        return "Native API credential configured" if "api key" in lowered else "Different native billing mode"
    try:
        data = json.loads(output)
    except (ValueError, TypeError):
        return "Unable to check native sign-in"
    return "Connected to Claude" if code == 0 and data.get("loggedIn") and data.get("authMethod") in {"claude.ai", "oauth", "oauth_token"} else "Not connected"


class AuthChange(NamedTuple):
    identity: str
    logged_in: bool
    attempt: str


class NativeAuthentication:
    def __init__(self):
        self.status = {}
        self.running = set()
        self.events = queue.Queue()
        self.processes = {}
        self.lock = threading.Lock()
        self.closed = False
        self.modes = {}
        self._attempts = {}
        self._cancelled = {}

    def attempt(self, identity):
        """The current logical sign-in/status attempt; a completion recheck retains this owner."""
        with self.lock:
            return self._attempts.get(identity)

    @staticmethod
    def _drain(process):
        if process is None:
            return True
        try:
            if process.poll() is not None:
                return True
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
        except Exception:
            return False  # Retain the exact owner when process inspection/termination cannot prove its exit.
        return process.poll() is not None

    def start(self, mode, settings, state_dir, login=False, identity=None, api_key="", continuation=None):
        Path(state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        command, env = native_auth_command(mode, settings, state_dir, login, bool(api_key))
        identity = identity or mode
        with self.lock:
            if identity in self.running or self.closed:
                raise RuntimeError("A native sign-in/status check is already running")
            if continuation is not None and (continuation != self._attempts.get(identity)
                                             or self._cancelled.get(identity) == continuation):
                raise RuntimeError("The sign-in attempt changed before its status recheck")
            attempt = continuation if continuation is not None else uuid.uuid4().hex
            self._attempts[identity] = attempt
            self._cancelled.pop(identity, None)
            self.modes[identity] = mode
            self.running.add(identity)
            self.status[identity] = "Saving API credential…" if api_key else "Complete sign-in in your browser…" if login else "Checking native sign-in…"

        def run():
            process, status, logged_in = None, "Native runtime unavailable; check its executable path", False
            try:
                with self.lock:
                    cancelled = self.closed or self._cancelled.get(identity) == attempt
                if not cancelled:
                    # Provider-owned browser flow; cancellation never closes a browser window.
                    process = launch(command, env=env, cwd=str(state_dir), stdin=subprocess.PIPE if api_key else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, keep_gui_programs=True)
                    with self.lock:
                        if self._attempts.get(identity) == attempt:
                            self.processes[identity] = process
                        if self.closed or self._cancelled.get(identity) == attempt:
                            process.terminate()
                    output, _ = process.communicate(input=api_key + "\n" if api_key else None, timeout=600 if login else 30)
                    if login:
                        status = "Sign-in complete. Refresh status to confirm." if process.returncode == 0 else "Sign-in did not complete. Retry or use the native Terminal command."
                    else:
                        status = metadata_status(mode, process.returncode, output)
                    logged_in = login and process.returncode == 0
            except subprocess.TimeoutExpired:
                status = "Native sign-in/status check timed out"
            except Exception:
                status = "Native runtime unavailable; check its executable path"
            finally:
                drained = self._drain(process)
                with self.lock:
                    if drained and self.processes.get(identity) is process:
                        self.processes.pop(identity, None)  # Never remove a newer attempt's process.
                    if self.closed or self._cancelled.get(identity) == attempt:
                        status, logged_in = "Native sign-in/status check cancelled", False
                    if not drained:
                        status, logged_in = "Native sign-in/status check could not stop; restart Angelo", False
                # A failed drain remains running/owned; it cannot trigger success or a new login.
                self.events.put((identity, status, logged_in, attempt, drained))

        try:
            threading.Thread(target=run, daemon=True, name="AngeloNativeAuthentication").start()
        except RuntimeError:
            self.events.put((identity, "Native sign-in/status check couldn't start; retry", False, attempt, True))
        return attempt

    def pump(self):
        changes = []
        while True:
            try:
                identity, status, logged_in, attempt, drained = self.events.get_nowait()
            except queue.Empty:
                return changes
            with self.lock:
                if self._attempts.get(identity) != attempt:
                    continue  # A late result never changes a newer login's readiness or running state.
                if self.closed or self._cancelled.get(identity) == attempt:
                    status, logged_in = "Native sign-in/status check cancelled", False
                if drained:
                    self.running.discard(identity)
                self.status[identity] = status
            changes.append(AuthChange(identity, logged_in, attempt))

    def cancel(self, identity, *, attempt=None):
        with self.lock:
            current = self._attempts.get(identity)
            if attempt is not None and attempt != current:
                return False
            if current is not None:
                self._cancelled[identity] = current  # Also fences a queued thread before process publication.
            process = self.processes.get(identity)
            if process and process.poll() is None:
                process.terminate()
            return current is not None or process is not None

    def close(self):
        with self.lock:
            self.closed = True
            for process in self.processes.values():
                if process.poll() is None:
                    process.terminate()
