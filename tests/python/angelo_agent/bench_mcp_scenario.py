# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scenario-set steps shared by both MCP runtimes of ``bench_mcp_vs_angelo.py`` (Claude Code and Codex).

A scenario turn attaches reference images (``scenario_specs.reference_files``,
SHA-256 checked) and is graded by ``scenario_checks`` over the add-on socket;
the driver also digests the scene before the first turn and after each turn,
and at the end runs ``bench_mcp_grading.scenario_finish_code``: a hygiene
report, a saved copy of the scene for scoring, then the Undo probe.

A one-shot test (``scenario_specs.is_oneshot``) adds its own rules through
``client_command``, ``guard`` and the still's grading (``oneshot_mcp``);
for every other task set those two functions change nothing.
"""

import base64
import json
import time

import bench_mcp_grading as grading
import scenario_specs

FINISH_TIMEOUT_S = 600.


def is_scenario(config):
    return scenario_specs.is_scenario(config.get("task_set", "default"))


def client_command(command, config, folder):
    """The Claude Code command of a trial: unchanged, except for a one-shot test's client allowances."""
    if not scenario_specs.is_oneshot(config.get("task_set", "default")):
        return command
    import oneshot_mcp
    return oneshot_mcp.client_command(command, folder)


def guard(stream, config, folder, output):
    """One-shot tests only: the stream sends one message and is stopped at the cost cap (``oneshot_mcp.guard``)."""
    if scenario_specs.is_oneshot(config.get("task_set", "default")):
        import oneshot_mcp
        oneshot_mcp.guard(stream, config, folder, output)


def attach(task, record):
    """The image paths a turn attaches (none for other tasks); their views and hashes go in ``record``.

    Tasks without references leave ``record`` untouched, so the default and
    long sets record exactly what they did before scenario sets existed.
    """
    if not task.get("references"):
        return []
    files = scenario_specs.reference_files(task["scenario"], task["references"])
    record["references"] = [{"view": view, "sha256": digest} for view, _, digest in files]
    return [path for _, path, _ in files]


def claude_content(text, images):
    """A Claude Code user message: the text, then each image as a base64 block (Angelo's order).

    Without images it is the plain text string, byte for byte what the other task sets send.
    """
    if not images:
        return text
    blocks = [{"type": "text", "text": text}]
    for path in images:
        with open(path, "rb") as handle:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": scenario_specs.media_type(path),
                                                       "data": base64.b64encode(handle.read()).decode("ascii")}})
    return blocks


def codex_input(text, images):
    """A Codex ``turn/start`` input: the text, then each image as a local file (Angelo's order)."""
    return [{"type": "text", "text": text}] + [{"type": "localImage", "path": str(path)} for path in images]


def begin(client, config, output):
    """The scenario's start file when it has one (both sides append the same bytes), then the digest and
    orphan data-blocks of the untouched scene, before the first turn."""
    name = scenario_specs.scenario_id(config["task_set"])
    start_file = scenario_specs.start_file(name)
    setup = None
    if start_file is not None:
        setup = client.execute(grading.scenario_setup_code(config["task_set"], *start_file),
                               timeout=getattr(scenario_specs.module(name), "SETUP_TIMEOUT_S", 600.0))
    start = client.execute(grading.scenario_digest_code(config["task_set"]))
    output["scenario"] = {"digests": [start["digest"]], "baseline_orphans": start["orphans"]}
    if setup is not None:
        output["scenario"]["setup"] = setup


OUTPUT_ROOTS_CODE = ("import os, tempfile\nimport bpy\n"
                     "result = {'roots': [os.getcwd(), tempfile.gettempdir(), bpy.app.tempdir]}\n")


def output_roots(client):
    """Where this side's Blender writes a relative path: its working folder and its temporary folders.

    The MCP side has no workspace of its own; a file a turn must produce
    (``scenario_checks.evaluate_outputs``) is looked for by name under these.
    """
    return [root for root in client.execute(OUTPUT_ROOTS_CODE)["roots"] if root]


def final_reply(record):
    """The turn's final reply, whole (``final_reply``; a record of an older bench keeps its first 1,500
    characters in ``result``): it names the file the agent gives as its final render (``final_render``)."""
    return str(record.get("final_reply") or (record.get("result") or {}).get("reply") or "")


def grade(client, task, config, record, output):
    try:
        record.update(client.execute(grading.scenario_grade_code(task["id"], config["task_set"],
                                                                   config.get("long_tasks", 40))))
        if task.get("scenario_outputs") and scenario_specs.is_oneshot(config["task_set"]):
            import oneshot_mcp   # Also where the scene's render path points, and only a file of this run.
            oneshot_mcp.grade_outputs(client, task, config, record, output, output_roots(client))
        elif task.get("scenario_outputs"):
            import scenario_checks
            result = scenario_checks.with_outputs(record["host_check"], task["scenario_outputs"], output_roots(client),
                                                  final_reply(record))
            record.update(host_check=result, host_check_passed=result["success"])
    finally:  # One digest per turn, even a failed read, keeps the Undo probe's boundaries aligned.
        output["scenario"]["digests"].append(record.get("state_digest"))


def _collect(path, output, what):
    """Wait for a timer's result file (at most ``FINISH_TIMEOUT_S``) and merge it into ``output["scenario"]``."""
    deadline = time.monotonic() + FINISH_TIMEOUT_S
    while not path.is_file():
        if time.monotonic() > deadline:
            output["scenario"][what + "_error"] = "the %s step did not finish in %g s" % (what, FINISH_TIMEOUT_S)
            return
        time.sleep(.5)
    output["scenario"].update(json.loads(path.read_text()))


def finish(client, folder, config, output, probe=True):
    """Hygiene, the saved scene and (unless ``probe`` is False) the Undo probe; under ``output["scenario"]``."""
    result_path, blend = folder / "scenario-finish.json", folder / "final.blend"
    client.execute(grading.scenario_finish_code(config["task_set"], output["scenario"]["digests"],
                                                output["scenario"].get("baseline_orphans"), blend, result_path, probe))
    _collect(result_path, output, "finish")


def undo_probe(client, folder, config, output):
    """The Undo probe ``finish`` left for after an ask-why follow-up."""
    result_path = folder / "scenario-undo.json"
    client.execute(grading.scenario_undo_code(config["task_set"], output["scenario"]["digests"], result_path))
    _collect(result_path, output, "undo")


def end_of_trial(client, stream, usage, folder, config, output):
    """After the last task: hygiene and the saved copy, then ``--ask-why-on-miss``'s follow-up when one is due, on
    the graded scene (``eval_ask_why``), and only then the Undo probe, which moves the scene back."""
    import eval_ask_why
    session = is_scenario(config) and output["tasks"]
    asking = eval_ask_why.mcp_due(config, output, stream)[1] is None
    if session:
        finish(client, folder, config, output, probe=not asking)
    eval_ask_why.ask_mcp(stream, usage, config, folder, output, client)
    if session and asking:
        undo_probe(client, folder, config, output)
