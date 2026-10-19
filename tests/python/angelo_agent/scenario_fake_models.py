# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripted models for the unbilled scenario dry run: no account, network service or inference.

* ``FakeMessages`` is a local Anthropic Messages endpoint. The real Claude Code
  CLI is pointed at it (``ANTHROPIC_BASE_URL``, a dummy key, a fresh profile),
  so its stream-json input, image blocks, MCP client and Blender Lab's MCP
  server all run for real. For each turn prompt it calls
  ``mcp__blender__execute_blender_code`` with that turn's ground-truth code
  (first ``ToolSearch`` when Claude Code deferred the tool), then replies.
* ``codex_script`` builds the turn-by-turn script for ``codex_prompt_probe.
  FakeResponses``, which stands in for OpenAI's Responses API behind the real
  ``codex app-server``.
* ``image_hashes`` finds every image a recorded request carried, so the dry
  run proves the reference bytes reached each model input unchanged.

Every request body is kept; nothing leaves 127.0.0.1.
"""

import base64
import hashlib
import http.server
import json
import re
import socketserver
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = "mcp__blender__execute_blender_code"


def turn_code(scenario, turn_id, job=None):
    """Python that applies one ground-truth turn in a live Blender (what a perfect model would send).

    It leaves ``sys.path`` and ``sys.modules`` as it found them, as a model's
    code would: the graders must import everything they use themselves.
    ``job``: the turn's background job (``scenario_specs``), for a side without
    Angelo's job tool: a still render is made in place, to the same relative
    path under Blender's working folder (see ``render_code``).
    """
    lines = ["import sys, bpy", "sys.dont_write_bytecode = True",
             "_saved_path, _saved_modules = list(sys.path), set(sys.modules)",
             "sys.path.insert(0, %r)" % str(HERE),
             "try:",
             "    import scenario_%s as _scenario" % scenario,
             "    _scenario.build_turn(bpy, %r)" % turn_id,
             "finally:",
             "    sys.path[:] = _saved_path",
             "    for _name in set(sys.modules) - _saved_modules:",
             "        del sys.modules[_name]"]
    if job:
        lines += render_code(job)
    lines.append("result = {'turn': %r, 'objects': len(bpy.context.scene.objects)}" % turn_id)
    return "\n".join(lines)


def render_code(job):
    """A blocking still render of a turn's ``RENDER_STILL`` job, as an MCP model without a job tool would do it."""
    if job.get("kind") != "RENDER_STILL":
        raise ValueError("The dry run can only play RENDER_STILL jobs, not %r" % job.get("kind"))
    width, height = job.get("resolution") or (1920, 1080)
    return ["import os",
            "_scene = bpy.context.scene",
            "_scene.render.resolution_x, _scene.render.resolution_y = %d, %d" % (width, height),
            "_scene.render.resolution_percentage = 100",
            "if _scene.render.engine == 'CYCLES':",
            "    _scene.cycles.samples = %d" % int(job.get("samples") or 8),
            "_scene.render.image_settings.file_format = %r" % job.get("format", "PNG"),
            "_scene.render.filepath = os.path.join(os.getcwd(), %r)" % job["output"],
            "bpy.ops.render.render(write_still=True)"]


def match_turn(text, tasks):
    """The task whose prompt this user text contains (prompts are distinct per turn).

    Containment, not a prefix: Claude Code puts its own reminder blocks before
    the prompt, and Angelo appends context after it.
    """
    for task in tasks:
        if task["prompt"][:120] in text:
            return task
    return None


