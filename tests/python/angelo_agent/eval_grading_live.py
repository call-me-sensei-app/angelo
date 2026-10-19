# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The trial driver's grading steps around its closed checks (``eval_live_agent_session.py``).

The driver grades an edit by closed host checks and a question by an unchanged
scene. ``LiveGrading`` adds what the grading contract (``eval_grading``) needs,
in four steps per task:

* ``before``: the task's fixtures (``survey_fixtures``), the question's ground
  truth (``survey_truth``), the names of the scene's objects, and whether the
  task's checks already hold (a check that holds before the turn cannot show
  that the turn did anything);
* ``observe``, on every driver tick of a visual question: which images were
  already captured when the answer line was last stated
  (``eval_answers.Watch``; a constant-time no-op for every other task);
* ``after``: the question's answer check with its evidence (``eval_answers``),
  the edit's result checks (``survey_checks``), and the final
  ``host_check_passed``: None (ungraded) for an edit the task declares
  ungraded, a check that already held, or a question no check could read;
* ``complete``: transient fixtures are undone, the record gets its ``grade``
  and the trial report its running totals.

A gate set's edits pass through untouched: their ``host_check_passed`` is the
driver's own, and only ``grade`` and ``evidence`` are added. Its questions are
read like the survey's from gate-set version 2 (``eval_gate``); a version-1
question keeps the driver's result and is reported as ungraded. A defect in
this module never aborts a trial: it is recorded as ``grading_error`` and the
task is left ungraded (or, for a task these steps do not grade, as the driver
graded it).
"""

import traceback

import eval_answers as answers
import eval_grading as grading
import eval_live_tasks as live

# Aliases a result check may name besides a task's own ``targets`` and created objects.
STATIC_ALIASES = {"$eval": live.EVAL_OBJECT, "$copy": live.COPY_OBJECT, "$rig": live.RIG_OBJECT,
                  "$sketch": live.SKETCH_OBJECT, "$cam": "Camera", "$key": "Light"}
VACUOUS = "the checks already held before the turn, so they cannot show that the turn did anything"
UNPAIRED = "its other variant was not graded, so a constant answer is not ruled out"


class LiveGrading:
    def __init__(self, task_set, tasks, report, bind_checks, grading_targets):
        """``bind_checks`` and ``grading_targets`` are the driver's own helpers, so both grade alike."""
        self.report, self.bind_checks, self.grading_targets = report, bind_checks, grading_targets
        # Only the survey's edits are probed before the turn; other sets keep their recorded grading.
        self.probe_edits = task_set.startswith("survey")
        self.kinds = {alias: kind for task in tasks for alias, kind in (task.get("creates") or {}).items()}
        self.created, self.pairs, self.turn = {}, {}, {}
        report["grading"] = grading.stamp(task_set, tasks)

    # ------------------------------------------------------------ targets

    def resolver(self, scene, task):
        """``resolve(alias)`` for result checks: the scene, this turn's new objects, named or created objects."""
        names = dict(STATIC_ALIASES, **(task.get("targets") or {}))
        new = [obj for obj in scene.objects if obj.name not in self.turn.get("names", ())]

        def resolve(alias):
            if alias == "$scene":
                return scene
            if alias == "$new":
                return new
            if alias in names:
                return scene.objects.get(names[alias])
            # An object the agent only now made still answers to the alias of its kind.
            return self.tracked(scene, alias) or [obj for obj in new if obj.type == self.kinds.get(alias)]
        return resolve

    def tracked(self, scene, alias):
        """The objects an earlier task created for ``alias``: by identity, so a rename keeps them, else by name."""
        identities = {identity for identity, _ in self.created.get(alias, ()) if identity is not None}
        found = [obj for obj in scene.objects if getattr(obj, "session_uid", None) in identities]
        return found or [obj for obj in (scene.objects.get(name) for _, name in self.created.get(alias, ()))
                         if obj is not None]

    def closed_success(self, scene, task):
        """Whether the task's closed checks hold now, resolved as the driver's own grading resolves them."""
        from angelo_agent.scene_validation import VerificationContext, verify
        if task.get("targets"):
            objects = {alias: scene.objects.get(name) for alias, name in live.task_targets(task).items()}
            if not all(objects.values()):
                return False
            context = VerificationContext(objects, list(scene.objects))
            checks = self.bind_checks(task["checks"], objects)
        else:
            obj = scene.objects.get(live.EVAL_OBJECT)
            if obj is None:
                return False
            context = VerificationContext(self.grading_targets(scene, obj), list(scene.objects))
            checks = self.bind_checks(task["checks"], obj)
        return bool(verify(context, checks)["success"])

    def edit_holds(self, bpy, scene, task):
        """Whether every check of an edit holds now (motion checks are not stepped before a turn)."""
        import survey_checks
        held = True
        if task.get("checks"):
            held = self.closed_success(scene, task)
        if held and task.get("result_checks"):
            if any(check["kind"] in survey_checks.MOTION_KINDS for check in task["result_checks"]):
                return False
            held = survey_checks.evaluate(bpy, scene, task["result_checks"], self.resolver(scene, task))["success"]
        return held

    # ------------------------------------------------------------ the three steps

    def before(self, bpy, scene, task, record):
        """Fixtures, ground truth and the before-probe of one task; never raises."""
        self.turn = {"restore": []}
        try:
            self.turn["names"] = {obj.name for obj in scene.objects}
            self._before(bpy, scene, task, record)
        except Exception as error:
            self.turn["problem"] = "%s: %s" % (type(error).__name__, str(error)[:300])
            record["grading_error"] = traceback.format_exc()[-2000:]

    def _before(self, bpy, scene, task, record):
        if task.get("fixtures"):
            import survey_fixtures
            for fixture in task["fixtures"]:
                try:
                    steps, restore = survey_fixtures.apply(bpy, scene, fixture)
                except survey_fixtures.FixtureError as error:
                    self.turn["problem"] = "the harness could not make this task's starting state: %s" % error
                    return
                record.setdefault("harness_fixture", []).extend(steps)
                if restore is not None:
                    self.turn["restore"].append(restore)
            self.turn["names"] = {obj.name for obj in scene.objects}
        if task.get("answer"):
            import survey_truth
            self.turn["truth"] = survey_truth.truth(bpy, scene, task["answer"]["truth"])
            if task["answer"].get("evidence"):
                self.turn["watch"] = answers.Watch(answers.FORMS[task["answer"]["form"]][0])
        elif self.probe_edits and task["kind"] == "edit" and (task.get("checks") or task.get("result_checks")):
            self.turn["held_before"] = self.edit_holds(bpy, scene, task)

    def observe(self, messages, first):
        """One driver tick while the turn runs (its rows start at ``first``); only a visual question is watched.

        Never raises: a watch that fails is dropped, and the answer is then timed by row order.
        """
        watch = self.turn.get("watch")
        if watch is not None:
            try:
                watch.observe(messages[first:])
            except Exception as error:
                # Without the samples the answer cannot be timed: the question is left ungraded, with the reason.
                del self.turn["watch"]
                self.turn["problem"] = "the evidence watch failed (%s: %s)" % (type(error).__name__, str(error)[:200])

    def after(self, bpy, scene, task, record, passed, rows):
        """The task's final ``host_check_passed`` (True, False or None for ungraded); never raises."""
        try:
            record["evidence"] = answers.visual_evidence(rows)
            return self._after(bpy, scene, task, record, passed, rows)
        except Exception:
            record["grading_error"] = traceback.format_exc()[-2000:]
            extended = task.get("answer") or task.get("result_checks") or self.turn.get("held_before")
            return None if extended else passed

    def _after(self, bpy, scene, task, record, passed, rows):
        new = [obj for obj in scene.objects if obj.name not in self.turn.get("names", ())]
        for alias, kind in (task.get("creates") or {}).items():
            made = sorted((obj for obj in new if obj.type == kind), key=lambda obj: obj.name)
            if made:
                self.created[alias] = [(getattr(obj, "session_uid", None), obj.name) for obj in made]
        if task["kind"] == "read_only":
            return self._question(task, record, passed, rows)
        if self.turn.get("problem") and (self.probe_edits or task.get("fixtures") or task.get("result_checks")):
            # Only an edit these steps grade: a frozen set's edit keeps the driver's own result.
            record["host_check"] = {"ungraded": True, "reason": self.turn["problem"]}
            return None
        if task.get("result_checks"):
            import survey_checks
            result = survey_checks.evaluate(bpy, scene, task["result_checks"], self.resolver(scene, task))
            if task.get("checks"):   # Both families: the driver's closed result stays beside the result checks.
                result["closed_checks"] = record.get("host_check")
                result["success"] = bool(result["success"] and passed)
            record["host_check"], passed = result, result["success"]
        elif not task.get("checks") and grading.ungraded_reason(task):
            record["host_check"] = {"ungraded": True, "reason": grading.ungraded_reason(task)}
            return None
        if passed and self.turn.get("held_before"):
            record["host_check"]["vacuous"] = VACUOUS
            return None
        return passed

    def _question(self, task, record, passed, rows):
        host_check = record["host_check"]   # The driver's: scene_unchanged, and reply_missing for the older check.
        spec = task.get("answer")
        if spec is None:
            if task.get("reply_includes"):
                host_check["answer"] = answers.includes_outcome(task["reply_includes"], rows)
                return passed
            legacy = grading.legacy_question(task)
            host_check["answer"] = {"outcome": "unchecked",
                                    "reason": grading.LEGACY_REASON if legacy else "the task has no answer check"}
            return passed if legacy or not passed else None
        truth = self.turn.get("truth") or {}
        problem = self.turn.get("problem") or truth.get("ungraded") or ("" if "accept" in truth else "no ground truth")
        if problem:
            host_check["answer"] = {"outcome": "ungraded", "reason": problem, "truth": truth.get("detail")}
            return None if passed else False
        watch = self.turn.get("watch")
        if watch is not None and watch.samples:
            watch.observe(rows)   # The rows as graded are the last sample.
        # A visual answer needs the receipt's proof that its picture reached the model (eval_answers.image_delivery).
        delivery = (record.get("receipt") or {}).get("path")
        result = dict(answers.grade(spec, truth["accept"], rows, watch, delivery=delivery), truth=truth.get("detail"))
        host_check["answer"] = result
        if result["outcome"] == "unverified":   # Delivery the receipt cannot prove: ungraded, never passed.
            result["reason"] = "the receipt does not prove that the picture reached the model"
            passed = None if passed else False
        else:
            passed = bool(passed) and result["outcome"] == "right"
        if spec.get("pair"):
            passed = self._pair(spec["pair"], record, result, passed)
        return passed

    def _pair(self, group, record, result, passed):
        """A yes/no question passes only together with its other variant.

        One yes or no alone is right half the time, so the first variant's pass
        is held back as ungraded (``UNPAIRED``) until the other one has been
        graded: a variant that was skipped, never ran or had no ground truth
        leaves it there. Both answered alike is a constant answer: neither
        passes.
        """
        if group not in self.pairs:
            self.pairs[group] = (record, result, passed)
            if passed:
                result.update(outcome="ungraded", reason=UNPAIRED, held="right")
                return None
            return passed
        first_record, first, first_passed = self.pairs.pop(group)
        if first_passed:
            del first["reason"], first["held"]
            first["outcome"] = "right"
        if answers.constant_pair(first, result):
            first["outcome"] = result["outcome"] = "constant"
            first_record.update(host_check_passed=False, grade="failed")
            return False
        if first_passed:   # Graded beside its other variant and answered differently: the held pass stands.
            first_record.update(host_check_passed=True, grade="passed")
        return passed

    def complete(self, bpy, scene, task, record):
        """Undo transient fixtures, stamp the record's grade and the trial's totals; never raises."""
        for restore in reversed(self.turn.get("restore", ())):
            try:
                restore()
            except Exception as error:
                record.setdefault("harness_fixture_errors", []).append(str(error)[:300])
        self.turn = {}
        try:
            record["grade"] = grading.state(record, task)
            self.report["grading"]["totals"] = grading.totals(self.report["tasks"],
                                                              planned=self.report["grading"]["tasks"])
            self.report["grading"]["answers"] = answers.lenient_counts(self.report["tasks"])
        except Exception:
            record["grading_error"] = traceback.format_exc()[-2000:]
