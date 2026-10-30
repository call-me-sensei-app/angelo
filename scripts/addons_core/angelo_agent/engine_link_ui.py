# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's side of the engine live link: turning it on, adding the Godot plugin to a project, and answering
an engine's request.

* **The chat bar's + menu** (a Godot group: turn the link on or off, stop editing the linked asset, add the plugin
  to a project) and **File > Export** (the same three), so it is reached from Director and Manual alike.
* **A request from an engine's editor** shows a corner card (``RequestCard``) that names the engine: the asset, the
  project, and Open; when the open file has unsaved changes it says so and offers Save and Open (or, for a file never
  saved, Discard and Open). Not now tells the editor it wasn't opened. Opening replaces the open file, which is why
  nothing opens without this click.
* **The status bar** says while the link is on, and which asset is linked to which engine; Director, which hides the
  status bar, shows the linked asset in its top bar with the link's menu.

doc/angelo/engine_link.rst is the contract; doc/angelo/godot_link.rst is Godot's.
"""

import os

import bpy

from . import engine_link, engine_link_assets as assets, godot_link_assets, update_card
from .ui_text import iface_, n_, rpt_


TITLE = n_("{engine} wants to edit {asset}")   # {engine}: the engine's name ("Godot"), never translated.
BODY = n_("From {project}. Changes you make here show live in {engine}, and saving updates the asset.")
BODY_DURABLE = n_("From {project}. Saving here updates the asset in {engine}.")   # An editor without live scenes.
BODY_UNSAVED = n_("From {project}. Opening it closes your current file, which has unsaved changes.")
BODY_UNTITLED = n_("From {project}. Opening it closes your current file, which was never saved.")
STALE = n_("{asset} changed since Angelo last saved its working file: Angelo starts again from it and keeps its own "
           "file as {kept}.")
OPEN, SAVE_OPEN, DISCARD_OPEN, NOT_NOW = n_("Open"), n_("Save and Open"), n_("Discard and Open"), n_("Not now")
STARTED = n_("Godot Live Link is on. In Godot, use Edit in Angelo on a .blend or .glb asset")
STOPPED = n_("Godot Live Link is off")
BUSY = n_("Another Angelo is already linked to Godot")
FAILED = n_("Godot Live Link couldn't start: {reason}")
OPEN_FAILED = n_("Angelo couldn't open {asset}: {reason}")
INSTALLED = n_("Angelo Link added to {project}. In Godot, enable it in Project Settings > Plugins")
NOT_PROJECT = n_("Choose the folder that holds project.godot")
SETTINGS_FILE = "engine-link-settings.json"
STATUS_ON, STATUS_LIVE = n_("Godot link on"), n_("Live in {engine}: {asset}")
STATUS_LINKED = n_("Linked to {engine}: {asset}")   # Saves update it; its editors show no live scenes.
STATUS_ALONE = n_("{engine} isn't connected: {asset}")
GROUP_WHY = n_("Edit Godot assets here and see every change live in the Godot editor")


def _settings_path():
    from .ui import state_directory
    return os.path.join(state_directory(), SETTINGS_FILE)


def remember(on):
    """Keep the person's choice: a link they turned on starts again with Angelo until they turn it off."""
    import json
    try:
        path = _settings_path()
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump({"on": bool(on)}, handle)
        os.replace(path + ".tmp", path)
    except Exception:
        pass   # Remembered for this session only.


def remembered():
    import json
    try:
        with open(_settings_path(), encoding="utf-8") as handle:
            return json.load(handle).get("on") is True
    except Exception:
        return False


