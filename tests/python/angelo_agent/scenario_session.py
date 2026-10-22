# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scenario-set duties of Angelo's trial driver (``eval_live_agent_session.py``), inside the app.

The driver stays the same for every task set; for a scenario set it hands
these steps to ``ScenarioSession``:

* before the first turn: the scenario's start file appended into the scene
  when it has one (``scenario_specs.start_file``), then the scene's state
  digest and orphan data-blocks;
* per turn: the reference images, attached through the chat's own attach
  path (private snapshots of the same bytes, SHA-256 checked), and after the
  turn the closed ``scenario_checks`` and a state digest, also for a turn
  that was stopped, so the Undo probe's boundaries stay aligned;
* approvals (``approval_decision``): the harness's typed allowlist plus,
  with ``--python-approvals approve``, reviewed Python, which a person
  approves in two clicks (the first opens the script, the second runs it),
  plus the tools a scenario names itself (``APPROVABLE``: the island's
  render job);
* background jobs: a turn that started one (a render) is graded only when
  the job has ended (``jobs_pending``, at most the scenario's
  ``JOB_WAIT_S``), because the job holds its own render settings on the
  scene while it runs and its file is part of the turn's result;
* files: a turn's ``outputs`` are looked for in the chat's workspace and
  counted with its checks;
* with ``--stage-copies``: a copy of the scene after every turn
  (``stages/NN-<turn>.blend``), for stage renders;
* at the end: ``scenario_hygiene.finish`` (hygiene, the saved copy for
  scoring, then the Undo probe) and, with ``--demo-record``, the recorder's
  frames (``scenario_demo``).

Importable without Blender: ``bpy`` is passed in.
"""

import os

import eval_live_tasks as live
import scenario_checks
import scenario_hygiene
import scenario_specs

# Scenario sets: an artist building a scene approves reviewed Python as well, as the MCP
# side runs model-written Python without any approval. Every approval is counted and
# reported; --python-approvals decline restores the default rule for comparison runs.
SCENARIO_APPROVABLE = live.HARNESS_APPROVABLE | {"angelo_execute_python"}


def grading_route(task, scenario_active):
    """How the trial driver grades a task: ``read_only``, ``scenario``, ``checks`` or ``ungraded``.

    A scenario turn keeps its checks in ``scenario_checks`` and has an empty
    ``checks`` list, like a survey request no closed check can grade; only a
    task outside a scenario session is ever ``ungraded``. (With the survey rule
    first, every Angelo scenario turn was recorded as failed, though its
    checks had passed.)
    """
    if task["kind"] == "read_only":
        return "read_only"
    if scenario_active:
        return "scenario"
    return "checks" if task["checks"] else "ungraded"


# What a scenario may add through ``APPROVABLE``: starting one of Blender's own background jobs
# (a render into the chat's workspace). Nothing else is ever a scenario's to allow.
SCENARIO_EXTRAS = frozenset({"angelo_job_start"})
# While a render job runs the demo recorder grabs the window this often instead of twice a second.
JOB_RECORD_INTERVAL_S = 5.0


def approval_decision(tool_name, python="approve", extra=frozenset()):
    """The scenario rule: typed bounded scene tools, plus reviewed Python when ``python`` is approve.

    ``extra``: the scenario's own ``APPROVABLE`` (only names in ``SCENARIO_EXTRAS`` count).
    File writes and deletes, Studio generation, delegation, memory notes,
    workspace changes, preview acceptance and sculpting are still declined:
    a benchmark never lets the model touch disk, accounts or billed generators.
    """
    allowed = SCENARIO_APPROVABLE if python == "approve" else live.HARNESS_APPROVABLE
    if (tool_name in allowed or tool_name in SCENARIO_EXTRAS & frozenset(extra)) \
            and not tool_name.startswith("angelo_studio_"):
        return "approve"
    return "decline"


def use_render_device(bpy, device):
    """``gpu``: the trial's Cycles preference uses the machine's GPU devices (Preferences > System), as a person
    with a GPU has it. A preference of this disposable profile, not a scene edit: the scene still chooses. The
    devices are selected as bench_gpu selects them on the GPU test VMs; no GPU listed fails the trial."""
    if device != "gpu":
        return {"device": "cpu"}
    import bench_gpu
    return {"device": "gpu", **bench_gpu.select(bpy)}


def for_trial(task_set, out, python_approvals="approve", demo_record=False, stage_copies=False):
    """The trial's ``ScenarioSession``, or None for every other task set."""
    if not scenario_specs.is_scenario(task_set):
        return None
    demo = None
    if demo_record:
        import scenario_demo
        demo = scenario_demo.Recorder(out / "demo", scenario_specs.scenario_id(task_set))
    if scenario_specs.is_oneshot(task_set):   # One message, its own approval rule, caps and path facts.
        import oneshot_session
        return oneshot_session.OneShotSession(task_set, out, python_approvals, demo, stage_copies)
    return ScenarioSession(task_set, out, python_approvals, demo, stage_copies)


