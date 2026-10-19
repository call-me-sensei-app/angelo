# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot duties of the Blender MCP side, for both of its runtimes (Claude Code and Codex).

``bench_mcp_scenario`` hands a one-shot test's steps to this module:

* ``client_command``: what the Claude Code client is allowed besides the
  Blender MCP server's tools. A person doing this work lets the client look
  at the pictures it rendered, so its Read tool is allowed on image files
  anywhere (``IMAGE_READS``), and the trial folder (stock Blender's working
  and temporary folder) is an additional directory, where its read-only file
  tools work as in its own working folder. Without this, a profile whose
  permission mode asks before reading outside the working folder has every
  such read denied, because nobody answers prompts (measured with
  ``--permission-mode default``). Codex needs nothing more: its read-only
  sandbox already reads files and shows images. Shell writes, edits and the
  web still prompt on Claude Code, and a prompt nobody answers is denied and
  counted. (Model-written Python in stock Blender is unrestricted on this
  side in every task set.)
* ``guard``: the stream can be sent to once; a second ``send`` raises. The
  reference images are copied into the client's working folder first (same
  bytes), as a person's project would hold them, so Codex is never handed a
  path next to the ground truth. While the turn runs, the model usage in the
  stream is priced at list prices, per model response (a price step is per
  request), and the turn is interrupted past the cost cap. The time cap is
  the trial's own task timeout.
* ``grade_outputs``: the brief's still, looked for in stock Blender's working
  and temporary folders, the client's working folder, where the scene's
  render path points and at any path the client itself stated; the file its
  final reply names as final is judged (``final_render``); written after the
  message was sent; copied into the trial folder for the harness's pixel
  check.
"""

import os
import shutil
import time
from pathlib import Path

import oneshot_checks
import oneshot_metrics as metrics
import oneshot_produced
import scenario_specs

# Permission rules: Claude Code's Read tool on image files anywhere ("//" is the filesystem root), nothing else.
IMAGE_SUFFIXES = ("png", "jpg", "jpeg", "exr", "webp", "tif", "tiff", "bmp")
IMAGE_READS = tuple("Read(//**/*.%s)" % suffix for suffix in IMAGE_SUFFIXES)
SLICE_S = 2.0
STOP_GRACE_S = 30.0
ONE_MESSAGE = "A one-shot test sends exactly one message; a second one was refused"
RENDER_PATH_CODE = "import bpy\nresult = {'path': bpy.path.abspath(bpy.context.scene.render.filepath)}\n"
ALLOWANCES = {
    "claude": ["every tool of the Blender MCP server (model-written Python included, unrestricted)",
               "Claude Code's Read tool on image files anywhere, and its read-only file tools in its working folder "
               "and in the trial folder (stock Blender's working and temporary folder): it can open the images it "
               "rendered",
               "the reference images also as files in its working folder",
               "anything else that would prompt (shell, edits, web) is denied and counted: nobody answers prompts"],
    "codex": ["every tool of the Blender MCP server (model-written Python included, unrestricted), approved "
              "automatically",
              "Codex's read-only sandbox: shell commands run read-only and it can open local images, the ones it "
              "rendered included",
              "the reference images as files in its working folder",
              "nothing asks a person: approval_policy is never"],
}


def plan(args):
    """A one-shot test's part of the bench's plan: one message, its effective limits, the client's allowances."""
    task_set = getattr(args, "task_set", "default")
    if not scenario_specs.is_oneshot(task_set):
        return {}
    limits = dict(scenario_specs.limits(scenario_specs.scenario_id(task_set)), wall_s=args.task_timeout)
    limits["cost_usd"] = getattr(args, "cost_cap", 0) or limits["cost_usd"]
    follow_ups = ("one diagnostic question after grading and saving, never graded (--ask-why-on-miss)"
                  if getattr(args, "ask_why_on_miss", False) else "none: the harness never types again")
    return {"oneshot": {"messages_per_side": 1, "follow_ups": follow_ups, "limits": limits,
                        "other_side_client": ALLOWANCES[getattr(args, "runtime", "claude")],
                        "grading": "closed checks and pixel statistics only; no model-judged score is counted"}}


def client_command(command, folder):
    """Claude Code's command with the one-shot allowances: image reads anywhere, and the trial folder to look in."""
    command = list(command)
    position = command.index("--allowedTools") + 1
    command[position] = ",".join([command[position]] + list(IMAGE_READS))
    return command + ["--add-dir", str(folder)]


