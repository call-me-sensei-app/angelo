# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in REAL native GPT/Claude handoff and delegation, isolated scratch scene.

Both user's subscriptions are billed directly. No Fast, API fallback, microphone
or scene/file mutation. The existing Claude native profile is used in place,
never copied or read by this test. All host transcripts use a NEW QA directory.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and os.environ.get("ANGELO_QA_LIVE_BOTH") == "1"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.controller import Controller
from angelo_agent.providers import CodexProvider, ClaudeProvider
from angelo_agent.routing import RuntimeContext
from angelo_agent.blender_tools import BlenderExecutor

NATIVE_CLAUDE_DIR = Path(os.environ["ANGELO_QA_CLAUDE_STATE_DIR"])
CLAUDE_CONNECTION = str(uuid.UUID(os.environ["ANGELO_QA_CLAUDE_CONNECTION_ID"]))


class ExistingProfileClaude(ClaudeProvider):
    def __init__(self, context, settings):
        # Only credential-profile location changes. No mocked native responses,
        # token reading, file copying or modifications to the Claude binary.
        context.store = SimpleNamespace(directory=NATIVE_CLAUDE_DIR)
        super().__init__(context, settings)


state = {"success": False, "checks": {}, "replies": [],
         "runtimes": "REAL unmodified Codex 0.160.0 and Claude Code 2.1.258; user-owned subscriptions"}
c = Controller(OUT / "state")
c.set_document(("two-real-provider-scratch", bpy.context.scene.session_uid))
executor = BlenderExecutor(bpy.context.window_manager.windows[0])
# A unique fixture name proves the collaborator got real scene evidence;
# "Cube/Light/Camera" alone could be guessed from factory defaults.
proof_object = "AngeloContextProof_" + uuid.uuid4().hex[:10]
bpy.context.scene.objects["Cube"].name = proof_object
state["proof_object"] = proof_object
gpt_id, claude_id = str(uuid.uuid4()), CLAUDE_CONNECTION
gpt_settings = {"model": "", "reuse_codex_login": True}
claude_settings = {"model": "", "reuse_codex_login": False, "connection_id": CLAUDE_CONNECTION}
snapshot = executor.execute("angelo_inspect_scene", {})
scene_before = {k: v for k, v in snapshot.items() if k != "native_exec_ms"}
expect_collaborator = None


def write():
    (OUT / "cross-provider-live.json").write_text(json.dumps(state, indent=2, default=str))


def discover(provider_type, mode, settings, identity):
    probe = RuntimeContext(c, mode, settings, "", identity, c._epoch)
    probe.emit = lambda *args, **kwargs: None
    provider = provider_type(probe, settings)
    try:
        return provider.list_models()
    finally:
        provider.close()


def bind(identity):
    if identity == gpt_id:
        return {"provider": "codex_subscription", "settings": gpt_settings, "models": gpt_models, "name": "Live ChatGPT", "api_key": ""}
    if identity == claude_id:
        return {"provider": "claude_subscription", "settings": claude_settings, "models": claude_models, "name": "Live Claude Max", "api_key": ""}
    raise RuntimeError("QA does not authorize another billing account")


def select(identity, model):
    binding = bind(identity)
    c.choose_connection(binding["provider"], binding["settings"], "", identity, binding["models"], model["id"])
    c.set_options(approval="READ_ONLY", effort="low", fast=False)


def turn(prompt):
    previous = len(c.state["messages"])
    c.send(prompt, snapshot)
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        c.pump_events()
        c.pump_tools(executor, c.document)
        for call in list(c.pending.values()):
            if call.approval:
                assert call.name == "angelo_delegate", "This QA never approves a mutation"
                assert expect_collaborator is not None
                assert (call.arguments["connection_id"], call.arguments["model"]) == expect_collaborator
                c.execute_tool(call, executor, c.document)
        if not c.busy and not (c._worker and c._worker.is_alive()):
            assert c.status == "Completed", c.state["messages"][previous:]
            messages = c.state["messages"][previous:]
            reply = "\n".join(m["text"] for m in messages if m["role"] == "assistant")
            state["replies"].append({"provider": c.state["provider"], "text": reply[:2000]})
            write()
            return reply, messages
        time.sleep(.015)
    raise RuntimeError("Live two-provider test timed out")


