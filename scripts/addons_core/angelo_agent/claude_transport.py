# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded native Claude stdio initialization and process/turn ownership.

Claude Code's native initialize handler and its bundled Agent SDK accept
``jsonSchema`` and ``systemPrompt`` on stdin (inspected in 2.1.258; ``systemPrompt``
probed on 2.1.292 and 2.1.301, ``prompt_fields``). Keep both out of argv; an older CLI may silently ignore unknown initialize fields, so
a launch requires the floor in ``cli_runtime.RUNTIMES``, which follows Claude
Code's releases and is launch-checked on each (doc/angelo/ai_providers.rst,
"Claude Code's range"). No SDK dependency or MCP is used.
"""

import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field

from .claude_stream import RoundStream
from .cli_runtime import RUNTIMES, run_cli, too_old_message
from .protocol import RpcError
from .provider_usage import ClaudeProcessUsage
from .request_fences import RequestFence
from .turn_activity import TurnActivity


MINIMUM_VERSION = RUNTIMES["claude"]["floor"]  # The tested range lives in cli_runtime.
# Far below CreateProcessW's 32767 UTF-16-unit limit, including its terminator.
# This applies on every host so schema/instruction regressions are testable.
COMMAND_BUDGET = 8192
# A control answer comes at once; without one the session is relaunched with the request's --effort instead.
EFFORT_SECONDS = 10.0


def require_supported_runtime(executable, env):
    """Version-only probe: never authenticate, initialize a session or infer.

    Below the floor, or unreadable, the launch is refused with the runtime
    update notice (``cli_runtime.too_old_message``): the version found and how
    to update, as for Codex.
    """
    try:
        probe = run_cli([executable, "--version"], env=env,
                        capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RpcError(too_old_message("claude")) from error
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+) \(Claude Code\)", probe.stdout.strip())
    version = tuple(map(int, match.groups())) if match and probe.returncode == 0 else None
    if version is None or version < MINIMUM_VERSION:
        raise RpcError(too_old_message("claude", version))
    return version


def windows_command_units(command):
    """Account for Python's actual Windows quoting and non-BMP Unicode."""
    return len(subprocess.list2cmdline(command).encode("utf-16-le")) // 2 + 1


def check_command_budget(command):
    if windows_command_units(command) > COMMAND_BUDGET:
        raise RpcError("Claude launch arguments exceed Angelo's safe Windows "
                       "command-line budget. Check executable/model settings; "
                       "schema and instructions must use native stdin initialization.")


def prompt_fields(instructions):
    """Angelo's instructions as the session's whole system prompt, rebuilt on every request (no snapshot).

    ``systemPrompt`` replaces Claude Code's own coding-CLI prompt (about 5.2 KB: a terminal, file paths, coding
    style, Claude model IDs) instead of following it, as ``appendSystemPrompt`` did; Claude Code still opens every
    request with its billing block and one identity line of its own, whichever field is sent.
    """
    return {"systemPrompt": [instructions], "systemPromptSnapshot": False}


def initialization_request(schema, instructions):
    """The complete unchanged schema, not a narrowed or prompt-only substitute."""
    return {"type": "control_request", "request_id": "angelo-init", "request": {
        "subtype": "initialize", "hooks": None, "jsonSchema": schema, **prompt_fields(instructions)}}


