# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The key sheet of an organization's model server, on Angelo's canvas over the AI Providers page.

A ``card_kit`` card like the connection sheet (``providers_sheet``): the server's
name, address and models, fixed by the organization; the person's own key in a
masked field, with the same Remember choice as personal API keys; Save; Test
connection (``endpoint_check``, which says it sends a few short requests to the
organization's server) and its findings; Remove key. The typed key lives only in
the field and the ``endpoint_ui.set_key`` call: it is wiped on save, cancel, close
and file load, and never drawn unmasked unless Show is on.
"""

import bpy

from . import card_kit as kit, endpoint_connections, endpoint_ui, ui_kit_field as field
from . import ui_tokens as tk
from .card_kit import BaseCard
from .display_text import shown
from .ui_text import iface_
from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)

POLL = 0.4
TONES = {"ok": tk.TEAL, "warning": tk.AMBER, "error": tk.CORAL}


class EndpointSheet(BaseCard):
    kind = "endpoint_sheet"
    modal = True
    priority = 60
    cancel_action = "cancel"
    default_action = "submit"

    def __init__(self, endpoint_id):
        from . import saved_keys_ui
        self.endpoint_id = endpoint_id
        self.key = field.FieldState(secret=True)
        slot = endpoint_ui.key_slot(endpoint_id)
        self.remember = saved_keys_ui.is_remembered(slot) is not False if slot else True
        self.status = None        # (notice kind, title, body)
        self._version = 0

    def _endpoint(self):
        return endpoint_connections.find(self.endpoint_id)

    def version(self):
        report = endpoint_ui.check_report(self.endpoint_id)
        return (self._version, self.key.text, self.key.editing, self.key.revealed, self.key.cursor,
                endpoint_ui.has_key(self.endpoint_id), endpoint_ui.checking(self.endpoint_id), id(report))

    def _changed(self, host):
        self._version += 1
        host.redraw()

    def wipe(self):
        self.key.text, self.key.cursor, self.key.revealed = "", None, False

    # -- layout --------------------------------------------------------------------------------------------
    def layout(self, view, region_w, region_h, scale, ui):
        return kit.fit(lambda s: self._plan(view, region_w, region_h, s, ui), region_w, region_h, scale)

    def _plan(self, view, region_w, region_h, s, ui):
        from . import saved_keys_ui
        endpoint = self._endpoint()
        width = min(540 * s, region_w - 32 * s)
        b = kit.Builder(view, s, width, ui, padding=24)
        b.heading(endpoint.name if endpoint else iface_("Model server removed"), 21, width=b.inner - 40 * s)
        b.close_button("cancel")
        b.gap(8)
        if endpoint is None:
            b.paragraph(shown(endpoint_connections.REMOVED), 13, tk.MUTED)
            b.gap(18)
            b.buttons([("cancel", None, iface_("Close"), "secondary", True)])
            return self._place(b, region_w, region_h, width, s)
        where, _left_out = endpoint_ui.source()
        b.paragraph(iface_("Set up by your organization's IT policy") if where == "policy"
                    else iface_("Set up by your organization in the Angelo console"), 12.5, tk.MUTED)
        b.gap(10)
        b.paragraph(iface_("Address: {url}", url=endpoint.base_url), 13, tk.TEXT, mono=False)
        for format_id, _form in endpoint.formats:
            names = [model_label(m) for m in endpoint.models_in(format_id)]
            if names:
                b.paragraph(iface_("{format} models: {models}", format=_format_label(format_id), models=", ".join(names)),
                            12.5, tk.MUTED)
        b.gap(14)
        has_key = endpoint_ui.has_key(self.endpoint_id)
        own_key = endpoint.credential == "member_key"
        if own_key:
            hint = (iface_("Leave blank to keep your current key") if has_key
                    else iface_("Paste the key your organization gave you"))
            b.text_field("field", "key", self.key, placeholder=hint, label=iface_("Your key"))
            b.gap(6)
        else:
            from .providers_page_endpoints import CREDENTIAL_LINES
            b.paragraph(iface_(CREDENTIAL_LINES[endpoint.credential]), 13, tk.TEXT)
            b.gap(6)
        b.paragraph(iface_("Requests go only to {host}, straight from this computer. Your organization's server "
                           "bills their use.", host=endpoint.host), 12, tk.MUTED)
        b.gap(8)
        if own_key and saved_keys_ui.available():
            b.toggle("remember", None, saved_keys_ui.TEXT.remember, self.remember)
            b.paragraph(iface_(saved_keys_ui.TEXT.remember_description), 12, tk.MUTED)
        elif own_key:
            b.paragraph(shown(saved_keys_ui.unavailable_reason()), 12, tk.MUTED)
        self._report(b)
        if self.status:
            b.gap(12)
            kind, title, body = self.status
            b.notice(kind, title, body)
        b.gap(12)
        b.paragraph(iface_("Test connection sends a few short requests to {host} with your key.", host=endpoint.host),
                    12, tk.MUTED)
        b.gap(14)
        checking = endpoint_ui.checking(self.endpoint_id)
        usable = has_key or bool(self.key.text.strip()) or not endpoint_connections.needs_key(endpoint.credential)
        left = [("test", None, iface_("Testing…") if checking else iface_("Test connection"), "secondary",
                 not checking and usable)]
        if has_key and own_key:
            left.append(("forget", None, iface_("Remove key"), "ghost", not checking))
        right = [("cancel", None, iface_("Close"), "secondary", True)]
        if own_key:
            right.append(("submit", None, iface_("Save"), "primary", bool(self.key.text.strip())))
        b.buttons(right, left=left)
        return self._place(b, region_w, region_h, width, s)

    def _report(self, b):
        report = endpoint_ui.check_report(self.endpoint_id)
        if endpoint_ui.checking(self.endpoint_id):
            b.gap(12)
            kind = "info"  # i18n: notice kind
            b.notice(kind, iface_("Testing the connection…"), "")
            return
        if report is None:
            return
        b.gap(12)
        title = (iface_("Test connection: everything works") if report.ok and not report.warnings
                 else iface_("Test connection: works, with notes") if report.ok
                 else iface_("Test connection found a problem"))
        kind = "success" if report.ok and not report.warnings else "warn" if report.ok else "error"  # i18n: notice kinds
        b.notice(kind, title, "")
        for finding in report.findings:
            b.gap(4)
            b.paragraph(shown(finding.text), 12.5, TONES.get(finding.level, tk.MUTED))

    @staticmethod
    def _place(b, region_w, region_h, width, s):
        height = b.height()
        y_top = min(region_h - 16 * s, (region_h + height) / 2)
        return b.place((region_w - width) / 2, y_top, True, (region_w, region_h))

    # -- actions -------------------------------------------------------------------------------------------
    def act(self, action, value, host):
        if action == "cancel":
            self.wipe()
            host.close("cancel")
            return
        if action == "field":
            self.key.editing = True
        elif action == "reveal":
            self.key.revealed = not self.key.revealed
        elif action == "paste":
            clip = (bpy.context.window_manager.clipboard or "").strip()
            if clip:
                field.paste(self.key, clip)
        elif action == "remember":
            self.remember = not self.remember
        elif action == "submit":
            self.submit()
        elif action == "test":
            if self.key.text.strip() and not self.submit():
                self._changed(host)
                return
            self._test(host)
        elif action == "forget":
            endpoint_ui.forget_key(self.endpoint_id)
            self.status = ("info", iface_("Your key was removed"), "")
        self._changed(host)

    def key_event(self, event, host):
        if not self.key.editing:
            return False
        result = field.handle_event(self.key, event, clipboard=lambda: bpy.context.window_manager.clipboard)
        if result == "submit":
            self.key.editing = False
            self.submit()
        elif result == "cancel":
            self.key.editing = False
        elif result is None:
            return False
        self._changed(host)
        return True

    def submit(self):
        """Use (and remember) the typed key; True when it was taken."""
        secret = self.key.text.strip()
        self.wipe()
        if not secret:
            return False
        try:
            endpoint_ui.set_key(self.endpoint_id, secret, self.remember)
        except (ValueError, RuntimeError) as error:
            self.status = ("error", iface_("This key can't be used"), shown(error.args[0]) if error.args else "")
            return False
        finally:
            secret = ""
        self.status = ("success", iface_("Your key is saved. The model server is available in the chat."), "")
        return True

    def _test(self, host):
        try:
            endpoint_ui.start_check(self.endpoint_id)
        except ValueError as error:
            self.status = ("error", iface_("Can't test yet"), shown(error.args[0]) if error.args else "")
            return
        self.status = None
        self._follow(host)

    def _follow(self, host):
        """Redraw until the check finishes (``endpoint_ui.pump`` applies its report)."""
        endpoint_ui.pump()
        self._changed(host)
        if endpoint_ui.checking(self.endpoint_id):
            host.after(POLL, lambda: self._follow(host))

    def closed(self, how):
        self.wipe()


def model_label(model):
    """A configured model's name as AI Providers shows it: untested when it maps to no Angelo model."""
    return iface_("{model} · untested", model=model.id) if model.profile is None else model.id


def _format_label(format_id):
    from .custom_endpoints import FORMATS
    return FORMATS[format_id][2]


_grant_guard.entry_methods(EndpointSheet, "submit")
