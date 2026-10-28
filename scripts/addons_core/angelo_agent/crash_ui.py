# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The crash report inside Blender: finding the last crash, asking, sending, saving.

At the next launch after a crash (``app_notices`` starts the scan once the window is ready) a worker
thread looks for Angelo's crash log in the temporary folder (``crash_report.find_pending``). The result is
handled on the main thread by consent (``crash_state``):

* ``never``  - nothing is shown or sent; the crash is recorded as declined.
* ``ask``    - the "Angelo quit unexpectedly" dialog (``crash_card``).
* ``always`` - the report is sent without a dialog, because the person chose that earlier, and a small
  notice says so with a way to see exactly what was sent or to change the choice. A send that fails is
  tried again at the next launch, up to three times. While no endpoint exists, the dialog is shown instead.

The machine policy ``DisableCrashReporting`` shows nothing and sends nothing. The crash log stays where
the crash handler wrote it; Angelo never deletes or uploads the file itself, only the report built from it.
"""

import os
import tempfile
import threading
import time
from datetime import datetime, timezone

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import (card_host, crash_card, crash_flow, crash_report, crash_state, crash_upload, managed_policy,
               update_card, update_ui, updates_prefs)
from .ui_text import iface_, n_, rpt_

_inbox = []
_lock = threading.Lock()
_scanner = None
flow = None            # The CrashFlow of the dialog on screen (the Save operator reads it).
scans = 0              # Completed scans this session; fixtures read it.
SENT_TITLE = n_("Crash report sent")
SENT_BODY = n_("A report about the last crash was sent, as you chose. It holds technical details only.")
VIEW, ASK_NEXT_TIME = n_("See what was sent"), n_("Ask me next time")
COULD_NOT_SAVE = n_("Could not save the crash report: {reason}")
WRITE_FAILED = n_("the file could not be written")


def temp_base():
    """The folder the crash handler writes into: the parent of this session's own temporary folder."""
    session = os.path.normpath(bpy.app.tempdir) if bpy.app.tempdir else ""
    return os.path.dirname(session) if session and session != "." else tempfile.gettempdir()


def start_scan():
    """Look for a crash on a worker thread; the result is handled by ``poll``."""
    global _scanner
    roots, base = [bpy.utils.resource_path('LOCAL')], temp_base()
    directory = update_ui.directory()

    def work():
        try:
            state = crash_state.load(directory)
            found = crash_report.find_pending(base, state, time.time(), roots)
        except Exception:  # Looking for a crash must never end silently or disturb the session.
            found = (None, [])
        with _lock:
            _inbox.append(found)

    _scanner = threading.Thread(target=work, name="angelo-crash-scan", daemon=True)
    _scanner.start()


def poll():
    """Main thread: handles a finished scan. Returns True once a scan has been handled."""
    global scans
    with _lock:
        found, _inbox[:] = (list(_inbox), [])
    for pending, records in found:
        scans += 1
        _handle(pending, records)
    return bool(found)


def gpu_info():
    try:
        import gpu
        platform = gpu.platform
        return platform.vendor_get(), platform.renderer_get(), platform.version_get(), platform.backend_type_get()
    except (ImportError, SystemError, RuntimeError, AttributeError):
        return None


def build_document(pending):
    build = lambda value: value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)
    system = crash_report.system_info(gpu_info())
    app = crash_report.app_info(bpy.app.version_string, build(bpy.app.build_hash), build(bpy.app.build_date))
    return crash_report.build_document(pending.parsed, system, app, pending.mtime_ns, datetime.now(timezone.utc))