def claude_session(executable, context, native_id=None, default_model=None):
    """``(command, initialize)``: one Claude Code session's arguments and its stdin initialization.

    The schema and instructions travel in the initialization request, never on
    the command line. The provider and the unbilled prompt probe
    (``tests/python/angelo_agent/claude_prompt_probe.py``) both build a launch here.
    """
    import json
    from . import claude_direct, debug_thinking
    from .chat_settings import claude_effort, clean_options
    from .claude_text import CLAUDE_BASE_INSTRUCTIONS as CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS, NO_WEB_SEARCH
    from .tool_specs import native_plan_schema
    options = context.turn_options or clean_options(context.state.get("options"))
    native_tools = ",".join(getattr(context, "native_tools", ("WebSearch", "WebFetch")))
    direct = claude_direct.enabled(context)   # Opt-in: Angelo's tools as direct calls instead of one plan per round.
    allowed_tools = native_tools + ("," + claude_direct.ALLOW_RULE if direct else "")
    # Never --bare: its built-in tools are only shell and file tools (runtime_environment.require_angelo_profile).
    command = [executable, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--include-partial-messages", "--tools", native_tools, "--allowedTools", allowed_tools, "--strict-mcp-config",
               "--permission-mode", "dontAsk", "--setting-sources", "",
               "--disable-slash-commands", "--settings", json.dumps({"fastMode": options["fast"], "hooks": {}}),
               "--mcp-config", '{"mcpServers":{}}',
               # The working directory, date and other per-machine lines leave the system prompt (Claude Code
               # moves them to the first user message), so the plan schema, the system prompt and Angelo's
               # appended instructions stay one byte-identical cached prefix across chats, days and installs.
               "--exclude-dynamic-system-prompt-sections"]
    # A context's own base (Studio's prompt editor) is its whole prompt: none of the chat agent's base, plan or
    # direct-tools text, which describe scenes, sculpting and views it can't touch (test_native_studio_editor_prompt).
    base = getattr(context, "base_instructions", None)
    web = "" if "WebSearch" in native_tools.split(",") else NO_WEB_SEARCH
    agent = "" if base is not None else claude_direct.INSTRUCTIONS if direct else CLAUDE_PLAN_INSTRUCTIONS
    instructions = (CLAUDE_INSTRUCTIONS + web if base is None else base) + getattr(context, "instructions", "") + agent
    initialize = (claude_direct.initialization_request(instructions) if direct else
                  initialization_request(native_plan_schema(getattr(context, "allowed_tools", None)), instructions))
    model = options["model"] or default_model
    if not model:   # A catalog with exactly one model has it chosen (chat_settings.selected_model): run that one.
        from .chat_settings import selected_model
        chosen = selected_model(context, options) if hasattr(context, "models") else None
        model = chosen["id"] if chosen and not chosen.get("runtime_default_alias") else ""
    if options["fast"] and not (model and (model.split("[", 1)[0] == "opus" or any(x in model for x in ("opus-5", "opus-4-8")))):
        raise RpcError("Select a supported Opus model before enabling Claude Fast mode; no automatic model switch is allowed")
    command += (["--model", model] if model else []) + ["--effort", claude_effort(options["effort"])]
    # Diagnostic runs only (debug_thinking.py): the thinking summaries a harness reads to find where a miss began.
    command += debug_thinking.claude_arguments(context)
    command += ["--resume=" + native_id] if native_id else []
    return command, initialize


def context_fence(controller):
    fence = getattr(controller, "request_fence", None)
    host = getattr(controller, "host", controller)
    if fence is None and hasattr(host, "_request_generation"):
        fence = RequestFence.capture(host)
    return fence


@dataclass(frozen=True)
class ClaudeRequest:
    """Immutable per-run authority; rebinding a provider cannot replace it."""

    controller: object
    host: object = field(init=False)
    fence: object = field(init=False)
    epoch: object = field(init=False)
    generation: object = field(init=False)

    def __post_init__(self):
        host = getattr(self.controller, "host", self.controller)
        fence = context_fence(self.controller)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "fence", fence)
        # A delayed worker can reach this constructor after the host advances;
        # its captured context's fence remains the authority, not today's host.
        object.__setattr__(self, "epoch", fence.epoch if isinstance(fence, RequestFence) else getattr(host, "_epoch", None))
        object.__setattr__(self, "generation", fence.generation if isinstance(fence, RequestFence) else getattr(host, "_request_generation", None))

    def owns_request(self):
        return (not self.controller.cancelled
                and not getattr(self.host, "_closing", False)
                and getattr(self.host, "_epoch", None) == self.epoch
                and getattr(self.host, "_request_generation", None) == self.generation)

    def same_request(self, other):
        return (isinstance(other, ClaudeRequest) and self.controller is other.controller
                and self.fence == other.fence and self.epoch == other.epoch
                and self.generation == other.generation)


class ClaudeTurn:
    """One waiter, never recycled for a subsequent host request or model round."""

    def __init__(self, owner):
        self.owner = owner if isinstance(owner, ClaudeRequest) else ClaudeRequest(owner)
        self.controller = self.owner.controller
        self.done = threading.Event()
        self.result = None
        self.error = None
        self.accepted = False  # First API response acknowledging this input, never the local stdin write.
        # Every StructuredOutput call of this turn the runtime accepted, by
        # tool_use id, in order: it returns only the last one
        # (plan_validation.superseded_plans).
        self.structured_outputs = {}
        # Signs of life and what the round streamed (turn_activity, claude_stream).
        self.activity = TurnActivity()
        self.stream = RoundStream(getattr(self.controller, "record_running_usage", None), self.count_retry,
                                  self.count_streamed)

    def owns_request(self):
        return self.owner.owns_request()

    def count_retry(self, kind):
        """Claude Code retried an API request of this turn: the receipt counts it (``claude_stream.retry_kind``)."""
        from .codex_attempts import receipt_count
        from .codex_retry import COUNTS
        receipt_count(self.controller, COUNTS[kind])

    def count_streamed(self, chars):
        """Characters this turn streamed, for the chat's live token count (``usage_meter.streamed``)."""
        from .usage_meter import streamed
        host = getattr(self.controller, "host", self.controller)
        streamed(getattr(host, "_request_metrics", None), chars)