class RequestCard(update_card.NoticeCard):
    """An engine's request to edit an asset, answered here (``engine_link.accept`` / ``decline``)."""

    kind, priority = "godot_link", 40

    def __init__(self, request):
        project = request["project_name"] or os.path.basename(request["asset"].project)
        engine = assets.display_name(request["asset"].engine)
        if not bpy.data.is_dirty:
            body = BODY if request["live"] else BODY_DURABLE
            buttons = (("later", NOT_NOW, "ghost"), ("open", OPEN, "primary"))
        elif bpy.data.filepath:
            body, buttons = BODY_UNSAVED, (("later", NOT_NOW, "ghost"), ("save_open", SAVE_OPEN, "primary"))
        else:
            body, buttons = BODY_UNTITLED, (("later", NOT_NOW, "ghost"), ("discard_open", DISCARD_OPEN, "secondary"))
        text = iface_(body, project=project, engine=engine)
        asset = request["asset"]
        if asset.kind != ".blend" and os.path.isfile(asset.editable) and not assets.working_is_current(asset):
            text += " " + iface_(STALE, asset=asset.name, kept=os.path.basename(assets.previous_path(asset)))
        super().__init__("link", iface_(TITLE, engine=engine, asset=request["asset"].name), text,
                         [(action, iface_(label), style) for action, label, style in buttons], self._answer)
        self.request = request

    def _answer(self, action, host):
        if action in {"later", "close"}:
            engine_link.decline()
            if action == "later":
                host.close("later")
            return
        window = host.window()
        host.close(action)
        from .splash_setup import after_close
        request = self.request
        after_close(window, lambda: _open(action, request))


def _open(action, request):
    """The card's Open, run after the card has closed (a load must not happen inside its modal operator). The card
    answers its own request only; and plain Open asks again, with Save and Open, when the file changed while the card
    waited, so no click loses work it didn't name."""
    window = bpy.context.window
    if engine_link.pending() is not request:
        return
    if action == "open" and bpy.data.is_dirty:
        _show_request(request)
        return
    try:
        if action == "save_open":
            if "FINISHED" not in bpy.ops.wm.save_mainfile():
                _show_request(request)
                return
        engine_link.accept(window, request)
    except (OSError, RuntimeError) as error:
        _notice("alert", iface_(OPEN_FAILED, asset=request["asset"].name, reason=str(error)[:200]))


def _notice(icon, text):
    from . import app_notices, card_host
    card_host.request(update_card.NoticeCard(icon, text, "", lifetime=10))
    card_host.pump(app_notices.ready)


def _show_request(request):
    from . import card_host
    card_host.request(RequestCard(request))
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.0)


def _pump():
    """Show the waiting request's card as soon as the window allows (a drag or another card may hold it back)."""
    from . import app_notices, card_host
    if engine_link.pending() is None or not card_host.queued(RequestCard.kind):
        return None
    card_host.pump(app_notices.ready)
    return 0.3


def _withdraw_request():
    from . import card_host
    card_host.withdraw(RequestCard.kind)
    if card_host.is_active(RequestCard.kind):
        card_host.close("withdrawn")


def _redraw():
    """Redraw where the link's state shows: the status bar and the top bar (Director's indicator). Both are global
    areas, outside ``Screen.areas``, so only the message bus reaches them."""
    from .chat_toggle import redraw_top_bars
    msgbus = getattr(bpy, "msgbus", None)
    if msgbus is not None:
        msgbus.publish_rna(key=(bpy.types.Window, "view_layer"))   # The status bar subscribes to it.
    redraw_top_bars()


class ANGELO_OT_godot_link(bpy.types.Operator):
    bl_idname = "angelo.godot_link"
    bl_label = "Godot Live Link"
    bl_description = ("Let the Angelo Link plugin in the Godot editor send assets here: edit them in Angelo and see "
                      "every change live in Godot. Listens on this computer only")

    action: bpy.props.EnumProperty(items=(("TOGGLE", "Toggle", ""), ("ON", "On", ""), ("OFF", "Off", ""),
                                          ("UNLINK", "Stop Editing", "")),
                                   default="TOGGLE", options={'SKIP_SAVE', 'HIDDEN'})

    def execute(self, context):
        action = self.action
        if action == "TOGGLE":
            action = "OFF" if engine_link.running() else "ON"
        if action == "UNLINK":
            engine_link.end()
            return {'FINISHED'}
        if action == "OFF":
            engine_link.stop()
            remember(False)
            self.report({'INFO'}, rpt_(STOPPED))
            return {'FINISHED'}
        try:
            engine_link.start()
        except engine_link.LinkBusy:
            self.report({'ERROR'}, rpt_(BUSY))
            return {'CANCELLED'}
        except OSError as error:
            self.report({'ERROR'}, rpt_(FAILED, reason=error.strerror or str(error)))
            return {'CANCELLED'}
        remember(True)
        self.report({'INFO'}, rpt_(STARTED))
        return {'FINISHED'}


