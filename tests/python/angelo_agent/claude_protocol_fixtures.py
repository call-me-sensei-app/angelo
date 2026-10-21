# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled stdio fixtures; no real executable, credentials, session or inference.

The initialization fixture models the inspected Claude Code contract (2.1.258;
systemPrompt 2.1.292 and 2.1.301): jsonSchema becomes the session's
structured-output schema, the systemPrompt strings become its whole system
prompt, and Boolean systemPromptSnapshot controls prompt recording. These fields are also in Anthropic's published SDK types.
This is protocol coverage, not a claim that mocks prove native inference.
"""

import json
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.chat_settings import clean_options
from angelo_agent.cli_runtime import RUNTIMES, format_version


def claude_version(version):
    """What ``claude --version`` prints for ``version`` (a tuple)."""
    return format_version(version) + " (Claude Code)\n"


def _before(version):
    major, minor, patch = version
    return (major, minor, patch - 1) if patch else (major, minor - 1, 999) if minor else (major - 1, 999, 999)


# The supported range as cli_runtime states it, so a floor or ceiling change needs no fixture edits.
FLOOR, CEILING = RUNTIMES["claude"]["floor"], RUNTIMES["claude"]["ceiling"]
SUPPORTED = claude_version(FLOOR)                   # The oldest Claude Code Angelo runs.
TOO_OLD = claude_version(_before(FLOOR))            # The release before it: refused.
TESTED = claude_version(CEILING)                    # The newest gated: no log line.
NEWER = claude_version(CEILING[:2] + (CEILING[2] + 1,))  # Newer than gated: runs as usual (one log line).
REQUIRES = "Angelo requires Claude Code %s or newer" % format_version(FLOOR)


class QueuePipe:
    def __init__(self):
        self.lines = queue.Queue()
        self.reads = 0

    def __iter__(self):
        while True:
            value = self.lines.get(timeout=5)
            if value is None:
                return
            self.reads += 1
            yield value

    def emit(self, event):
        self.lines.put(json.dumps(event) + "\n")

    def end(self):
        self.lines.put(None)


class InputPipe:
    def __init__(self, process):
        self.process = process
        self.messages = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise ValueError("Fixture stdin is closed")
        event = json.loads(data)
        self.messages.append(event)
        self.process.receive(event)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class NativeInitializeFixture:
    def __init__(self, *, on_user=None, initialization_error=None):
        self.stdout, self.stderr = QueuePipe(), QueuePipe()
        self.stdin = InputPipe(self)
        self.on_user = on_user
        self.initialization_error = initialization_error
        self.initialized_schema = None
        self.system_prompt = None
        self.prompt_snapshot = None
        self.returncode = None
        self.user_written = threading.Event()
        self.waits = 0
        # apply_flag_settings (a running session's effort): "success", "error" or None (never answered).
        self.flag_settings_answer, self.flag_settings = "success", []

    def receive(self, event):
        if event["type"] == "control_request" and event["request"]["subtype"] == "initialize":
            self.initialized_schema = event["request"]["jsonSchema"]
            self.system_prompt = "".join(event["request"]["systemPrompt"])
            self.prompt_snapshot = event["request"]["systemPromptSnapshot"]
            response = {"subtype": "success", "request_id": event["request_id"],
                        "response": {"models": [{"value": "sonnet", "displayName": "Sonnet"}]}}
            if self.initialization_error:
                response.update(subtype="error", error=self.initialization_error)
            self.stdout.emit({"type": "control_response", "response": response})
        elif event["type"] == "control_request" and event["request"]["subtype"] == "apply_flag_settings":
            self.flag_settings.append(event["request"]["settings"])
            if self.flag_settings_answer is not None:
                response = {"subtype": self.flag_settings_answer, "request_id": event["request_id"]}
                if self.flag_settings_answer == "error":
                    response["error"] = "apply_flag_settings: unrecognized effortLevel"
                self.stdout.emit({"type": "control_response", "response": response})
        elif event["type"] == "user":
            self.user_written.set()
            if self.on_user:
                self.on_user(self, event)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waits += 1
        self.returncode = 0
        self.stdout.end()
        self.stderr.end()
        return self.returncode

    def terminate(self):
        self.wait()

    def kill(self):
        self.wait()


def host():
    context = SimpleNamespace(cancelled=False, turn_options=clean_options({}),
        turn_attachments=[], state={"provider": "claude_subscription", "options": {}, "native_id": None},
        workspace=Path("/fixture/workspace"), instructions="", allowed_tools=None,
        native_tools=("WebSearch", "WebFetch"), _epoch=7, _request_generation=11,
        references=SimpleNamespace(claude_inputs=Mock(return_value=[]), tool_images=Mock(return_value=[])),
        record_provider_turn=Mock(), record_model_round=Mock(), record_runtime_start=Mock(),
        record_provider_usage=Mock(),
        events=[], request_tool=Mock())
    def emit(kind, data):
        context.events.append((kind, data))
        if kind == "native_session":
            context.state["native_id"] = data["id"]
    context.emit = emit
    return context


def completed(reply="Verified"):
    return {"type": "result", "subtype": "success", "is_error": False,
            "session_id": "fixture-native", "structured_output": {
                "reply": reply, "operations": [], "verify": False}}


class ClaudeAdapterFixture:
    """Non-TestCase fixture: every concrete suite has its own discoverable cases."""

    def setUp(self):
        from angelo_agent.providers import ClaudeProvider
        self.host = host()
        self.provider = ClaudeProvider(self.host, {})
        self.provider.executable = "claude.exe"
        self.provider.env = {"FIXTURE": "only"}
        self.process = NativeInitializeFixture(on_user=lambda process, _: process.stdout.emit(completed()))
        self.probe = patch("angelo_agent.child_process.run", return_value=SimpleNamespace(
            returncode=0, stdout=SUPPORTED))
        self.launch = patch("angelo_agent.child_process.popen", return_value=self.process)
        self.run_mock, self.popen_mock = self.probe.start(), self.launch.start()
        self.addCleanup(self.probe.stop)
        self.addCleanup(self.launch.stop)
        self.addCleanup(self.provider.close)

    def start(self, native_id, *, owner=None):
        from angelo_agent.claude_transport import ClaudeRequest
        return self.provider._start(native_id, owner=owner or ClaudeRequest(self.provider.controller))

    def turn(self, native_id, text, references=(), *, owner=None):
        from angelo_agent.claude_transport import ClaudeRequest
        return self.provider._turn(native_id, text, references,
                                   owner=owner or ClaudeRequest(self.provider.controller))
