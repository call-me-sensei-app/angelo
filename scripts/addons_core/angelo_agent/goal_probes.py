# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a held goal reads of Blender, the usage ledger and the chat's settings (``goal_loop``, ``goal_controls``).

Each is read when a round is checked, never cached; tests replace them (``mock.patch.object(goal_probes, ...)``),
and both goal modules call them through this module so a replacement reaches every caller. No bpy at import.
"""


def spent(controller):
    """``(tokens, usd)`` this chat and its workers' chats have used (the usage meter's ledger)."""
    from . import usage_meter
    ledger = usage_meter.UsageLedger.shared(controller.telemetry.directory)
    chats = [controller.state["id"]]
    try:
        from . import worker_sessions
        chats += list(worker_sessions.workers_of(controller.state["id"]))
    except Exception:  # noqa: BLE001 - no parallel edit in this build: the chat's own usage only.
        pass
    tokens, usd = 0, 0.0
    for chat_id in chats:
        record = ledger.chat(chat_id) or {}
        for bucket in (record.get("modes") or {}).values():
            tokens += sum(bucket.get(key, 0) for key in usage_meter.TOKEN_KEYS)
            usd += bucket.get("usd", 0.0)
    return tokens, usd

def live_usage(controller):
    """``(tokens, usd)`` the running request has used so far and the ledger does not hold yet (lower bounds)."""
    from . import usage_meter
    running = usage_meter.in_progress(getattr(controller, "_request_metrics", None)) or {}
    tokens = sum(sum(bucket.get(key, 0) for key in usage_meter.TOKEN_KEYS) for bucket in running.values())
    return tokens, sum(bucket.get("usd", 0.0) for bucket in running.values())

def paid(controller):
    from . import usage_view
    return usage_view.billing(controller.state["provider"]) == usage_view.PAID

def blender_busy(window):
    """A render, bake or job holds Blender, or a modal tool runs in ``window``: a request now would be refused."""
    from . import worker_resume
    if worker_resume._blender_busy():
        return True
    try:
        from .view_capture import tool_running
        return bool(window is not None and tool_running(window))
    except ImportError:
        return False

def away(controller, ui):
    from . import document_scene
    return bool(document_scene.away(controller, ui.document_key()))

def editor_drafts():
    """The drafts in every chat editor shown now (each shows the active chat), or None when they can't be read."""
    try:
        import bpy
        from . import console, live_windows
        from .editor_types import read_chat_draft
        drafts = [read_chat_draft(area.spaces.active) for _window, area in
                  live_windows.window_areas(bpy.context.window_manager) if console.is_chat(area)]
    except Exception:  # noqa: BLE001 - unreadable editors: the remembered draft decides.
        return None
    return [str(draft[0]) for draft in drafts if draft is not None]

def live_draft(controller, ui):
    """The text in this chat's composer now: the shown chat's editors read live (``ui._drafts`` is a snapshot taken
    only when a chat is hidden), a hidden chat's remembered draft."""
    if controller is getattr(ui, "_controller", None):
        drafts = editor_drafts()
        if drafts:   # Shown in at least one editor: what is typed there now.
            return next((draft for draft in drafts if draft.strip()), "")
    draft = (getattr(ui, "_drafts", {}) or {}).get(controller.state["id"])
    return str(draft[0]) if draft else ""

def document_now(ui):
    return ui.document_key()

def live_chats(ui=None):
    """Every chat this process hosts: ``{chat id: controller}``."""
    if ui is None:
        import sys
        ui = sys.modules.get(__package__ + ".ui")
    return dict(getattr(ui, "_controllers", None) or {})

def worker_chat(controller):
    from . import parallel_access
    return bool(parallel_access.worker_chat(controller))

def approval(controller):
    from .chat_settings import clean_options
    return clean_options(controller.state.get("options"))["approval"]


def connected(controller, ui):
    """The chat's current connection rule, never its one-shot setup_required pump signal."""
    check = getattr(ui, "connection_available", None)
    if check is None:
        return True   # Hosts without a window connection rule leave admission to the send itself.
    try:
        return bool(check(controller))
    except Exception as error:   # noqa: BLE001 - a failing connection probe waits rather than sending.
        from .pump_guard import report_defect
        report_defect("a goal's connection check", error)
        return False