class ANGELO_OT_godot_link_install(bpy.types.Operator):
    bl_idname = "angelo.godot_link_install"
    bl_label = "Add Angelo Link to a Godot Project"
    bl_description = ("Copy the Angelo Link plugin into a Godot project's addons folder and turn on Godot Live Link. "
                      "Enable the plugin in Godot's Project Settings afterwards")

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        project = self.directory.rstrip("/\\")
        if not godot_link_assets.is_project(project):
            self.report({'ERROR'}, rpt_(NOT_PROJECT))
            return {'CANCELLED'}
        try:
            assets.install_plugin(godot_link_assets, project)
        except OSError as error:
            self.report({'ERROR'}, rpt_(FAILED, reason=error.strerror or str(error)))
            return {'CANCELLED'}
        if not engine_link.running():
            try:
                engine_link.start()
                remember(True)
            except (engine_link.LinkBusy, OSError):
                pass   # The plugin is in place; the link says why when it is turned on.
        self.report({'INFO'}, rpt_(INSTALLED, project=os.path.basename(project)))
        return {'FINISHED'}


def _group_rows(chosen, fit_target):
    from . import chat_destinations as cd
    rows = []
    if engine_link.running():
        linked = engine_link.session()
        if linked is not None:
            engine = assets.display_name(linked.engine)
            rows.append(cd.leaf("godot_link", "godot_link_unlink", (n_("Stop editing {asset} for {engine}"),
                                                                     {"asset": linked.name, "engine": engine}),
                                (n_("{engine} shows the asset as it was last saved"), {"engine": engine}),
                                glyph="link",
                                operator=ANGELO_OT_godot_link.bl_idname, properties=(("action", "UNLINK"),)))
        rows.append(cd.leaf("godot_link", "godot_link_off", n_("Turn off Godot Live Link"),
                            n_("Godot can't send assets here until you turn it on again"), glyph="link",
                            operator=ANGELO_OT_godot_link.bl_idname, properties=(("action", "OFF"),)))
    else:
        rows.append(cd.leaf("godot_link", "godot_link_on", n_("Turn on Godot Live Link"),
                            n_("Listens on this computer for the Angelo Link plugin in Godot"), glyph="link",
                            operator=ANGELO_OT_godot_link.bl_idname, properties=(("action", "ON"),)))
    rows.append(cd.leaf("godot_link", "godot_link_install", n_("Add the plugin to a Godot project…"),
                        n_("Copies Angelo Link into the project's addons folder"), glyph="link",
                        operator=ANGELO_OT_godot_link_install.bl_idname))
    return rows


def _destination():
    from . import chat_destinations as cd
    return cd.Feature("godot_link", n_("Godot live link"), GROUP_WHY, glyph="link", order=52, section=n_("Share"),
                      children=_group_rows, owner="engine_link_ui")


def draw_menu(self, context, separate=True):
    layout = self.layout
    if separate:
        layout.separator()
    if engine_link.running():
        linked = engine_link.session()
        if linked is not None:
            layout.operator(ANGELO_OT_godot_link.bl_idname, translate=False,
                            text=iface_("Stop Editing for {engine}", engine=assets.display_name(linked.engine))
                            ).action = "UNLINK"
        layout.operator(ANGELO_OT_godot_link.bl_idname, text=iface_("Turn Off Godot Live Link"),
                        translate=False).action = "OFF"
    else:
        layout.operator(ANGELO_OT_godot_link.bl_idname, text=iface_("Godot Live Link"),
                        translate=False).action = "ON"
    layout.operator(ANGELO_OT_godot_link_install.bl_idname, text=iface_("Add Angelo Link to a Godot Project…"),
                    translate=False)


