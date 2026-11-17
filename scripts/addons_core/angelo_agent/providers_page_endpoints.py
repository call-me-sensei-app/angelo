# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI Providers page's "Your organization's model servers" section, and its two operators.

Page model data (``providers_page_layout``), one row per custom endpoint
(``endpoint_ui``): its name, a lock fact saying who set it up, its address and
formats, its models (unmapped ones marked untested), its status, and two
buttons: Add key / Change key (``angelo.endpoint_key``, the key sheet
``endpoint_sheet``) and Test connection (``angelo.endpoint_test``). Members can't
edit the endpoint itself: the organization owns it. Strings are translated here.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import grant_guard  # noqa: E402  (only the person grants: grant_guard.grant)
from . import endpoint_connections, endpoint_ui
from .display_text import Text, shown
from .ui_text import iface_, n_

TITLE = n_("Your organization's model servers")
DESCRIPTION = n_("Your organization runs these servers for its AI models. Add your own key once; Angelo sends your "
                 "requests to them directly.")
CREDENTIAL_LINES = {"command": n_("Gets a short-lived key from your organization's sign-in program"),
                    "none": n_("This server needs no key")}
OPERATORS = {"endpoint_key": ("endpoint_key", 'INVOKE_DEFAULT', "endpoint"),
             "endpoint_test": ("endpoint_test", 'EXEC_DEFAULT', "endpoint")}


def section():
    """The section, or None when no endpoint applies to this person."""
    endpoints = endpoint_connections.current()
    where, left_out = endpoint_ui.source()
    if not endpoints and not left_out:
        return None
    rows = [_row(endpoint, where) for endpoint in endpoints]
    output = {"title": iface_(TITLE), "description": iface_(DESCRIPTION), "rows": rows}
    if left_out:
        output["description"] += " " + shown(Text(endpoint_ui.LEFT_OUT, count=left_out))
    return output


def _row(endpoint, where):
    from .endpoint_sheet import _format_label, model_label
    records = [r for r in endpoint_ui.records().values() if r["endpoint"] == endpoint.id]
    ready = bool(records) and all(endpoint_ui.ready(r) for r in records)
    status = shown(endpoint_ui.reason(records[0])) if records and not ready else ""
    if not records:   # Signed out, or no seat: the account says why.
        status = shown(endpoint_ui.reason({"endpoint": endpoint.id, "mode": ""}))
    formats = ", ".join(_format_label(f) for f, _ in endpoint.formats)
    lines = [iface_("{host} · {formats}", host=endpoint.host, formats=formats),
             iface_("Models: {models}", models=", ".join(model_label(m) for m in endpoint.models))]
    if endpoint.credential in CREDENTIAL_LINES:
        lines.append(iface_(CREDENTIAL_LINES[endpoint.credential]))
    if status:
        lines.append(status)
    report = endpoint_ui.check_report(endpoint.id)
    if report is not None and not endpoint_ui.checking(endpoint.id):
        problem = next((f for f in report.findings if f.level == "error"), None)
        lines.append(shown(problem.text) if problem else iface_("Test connection: works, with notes") if report.warnings
                     else iface_("Test connection: everything works"))
    lock = (iface_("Set up by your organization's IT policy") if where == "policy"
            else iface_("Set up by your organization in the Angelo console"))
    has_key = endpoint_ui.has_key(endpoint.id)
    buttons = []
    own_key = endpoint.credential == "member_key"
    if records and own_key:
        buttons.append((iface_("Change key") if has_key else iface_("Add key"), "endpoint_key", endpoint.id,
                        "secondary" if has_key else "primary"))
    if has_key or (records and not endpoint_connections.needs_key(endpoint.credential)):
        buttons.append((iface_("Testing…") if endpoint_ui.checking(endpoint.id) else iface_("Test connection"),
                        "endpoint_test", endpoint.id, "ghost"))
    if ready:
        chip = (iface_("Available in chat"), "teal", False)
    elif endpoint_ui.checking(endpoint.id):
        chip = (iface_("Testing…"), "muted", True)
    else:
        chip = (iface_("Needs your key") if records and own_key and not has_key else iface_("Not ready"), "amber", False)
    return {"kind": "connection", "glyph": "server", "dot": "teal" if ready else "muted", "name": endpoint.name,
            "tag": iface_("Organization"), "lines": lines, "facts": [("lock", lock, "muted")], "chip": chip,
            "buttons": buttons}


class ANGELO_OT_endpoint_key(Operator):
    bl_idname = "angelo.endpoint_key"
    bl_label = "Your Key for an Organization Model Server"
    bl_description = "Add or change your own key for one of your organization's model servers"
    bl_options = {'INTERNAL'}

    endpoint: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})

    def consent_target(self, context):
        """The organization endpoint its sheet opens for (a removed or changed one is not what was pressed)."""
        return endpoint_connections.find(self.endpoint)

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        if endpoint_connections.find(self.endpoint) is None:
            self.report({'ERROR'}, shown(endpoint_connections.REMOVED))
            return {'CANCELLED'}
        from .endpoint_sheet import EndpointSheet
        from .providers_page_host import open_sheet
        open_sheet(EndpointSheet(self.endpoint))
        return {'FINISHED'}


class ANGELO_OT_endpoint_test(Operator):
    bl_idname = "angelo.endpoint_test"
    bl_label = "Test Connection"
    bl_description = ("Send a few short requests to your organization's model server with your key, and report what "
                      "works")
    bl_options = {'INTERNAL'}

    endpoint: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})

    def execute(self, context):
        try:
            endpoint_ui.start_check(self.endpoint)
        except ValueError as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args else "")
            return {'CANCELLED'}
        if not bpy.app.timers.is_registered(_follow):
            bpy.app.timers.register(_follow, first_interval=0.4)
        return {'FINISHED'}


def _follow():
    """Redraw AI Providers until every running check has reported (``endpoint_ui.pump``)."""
    from . import preferences_ui
    endpoint_ui.pump()
    preferences_ui.redraw()
    return 0.4 if any(endpoint_ui.checking(e.id) for e in endpoint_connections.current()) else None


CLASSES = (ANGELO_OT_endpoint_key, ANGELO_OT_endpoint_test)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    if bpy.app.timers.is_registered(_follow):
        bpy.app.timers.unregister(_follow)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


grant_guard.guard(ANGELO_OT_endpoint_key)   # Only the person's click grants (grant_guard).
