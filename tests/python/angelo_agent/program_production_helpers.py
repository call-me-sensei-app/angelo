# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The production path of a model's plan, for native GUI suites: Controller admission, approval and commit.

``production_call`` sends one call through the actual ``Controller``: admitted,
approved by the fixture (as the person clicks Approve), staged by
``BlenderExecutor`` and committed by ``angelo.commit_native_job`` through
``program_undo.commit``, the native ``_angelo_undo`` bridge and its
``CommitScope``. No controller or job method is stubbed. The suite registers
``angelo.commit_native_job`` (``agent_workspace_ui.workspace_operators``) first.
"""

import contextlib
import tempfile

import bpy
from angelo_agent import ui
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.controller import Controller, ToolCall, ToolDispatchBudget


@contextlib.contextmanager
def production_controller(root, approval="ASK"):
    """A real chat Controller bound to the current document (no queries.invalidate: handles stay valid)."""
    controller = Controller(tempfile.mkdtemp(prefix="production-controller-", dir=root))
    session = controller.state["id"]
    controller.set_document(ui.document_key())
    controller.busy = True
    controller.state["options"]["approval"] = approval
    ui._controllers[session] = controller
    try:
        yield controller
    finally:
        ui._controllers.pop(session, None)
        controller.busy = False
        controller.close()


def production_call(controller, window, name, tool, arguments, *, commit=True, before_commit=None):
    """One call through the Controller; returns its one delivered result with ``ok`` (committed, one Undo step).

    A refused call returns at once with ``ok`` False. ``deliveries`` counts the
    results the call delivered (exactly one for a committed or refused call).
    """
    session, document, executor, responses = controller.state["id"], controller.document, BlenderExecutor(window), []
    controller.request_tool(name, tool, arguments, responses.append)
    call = controller.pending.get(session + "/" + name)
    if not isinstance(call, ToolCall):
        return dict(responses[-1] if responses else {}, ok=False, deliveries=len(responses))
    job = None
    for _ in range(400):
        controller.pump_tools(executor, document, budget=ToolDispatchBudget(seconds=60))
        if call.approval and not call.authorized and not responses:
            controller.approve_tool(call, executor, document)
            continue
        job = controller.native_jobs.get(call.id)
        if responses or job is not None and job.ready_to_commit:
            break
    if responses or not commit:
        return dict(responses[-1] if responses else {}, ok=False, deliveries=len(responses))
    if job is None or not job.ready_to_commit:
        raise AssertionError("the production call neither staged nor delivered a result")
    if before_commit is not None:
        before_commit()
    try:
        outcome = bpy.ops.angelo.commit_native_job("EXEC_DEFAULT", True, call_id=call.id, session_id=session)
    except RuntimeError:
        outcome = {"CANCELLED"}  # The operator reported the refusal; the delivered result says why.
    for _ in range(400):  # A refusal may restage first; it is final once the call delivers its one result.
        if responses:
            break
        controller.pump_tools(executor, document, budget=ToolDispatchBudget(seconds=60))
    result = dict(responses[0]) if responses else {}
    return dict(result, ok=outcome == {"FINISHED"} and result.get("undoable") is True and not result.get("error"),
                deliveries=len(responses), pending=call.id in controller.pending)