class ANGELO_MT_godot_link(bpy.types.Menu):
    bl_idname = "ANGELO_MT_godot_link"
    bl_label = "Godot Live Link"

    def draw(self, context):
        draw_menu(self, context, separate=False)


def _indicator():
    """(text, icon) of the link's state, or None when it is off: live while an editor of the linked asset's engine
    and project that shows live scenes is connected, linked while only editors that re-import on save are, otherwise
    that the engine isn't (closed, or the connection was lost)."""
    if not engine_link.running():
        return None
    linked = engine_link.session()
    if linked is None:
        return iface_(STATUS_ON), 'LINKED'
    engine = assets.display_name(linked.engine)
    if engine_link.editors(live=True):
        return iface_(STATUS_LIVE, engine=engine, asset=linked.name), 'LINKED'
    if engine_link.editors():
        return iface_(STATUS_LINKED, engine=engine, asset=linked.name), 'LINKED'
    return iface_(STATUS_ALONE, engine=engine, asset=linked.name), 'UNLINKED'


def draw_director_bar(self, context):
    """Director hides the status bar, so a linked asset sits beside the mode's title in the top bar, with the link's
    state and menu."""
    from .director_layout import is_director
    if engine_link.session() is None or context.region.alignment == 'RIGHT' or not is_director(context.window):
        return
    from . import top_bar_fit
    text, icon = _indicator()
    self.layout.separator()
    row = top_bar_fit.fitted_row(self.layout, context, "godot_link", text)   # Never clipped, in any language.
    row.emboss = 'NONE'
    row.alert = icon == 'UNLINKED'
    row.menu(ANGELO_MT_godot_link.bl_idname, text=text, icon=icon, translate=False)


def draw_status(self, context):
    shown = _indicator()
    if shown is None:
        return
    row = self.layout.row()
    row.alert = shown[1] == 'UNLINKED'
    row.label(text=shown[0], icon=shown[1], translate=False)


CLASSES = (ANGELO_OT_godot_link, ANGELO_OT_godot_link_install, ANGELO_MT_godot_link)


def _start_remembered():
    """A link the person left on starts again with Angelo (never in a background or automation session)."""
    from . import app_notices
    if bpy.app.background or app_notices.automation_off() or engine_link.running() or not remembered():
        return None
    try:
        engine_link.start()
    except (engine_link.LinkBusy, OSError):
        pass   # Another Angelo has it, or the port can't open: the person turns it on again to see why.
    return None


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    chat_destinations.register(_destination())
    engine_link.hooks.update(request=_show_request, withdraw=_withdraw_request, changed=_redraw)
    engine_link.register()
    bpy.types.TOPBAR_MT_file_export.append(draw_menu)
    bpy.types.STATUSBAR_HT_header.append(draw_status)
    bpy.types.TOPBAR_HT_upper_bar.append(draw_director_bar)
    bpy.app.timers.register(_start_remembered, first_interval=2.0, persistent=True)   # A file given at launch
    # loads before it fires, and a load drops timers that aren't persistent.


def unregister():
    from . import chat_destinations
    for timer in (_start_remembered, _pump):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    bpy.types.TOPBAR_HT_upper_bar.remove(draw_director_bar)
    bpy.types.STATUSBAR_HT_header.remove(draw_status)
    bpy.types.TOPBAR_MT_file_export.remove(draw_menu)
    engine_link.unregister()
    engine_link.hooks.update(request=None, withdraw=None, changed=None)
    chat_destinations.unregister("godot_link")
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
