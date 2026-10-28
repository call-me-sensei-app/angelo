# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What makes two sides' trials comparable, recorded per trial and checked before they are paired or reported.

* **What each trial was sent and graded with** (``trial_stamp``): the SHA-256
  of the prompt text each task sent, and the test code's version: the
  checkout's commit (with a dirty flag) and the SHA-256 of the harness sources
  and of the product modules its graders run (``GRADER_ROOTS`` and their
  imports: ``test_code``). Both sides' task records carry them, so a
  report shows what each trial actually had, not what today's modules say.
* **Reusing earlier MCP trials** (``reuse_problems``): ``--reuse-mcp-results``
  pairs new Angelo trials with an earlier run's MCP trials only when that run
  had the same task set, runtime, model and effort, the same prompt SHA for
  every task, the same reference digest and the same test code (the harness
  and the product modules its graders run), and was no diagnostic run. A trial
  recorded before these stamps existed cannot prove that and is refused.
  ``--cross-model-reuse`` allows another model or effort (never another
  runtime) for a tier measurement (``model_routing.rst``); such a run is
  labelled in every report and is never a head-to-head or gate figure.
* **Scripts** (``scripts_label``): a head-to-head or one-shot comparison runs
  Angelo with the product's Always allow scripts setting, as the MCP side's
  scripts run without a prompt; every report then says "scripts allowed".
* **Planned n** (``n_problems``): a run (and a campaign, per step) records
  its planned trials per side before the first trial; a report flags any side
  whose valid trial count differs.

Standard library only; reads files and, for the commit, runs ``git``.
"""

import ast
import functools
import hashlib
import json
import os
import subprocess
from pathlib import Path
import eval_send_record as send_record  # The trial's record: an end write, never one written at a send.

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCRIPTS_ALLOWED = "scripts allowed"
SCRIPT_LABELS = {"allowed": SCRIPTS_ALLOWED + " (Angelo: the Always allow scripts setting, or the chat's Trusted "
                                              "scripts in a run that names it; MCP: its scripts run without a prompt)",
                 "reviewed": "scripts reviewed (Angelo: Auto, each script waits for the tester's approval; MCP: its "
                             "scripts run without a prompt)",
                 "ask": "scripts reviewed (Angelo: Ask, every edit waits for the tester's approval; MCP: its scripts "
                        "run without a prompt)"}
# The tests of the harness, not the harness: their changes never change what a trial sent or how it was graded.
NOT_TEST_CODE = ("test_native_", "test_blender_")


def prompt_sha256(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


# The product modules that grade a trial on either side (``bench_mcp_grading``, ``eval_grading_live``,
# ``survey_checks``): their import closure inside the add-on is part of the test code, because a change there
# grades the two sides of a reused comparison differently.
GRADER_ROOTS = ("scene_validation", "validation_spec")


def _product_imports(addon, name, found):
    """``name`` and every add-on module it imports, transitively (relative or ``angelo_agent.*`` imports)."""
    path = addon / (name + ".py")
    if name in found or not path.is_file():
        return found
    found.add(name)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.level == 1 or (node.module or "").startswith("angelo_agent")):
            module = (node.module or "") if node.level == 1 else (node.module or "")[len("angelo_agent"):].lstrip(".")
            names = [module.split(".")[0]] if module else [alias.name for alias in node.names]
            for child in names:
                _product_imports(addon, child, found)
    return found


def test_code_sources(root=ROOT):
    """What a trial was sent and graded with: every ``tests/python/angelo_agent`` module but its tests, and the
    add-on modules the graders run (``GRADER_ROOTS`` and everything they import)."""
    folder, addon = Path(root) / "tests/python/angelo_agent", Path(root) / "scripts/addons_core/angelo_agent"
    harness = [path for path in folder.glob("*.py") if not path.name.startswith(NOT_TEST_CODE)]
    graders = set()
    for name in GRADER_ROOTS:
        _product_imports(addon, name, graders)
    return sorted(harness) + sorted(addon / (name + ".py") for name in graders)


@functools.lru_cache(maxsize=4)
def test_code(root=ROOT):
    """``{"commit", "dirty", "sha256"}`` of the checkout's test code (the commit None where no git tree exists)."""
    digest = hashlib.sha256()
    for path in test_code_sources(root):
        name = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        digest.update(name.encode("utf-8") + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    commit = dirty = None
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
        if head.returncode == 0:
            commit = head.stdout.strip()
            status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no", "--",
                                     "tests/python/angelo_agent", "scripts/addons_core/angelo_agent"],
                                    capture_output=True, text=True, timeout=30,
                                    env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
            dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    # An extracted source tree (the Windows bench) has no git: the stage records its commit.
    commit = commit or os.environ.get("ANGELO_SOURCE_COMMIT") or None
    return {"commit": commit, "dirty": dirty, "sha256": digest.hexdigest()}