def _handle(pending, records):
    global flow
    directory = update_ui.directory()
    state = crash_state.load(directory)
    for fingerprint, status in records:
        state.mark(fingerprint, status)
    if pending is None:
        crash_state.save(directory, state)
        return
    if managed_policy.current().disable_crash_reporting:
        crash_state.save(directory, state)   # Nothing offered, nothing recorded: a lifted policy can still ask.
        return
    if state.consent == crash_state.NEVER:
        state.mark(pending.fingerprint, crash_state.DECLINED)
        crash_state.save(directory, state)
        return
    if state.held_back(pending.fingerprint):
        crash_state.save(directory, state)   # The service asked to wait (Retry-After): ask again at a later launch.
        return
    crash_state.save(directory, state)
    endpoint = crash_upload.endpoint(update_ui.checker().crash_report_url())
    new = crash_flow.CrashFlow(directory, pending, build_document(pending), endpoint)
    if state.consent == crash_state.ALWAYS and new.can_send:
        if state.attempts(pending.fingerprint) < crash_state.MAX_ATTEMPTS:
            _send_automatically(new)
        return
    flow = new   # Ask (also "always" while no endpoint exists: the dialog still offers Save report… and the website).
    card_host.request(crash_card.CrashCard(new, Actions()))


def _send_automatically(automatic):
    automatic.start_send()

    def check():
        if not automatic.poll():
            return 0.2
        status = automatic.finish_automatic()
        updates_prefs.refresh()
        if status == crash_state.SENT:
            card_host.request(update_card.NoticeCard(
                "check", iface_(SENT_TITLE), iface_(SENT_BODY),
                [("change", iface_(ASK_NEXT_TIME), "ghost"), ("view", iface_(VIEW), "primary")],
                on_action=_notice_action, lifetime=20))
        return None

    bpy.app.timers.register(check, first_interval=0.2)


def _notice_action(action, host):
    # Close first: a card requested while this corner notice is showing would replace it and queue it again.
    if action == "view":
        host.close("view")
        show_last_sent()
    elif action == "change":
        host.close("change")
        set_consent(crash_state.ASK)


def show_last_sent():
    """The review card: exactly what the last sent report said."""
    global flow
    last = crash_flow.last_sent(update_ui.directory())
    if last is None:
        return False
    flow = crash_flow.CrashFlow.review(update_ui.directory(), last)
    card_host.request(crash_card.CrashCard(flow, Actions()))
    return True


class Actions:
    """What the dialog's buttons do inside Blender."""

    def save(self):
        from . import live_windows
        window = live_windows.first(bpy.context.window_manager)
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.crash_save_report('INVOKE_DEFAULT')

    def open_problem(self, url):
        update_card.open_link(url)

    def copy(self, text):
        bpy.context.window_manager.clipboard = text

    def finished(self):
        global flow
        flow = None
        updates_prefs.refresh()   # "View Last Report Sent" becomes available after a send.

    def stop_automatic(self):
        set_consent(crash_state.ASK)


def consent():
    return crash_state.load(update_ui.directory()).consent


def set_consent(value):
    directory = update_ui.directory()
    state = crash_state.load(directory)
    state.consent = value if value in crash_state.CONSENTS else crash_state.ASK
    crash_state.save(directory, state)
    updates_prefs.refresh()


class ANGELO_OT_crash_save_report(Operator):
    bl_idname = "angelo.crash_save_report"
    bl_label = "Save Crash Report"
    bl_description = "Save the crash report, exactly as it would be sent, to a file you choose"
    bl_options = {'INTERNAL'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return flow is not None

    def invoke(self, context, event):
        self.filepath = flow.default_file_name()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            flow.save_to(self.filepath)
        except OSError as error:
            self.report({'ERROR'}, iface_(COULD_NOT_SAVE, reason=error.strerror or iface_(WRITE_FAILED)))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_("Crash report saved"))
        return {'FINISHED'}


class ANGELO_OT_crash_report_last(Operator):
    bl_idname = "angelo.crash_report_last"
    bl_label = "View Last Crash Report Sent"
    bl_description = "Show exactly what Angelo's last crash report said"

    @classmethod
    def poll(cls, context):
        return crash_flow.last_sent(update_ui.directory()) is not None

    def execute(self, context):
        show_last_sent()
        return {'FINISHED'}


CLASSES = (ANGELO_OT_crash_save_report, ANGELO_OT_crash_report_last)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global flow
    flow = None
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
