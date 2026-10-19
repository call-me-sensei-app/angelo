# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline provider fixtures explicitly model a completed native proof boundary.

These stand-ins do not evaluate Blender data. Unlike result-only fixtures, they
supply a canonical job's immutable obligations, exact rows and closed lifecycle;
the production completion API checks these before the request receives proof.
"""

import copy
from types import SimpleNamespace
import uuid

from angelo_agent.program_jobs import ProgramJob, WriterLease
from angelo_agent.program_completion import CompletionScope, capture_program, mint_completion, program_digest
from angelo_agent.request_fences import RequestFence
from angelo_agent.tool_specs import validate_call


def completed_job(arguments, result):
    """Represent the native fixture's finished stage, with production integrity gates."""
    if not isinstance(result, dict) or not isinstance(result.get("checks"), list):
        return None
    canonical = validate_call("angelo_scene_program", arguments)
    job = ProgramJob(None, canonical, writer=WriterLease())
    job.result, job.check_results = result, copy.deepcopy(result["checks"])
    job.closed, job.phase = True, "PREVIEW" if result.get("status") == "preview" else "COMMITTED"
    return job


def register_completion(context, name, arguments, result):
    """A direct-call fixture's private delivery, separate from the result JSON."""
    if name != "angelo_scene_program":
        return
    job = completed_job(arguments, result)
    if job is None:
        return
    if not hasattr(context, "program_completions"):
        context.host, context._closing = context, False
        context.request_fence = RequestFence.capture(context)
        context.program_completions = CompletionScope(context)
    host, fence = context.host, context.request_fence
    call = SimpleNamespace(id="native-fixture/" + str(uuid.uuid4()), name=name, arguments=job.program,
                           program_arguments=None, epoch=fence.epoch, generation=fence.generation,
                           submitted_arguments_digests=(program_digest(arguments),))
    completion = mint_completion(host, call, capture_program(job), result)
    context.program_completions.record(result, completion)
