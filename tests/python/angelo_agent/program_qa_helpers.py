# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared fixture adapters for isolated native program GUI safety checks."""

import bpy
from unittest.mock import patch
from angelo_agent import native_programs as native, scene_queries as queries


def args_for(window, operations, *, writes=(), reads=(), mode="APPLY", checks=(), store=None):
    store = store or queries
    entries = lambda objects: [{"handle": queries.object_handle(obj), "revision": store.object_revision(window, obj)} for obj in objects]
    return {"mode": mode, "read_set": entries(reads), "write_set": entries(writes),
            "operations": operations, "expected_checks": list(checks)}


def move(window, obj, location=(2, 3, 4), mode="APPLY", checks=()):
    handle = queries.object_handle(obj)
    return args_for(window, [{"kind": "transform", "target": handle, "parameters": {"location": list(location)}}],
                    writes=[obj], mode=mode, checks=checks)


def no_stages():
    return not any(scene.get("_angelo_program_stage") for scene in bpy.data.scenes)


class FullStore:
    """Admit full fingerprints for isolation fixtures outside observation allowlists.

    This is not a production bypass: the real backend performs the very same
    full fingerprint again before commit. Production uses queries.object_revision.
    """
    @staticmethod
    def resolve_object(window, handle):
        return queries.resolve_object(window, handle)

    @staticmethod
    def object_revision(window, obj):
        return native._fingerprint(obj)


def commit_operator(report):
    class ProgramQACommit(bpy.types.Operator):
        bl_idname = "angelo.program_qa_commit"
        bl_label = "Native program QA commit"
        bl_options = {"REGISTER", "UNDO"}
        program_id: bpy.props.StringProperty()

        def execute(self, context):
            result = native.commit_job(self.program_id, context.window)
            report["last_commit"] = {key: value for key, value in result.items() if key != "delta"}
            return {"FINISHED"} if result.get("success") else {"CANCELLED"}

    return ProgramQACommit


def partial_cleanup_regression(window, source, settle, check):
    """Free one owned RNA ID, then fail: fallback must not dereference it."""
    bpy.ops.ed.undo_push(message='Program partial cleanup baseline')
    job = native.begin(window, 'angelo_scene_program', move(window, source))
    settle(job)
    original = native.BlenderProgramBackend._remove_ids

    def fault_after_first(self, ids):
        first = next(block for block in ids if isinstance(block, bpy.types.Object))
        original(self, [first])
        raise RuntimeError('injected cleanup failure after first ID was freed')

    with patch.object(native.BlenderProgramBackend, '_remove_ids', new=fault_after_first):
        result = bpy.ops.angelo.program_qa_commit('EXEC_DEFAULT', True, program_id=job.id)
    check('cleanup_fallback_survives_freed_rna', 'FINISHED' in result and job.result['cleanup_warning'] and
          not job.result.get('cleanup_pending') and no_stages() and list(source.location) == [2,3,4])
    bpy.ops.ed.undo()
    source = window.scene.objects['Cube']
    check('partial_cleanup_fault_keeps_one_undo', list(source.location) == [0,0,0] and no_stages())
    return source


def spent_budget():
    """Patch the budget clock so every unit spends a whole poll, whatever the machine load.

    Each reading advances by more than the largest fused budget, which a
    measured hydration can raise to ``MAX_AMORTIZED_POLL_SECONDS``.
    """
    from angelo_agent.program_jobs import MAX_AMORTIZED_POLL_SECONDS
    ticks = [0.]

    def clock(_backend):
        ticks[0] += 2 * MAX_AMORTIZED_POLL_SECONDS
        return ticks[0]

    return patch.object(native.BlenderProgramBackend, 'operation_quantum_clock', clock, create=True)


def quantum_cancellation_regression(window, source, check):
    """No live edit or Main residency after a budget-bounded fused poll."""
    handle = queries.object_handle(source)
    operations = [{'kind':'transform', 'target':handle, 'parameters':{'location':[index*.1,0,0]}}
                  for index in range(24)]
    before = native._fingerprint(source)
    job = native.begin(window, 'angelo_scene_program', args_for(window, operations, writes=[source]))
    with spent_budget():
        job.poll()  # Preparation alone.
        result = job.poll()
    done = result['progress']['operation_index']
    check('native_quantum_is_bounded_and_leaves_no_main_ids', done == 1 and no_stages() and
          not native.runtime_is_staging() and native._fingerprint(source) == before)
    result = job.cancel()
    check('cancel_between_quanta_discards_all_staged_actions', result['cancelled'] and no_stages() and
          native._fingerprint(source) == before)


def fused_poll_regression(window, source, check):
    """A cheap program's units share one hydration; accept verifies without one."""
    handle = queries.object_handle(source)
    expected = {'kind': 'transform', 'target': handle, 'property': 'location', 'expected': [2, 3, 4], 'tolerance': 1e-5}
    hydrations, suspensions = [], []
    resume, suspend = native.BlenderProgramBackend.resume_stage, native.BlenderProgramBackend.suspend_stage

    def counted_resume(self, stage):
        hydrations.append(isinstance(stage, native.SuspendedStage))
        return resume(self, stage)

    def counted_suspend(self, stage, **kwargs):
        suspensions.append(not isinstance(stage, native.SuspendedStage))
        return suspend(self, stage, **kwargs)

    before = native._fingerprint(source)
    # A constant budget clock: the outcome cannot depend on machine load.
    with patch.object(native.BlenderProgramBackend, 'resume_stage', counted_resume), \
            patch.object(native.BlenderProgramBackend, 'suspend_stage', counted_suspend), \
            patch.object(native.BlenderProgramBackend, 'operation_quantum_clock', lambda _backend: 0., create=True):
        job = native.begin(window, 'angelo_scene_program', move(window, source, checks=[expected]))
        intent = job.poll()
        check('fused_cheap_program_ready_after_one_poll', intent.get('ready_to_commit') and
              (sum(hydrations), sum(suspensions)) == (0, 1) and no_stages() and native._fingerprint(source) == before)
        for _ in range(3):
            check('ready_poll_neither_hydrates_nor_edits', job.poll().get('ready_to_commit') and sum(hydrations) == 0 and
                  no_stages() and native._fingerprint(source) == before)
        result = job.commit()
        check('fused_program_commits_after_one_hydration', result['success'] and sum(hydrations) == 1 and
              list(source.location) == [2, 3, 4] and no_stages() and native.WRITER.owner is None)
        source.location = (0, 0, 0)
        window.view_layer.update()
        preview = native.begin(window, 'angelo_scene_program', move(window, source, mode='PREVIEW', checks=[expected]))
        retained = preview.poll()
        check('fused_preview_retained_after_one_poll', retained.get('status') == 'preview' and no_stages())
        count = sum(hydrations)
        accepted = native.begin(window, 'angelo_accept_preview', {'preview_id': retained['preview_id']})
        intent = accepted.poll()
        check('fused_accept_verifies_and_checks_with_one_hydration', intent.get('ready_to_commit') and
              sum(hydrations) == count + 1 and no_stages() and list(source.location) == [0, 0, 0])
        check('cancel_accepted_preview_keeps_original', accepted.cancel()['cancelled'] and no_stages() and
              list(source.location) == [0, 0, 0] and native.WRITER.owner is None)