class ClaudeTransport:
    """Captured pipes and initialization state for exactly one native process."""

    def __init__(self, process, owner, *, resumed=False, native_id=None, effort=None):
        self.process = process
        self.owner = owner
        # The session it was launched for (``--resume``), and whether a turn ever ran on it: a session
        # launched before its request (runtime_prewarm) serves only a request for that same session.
        self.native_id, self.used = native_id, False
        self.lock = threading.RLock()
        self.close_lock = threading.Lock()
        self.retired = threading.Event()
        self.initialized = threading.Event()
        self.initialization_error = None
        self.initialization_response = {}
        self.turn = None
        self.interrupted = False
        # The session's reasoning effort: its --effort, then each change it acknowledged (``apply_effort``).
        self.effort = effort
        self._controls = {}   # request_id -> [Event, the control response]: Angelo's requests awaiting an answer.
        # Result usage is cumulative per process, so each process differences
        # its own consecutive results; a resumed one starts from restored totals.
        self.usage = ClaudeProcessUsage(resumed=resumed)

    def begin_turn(self, owner):
        with self.lock:
            if (not self.owner.same_request(owner) or not owner.owns_request()
                    or self.retired.is_set() or self.turn is not None):
                raise RpcError("Claude runtime is no longer available for this turn; reconnect")
            self.turn, self.used = ClaudeTurn(owner), True
            return self.turn

    def end_turn(self, turn):
        with self.lock:
            if self.turn is turn:
                self.turn = None

    def expect(self, request_id):
        """Register an Angelo control request before it is written; the waiter ``answer`` completes."""
        with self.lock:
            waiter = self._controls[request_id] = [threading.Event(), None]
            if self.retired.is_set():
                waiter[0].set()   # A retired session answers nothing.
            return waiter

    def answer(self, response):
        """Complete the waiter of the control request ``response`` answers; whether there was one."""
        with self.lock:
            waiter = self._controls.pop(response.get("request_id"), None)
        if waiter is not None:
            waiter[1] = response
            waiter[0].set()
        return waiter is not None

    def retire(self, reason, *, interrupted=False):
        with self.lock:
            self.retired.set()
            for waiter in self._controls.values():   # A request the process never answers fails at once.
                waiter[0].set()
            self._controls.clear()
            self.interrupted = self.interrupted or interrupted
            self.initialization_error = self.initialization_error or reason
            self.initialized.set()
            if self.turn is not None:
                if self.turn.result is None:
                    self.turn.error = reason
                self.turn.done.set()
                self.turn = None


def apply_effort(write, transport, effort, timeout=None):
    """Give a running session the next turn's reasoning effort; whether it now has ``effort``.

    Effort is a request parameter, not a launch match: Claude Code's SDK control
    handler changes a running session's level with ``apply_flag_settings``
    ``{effortLevel}`` (read in the 2.1.292 and 2.1.293 binaries, the floor of the
    gated range: it sets the session's effort, as ``/effort`` does), so a chat
    at another effort keeps its warm process. ``write(message)`` sends on the
    session's stdin. An error answer, none within ``timeout`` or a retired
    session gives False, and the caller relaunches with ``--effort``, as before.
    """
    if transport.effort == effort:
        return True
    request_id = "angelo-effort-" + uuid.uuid4().hex[:12]
    waiter = transport.expect(request_id)
    try:
        write({"type": "control_request", "request_id": request_id,
               "request": {"subtype": "apply_flag_settings", "settings": {"effortLevel": effort}}})
        answered = waiter[0].wait(EFFORT_SECONDS if timeout is None else timeout)
    except (RpcError, OSError, ValueError):
        answered = False
    if not answered or not isinstance(waiter[1], dict) or waiter[1].get("subtype") != "success":
        transport.answer({"request_id": request_id})   # Forget it: a late answer completes nothing.
        return False
    transport.effort = effort
    return True


def launch_options(controller):
    """The request's options a running session must have been launched with: all but effort (``apply_effort``)."""
    from .chat_settings import clean_options
    from .runtime_prewarm import launch_options as without_effort
    return without_effort(controller.turn_options or clean_options(controller.state.get("options")))


def session_effort(provider, transport, owner, controller):
    """``apply_effort`` on ``provider``'s session with the effort ``controller``'s request launches with (``--effort``)."""
    from .chat_settings import claude_effort, clean_options
    options = controller.turn_options or clean_options(controller.state.get("options"))
    return apply_effort(lambda message: provider._write(message, transport, owner=owner), transport,
                        claude_effort(options["effort"]))


def discard_stderr(pipe):
    # Consume without retaining prompts, errors or an unbounded list of lines.
    for _ in pipe:
        pass


def close_process(process):
    """Close only the captured process, even when a newer one is already warm."""
    if process.poll() is not None:
        return
    try:
        process.stdin.close()
        process.wait(timeout=3)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