def image_hashes(raw):
    """SHA-256 of every base64 image in a request body (Anthropic image blocks or OpenAI data URLs)."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    found = []
    for match in re.finditer(r'"data":\s*"([A-Za-z0-9+/=]{200,})"', text):
        found.append(hashlib.sha256(base64.b64decode(match.group(1))).hexdigest())
    for match in re.finditer(r'data:image/[a-z]+;base64,([A-Za-z0-9+/=]{200,})', text):
        found.append(hashlib.sha256(base64.b64decode(match.group(1))).hexdigest())
    return found


def codex_script(scenario, tasks):
    """FakeResponses steps: per turn, one ``exec`` that calls the MCP tool, then a closing message."""
    steps = []
    for task in tasks:
        build = ("const r = await tools.%s({code: %s}); text(JSON.stringify(r).slice(0, 300));"
                 % (TOOL, json.dumps(turn_code(scenario, task["id"], task.get("scenario_job")))))
        steps += [("exec", build), ("message", "Done: %s." % task["title"])]
    return steps


def _sse(events):
    return b"".join(b"event: %s\ndata: %s\n\n" % (event["type"].encode(), json.dumps(event).encode())
                    for event in events)


class FakeMessages:
    """A local Anthropic Messages endpoint that plays a perfect model for a scenario's turns."""

    def __init__(self, out, scenario, tasks, model="claude-opus-5-5"):
        self.out, self.scenario, self.tasks, self.model = Path(out), scenario, tasks, model
        self.out.mkdir(parents=True, exist_ok=True)
        self.bodies, self.count, self.lock = [], 0, threading.Lock()
        probe = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self._json({"data": [{"id": probe.model, "type": "model", "display_name": "Fake"}], "has_more": False})

            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                if "count_tokens" in self.path:
                    return self._json({"input_tokens": 1000})
                body, events = probe.reply(raw)
                if body.get("stream"):
                    data = _sse(events)
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json(probe.message(events))

            def _json(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="FakeMessages").start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()

    def reply(self, raw):
        with self.lock:
            index = self.count
            self.count += 1
        (self.out / ("request-%03d.json" % index)).write_bytes(raw)
        self.bodies.append(raw)
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        return body, self._events(index, body, self._decide(body))

    def _decide(self, body):
        """("tool", name, input) or ("text", text) for this request."""
        messages = body.get("messages") or []
        # Claude Code may append a system-role message after the user's; decide on the last user message.
        users = [message for message in messages if message.get("role") == "user"]
        last = users[-1] if users else {}
        content = last.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
        tools = {tool.get("name") for tool in body.get("tools") or []}
        if results:
            # After ToolSearch loaded the tool, call it for the turn that asked; otherwise close the turn.
            pending = self._turn_of(messages)
            if pending and TOOL in tools and not self._called(messages, pending):
                return ("tool", TOOL, {"code": turn_code(self.scenario, pending["id"], pending.get("scenario_job"))})
            return ("text", "Done: %s." % (pending["title"] if pending else "step"))
        text = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        task = match_turn(text, self.tasks)
        if task is None:
            return ("text", "OK")   # Any request the CLI makes on its own (titles, quota probes).
        if TOOL in tools:
            return ("tool", TOOL, {"code": turn_code(self.scenario, task["id"], task.get("scenario_job"))})
        if "ToolSearch" in tools:
            return ("tool", "ToolSearch", {"query": "select:" + TOOL, "max_results": 5})
        return ("text", "No Blender tool is available.")

    def _turn_of(self, messages):
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            text = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
            task = match_turn(text, self.tasks)
            if task:
                return task
        return None

    def _called(self, messages, task):
        """Whether the turn's build call was already made (after the turn's prompt)."""
        seen_prompt = False
        for message in messages:
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            if message.get("role") == "user":
                text = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text") \
                    if blocks else str(content or "")
                if match_turn(text, [task]):
                    seen_prompt = True
            elif seen_prompt and any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == TOOL
                                     for b in blocks):
                return True
        return False

    def _events(self, index, body, decision):
        identity = "msg_fake_%03d" % index
        start = {"type": "message_start", "message": {
            "id": identity, "type": "message", "role": "assistant", "model": body.get("model") or self.model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 1, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}}}
        if decision[0] == "tool":
            _, name, arguments = decision
            block = {"type": "tool_use", "id": "toolu_fake_%03d" % index, "name": name, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(arguments)}
            stop = "tool_use"
        else:
            block = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": decision[1]}
            stop = "end_turn"
        return [start, {"type": "content_block_start", "index": 0, "content_block": block},
                {"type": "content_block_delta", "index": 0, "delta": delta},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                 "usage": {"output_tokens": 40}},
                {"type": "message_stop"}]

    @staticmethod
    def message(events):
        """The non-streaming form of the same reply."""
        start, block, delta = events[0]["message"], dict(events[1]["content_block"]), events[2]["delta"]
        if block["type"] == "tool_use":
            block["input"] = json.loads(delta["partial_json"])
        else:
            block["text"] = delta["text"]
        return dict(start, content=[block], stop_reason=events[4]["delta"]["stop_reason"],
                    usage={"input_tokens": 1200, "output_tokens": 40})
