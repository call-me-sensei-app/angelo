# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's Stop (``ANGELO_OT_stop``): its notice, and what else it ends (``person_stop``).

It says what Stop does to the work the request has in flight, read before the
request stops: a native preview or job stops between steps; a running agent
script (``angelo_execute_python``) cannot be interrupted, while queued work is
cancelled; work a provider was already sent and bills (a generation, a flow
run: a watched job whose call carries a usage label, ``remote_paid``) is only
no longer watched, and the provider may still finish it and charge for it. A
request with none of these (a reply streaming, thinking, a call waiting in the
queue or for approval) simply stops, and the notice says only that.

``text`` stays English in the chat history (a later context reads it in the
handoff), ``i18n`` lets the canvas translate it (``ui_text.notice``).
"""

from .ui_text import n_, notice

SCRIPT_TOOL = "angelo_execute_python"
PAID_ONLY = n_("Stopped watching. The provider may still finish the work already sent and charge for it.")
PAID_AND_STEPS = n_("Stopping between steps. The provider may still finish the work already sent and charge for it; "
                    "changes already applied can still be undone.")
PAID_AND_PREVIEW = n_("Stopping. Previews are not applied to your scene; work in progress stops between steps. The "
                      "provider may still finish the work already sent and charge for it.")
PAID_AND_SCRIPT = n_("Stop requested. A running script can't be interrupted. The provider may still finish the work "
                     "already sent and charge for it.")


def stop_notice(descriptors, script_running=False, remote_paid=False):
    """The notice for the native cards ``descriptors`` (``agent_workspace_ui.native_cards``), a running script and
    billed provider work that keeps running after Stop (``remote_paid``). Never a bare "Stopped" while billed work
    was running: the provider is the one that decides whether it finishes."""
    preview = any(str(item.get('state', '')).upper() == 'PREVIEW' for item in descriptors)
    if remote_paid:   # One whole sentence per case, so each translates as written.
        return notice(PAID_AND_PREVIEW if preview else PAID_AND_STEPS if descriptors else PAID_AND_SCRIPT
                      if script_running else PAID_ONLY)
    if preview:
        return notice('Stopping. Previews are not applied to your scene; work in progress stops between steps.')
    if descriptors:
        return notice('Stopping between steps. Changes already applied can still be undone.')
    if script_running:
        return notice('Stop requested. A running legacy script cannot be interrupted; queued work is cancelled.')
    return notice('Stopped')


def stop_message(descriptors, script_running=False, remote_paid=False):
    return stop_notice(descriptors, script_running, remote_paid)['text']


def remote_paid(controller):
    """Whether ``controller`` is watching billed work a provider was already sent: a submitted job
    (``Controller.media_jobs``) whose call carries a usage label (the account it bills, set for every paid tool).
    Stop ends only the watching (``Controller.cancel_tools``)."""
    jobs = getattr(controller, "media_jobs", None) or {}
    return any(getattr(call, "usage_label", "") and call.id in jobs for call in tuple(controller.pending.values()))


def stop_notice_for(controller):
    """The notice for Stop in ``controller``'s chat; call it before ``Controller.stop``."""
    from .agent_workspace_ui import native_cards
    running = any(call.executing and call.name == SCRIPT_TOOL for call in tuple(controller.pending.values()))
    return stop_notice(native_cards(controller), running, remote_paid(controller))


def person_stop(controller):
    """The person's Stop (``ANGELO_OT_stop``), before ``Controller.stop``: its notice, and the work that ends with it:
    a held goal (``goal_controls.person_stopped``; goal_mode.rst, "Stopping") and a lead's batch of workers
    (``worker_resume.stop``, EVENT L-3)."""
    from . import goal_controls, worker_resume
    stopped = stop_notice_for(controller)
    goal_controls.person_stopped(controller)
    worker_resume.stop(controller)
    return stopped
