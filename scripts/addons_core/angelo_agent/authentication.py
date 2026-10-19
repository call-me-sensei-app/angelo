# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run providers' own sign-in commands. Never read or intermediate OAuth tokens."""

import json
import queue
import subprocess
import threading
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


class NativeAuthentication:
    def __init__(self):
        self.status = {}
        self.running = set()
        self.events = queue.Queue()
        self.processes = {}
        self.lock = threading.Lock()
        self.closed = False
        self.modes = {}

    def start(self, mode, settings, state_dir, login=False, identity=None, api_key=""):
        Path(state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        command, env = native_auth_command(mode, settings, state_dir, login, bool(api_key))
        identity = identity or mode
        self.modes[identity] = mode
        with self.lock:
            if identity in self.running or self.closed:
                raise RuntimeError("A native sign-in/status check is already running")
            self.running.add(identity)
            self.status[identity] = "Saving API credential…" if api_key else "Complete sign-in in your browser…" if login else "Checking native sign-in…"

        def run():
            process = None
            try:
                # With no flags Codex opens its own ChatGPT browser flow;
                # Claude's unmodified auth command handles its own browser flow. Cancelling or timing out
                # never closes that browser (on Windows it joins the CLI's Job Object): keep_gui_programs.
                process = launch(command, env=env, cwd=str(state_dir), stdin=subprocess.PIPE if api_key else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, keep_gui_programs=True)
                with self.lock:
                    self.processes[identity] = process
                    if self.closed:
                        process.terminate()
                output, _ = process.communicate(input=api_key + "\n" if api_key else None, timeout=600 if login else 30)
                if login:
                    status = "Sign-in complete. Refresh status to confirm." if process.returncode == 0 else "Sign-in did not complete. Retry or use the native Terminal command."
                else:
                    status = metadata_status(mode, process.returncode, output)
                self.events.put((identity, status, login and process.returncode == 0))
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                self.events.put((identity, "Native sign-in/status check timed out", False))
            except Exception:
                self.events.put((identity, "Native runtime unavailable; check its executable path", False))
            finally:
                with self.lock:
                    self.processes.pop(identity, None)

        threading.Thread(target=run, daemon=True, name="AngeloNativeAuthentication").start()

    def pump(self):
        changes = []
        while True:
            try:
                mode, status, logged_in = self.events.get_nowait()
            except queue.Empty:
                return changes
            self.running.discard(mode)
            self.status[mode] = status
            changes.append((mode, logged_in))

    def cancel(self, mode):
        with self.lock:
            process = self.processes.get(mode)
            if process and process.poll() is None:
                process.terminate()

    def close(self):
        with self.lock:
            self.closed = True
            for process in self.processes.values():
                if process.poll() is None:
                    process.terminate()