def trial_stamp(task):
    """The exact sent-prompt/test-code identities, plus a prospective quality directive's version when present.

    The prompt SHA remains authoritative for message validation; protocol metadata
    is recorded in both completed and at-send evidence, never a semantic-quality verdict.
    """
    return {"prompt_sha256": prompt_sha256(task["prompt"]), "test_code": dict(test_code()),
            **({"quality_loop": dict(task["quality_loop"])} if task.get("quality_loop") else {}),
            **({"parallel_request": task["parallel_request"]} if task.get("parallel_request") else {})}


# ---------------------------------------------------------------- reusing earlier MCP trials (I5)

def reuse_problems(reused, plan, tasks, code=None, cross_model=False, expected=None):
    """Why an earlier run's MCP trials may not be paired with this run's Angelo trials (empty: they may).

    ``plan`` is this run's plan (``bench_mcp_vs_angelo.plan``), ``tasks`` its task list, ``code`` its
    ``test_code()``. The earlier run's ``RUN-PLAN.json`` and every ``mcp-trial-NN/trial.json`` are read.
    A diagnostic run (``eval_diagnostics``) is never reused, and another runtime never; another model or
    effort only with ``cross_model`` (``--cross-model-reuse``: a labelled tier measurement, never a claim).
    """
    import eval_diagnostics
    reused, code = Path(reused), code or test_code()
    problems = []
    try:
        earlier = json.loads((reused / "RUN-PLAN.json").read_text())
    except (OSError, ValueError):
        return ["%s has no readable RUN-PLAN.json, so what its MCP trials were run with is unknown" % reused]
    stamp = eval_diagnostics.claim_refusal(reused)
    if stamp:   # Its MCP side ran with a display or a follow-up nobody measured: never paired into a comparison.
        problems.append(stamp)
    keys = ("task_set", "runtime") + (() if cross_model else ("model", "effort"))
    for key in keys:
        if earlier.get(key) != plan.get(key):
            problems.append("its %s was %r, this run's is %r%s" % (key, earlier.get(key), plan.get(key), (
                "; --cross-model-reuse allows another model or effort" if key in ("model", "effort") else "")))
    digests = [(entry or {}).get("reference_digest") for entry in (earlier.get("scenario"), plan.get("scenario"))]
    if digests[0] != digests[1]:
        problems.append("its reference digest was %r, this run's is %r" % tuple(digests))
    # ``expected``: the prompts the Angelo trials recorded (a re-summary), else this run's own prompts.
    expected = expected or {task["id"]: prompt_sha256(task["prompt"]) for task in tasks
                            if task.get("kind") != "preview"}
    folders = sorted(reused.glob("mcp-trial-*/trial.json"))
    if not folders:
        problems.append("it holds no MCP trial")
    for path in folders:
        try:
            trial = json.loads(path.read_text())
        except (OSError, ValueError):
            problems.append("%s cannot be read" % path.parent.name)
            continue
        recorded = trial.get("test_code") or {}
        if not recorded.get("sha256"):
            problems.append("%s was recorded before trials kept their test code's version" % path.parent.name)
        elif recorded["sha256"] != code["sha256"]:
            problems.append("%s ran other test code (commit %s, this run's %s)"
                            % (path.parent.name, recorded.get("commit"), code.get("commit")))
        for record in trial.get("tasks") or ():
            sent = record.get("prompt_sha256")
            if record.get("task") not in expected:
                problems.append("%s ran task %r, which this run does not have" % (path.parent.name, record.get("task")))
            elif not sent:
                problems.append("%s task %s was recorded before trials kept their prompt's SHA-256"
                                % (path.parent.name, record.get("task")))
            elif sent != expected[record["task"]]:
                problems.append("%s task %s was sent another prompt" % (path.parent.name, record["task"]))
    return list(dict.fromkeys(problems))


