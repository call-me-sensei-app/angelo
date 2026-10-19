# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script's own measurements as information for the model and the person, never a gate on the turn.

A reviewed script records measured values beside their targets with ``kit`` (``script_kit``); the executor
returns them as ``self_checks`` (and the names of the checks past those listed as ``self_checks_passed`` and
``self_checks_failed``, which only the host reads). Tools aid the model, never handicap it: most failing checks in
the recorded runs were wrong checks on a right scene, and a mandate to make them pass moved correct parts and ended
turns failed without their still (doc/angelo/agent_execution.rst, "The script kit and self-checks").

* When a plan's own scripts record a failing check the model has not seen (a new name, or a new value), its
  results go back to the model before the turn can end (``note``), even after a plan that carried its reply and no
  ``verify``. A plan's reply is shown when the plan arrives, before anything runs; what this guarantees is that the
  turn never ends on such a reply while a check its scripts failed is unseen by the model. The note names each
  failure (measured, target, tolerance) and leaves the choice to the model: fix the scene, fix the check, or reply.
  A failure the model has seen already (the same check and values again) forces one more round only after a plan
  that carried a reply (the reply was written before that result, so a fix that changed nothing is seen once),
  never a third: a model re-running the same failing check cannot loop the turn to its round cap. A check that
  passes and later fails again is news again (a regression).
* Nothing is mandated: no correction rounds, no failed ending, and jobs start whatever the checks say.
* A check is open until a later script records it passing; within one plan a failure of a name outweighs its
  passes. However the turn ends, the checks still open are listed in the person's notice (``row``), as facts.

A check the kit could not measure (``passed`` None) never passes, never fails and never closes an open check. A
check without a measurement shows its reason in the rows.
"""

import json

from .ui_text import notice

MAX_LISTED = 8
# Each failure shows its measured value against its target (_line). Either side can be wrong: most failing checks
# in recorded runs were wrong checks on a right scene, and some were met by moving correct parts (mistake
# catalogue, class 1). The request decides which: a target is what it states. The opening words stay: the replay
# tools find these rounds by them.
NOTE = ("A self-check of your script failed: {failures}. The scene or the check can be what is wrong: compare the "
        "measured value and the target with what the request states. Fix whichever differs (a check again under the "
        "same name), or reply as it is: the person sees the checks that still fail when the turn ends.")


def _line(check):
    if check.get("measured") is None and check.get("reason"):
        return "%s: %s (target %s)" % (str(check.get("name"))[:120], str(check["reason"])[:200],
                                       json.dumps(check.get("target"))[:160])
    line = "%s: measured %s, target %s (tolerance %s)" % (
        str(check.get("name"))[:120], json.dumps(check.get("measured"))[:160],
        json.dumps(check.get("target"))[:160], check.get("tolerance"))
    if check.get("support"):   # What a floating-parts check looked for (script_kit.check_supported).
        line += ", support looked for: " + str(check["support"])[:160]
    if check.get("floating"):
        line += ", floating: " + json.dumps(check["floating"])[:600]
    return line


class SelfChecks:
    """One request's open self-checks: what the model reads after a plan whose checks failed, and the person reads at
    the end."""

    def __init__(self):
        self.open, self.rounds = {}, {}   # rounds: name -> {failure line: rounds it has had before the model}.

    def read(self, operations, results):
        """Record every script check of a plan that ran; returns the names that failed in it."""
        passed, failed = set(), {}
        for operation, result in zip(operations, results):
            if not isinstance(operation, dict) or operation.get("tool") != "angelo_execute_python":
                continue
            if isinstance(result, dict):   # Checks past the listed ones, by name (script_kit.outcome).
                passed.update(str(name)[:120] for name in result.get("self_checks_passed") or ()
                              if isinstance(name, str))
                failed.update((str(name)[:120], "%s: failed (not listed)" % str(name)[:120])
                              for name in result.get("self_checks_failed") or () if isinstance(name, str))
            for check in (result.get("self_checks") if isinstance(result, dict) else None) or ():
                if not isinstance(check, dict):
                    continue
                name = str(check.get("name"))[:120]
                if check.get("passed") is True:
                    passed.add(name)
                elif check.get("passed") is False:
                    failed[name] = _line(check)
        for name in passed - set(failed):   # In one plan a failure of a name outweighs its passes.
            self.open.pop(name, None)
            self.rounds.pop(name, None)     # Passed: a later failure of it is a regression, news again.
        self.open.update(failed)
        return list(failed)

    def note(self, failed, *, replied=False):
        """The next round's first line for the failures among ``failed`` (``read``) that need one, else None.

        A failure the model has not been shown needs one; one shown once needs one more after a plan that carried a
        reply (``replied``). The caller always sends that round, so it is counted here.
        """
        lines = []
        for name in failed:
            line, had = self.open.get(name), self.rounds.setdefault(name, {})
            if line is not None and (had.get(line, 0) == 0 or replied and had.get(line, 0) == 1):
                had[line] = had.get(line, 0) + 1
                lines.append(line)
        return NOTE.format(failures="; ".join(lines[:MAX_LISTED])) if lines else None

    def sharer(self, context):
        """``run_operations``' ``before``: ahead of a job start, the host's ``open_self_checks`` are the checks open
        then (this plan's scripts so far counted, as ``read`` counts them), so the job's approval states them as a
        fact (``job_host.precondition``); cleared after the plan. Nothing here holds the job."""
        host = getattr(context, "host", context)

        def before(operation, done, results):
            shown = []
            if isinstance(operation, dict) and operation.get("tool") == "angelo_job_start":
                now = SelfChecks()
                now.open = dict(self.open)
                now.read(done, results)
                shown = list(now.open.values())
            setattr(host, "open_self_checks", shown)
        return before

    def row(self):
        """The person's notice where the turn completes with checks still open, else None: facts, not a verdict."""
        if not self.open:
            return None
        return {"role": "system", **notice("The script's self-checks still fail:\n{failures}",
                                           failures="\n".join(list(self.open.values())[:MAX_LISTED]))}
