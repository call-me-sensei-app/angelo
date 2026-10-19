# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo account in the status bar, right of the version number, and its menu.

The status bar shows which account and team are
signed in, with a menu to view the profile and usage, see the person's own
numbers, open Your devices, open AI Providers and sign out (or sign in).

The item is appended to Blender's ``STATUSBAR_HT_header`` (drawn after the
version) and only reads ``team_account_ui``'s account view: drawing never does
network or store I/O. Opening the menu asks the account for fresh usage
numbers on its worker. Console pages open in the system browser; their URLs
carry only organization and person ids, never a token.

Text is translated where it is drawn (``ui_text``, ``display_text``).
"""

import bpy
from bpy.props import EnumProperty
from bpy.types import Menu, Operator

from . import managed_policy, team_account_ui
from .display_text import Text, shown
from .team_view import OFFLINE, RESTORING, SIGNED_IN, SIGNING_IN
from .ui_text import iface_, n_

PAGES = (("PROFILE", n_("Profile and Usage"), n_("Your profile and usage in the Angelo console")),
         ("DEVICES", n_("Your Devices"), n_("The computers signed in to your account; sign any of them out")),
         ("LIBRARY", n_("Team Library"), n_("Your team's library in the Angelo console")),
         ("PEOPLE", n_("People"), n_("Your team's people and seats in the Angelo console")),
         ("KEYS", n_("Keys", "Angelo"), n_("Your team's AI provider keys in the Angelo console")))
SIGNED_IN_LABEL = n_("{org} · {name}")
SIGNING_IN_LABEL = n_("Signing in…")
OFFLINE_LABEL = n_("Team offline")
SIGN_IN_LABEL = n_("Sign in")
NO_SEAT_LINE = n_("No seat · Contact your admin for access")
SEAT_LINE = n_("Seat: yes")
PERIOD_LINE = n_("This period: {tokens} tokens · {tasks} agent tasks · {generations} generations")
STREAK_LINE = n_("Active {days} days this period · streak {streak} days")


def short_number(value):
    """1234 → "1.2K", 3400000 → "3.4M": the menu's compact counts (digits only, no locale words)."""
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= limit:
            return ("%.1f" % (value / limit)).rstrip("0").rstrip(".") + suffix
    return str(value)


def status_item(view, policy, unavailable=False):
    """``(text, icon)`` of the status-bar item for an account view (pure; tested on the host)."""
    if unavailable:
        return (iface_("Team sign-in off"), 'ERROR') if policy.team_only else ("", 'NONE')
    if view.status == SIGNED_IN:
        name = view.email.partition("@")[0] or view.email
        return shown(Text(SIGNED_IN_LABEL, org=view.org_name, name=name)), ('COMMUNITY' if view.seat else 'ERROR')
    if view.status == SIGNING_IN:
        return iface_(SIGNING_IN_LABEL), 'TIME'
    if view.status == RESTORING:
        return "", 'TIME'
    if view.status == OFFLINE:
        return iface_(OFFLINE_LABEL), 'ERROR'
    return iface_(SIGN_IN_LABEL), ('ERROR' if policy.team_only else 'USER')


def draw_status(self, context):
    account = team_account_ui.account()
    view = account.view() if account is not None else None
    text, icon = status_item(view, managed_policy.current(), unavailable=account is None)
    if not text and icon == 'NONE':
        return
    row = self.layout.row(align=True)
    row.separator(factor=-0.5)
    row.label(text="|", translate=False)  # As the version's own separators (uiTemplateStatusInfo).
    row.separator(factor=-0.5)
    # An operator button, not ``row.menu``: header menus are sized without room for an icon and text, which
    # cut the name short.
    row.emboss = 'NONE'
    row.operator("wm.call_menu", text=text, icon=icon, translate=False).name = ANGELO_MT_team_account.bl_idname


class ANGELO_MT_team_account(Menu):
    bl_idname = "ANGELO_MT_team_account"
    bl_label = "Angelo Account"

    def draw(self, context):
        layout = self.layout
        account = team_account_ui.account()
        policy = managed_policy.current()
        if account is None:
            layout.label(text=shown(team_account_ui._reason or Text(team_account_ui.UNAVAILABLE)), icon='ERROR',
                         translate=False)
            layout.operator("angelo.settings", text="AI Providers…", icon='PREFERENCES')
            return
        view = account.view()
        if view.status == SIGNED_IN:
            account.refresh_usage()
            layout.label(text=view.email or iface_("signed in"), icon='USER', translate=False)
            layout.label(text=view.org_name, icon='COMMUNITY', translate=False)
            layout.label(text=iface_(SEAT_LINE) if view.seat else iface_(NO_SEAT_LINE),
                         icon='CHECKMARK' if view.seat else 'ERROR', translate=False)
            if not view.seat or view.providers == ():
                layout.label(text=shown(managed_policy.contact(policy)), translate=False)
            if view.usage:
                usage = view.usage
                layout.label(text=iface_(PERIOD_LINE, tokens=short_number(usage["tokens"]),
                                         tasks=usage["agent_tasks"], generations=usage["generations"]),
                             icon='SORTTIME', translate=False)
                layout.label(text=iface_(STREAK_LINE, days=usage["active_days"], streak=usage["streak"]),
                             translate=False)
            layout.separator()
            for page, label, _description in PAGES[:3]:  # People and Keys open from a reason that needs them.
                layout.operator("angelo.team_open_console", text=iface_(label), icon='URL', translate=False).page = page
            layout.operator("angelo.settings", text="AI Providers…", icon='PREFERENCES')
            layout.separator()
            layout.operator("angelo.team_sign_out", text="Sign Out", icon='QUIT')
            return
        if view.status == SIGNING_IN:
            layout.label(text=iface_(team_account_ui.CHAT_SIGNING_IN), icon='TIME', translate=False)
            layout.operator("angelo.team_copy_sign_in_link", text="Copy Sign-in Link")
            layout.operator("angelo.team_cancel_sign_in", text="Cancel Sign-in")
            return
        if view.message:
            layout.label(text=shown(view.message), icon='INFO', translate=False)
        if view.status == OFFLINE:
            layout.operator("angelo.team_sign_out", text="Sign Out", icon='QUIT')
        elif view.status != RESTORING and not managed_policy.sign_in_problem(policy):
            layout.operator("angelo.team_sign_in", text="Sign in to Angelo Team", icon='URL')
        layout.operator("angelo.settings", text="AI Providers…", icon='PREFERENCES')


class ANGELO_OT_team_open_console(Operator):
    bl_idname = "angelo.team_open_console"
    bl_label = "Open in Angelo Console"
    bl_description = "Open this page of the Angelo console in your browser"

    page: EnumProperty(items=PAGES, options={'SKIP_SAVE'})

    def execute(self, context):
        account = team_account_ui.account()
        if account is None or not account.view().signed_in:
            return {'CANCELLED'}
        view = account.view()
        path = {"PROFILE": "/orgs/%s/usage/%s" % (view.org_id, view.user_id), "DEVICES": "/account/devices",
                "LIBRARY": "/orgs/%s/library" % view.org_id, "PEOPLE": "/orgs/%s/people" % view.org_id,
                "KEYS": "/orgs/%s/keys" % view.org_id}[self.page]
        team_account_ui.open_in_browser(account.console_url + path)
        return {'FINISHED'}


CLASSES = (ANGELO_MT_team_account, ANGELO_OT_team_open_console)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.STATUSBAR_HT_header.append(draw_status)


def unregister():
    bpy.types.STATUSBAR_HT_header.remove(draw_status)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