try:
    with patch("angelo_agent.controller.ClaudeProvider", ExistingProfileClaude), patch("angelo_agent.delegation.ClaudeProvider", ExistingProfileClaude):
        gpt_models = discover(CodexProvider, "codex_subscription", gpt_settings, gpt_id)
        claude_models = discover(ExistingProfileClaude, "claude_subscription", claude_settings, claude_id)
        gpt_model = next((m for m in gpt_models if m["id"] == "gpt-6.1-sol"), next(m for m in gpt_models if m["default"]))
        claude_model = next((m for m in claude_models if m["id"] == "sonnet" or "sonnet" in m["id"]), claude_models[0])
        state["models"] = [gpt_model["id"], claude_model["id"]]
        state["claude_catalog"] = [{"id": m["id"], "name": m["name"]} for m in claude_models]
        c.delegation_resolver = bind
        c.collaborators = [{"connection_id": identity, "name": bind(identity)["name"],
            "billing": "Subscription; native user credentials", "models": [{"id": model["id"]}]}
            for identity, model in ((gpt_id, gpt_model), (claude_id, claude_model))]
        host_id = c.state["id"]
        select(gpt_id, gpt_model)
        turn("Remember this scene's design codename: Cobalt Nova 72. Acknowledge in one short sentence. Do not call tools or change anything.")
        native_gpt = c.state["native_id"]
        select(claude_id, claude_model)
        reply, _ = turn("What design codename did the previous assistant acknowledge? Reply with just that codename. Return an empty native operations list; do not call tools.")
        native_claude = c.state["native_id"]
        state["checks"]["real_claude_reads_gpt_handoff"] = "Cobalt Nova 72" in reply
        select(gpt_id, gpt_model)
        state["checks"]["gpt_native_context_resumed"] = c.state["native_id"] == native_gpt
        reply, _ = turn("What design codename did Claude repeat? Reply with just that codename; no tools.")
        state["checks"]["real_gpt_reads_claude_handoff"] = "Cobalt Nova 72" in reply
        select(claude_id, claude_model)
        state["checks"]["claude_native_context_resumed"] = c.state["native_id"] == native_claude
        expect_collaborator = (gpt_id, gpt_model["id"])
        prompt = "Consult ChatGPT using exactly one angelo_delegate operation: " + json.dumps({"connection_id": gpt_id, "model": gpt_model["id"], "task": "Read-only: list the primitive object names from the supplied scene observation in one short sentence. No tools or changes."})
        prompt += ". Set verify=true so you can use its actual reply. Then summarize its answer in one sentence. No other operations or changes."
        _, messages = turn(prompt)
        state["checks"]["claude_lead_consults_real_gpt"] = any(m.get("tool") == "angelo_delegate" and m.get("status") == "completed" and m.get("result", {}).get("reply") for m in messages)
        state["checks"]["gpt_collaborator_sees_real_scene_evidence"] = any(proof_object in m.get("result", {}).get("reply", "") for m in messages if m.get("tool") == "angelo_delegate")
        select(gpt_id, gpt_model)
        expect_collaborator = (claude_id, claude_model["id"])
        prompt = "Consult Claude using exactly one angelo_delegate call with " + json.dumps({"connection_id": claude_id, "model": claude_model["id"], "task": "Read-only: list the primitive object names from the supplied scene observation in one short sentence. No tools or changes."})
        prompt += ". Then summarize its actual returned answer in one sentence. Do not call other tools or change anything."
        _, messages = turn(prompt)
        state["checks"]["gpt_lead_consults_real_claude"] = any(m.get("tool") == "angelo_delegate" and m.get("status") == "completed" and m.get("result", {}).get("reply") for m in messages)
        state["checks"]["claude_collaborator_sees_real_scene_evidence"] = any(proof_object in m.get("result", {}).get("reply", "") for m in messages if m.get("tool") == "angelo_delegate")
        state["checks"]["one_shared_chat_all_five_requests"] = c.state["id"] == host_id and sum(m["role"] == "user" for m in c.state["messages"]) == 5
        state["checks"]["native_ids_not_conflated"] = native_gpt != native_claude
        observed = executor.execute("angelo_inspect_scene", {})
        state["checks"]["scene_unchanged"] = scene_before == {k: v for k, v in observed.items() if k != "native_exec_ms"}
        state["success"] = all(state["checks"].values())
except Exception as error:
    import traceback
    state["error"], state["traceback"] = str(error), traceback.format_exc()
finally:
    c.close()
    write()
print("ANGELO_TWO_REAL_PROVIDERS", json.dumps(state), flush=True)
assert state["success"], state