def cost_cap(config):
    name = scenario_specs.scenario_id(config["task_set"])
    return float(config.get("cost_cap") or 0) or scenario_specs.limits(name)["cost_usd"]


def copy_references(images, cwd):
    """The reference files copied into the client's working folder (``references/``); returns the copies."""
    if not images:
        return []
    folder = Path(cwd) / "references"
    folder.mkdir(parents=True, exist_ok=True)
    return [shutil.copyfile(path, folder / ("%02d-%s" % (index, Path(path).name)))
            for index, path in enumerate(images, 1)]


def guard(stream, config, folder, output):
    """One message, a cost cap and the working-folder references on a Claude Code or Codex stream.

    ``output["oneshot"]`` is the state both wrappers update: messages sent,
    when, and why the harness stopped the turn (None when it did not).
    """
    state = output["oneshot"] = {"messages": 0, "refused_sends": 0, "stop": None, "sent_wall": None,
                                 "cost_cap_usd": cost_cap(config), "folder": str(folder), "watch": {},
                                 "allowances": ALLOWANCES["codex" if config.get("runtime") == "codex" else "claude"]}
    send, collect = stream.send, stream.collect
    codex = config.get("runtime") == "codex"
    usage_of = metrics.usage_from_codex if codex else metrics.usage_from_claude
    requests_of = metrics.codex_requests if codex else metrics.claude_requests

    def send_once(text, images=()):
        if state["messages"]:
            state["refused_sends"] += 1
            raise RuntimeError(ONE_MESSAGE)
        copies = copy_references(images, config["claude_cwd"])
        state.update(messages=1, sent_wall=time.time())
        return send(text, copies)

    def collect_watched(deadline, task):
        """The stream's own ``collect`` in short slices, pricing the usage seen between them."""
        events = []
        while True:
            more, result, problem = collect(min(deadline, time.monotonic() + SLICE_S), task)
            events += more
            if result is not None or problem == "exited" or time.monotonic() >= deadline:
                return events, result, problem
            usage = usage_of(events)
            # Priced per model response, as a price step applies (oneshot_metrics.cap_cost).
            cost, bound = metrics.cap_cost(usage, config.get("model"), "5m", requests_of(events))
            if usage:
                state["watch"] = {"usage": usage, "cost_usd": None if bound else cost}
                state["watch"].update({"cap_cost_usd": cost, "cap_bound": bound} if bound else {})
            if cost is not None and cost >= state["cost_cap_usd"] and state["stop"] is None:
                state["stop"] = {"reason": "cost_cap", "cost_usd": cost, "cap_usd": state["cost_cap_usd"],
                                 **({"bound": bound} if bound else {})}
                stream.interrupt()
                deadline = min(deadline, time.monotonic() + STOP_GRACE_S)

    stream.send, stream.collect = send_once, collect_watched
    return state


def said(folder, record):
    """What the client wrote so far (its raw stream: scripts and replies), where it may name the still's path."""
    texts = [str((record.get("result") or {}).get("reply") or "")]
    for name in ("claude-stream.jsonl", "codex-stream.jsonl"):
        path = Path(folder or "/nonexistent") / name
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(texts)


def grade_outputs(client, task, config, record, output, roots):
    """The brief's still on the MCP side; ``roots`` are stock Blender's working and temporary folders."""
    state = output.get("oneshot") or {}
    render_path = client.execute(RENDER_PATH_CODE).get("path", "")
    roots, wrote = list(roots) + [config["claude_cwd"]], said(state.get("folder"), record)
    import bench_mcp_scenario   # The render judged is the one the reply names as final (final_render).
    result = oneshot_checks.with_outputs(record["host_check"], task["scenario_outputs"], roots,
                                         state.get("sent_wall"), render_path, wrote,
                                         bench_mcp_scenario.final_reply(record))
    record.update(host_check=result, host_check_passed=result["success"])
    # Every image file the run wrote, whatever its name: a missing still is then "rendered something else"
    # or "rendered nothing", not a guess.
    state["produced"] = oneshot_produced.images(roots, state.get("sent_wall"), render_path, wrote)
    path = next((entry.get("path") for entry in result["results"] if entry.get("kind") == "output"
                 and entry.get("passed")), None)
    if path and state.get("folder"):
        try:
            state["still"] = shutil.copy2(path, os.path.join(state["folder"], "agent-final"
                                                             + os.path.splitext(path)[1].lower()))
        except OSError as error:
            state["still"] = "failed: %s" % str(error)[:200]
