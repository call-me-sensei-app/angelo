# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a picture from a dynamic tool keeps a Codex chat's request prefix: request bytes, unbilled.

A provider caches a request's longest prefix it has seen before, so a later request reads from the cache only up to
the first byte that differs from what was sent earlier. Gate receipts show turns with a picture (``angelo_view``)
reading about half their input over their two requests, while every other turn reads 96-99% (1.0.2's
``view_check_cropped``, cut-14's ``view_check``). This drives ``codex app-server`` with Angelo's launch flags and
picture forwarding (``runtime_launch.FORWARD_IMAGES_KEY``, as a bundled runtime launches) against
``codex_prompt_probe.FakeResponses``. Turn 1 and turn 2 each call ``angelo_view`` from code mode's ``exec``; the probe
answers with Angelo's packing (``references.codex_result_images``: text, then an ``inputImage`` data URL; a different
picture each time), then a plain turn follows. Every request body is kept.

The report compares each request with the one before it: ``kept_prefix_bytes`` is how much of the earlier request's
serialized input the later one starts with, ``first_changed_item`` the first earlier input item that is no longer the
same (with its type and whether it holds a picture), and the other top-level fields (instructions, tools, the cache
key). A chat that only appends keeps every earlier byte. Nothing leaves 127.0.0.1; CODEX_HOME and HOME are fresh
folders under OUT. Usage::

    python3.13 codex_image_prefix_probe.py OUT_DIR --codex <agent runtime's codex> [--model gpt-6.1-sol]
"""

import argparse
import base64
import json
import struct
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import codex_prompt_probe as probe  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402
from angelo_agent.providers import codex_instructions  # noqa: E402
from angelo_agent.runtime_launch import FORWARD_IMAGES_KEY  # noqa: E402
from angelo_agent.team_library_spec import TEAM_NAMES  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, codex_tools  # noqa: E402

VIEW = 'const v = await tools.angelo_view({views: [{camera: "SCENE_CAMERA", shading: "SOLID"}]}); text(v);'


def png(shade, width=16, height=16):
    """A valid greyscale PNG (a diagonal gradient, so it does not compress to nothing); ``shade`` makes each picture's
    bytes different. ``--size`` makes them viewport-sized, so the runtime's own image preparation (resizing) runs."""
    rows = b"".join(b"\x00" + bytes((shade + x + y) & 255 for x in range(width)) for y in range(height))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def compare(earlier, later):
    """How the later request starts from the earlier one: kept bytes, the first changed item, other fields."""
    a, b = json.loads(earlier), json.loads(later)
    left, right = a.get("input") or [], b.get("input") or []
    first = next((index for index, (x, y) in enumerate(zip(left, right)) if x != y), None)
    if first is None and len(right) < len(left):
        first = len(right)
    left_text = json.dumps(left, separators=(",", ":"))
    right_text = json.dumps(right, separators=(",", ":"))
    kept = next((index for index, (x, y) in enumerate(zip(left_text, right_text)) if x != y), min(len(left_text), len(right_text)))
    changed = None
    if first is not None:
        item = left[first]
        changed = {"index": first, "type": item.get("type"), "role": item.get("role"),
                   "has_picture": "input_image" in json.dumps(item) or "data:image" in json.dumps(item),
                   "earlier": json.dumps(item)[:300], "later": json.dumps(right[first])[:300] if first < len(right) else None}
    return {"earlier_items": len(left), "later_items": len(right), "kept_prefix_bytes": kept,
            "earlier_input_bytes": len(left_text), "first_changed_item": changed,
            "other_fields_equal": {name: a.get(name) == b.get(name) for name in sorted(set(a) | set(b)) if name != "input"}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--size", default="16x16", help="the pictures' WIDTHxHEIGHT (a viewport is about 2048x1536)")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    for name in ("state", "home", "workspace"):
        (out / name).mkdir(parents=True, exist_ok=True)
    steps = [("exec", VIEW), ("message", "Looked."), ("exec", VIEW), ("message", "Looked again."), ("message", "Thanks.")]
    fake = probe.FakeResponses(out, steps)
    command, env = probe.fake_command(args.codex, out, fake.port, ["%s=%s" % (FORWARD_IMAGES_KEY, json.dumps(["angelo_view"]))])
    (out / "command.json").write_text(json.dumps(command, indent=1))
    width, height = (int(value) for value in args.size.split("x"))
    events, pictures = [], [png(96, width, height), png(160, width, height)]

    def on_request(identity, method, params):
        if method == "item/tool/call" and params.get("tool") == "angelo_view":
            data = pictures.pop(0) if pictures else png(200, width, height)
            rpc.respond(identity, {"success": True, "contentItems": [
                {"type": "inputText", "text": '{"views":[{"view_id":"v1","camera":"SCENE_CAMERA"}]}'},
                {"type": "inputImage", "imageUrl": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}]})
        elif method == "item/tool/call":
            rpc.respond(identity, {"success": True, "contentItems": [{"type": "inputText", "text": '{"probe":true}'}]})
        else:
            rpc.write({"id": identity, "error": {"code": -32601, "message": "not handled by the probe"}})

    rpc = JsonRpcProcess(command, env=env, cwd=str(out / "workspace"),
                         on_event=lambda method, params: events.append(method), on_request=on_request)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        allowed = frozenset(TOOL_MAP) - TEAM_NAMES
        thread = rpc.request("thread/start", {
            "cwd": str(out / "workspace"), "modelProvider": "angelo_probe", "sandbox": "read-only",
            "approvalPolicy": "on-request", "serviceTier": "default", "model": args.model,
            "baseInstructions": codex_instructions(short_identifiers=True, deferred=True, team=False),
            "dynamicTools": codex_tools(allowed, short_identifiers=True)})["thread"]["id"]
        for text in ("Look at the scene.", "Look again.", "Thanks."):
            seen = len(events)
            rpc.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}],
                                       "effort": "low", "model": args.model})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and "turn/completed" not in events[seen:]:
                time.sleep(.05)
    finally:
        rpc.close()   # Waits for the exit: Windows keeps an exiting process's files locked (its state database).
    report = {"codex": args.codex, "model": args.model, "requests": len(fake.bodies),
              "pairs": [dict(compare(fake.bodies[i - 1], fake.bodies[i]), pair=[i - 1, i]) for i in range(1, len(fake.bodies))],
              "stderr_tail": rpc.stderr_tail[-5:]}
    (out / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