def recorded_plan(output):
    """The plan a run folder started with (``RUN-PLAN.json``), or None."""
    try:
        return json.loads((Path(output) / "RUN-PLAN.json").read_text())
    except (OSError, ValueError):
        return None


def recorded_angelo(output):
    """``(test code, {task: prompt SHA-256})`` the run's Angelo trials recorded; problems when they cannot say."""
    codes, shas, problems = set(), {}, []
    for path in send_record.finished_only(sorted(Path(output).glob("angelo-trial-*/" + send_record.NAME))):
        try:
            trials = send_record.read(path).get("trials") or ()
            tasks = [task for trial in trials for task in trial.get("tasks") or ()]
        except (OSError, ValueError):
            problems.append("%s cannot be read" % path.parent.name)
            continue
        for task in tasks:
            if not (task.get("test_code") or {}).get("sha256") or not task.get("prompt_sha256"):
                problems.append("%s was recorded before trials kept their prompt and test code" % path.parent.name)
                break
            codes.add(task["test_code"]["sha256"])
            shas.setdefault(task.get("task"), task["prompt_sha256"])
    if len(codes) > 1:
        problems.append("the Angelo trials ran different test code")
    return ({"sha256": codes.pop(), "commit": None} if len(codes) == 1 else None), shas, list(dict.fromkeys(problems))


PLAN_KEYS = ("order", "task_set", "runtime", "model", "effort")


def summary_problems(output, plan, recorded, tasks, reuse=None, cross_model=False):
    """Why ``--summarize-only`` must not rebuild ``output`` with these options (empty: it may).

    The options must name the recorded plan's order, task set, runtime, model and effort, so nothing is
    re-planned, and a reused MCP folder passes every reuse check against the Angelo trials as they were recorded.
    """
    problems = ["%s: recorded %r, asked %r" % (key, recorded.get(key), plan.get(key)) for key in PLAN_KEYS
                if recorded is not None and key in recorded and recorded.get(key) != plan.get(key)]
    if reuse is not None:
        code, shas, recorded_problems = recorded_angelo(output)
        problems += recorded_problems
        if code is not None:   # Against the Angelo trials as they were recorded.
            problems += reuse_problems(reuse, plan, [], code, cross_model, expected=shas)
        elif not recorded_problems:   # No Angelo trial yet: against this checkout's prompts and test code.
            problems += reuse_problems(reuse, plan, tasks, None, cross_model)
    return problems


def trial_count(output, plan, reuse=None):
    """How many trial numbers a summary reads: the planned ones, and any folder beyond them (never dropped)."""
    numbers = [int(str(entry).rsplit("-", 1)[1]) for entry in (plan or {}).get("order") or ()]
    for folder in [Path(output)] + ([Path(reuse)] if reuse else []):
        for path in folder.glob("*-trial-[0-9][0-9]"):
            numbers.append(int(path.name.rsplit("-", 1)[1]))
    return max(numbers, default=0)


def planned_plan(output, plan, reuse=None):
    """The plan whose order fixes the planned n: the recorded one, plus a reused run's planned MCP trials."""
    recorded = recorded_plan(output) or plan
    if reuse is None:
        return recorded
    reused = recorded_plan(reuse) or {}
    extra = [entry for entry in reused.get("order") or () if str(entry).startswith("mcp-")]
    order = list(recorded.get("order") or ())
    return dict(recorded, order=order + [entry for entry in extra if entry not in order])


