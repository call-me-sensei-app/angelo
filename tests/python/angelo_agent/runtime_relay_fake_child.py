# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in model runtime for ``test_native_runtime_relay``: JSON lines on stdio, Codex- or Claude-shaped.

It answers every request with what it saw (its own environment's keys included, so a test can prove they reach the
worker only redacted), asks its host one question of its own after ``thread/start``, writes its key to stderr (which
must stay in Angelo), and exits with code 3 on ``turn/interrupt`` or 0 when its stdin ends.
"""

import json
import os
import sys

KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("FAKE_RUNTIME_KEY") or ""
print("stderr line with the key " + KEY, file=sys.stderr, flush=True)
seen = []


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    seen.append(message.get("method") or message.get("type"))
    if message.get("type") == "control_request":
        send({"type": "control_response", "response": {"subtype": "success", "request_id": message["request_id"],
                                                       "response": {"key_seen": KEY, "models": []}}})
        if message["request"].get("subtype") == "initialize":
            send({"type": "control_request", "request_id": "child-ask-1", "request": {"subtype": "can_use_tool"}})
        continue
    if message.get("type") in ("user", "control_response"):
        send({"type": "assistant", "echo": message, "key": KEY})
        continue
    method = message.get("method")
    if method is None:   # An answer to the question it asked.
        send({"method": "got", "params": message})
        continue
    if "id" not in message:
        continue
    if method == "turn/interrupt":
        sys.exit(3)
    if method == "account/login/start":
        KEY = message["params"]["apiKey"]   # Echoed by account/read: Angelo must redact it on its way out.
    result = {"echo": method, "params": message.get("params"), "seen": seen[-5:]}
    if method == "initialize":
        result = {"userAgent": "fake/1.0"}
    elif method == "account/read":
        result = {"account": {"type": "apiKey"}, "note": "key " + KEY}
    elif method == "model/list":
        result = {"data": "x" * int((message.get("params") or {}).get("size", 1))}
    send({"id": message["id"], "result": result})
    if method == "thread/start":
        send({"id": 99, "method": "item/tool/call", "params": {"threadId": "t1", "tool": "angelo_inspect_scene"}})
