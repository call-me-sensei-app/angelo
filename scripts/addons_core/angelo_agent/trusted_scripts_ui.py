# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The approval policy menu and operator, the explicit trust dialog and Always allow scripts' controls.

Registered in the existing chat UI (``ui.CLASSES``); the consent itself is
owned by ``trusted_scripts`` (per chat) and ``always_allow`` (per person).
"""
from bpy.props import StringProperty
from bpy.types import Menu, Operator
from .chat_settings import APPROVALS, clean_options
from .display_text import Text, shown
from .ui_text import iface_, n_, rpt_


def get_controller():
    from . import ui
    return ui.get_controller()


class ANGELO_OT_trusted_scripts(Operator):
    bl_idname = "angelo.trusted_scripts"
    bl_label = "Trust Scripts for This Chat"

    def invoke(self, context, event):
        self._consent_owner = None
        controller = get_controller()
        if controller.trusted_scripts:
            controller.set_trusted_scripts(False)
            return {'FINISHED'}
        self._consent_owner = (controller, controller.state["id"], controller.document)
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        for text in (
            n_("Allow future scripts in this chat without individual code review?"),
            n_("Scripts have full Blender privileges: files, network and programs."),
            n_("External effects are not guaranteed undoable; scene Undo is limited."),
            n_("Stop cannot interrupt an atomic Python script."),
            n_("Consent lasts only in this open chat/document; restart clears it."),
            n_("Disable it here at any time. Already queued scripts still ask."),
        ):
            self.layout.label(text=iface_(text), translate=False)

    def cancel(self, context):
        self._consent_owner = None

    def execute(self, context):
        owner = getattr(self, "_consent_owner", None)
        self._consent_owner = None  # Confirmation is single-use, including failed or revoked confirmation.
        controller = get_controller()
        if owner != (controller, controller.state["id"], controller.document):
            self.report({'ERROR'}, rpt_("Open the trust dialog for the current chat first"))
            return {'CANCELLED'}
        try:
            controller.set_trusted_scripts(True)
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_set_approval(Operator):
    bl_idname = "angelo.set_approval"
    bl_label = "Set Approval Policy"
    mode: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return APPROVALS.get(properties.mode, ("", ""))[1]

    def execute(self, context):
        from . import managed_policy
        if self.mode not in APPROVALS:  # Only the three modes; never a typo read as some other mode.
            return {'CANCELLED'}
        if self.mode == "AUTO_BOUNDED" and managed_policy.current().require_ask_for_edits:
            # RequireAskForEdits: the chat reads as Ask either way (chat_settings.clean_options); say why.
            self.report({'ERROR'}, shown(Text(managed_policy.ASK_REQUIRED)))
            return {'CANCELLED'}
        controller = get_controller()
        try:
            controller.set_options(approval=self.mode)
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_MT_approval(Menu):
    bl_label = "Approval Policy"

    def draw(self, context):
        current = clean_options(get_controller().state.get("options"))["approval"]
        for mode, (name, _) in APPROVALS.items():
            self.layout.operator("angelo.set_approval", text=iface_(name), translate=False, icon='CHECKMARK' if mode == current else 'NONE').mode = mode
        self.layout.separator()
        controller = get_controller()
        self.layout.operator("angelo.trusted_scripts", text="Disable trusted scripts" if controller.trusted_scripts
                             else "Trust scripts for this chat…", icon='CHECKMARK' if controller.trusted_scripts else 'NONE')
        self.layout.label(text="Trusted scripts run unrestricted Blender Python.")


# A change to the person's choice that did not come from the person (a script, an add-on, a keymap entry).
NOT_YOURS = n_("Always allow scripts was not changed: only you can change it, in Preferences or on a script's approval "
               "card.")


def agent_script_running():
    """Whether an agent script is executing in any chat now (its own code must never change the choice)."""
    from . import ui
    return any(call.executing and call.name == "angelo_execute_python"
               for controller in list(ui._controllers.values()) for call in list(controller.pending.values()))


def always_allow_get(prefs):
    """``ANGELO_Preferences.always_allow_scripts`` shows the saved choice (always_allow), never a stored value."""
    from . import always_allow
    return always_allow.enabled()


def always_allow_set(prefs, value):
    """The Preferences toggle: the person's change, made in the Preferences editor while no agent script runs.

    Any other assignment (a script, an add-on, a keymap entry) changes nothing
    and the chat says so. Defence in depth inside one process, not a sandbox.
    """
    import bpy
    from . import always_allow, ui
    from .ui_text import notice
    if bool(value) == always_allow.enabled():
        return
    space = getattr(bpy.context, "space_data", None)
    if getattr(space, "type", "") == 'PREFERENCES' and not agent_script_running():
        try:
            always_allow.set_enabled(value)
        except OSError as error:   # Turning it on is lost (stays off); turning it off holds for this session.
            print("Angelo: Always allow scripts could not be saved:", error)
        return
    controller = ui._controller
    if controller is not None:
        controller.emit("message", {"role": "system", **notice(NOT_YOURS)})


def draw_always_allow(layout, prefs):
    """Preferences: the Always allow scripts toggle, or the organization's lock in its place (AllowAutoApproval)."""
    from . import managed_policy
    if not managed_policy.current().allow_auto_approval:
        row = layout.row()
        row.label(text="Always allow scripts")
        row.label(text=shown(Text(managed_policy.AUTO_APPROVAL_OFF)), icon='LOCKED', translate=False)
    elif prefs is not None:
        layout.prop(prefs, "always_allow_scripts")


def always_allow_from_card(context, controller, call_id):
    """The script card's Always allow scripts: save the person's choice, and let it cover this script too.

    Offered only for a script in an Auto chat outside a recovery review
    (``offers_always_allow``); the pump then runs the script through the
    usual identity and consent checks. False when nothing was allowed.
    """
    from . import always_allow
    call = controller.pending.get(call_id)
    if call is None or not controller.offers_always_allow(call) or agent_script_running():
        return False
    if not always_allow.enabled():   # A repeated click keeps the current consent (never re-mints it).
        try:
            always_allow.set_enabled(True)   # Saved at once (always_allow), before it takes effect.
        except OSError as error:
            print("Angelo: Always allow scripts could not be saved:", error)
            return False
    return controller.always_allow_script(call)


def script_file_allow_from_card(context, controller, call_id):
    """The script card's "Always allow this file while it's unchanged" in an External conversation: save the
    approval for this app, project, path and content (``script_files``), and let it cover this run too.

    Offered only once the person opened the file's exact text (``offers_script_file_allow``); never while an agent
    script is running, so a script can't approve itself. False when nothing was allowed.
    """
    call = controller.pending.get(call_id)
    if call is None or not controller.offers_script_file_allow(call) or agent_script_running():
        return False
    try:
        return controller.allow_script_file(call)
    except OSError as error:
        print("Angelo: the script file approval could not be saved:", error)
        return False
