# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The warm-up message before a measured run: every measured test starts warm.

``--warm-up-only`` (the bench and the live eval) sends one short message, ``PROMPT``, per selected side through
exactly the launch that side's trials use, then exits: the Angelo app with the run's connection, model, effort,
approval, debug thinking and diagnostic tool mode, or the MCP side's Claude Code with the run's MCP server. The
model's reply writes the system prompt and tools the trials will read into the provider's prompt cache. It is
untimed and ungraded, and it is never a trial:

* the bench writes it under ``warm-up/`` in the run's output folder (``bench_warm_up``), which no trial count,
  ``gather``, summary, scorecard or claim tool reads, and never writes the run's plan, so ``--resume`` then adds
  trial 1 as before;
* its record (``warm-up/WARM-UP-NN.json``) says what it was, with its usage and list price.

In the app (``eval_live_agent_session``) the trial driver boots and configures the chat exactly as for a trial,
then sends ``PROMPT`` instead of the first task, declines anything that would change the scene, records the
request's receipt and quits without grading. Standard library only; nothing reads a credential.
"""

import os
import time

WARM_UP_ENV = "ANGELO_EVAL_WARM_UP"
PROMPT = "Reply with OK."
FOLDER = "warm-up"
NOTE = ("A warm-up, never a trial: one short, untimed, ungraded message through the trials' own launch so the measured "
        "run starts with a warm prompt cache. Scoring, summaries and the claim tools never read it.")


def add_arguments(parser):
    parser.add_argument("--warm-up-only", action="store_true",
                        help="send one short untimed, ungraded message (%r) per selected side through the trials' own "
                             "launch, record it under %s/ as a warm-up (usage and cost), and exit; never a trial, and "
                             "--resume then adds trial 1 as before" % (PROMPT, FOLDER))


def check(parser, args):
    if getattr(args, "warm_up_only", False) and getattr(args, "returning_user", False):
        parser.error("--warm-up-only would add a chat to --returning-user's kept profile")
    return args


def environment(args):
    return {WARM_UP_ENV: "1" if getattr(args, "warm_up_only", False) else ""}


def plan(args):
    """The live eval's plan entry: nothing for a trial run, so its plan is unchanged."""
    return {"warm_up": NOTE} if getattr(args, "warm_up_only", False) else {}


# In the app (eval_live_agent_session) ------------------------------------------------------------------------------

def active(environ=None):
    return (os.environ if environ is None else environ).get(WARM_UP_ENV) == "1"


def send(trial, ui, observation_mode, context_rebase_tokens, task_timeout):
    """The trial driver's send phase, for the warm-up: the trial's own send path with ``PROMPT``."""
    from eval_receipts import receipt_marker
    trial.record = {"task": "warm-up", "kind": "warm_up", "warm_up": True, "prompt": PROMPT, "approved": [],
                    "declined": []}
    trial.receipts = receipt_marker(trial.controller)
    ui.prepare_controller()
    trial.controller.context_rebase_tokens = context_rebase_tokens
    turn = None
    if observation_mode == "turn-context":
        from angelo_agent import turn_context
        turn = turn_context.prepare(ui.main_window(), trial.controller.state["id"], message=PROMPT)
        observation = turn.observation
    else:   # The observation a legacy trial sends (eval_live_agent_session.on_send).
        from angelo_agent.blender_tools import BlenderExecutor
        observation = BlenderExecutor(ui.main_window()).execute("angelo_inspect_scene", {})
        observation["objects"] = observation["objects"][:32]
        observation["truncated"] = observation["object_count"] > len(observation["objects"])
    trial.sent = time.monotonic()
    trial.controller.send(PROMPT, observation=observation,
                          recovery_evidence=turn.recovery_evidence if turn is not None else None)
    if turn is not None:
        turn.commit()
    trial.deadline, trial.phase = trial.sent + task_timeout, "warm"
    trial.on_warm = lambda: _wait(trial)
    return .05


def _wait(trial):
    """Until the request ends: decline anything that asks (a warm-up changes nothing), then record and quit."""
    import eval_abnormal_end as end
    from eval_receipts import receipt_since, summarize_receipt
    for call in list(trial.controller.pending.values()):
        if call.approval and not call.executing and call.id not in trial.decided:
            trial.decided.add(call.id)
            trial.controller.decline_tool(call.id)
            trial.record["declined"].append(call.name)
    if not end.turn_ended(trial.controller) and time.monotonic() < trial.deadline:
        return .05
    status = "completed" if end.turn_ended(trial.controller) else "timeout"
    if status == "timeout":
        end.stop(trial, "warm_up_timeout")   # Its block (eval_abnormal_end), as for a task.
    trial.record.update(status=status, send_to_idle_ms=round((time.monotonic() - trial.sent) * 1000, 3),
                        receipt=receipt_since(trial.controller, trial.receipts))
    trial.record.update(summarize_receipt(trial.record["receipt"]))
    end.settle(trial, status, None)
    trial.report.update(warm_up=trial.record, tasks=[], success=status == "completed")
    return trial.finish()