def written(job):
    """The file name a job record says it wrote (its receipt, else its output path), or None."""
    receipt, detail = job.get("receipt") or {}, job.get("detail") or {}
    path = receipt.get("output") or detail.get("output_path")
    return os.path.basename(str(path)) if path else None


def final_reply(rows):
    """The text of a turn's last assistant row (its final reply), or ``""``."""
    replies = [row.get("text") or "" for row in rows or () if isinstance(row, dict) and row.get("role") == "assistant"]
    return str(replies[-1]) if replies else ""


class ScenarioSession:
    def __init__(self, task_set, out, python_approvals="approve", demo=None, stage_copies=False):
        self.name = scenario_specs.scenario_id(task_set)
        self.out, self.python_approvals, self.demo = out, python_approvals, demo
        self.extra, self.job_wait_s = scenario_specs.approvable(self.name), scenario_specs.job_wait(self.name)
        self.stage_copies, self.stages = stage_copies, []
        self.report = {"python_approvals": python_approvals}
        self.turn_start = (None, 0)   # (controller, first row of the turn), set by ``attach``.

    def begin(self, bpy, window):
        """The scenario's start file when it has one (``setup``), then the untouched scene's digest and orphans;
        starts the demo recorder when there is one."""
        start = scenario_specs.start_file(self.name)
        if start is not None:   # The same bytes the MCP side appends, before the first message and its clock.
            import time
            started = time.perf_counter()
            self.report["setup"] = dict(scenario_specs.module(self.name).setup(bpy, window.scene, *start),
                                        setup_s=round(time.perf_counter() - started, 3))
        self.report.update(digests=[scenario_hygiene.state_digest(bpy, window.scene)],
                           baseline_orphans=scenario_hygiene.orphans(bpy))
        if self.demo is not None:
            self.demo.start(bpy, window)
        return self.report

    def ready(self, finishing=False):
        """Whether the driver may go on: a demo recording waits for its layout before the first
        message, and after the last turn records the finished scene before the end-of-session steps."""
        if self.demo is None:
            return True
        return self.demo.drained() if finishing else self.demo.ready()

    def attach(self, controller, task, record):
        """Attach a turn's references as the paperclip does; their views and hashes go in ``record``. The turn's
        chat rows start here (``turn_rows``: its final reply names the render it gives as final)."""
        self.turn_start = (controller, len(controller.state["messages"]))
        if not task.get("references"):
            return
        files = scenario_specs.reference_files(task["scenario"], task["references"])
        controller.attachments.extend(controller.references.import_images([path for _, path, _ in files],
                                                                          controller.attachments))
        record["references"] = [{"view": view, "sha256": digest} for view, _, digest in files]
        if self.demo is not None:
            self.demo.mark("turn %s" % task["id"])

    def decide(self, tool_name):
        return approval_decision(tool_name, self.python_approvals, self.extra)

    def jobs_pending(self):
        """Whether a background job (a render the turn started) is queued or running."""
        if not self.job_wait_s:
            return False
        from angelo_agent import job_ui
        manager = job_ui.manager(create=False)
        pending = bool(manager is not None and manager.has_work())
        if self.demo is not None:
            import scenario_demo
            self.demo.interval = JOB_RECORD_INTERVAL_S if pending else scenario_demo.INTERVAL_S
        return pending

    def job_records(self):
        """The session's job records for the report: kind, status, seconds, the output's name it was asked for
        and the one it wrote (no paths; a taken name is never overwritten, so the two can differ).

        ``shown`` is the add-on's own mark of a job reaching the model (``result_at``, ``image_at``,
        ``images``); None on an add-on that keeps none.
        """
        from angelo_agent import job_ui
        manager = job_ui.manager(create=False)
        records = list(manager.store.records.values()) if manager is not None else []
        return [{"job": r["id"], "kind": r["kind"], "status": r["status"], "reason": r.get("reason") or None,
                 "seconds": round(r["ended"] - r["started"], 1) if r.get("ended") and r.get("started") else None,
                 "output": str(r["request"].get("output") or ""), "wrote": written(r),
                 "shown": dict(r["shown"]) if isinstance(r.get("shown"), dict) else None} for r in records]

    def turn_rows(self):
        """The chat rows since the turn's message was sent (none before ``attach``)."""
        controller, start = self.turn_start
        return controller.state["messages"][start:] if controller is not None else []

    def grade(self, bpy, scene, task, record, workspace=None, window=None):
        """The turn's closed checks, its required files and the state digest after it; True when all passed.

        ``workspace``: the chat's file workspace, where a job publishes its output. The turn's final reply,
        then the job receipts, name the render judged as final (``final_render``).
        """
        result = scenario_checks.evaluate(bpy, scene, task["scenario_checks"])
        if task.get("scenario_outputs"):
            record["jobs"] = self.job_records()
            result = scenario_checks.with_outputs(result, task["scenario_outputs"], [workspace] if workspace else [],
                                                  final_reply(self.turn_rows()), record["jobs"])
        record["host_check"] = result
        record["state_digest"] = scenario_hygiene.state_digest(bpy, scene)
        self.report["digests"].append(record["state_digest"])
        if self.stage_copies and window is not None:
            self.stage_copy(bpy, window, task)
        return result["success"]

    def stage_copy(self, bpy, window, task):
        """A copy of the scene as this turn left it (no thumbnail; the open file is untouched)."""
        folder = self.out / "stages"
        folder.mkdir(exist_ok=True)
        path = folder / ("%02d-%s.blend" % (len(self.stages) + 1, task["id"]))
        try:
            scenario_hygiene.save_copy(bpy, window, path)
            self.stages.append(str(path))
        except Exception as error:   # A failed copy never fails the turn; it is reported.
            self.stages.append("failed: %s" % str(error)[:200])
        self.report["stages"] = self.stages

    def stopped(self, bpy, scene, task, record, workspace=None, window=None):
        """A turn that ended without grading (stopped, timed out) is still graded and digested, never passed."""
        if task.get("scenario_checks") and "state_digest" not in record:
            self.grade(bpy, scene, task, record, workspace, window)
            record["host_check_passed"] = False

    def finish(self, bpy, window, probe=True):
        """Hygiene, the saved copy and the Undo probe, after the demo recorder stopped.

        ``probe`` False: the Undo probe waits for ``undo_probe`` (a diagnostic follow-up comes first).
        """
        if self.demo is not None:
            self.report["demo"] = self.demo.stop(bpy)
        turns = scenario_specs.module(self.name).TURNS
        self.report.update(scenario_hygiene.finish(bpy, window, turns, self.report["digests"],
                                                   self.out / "final.blend", self.report.get("baseline_orphans"),
                                                   probe))
        return self.report

    def undo_probe(self, bpy, window):
        """The Undo probe ``finish`` left for later; the report says it ran after the follow-up."""
        self.report.update(scenario_hygiene.late_undo_probe(bpy, window, self.report["digests"]))
        return self.report