# ---------------------------------------------------------------- scripts (I1)

def script_mode(angelo):
    """``allowed``, ``reviewed`` or ``ask`` from a plan's ``angelo`` entry (or a trial report)."""
    angelo = angelo or {}
    if angelo.get("always_allow_scripts") or angelo.get("trusted_scripts"):
        return "allowed"
    return "ask" if str(angelo.get("approval", "")).lower() == "ask" else "reviewed"


def scripts_label(angelo):
    return SCRIPT_LABELS[script_mode(angelo)]


# ---------------------------------------------------------------- planned n (I10)

def planned_n(plan):
    """``{side: trials}`` a bench plan names before its first trial (from its alternating ``order``)."""
    counts = {}
    for entry in plan.get("order") or ():
        side = str(entry).rsplit("-", 1)[0]
        counts[side] = counts.get(side, 0) + 1
    return counts


def recorded_n(records):
    """``{side: {"valid": n, "invalid": m}}`` of distinct trials in benchmark records."""
    trials = {}
    for record in records:
        entry = trials.setdefault(record.get("side", "angelo"), {"valid": set(), "invalid": set()})
        entry["invalid" if record.get("invalid") else "valid"].add(record.get("trial"))
    return {side: {"valid": len(entry["valid"]), "invalid": len(entry["invalid"] - entry["valid"])}
            for side, entry in trials.items()}


def n_problems(planned, recorded):
    """One line per side whose valid trial count is not the planned n (empty when every side has it)."""
    problems = []
    for side in sorted(set(planned) | set(recorded)):
        want, got = planned.get(side, 0), (recorded.get(side) or {}).get("valid", 0)
        if want != got:
            invalid = (recorded.get(side) or {}).get("invalid", 0)
            problems.append("%s: %d valid trial(s), %d planned%s" % (
                side, got, want, " (%d invalid, rate-limited)" % invalid if invalid else ""))
    return problems


def conditions(plan, records):
    """The run conditions every report states: scripts, planned and recorded n, any n that differs, a
    cross-model reuse, and the trials the waited-out throttle rule could not be applied to."""
    planned, recorded = planned_n(plan), recorded_n(records)
    older = sorted({"%s %s" % (record.get("side", "angelo"), record.get("trial")) for record in records
                    if not (record.get("test_code") or {}).get("sha256")})
    return {"scripts": scripts_label(plan.get("angelo")), "script_mode": script_mode(plan.get("angelo")),
            "planned_n": planned, "recorded_n": recorded, "n_differs": n_problems(planned, recorded),
            "cross_model_reuse": plan.get("cross_model_reuse"), "throttle_rule_not_applied": older}


def markdown_lines(found):
    """The run-conditions lines of a report (none for a summary made before they were recorded)."""
    if not found:
        return []
    lines = ["Scripts: %s." % found["scripts"],
             "Planned n per side: %s; valid trials: %s." % (
                 ", ".join("%s %d" % item for item in sorted(found["planned_n"].items())) or "none",
                 ", ".join("%s %d" % (side, entry["valid"]) for side, entry in sorted(found["recorded_n"].items()))
                 or "none")]
    if found["n_differs"]:
        lines.append("**n differs from the plan:** %s. Results of a side with fewer or more trials than planned are "
                     "not the planned comparison." % "; ".join(found["n_differs"]))
    cross = found.get("cross_model_reuse")
    if cross:
        lines.append("**Cross-model reuse:** MCP trials of %s (effort %s) are paired with Angelo's %s (effort %s). "
                     "A tier measurement, never a gate, timing or benchmark figure." % (
                         cross["mcp"].get("model"), cross["mcp"].get("effort"), cross["angelo"].get("model"),
                         cross["angelo"].get("effort")))
    if found.get("throttle_rule_not_applied"):
        lines.append("The waited-out throttle rule (an HTTP 429 a runtime retried makes a trial invalid) cannot be "
                     "applied to trials recorded before both sides counted it, so it is applied to neither side of "
                     "them: %s." % ", ".join(found["throttle_rule_not_applied"]))
    return lines + [""]
