# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in real Codex + explicitly fake Claude host-integration QA.

Only run in a factory-startup scratch Blender with ANGELO_QA_LIVE_CODEX=1.
Three tiny ordinary-tier ChatGPT requests; no paid Fast, keys, file mutations,
microphone or Claude inference. This cannot establish live Claude integration.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and os.environ.get("ANGELO_QA_LIVE_CODEX") == "1"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.controller import Controller
from angelo_agent.providers import CodexProvider
from angelo_agent.routing import RuntimeContext
from angelo_agent.chat_settings import normalize_models
from angelo_agent.blender_tools import BlenderExecutor

state = {"success": False, "checks": {}, "claude_runtime": "FIXTURE ONLY; no live Claude inference",
         "codex_runtime": "Official unmodified binary, native ChatGPT subscription, default tier, low effort"}
c = Controller(OUT / "state")
c.set_document(("shared-provider-live-scratch", bpy.context.scene.session_uid))
executor = BlenderExecutor(bpy.context.window_manager.windows[0])
gpt_id, claude_id = str(uuid.uuid4()), str(uuid.uuid4())
settings = {"model": "", "reuse_codex_login": True}
delegation_task = "Read-only: list the primitive object names in the supplied current scene observation in one short sentence. Do not call tools or change anything."
claude_models = normalize_models([{"value": "sonnet", "displayName": "Claude fixture", "isDefault": True}], "claude")


def write():
    (OUT / "shared-sessions.json").write_text(json.dumps(state, indent=2, default=str))


class ClaudeFixture:
    """A deterministic stand-in. No claim that Claude answered these turns."""
    def __init__(self, context, settings):
        self.controller = context

    def connect(self):
        identity = self.controller.state["native_id"] or str(uuid.uuid4())
        self.controller.emit("native_session", {"id": identity})
        return identity

    def run(self, identity, text, observation=None):
        if "fixture delegate" in text:
            import threading
            done, answer = threading.Event(), {}
            self.controller.request_tool("fixture-consult", "angelo_delegate",
                {"connection_id": gpt_id, "model": model["id"], "task": delegation_task},
                lambda result: (answer.update(result), done.set()))
            if not done.wait(180) or answer.get("error"):
                raise RuntimeError("Real Codex collaboration failed: " + str(answer))
            self.controller.emit("message_complete", {"item": "fixture", "text": "Fixture lead received real Codex advice: " + answer["reply"]})
            state["checks"]["fixture_claude_lead_received_real_codex_reply"] = bool(answer["reply"]) and answer["read_only"]
        else:
            self.controller.emit("message_complete", {"item": "fixture", "text": "The design codename is Cobalt Nova 72."})
        return "completed"

    def stop(self):
        pass

    def close(self):
        pass


def pump():
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        c.pump_events()
        c.pump_tools(executor, c.document)
        for call in list(c.pending.values()):
            if call.approval:
                assert call.name == "angelo_delegate", "No scene/file mutation is authorized by this test"
                assert call.arguments == {"connection_id": gpt_id, "model": model["id"], "task": delegation_task}
                c.execute_tool(call, executor, c.document)
                state["checks"]["exact_read_only_consultation_usage_approved"] = True
        if not c.busy and not (c._worker and c._worker.is_alive()):
            assert c.status == "Completed", c.state["messages"]
            return
        time.sleep(.015)
    raise RuntimeError("Live scratch test timed out")


try:
    probe = RuntimeContext(c, "codex_subscription", settings, "", gpt_id, c._epoch)
    probe.emit = lambda *args, **kwargs: None
    catalog = CodexProvider(probe, settings)
    try:
        models = catalog.list_models()
    finally:
        catalog.close()
    model = next((m for m in models if m["id"] == "gpt-6.1-sol"), next(m for m in models if m["default"]))
    state["model"] = model["id"]
    c.choose_connection("codex_subscription", settings, "", gpt_id, models, model["id"])
    c.set_options(approval="READ_ONLY", effort="low", fast=False)
    c.delegation_resolver = lambda identity: {"provider": "codex_subscription", "settings": settings, "api_key": "", "name": "Live ChatGPT", "models": models} if identity == gpt_id else None
    host_id = c.state["id"]
    baseline = executor.execute("angelo_inspect_scene", {})
    c.send("Reply briefly with 'GPT connected.' Do not call any tools or change anything.", baseline)
    pump()
    native_gpt = c.state["native_id"]
    state["checks"]["real_codex_with_expanded_host_toolset"] = bool(native_gpt)
    with patch("angelo_agent.controller.ClaudeProvider", ClaudeFixture):
        c.choose_connection("claude_subscription", settings, "", claude_id, claude_models, "sonnet")
        c.send("Fixture Claude: pick a design codename.", baseline)
        pump()
        native_fixture_claude = c.state["native_id"]
        c.choose_connection("codex_subscription", settings, "", gpt_id, models, model["id"])
        state["checks"]["return_resumes_real_gpt_native_id"] = c.state["native_id"] == native_gpt
        c.send("What design codename did the previous assistant pick? Reply with just that codename. Do not call tools.", baseline)
        pump()
        reply = next(m["text"] for m in reversed(c.state["messages"]) if m["role"] == "assistant")
        state["handoff_reply"] = reply
        state["checks"]["real_gpt_reads_shared_handoff_from_other_route"] = "Cobalt Nova 72" in reply
        c.choose_connection("claude_subscription", settings, "", claude_id, claude_models, "sonnet")
        c.send("Run fixture delegate consultation", baseline)
        pump()
    state["checks"]["same_host_chat_all_four_turns"] = c.state["id"] == host_id and sum(m["role"] == "user" for m in c.state["messages"]) == 4
    state["checks"]["native_contexts_stayed_separate"] = c.state["native_id"] == native_fixture_claude and native_fixture_claude != native_gpt
    observed = executor.execute("angelo_inspect_scene", {})
    state["checks"]["blender_scene_unchanged"] = {k: v for k, v in observed.items() if k != "native_exec_ms"} == {k: v for k, v in baseline.items() if k != "native_exec_ms"}
    state["checks"]["no_fast_billing"] = not c.state["options"]["fast"]
    state["success"] = all(state["checks"].values())
except Exception as error:
    import traceback
    state["error"], state["traceback"] = str(error), traceback.format_exc()
finally:
    c.close()
    write()
print("ANGELO_SHARED_SESSIONS_LIVE", json.dumps(state), flush=True)
assert state["success"], state
