# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How each side of a heavy run read the scene: per turn, its tools, its code and what came back (host, unbilled).

    python3 tests/python/angelo_agent/heavy_report.py RUN_FOLDER [--json OUT]

Reads a ``bench_mcp_vs_angelo.py`` run folder of ``scenario-heavy`` (or any
scenario set) and prints, per side, trial and turn: pass, wall time, model
requests, tokens and list price (from the run's own records), then

* the MCP side (Claude Code ``claude-stream.jsonl`` or Codex
  ``codex-stream.jsonl``): every tool call, how its code reads geometry
  (``ROUTES``: per-vertex loops, ``foreach_get``, NumPy, ray casts, bmesh,
  scene listings) and how many characters of tool results came back into the
  model's context, with an estimate in tokens (``CHARS_PER_TOKEN``);
* Angelo: the tools its receipt counted, its Python fallbacks, and the
  receipt's phases (checkpoint, preconditions, execution, main-thread time),
  which is where its own safety checks are paid.

Nothing is sent; nothing is judged here.
"""

import argparse
import json
import re
import sys
from pathlib import Path

CHARS_PER_TOKEN = 4.0   # A rough estimate for code and JSON; the turn's real input tokens are reported beside it.
ROUTES = {
    "per-vertex loop": re.compile(r"for\s+\w+\s+in\s+[\w.\[\]'\"]*\.(vertices|polygons|loops|verts|faces)\b|"
                                  r"\.vertices\[\s*\w+\s*\]"),
    "foreach_get": re.compile(r"foreach_get"),
    "numpy": re.compile(r"\bnumpy\b|\bnp\."),
    "ray_cast": re.compile(r"ray_cast|closest_point_on_mesh|BVHTree"),
    "bmesh": re.compile(r"\bbmesh\b"),
    "decimate": re.compile(r"DECIMATE|decimate", re.IGNORECASE),
    "normals": re.compile(r"normals_make_consistent|recalc_face_normals|flip_normals|\.flip\(|normal"),
}


def routes(code):
    return sorted(name for name, pattern in ROUTES.items() if pattern.search(code or ""))


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_text(part.get("text") if isinstance(part, dict) else part) for part in content)
    if isinstance(content, dict):
        return json.dumps(content)
    return "" if content is None else str(content)


def claude_calls(stream):
    """``{task: [call]}`` from a Claude Code stream: name, code, routes and result characters per tool call."""
    calls, by_id = {}, {}
    for line in Path(stream).read_text().splitlines():
        entry = json.loads(line)
        message, task = entry.get("message") or {}, entry.get("task")
        content = (message.get("message") or {}).get("content")
        if message.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    code = (block.get("input") or {}).get("code", "")
                    call = {"tool": block.get("name"), "code_chars": len(code), "routes": routes(code),
                            "input": json.dumps(block.get("input"))[:200] if not code else None, "result_chars": 0}
                    by_id[block.get("id")] = call
                    calls.setdefault(task, []).append(call)
        elif message.get("type") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    by_id[block["tool_use_id"]]["result_chars"] += len(_text(block.get("content")))
    return calls


def codex_calls(stream):
    """``{task: [call]}`` from a Codex app-server stream (``item/completed`` MCP and shell calls)."""
    calls = {}
    for line in Path(stream).read_text().splitlines():
        entry = json.loads(line)
        message = entry.get("message") or {}
        if message.get("method") != "item/completed":
            continue
        item = (message.get("params") or {}).get("item") or {}
        if item.get("type") not in ("mcpToolCall", "commandExecution"):
            continue
        code = (item.get("arguments") or {}).get("code", "") if item["type"] == "mcpToolCall" else ""
        result = item.get("result") if item["type"] == "mcpToolCall" else item.get("aggregatedOutput")
        calls.setdefault(entry.get("task"), []).append({
            "tool": item.get("tool") or item.get("command"), "code_chars": len(code), "routes": routes(code),
            "input": None if code else json.dumps(item.get("arguments"))[:200], "result_chars": len(_text(result))})
    return calls


def _usage(record):
    usage = record.get("provider_usage") or {}
    return {key: usage.get(key) for key in ("model_requests", "input_tokens", "cached_input_tokens",
                                             "output_tokens") if key in usage}


def side_rows(folder, records):
    rows = []
    for record in records:
        trial, side, task = record["trial"], record["side"], record["task"]
        row = {"side": side, "trial": trial, "task": task, "passed": record.get("host_check_passed"),
               "status": record.get("status"), "wall_s": round((record.get("wall_ms") or 0) / 1000, 1),
               "usd": record.get("list_price_usd"), **_usage(record)}
        if side == "mcp":
            trial_folder = Path(folder) / ("mcp-trial-%02d" % trial)
            stream = next((trial_folder / name for name in ("claude-stream.jsonl", "codex-stream.jsonl")
                           if (trial_folder / name).is_file()), None)
            if stream is not None:
                calls = (claude_calls if stream.name.startswith("claude") else codex_calls)(stream).get(task, [])
                chars = sum(call["result_chars"] for call in calls)
                row.update(tool_calls=len(calls), result_chars=chars,
                           result_tokens_estimate=int(chars / CHARS_PER_TOKEN),
                           routes=sorted({route for call in calls for route in call["routes"]}),
                           tools=sorted({call["tool"] for call in calls}), calls=calls)
            row["tool_round_trip_ms"] = record.get("tool_round_trip_ms_total")
        else:   # The trial's own record holds the receipt the bench's summary leaves out.
            found = Path(folder) / ("angelo-trial-%02d" % trial) / "eval-live-agent.json"
            tasks = json.loads(found.read_text())["trials"][0]["tasks"] if found.is_file() else []
            own = next((entry for entry in tasks if entry.get("task") == task), record)
            phases = (own.get("receipt") or {}).get("phases") or {}
            row.update(tools=(own.get("activity") or {}).get("tools"),
                       python_fallbacks=own.get("python_fallbacks"),
                       phases={key: phases.get(key) for key in ("checkpoint_ms", "precondition_ms", "execution_ms",
                                                                 "main_thread_block_ms", "approval_wait_ms",
                                                                 "total_ms")})
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)
    data = json.loads((Path(args.folder) / "bench-mcp-vs-angelo.json").read_text())
    rows = side_rows(args.folder, data["records"])
    for row in rows:
        brief = {key: row.get(key) for key in ("passed", "wall_s", "model_requests", "output_tokens", "usd",
                                               "tool_calls", "result_tokens_estimate", "routes", "tools", "phases")
                 if row.get(key) is not None}
        print("%-6s %d %-9s %s" % (row["side"], row["trial"], row["task"], json.dumps(brief)))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
