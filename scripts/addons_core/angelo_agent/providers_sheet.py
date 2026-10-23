# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The connection sheet: add or edit an AI connection on Angelo's canvas (replaces the stock dialog).

A ``card_host`` card (``BaseCard``): ``card_host`` shows it over the 3D view when the chat's "Use an API key"
row opens it, and ``providers_page_host`` over the AI Providers page. It lays out with ``card_kit.Builder`` and
the kit's components (segmented control, dropdown, text field with a masked secret, toggle, notice), and saves
through the same operator as before (``angelo.new_connection`` with ``EXEC_DEFAULT``), then shows the live check:
checking, works, or the reason in coral with a fix. The key lives only in the sheet's ``FieldState`` and the
operator call: it is cleared on submit, cancel, close and file load, and never logged or drawn unmasked
unless Show is on.
"""

import time

import bpy

from . import card_kit as kit, ui_kit_field as field
from .card_kit import BaseCard, MUTED
from .provider_registry import CODING, GENAI, PROVIDER_NAMES
from .ui_text import iface_, n_

POLL = 0.4
CHECK_SECONDS = 180


class ConnectionSheet(BaseCard):
    kind = "connection_sheet"
    modal = True
    priority = 60
    cancel_action = "cancel"
    default_action = "submit"

    def __init__(self, group=CODING, connection_id="", provider="", method="", reuse_existing=None):
        from . import saved_keys_ui, ui
        record = ui.connections().records.get(connection_id) if connection_id else None
        self.connection_id = connection_id if record else ""
        self.editing = bool(record)   # The title and the primary keep saying Add/Connect after a new one is saved.
        self.group = ui.spec_for(record["mode"])["group"] if record else (group if group in {CODING, GENAI} else CODING)
        self.provider = record["provider"] if record else provider or self._providers()[0][0]
        self.method = record["method"] if record else method or "SUBSCRIPTION"
        self.name = field.FieldState(text=record["name"] if record else "")
        self.key = field.FieldState(secret=True)
        self.model = field.FieldState(text=(record.get("model") or "") if record else "")
        self.remember = saved_keys_ui.is_remembered(record["id"]) is not False if record else True
        # None: ticked only while this provider's sign-in is found here (_default_reuse); a caller or a click decides
        # otherwise (a found sign-in's own form passes True).
        self.reuse_chosen, self.reuse = reuse_existing is not None, bool(reuse_existing)
        self.advanced = bool(self.model.text)
        self.select_open, self.highlight = False, None
        self.status = None        # (notice kind, title, body)
        self.record_id = ""
        self.started = 0.0
        self.animated = False
        self._version = 0
        self._fix_method()

    # -- data ----------------------------------------------------------------------------------------------
    def _providers(self):
        from . import ui
        return [(item[0], item[1]) for item in ui._connection_provider_items[self.group]]

    def _methods(self):
        from . import ui
        return ui.methods(self.group, self.provider)

    def _default_reuse(self):
        from . import native_suggestions, ui
        if not self.reuse_chosen:
            self.reuse = native_suggestions.reuse_by_default(self.provider, ui._authentication.status)

    def _subscription_note(self):
        """The welcome card's words for what Connect does: reuse the sign-in found here, or open the browser."""
        from . import first_run, native_suggestions, ui
        from .provider_registry import MODE_BY_CONNECTION
        mode = MODE_BY_CONNECTION.get((CODING, self.provider, "SUBSCRIPTION"))
        if mode not in first_run.CONNECT:
            return ""
        found = native_suggestions.reuse_by_default(self.provider, ui._authentication.status)
        note = first_run.CONNECT[mode]["signed_in" if found and self.reuse else "browser"]  # A marked message id.
        return iface_(note)

    def _fix_method(self):
        supported = self._methods()
        if supported and self.method not in supported:
            self.method = supported[0]

    def version(self):
        from . import native_suggestions, ui
        found = native_suggestions.reuse_by_default(self.provider, ui._authentication.status)  # A check that finishes.
        return (self._version, self.key.text, self.name.text, self.model.text, self.key.editing, self.name.editing,
                self.model.editing, self.key.revealed, self.key.cursor, self.name.cursor, self.model.cursor, found)

    def _changed(self, host):
        self._version += 1
        host.redraw()

    def _fields(self):
        return {"name": self.name, "key": self.key, "model": self.model}

    def wipe(self):
        """Forget the typed key (cancel, close, submit, file load)."""
        self.key.text, self.key.cursor, self.key.revealed = "", None, False

    # -- layout --------------------------------------------------------------------------------------------
    def layout(self, view, region_w, region_h, scale, ui):
        return kit.fit(lambda s: self._plan(view, region_w, region_h, s, ui), region_w, region_h, scale)

    def _plan(self, view, region_w, region_h, s, ui):
        from . import saved_keys_ui
        width = min(520 * s, region_w - 32 * s)
        b = kit.Builder(view, s, width, ui, padding=24)
        editing = bool(self.connection_id)
        self._default_reuse()  # The start-up sign-in check may finish while the sheet is open.
        b.heading(iface_("Edit connection") if self.editing else iface_("Add connection"), 21, width=b.inner - 40 * s)
        b.close_button("cancel")
        b.gap(10)
        names = dict(self._providers())
        provider = names.get(self.provider, PROVIDER_NAMES.get(self.provider, self.provider))
        if editing:
            b.paragraph(iface_("{provider} · fixed for this connection", provider=provider), 13, MUTED)
        else:
            b.paragraph(iface_("What it is for"), 12.5)
            b.gap(4)
            b.segmented("group", [(CODING, iface_("Coding agent")), (GENAI, iface_("Media generation"))], self.group,
                        width=b.inner)
            b.gap(14)
            b.select("provider_open", None, self._providers(), self.provider, open=self.select_open,
                     highlighted=self.highlight, label=iface_("Provider"), option_action="provider")
            if len(self._methods()) > 1:
                b.gap(14)
                b.paragraph(iface_("Connect with"), 12.5)
                b.gap(4)
                b.segmented("method", [("SUBSCRIPTION", iface_("Subscription")), ("API_KEY", iface_("API key"))],
                            self.method, width=b.inner)
        b.gap(14)
        b.text_field("field", "name", self.name, placeholder=provider, label=iface_("Name (optional)"))
        if self.method == "API_KEY":
            b.gap(14)
            hint = iface_("Leave blank to keep the current key") if editing else iface_("Paste your API key")
            b.text_field("field", "key", self.key, placeholder=hint, label=iface_("API key"))
            b.gap(6)
            b.paragraph(iface_("API usage is billed to your {provider} account.", provider=provider), 12, MUTED)
            b.gap(8)
            if saved_keys_ui.available():
                b.toggle("remember", None, saved_keys_ui.TEXT.remember, self.remember)
                b.paragraph(iface_(saved_keys_ui.TEXT.remember_description), 12, MUTED)
            else:
                b.paragraph(saved_keys_ui.unavailable_reason(), 12, MUTED)
        elif not editing:
            b.gap(10)
            b.checkbox("reuse", None, iface_("Use existing sign-in on this computer"), self.reuse)
            note = self._subscription_note()
            if note:
                b.gap(4)
                b.paragraph(note, 12, MUTED)
        else:
            b.gap(8)
            b.paragraph(iface_("Your subscription sign-in stays with its official runtime."), 12, MUTED)
        b.gap(12)
        b.disclosure("advanced", iface_("Advanced"), self.advanced)
        if self.advanced:
            b.gap(6)
            b.text_field("field", "model", self.model, placeholder=iface_("Runtime default"), label=iface_("Model ID"))
            b.gap(4)
            b.paragraph(iface_("Blank uses the runtime's default model."), 12, MUTED)
        if self.status:
            b.gap(14)
            kind, title, body = self.status
            b.notice(kind, title, body)
        b.gap(18)
        busy = bool(self.status and self.status[0] == "info")
        confirm = iface_("Save") if self.editing else iface_("Connect")
        b.buttons([("cancel", None, iface_("Close") if busy else iface_("Cancel"), "secondary", True),
                   ("submit", None, confirm, "primary", not busy)])
        height = b.height()
        y_top = min(region_h - 16 * s, (region_h + height) / 2)
        return b.place((region_w - width) / 2, y_top, True, (region_w, region_h))

    # -- actions -------------------------------------------------------------------------------------------
    def act(self, action, value, host):
        if action == "cancel":
            self.wipe()
            host.close("cancel")
            return
        if action == "group" and not self.connection_id:
            self.group, self.provider = value, None
            self.provider = self._providers()[0][0]
            self._fix_method()
        elif action == "provider_open":
            self.select_open = not self.select_open
        elif action == "provider":
            self.provider, self.select_open = value, False
            self._fix_method()
        elif action == "method":
            self.method = value
        elif action == "field":
            for name, state in self._fields().items():
                state.editing = name == value
        elif action == "reveal":
            self.key.revealed = not self.key.revealed
        elif action == "paste":
            clip = (bpy.context.window_manager.clipboard or "").strip()
            target = self._fields().get(value) or self.key
            if clip:
                field.paste(target, clip)
        elif action == "remember":
            self.remember = not self.remember
        elif action == "reuse":
            self.reuse, self.reuse_chosen = not self.reuse, True
        elif action == "advanced":
            self.advanced = not self.advanced
        elif action == "submit":
            self.submit(host)
        self._changed(host)

    def key_event(self, event, host):
        if self.select_open and event.type == 'ESC':
            self.select_open = False
            self._changed(host)
            return True
        editing = next((state for state in self._fields().values() if state.editing), None)
        if editing is None:
            return False
        result = field.handle_event(editing, event, clipboard=lambda: bpy.context.window_manager.clipboard)
        if result == "submit":
            editing.editing = False
            self.submit(host)
        elif result == "cancel":
            editing.editing = False
        elif result is None:
            return False
        self._changed(host)
        return True

    def submit(self, host):
        from . import ui
        secret = self.key.text.strip()
        if self.method == "API_KEY" and not self.connection_id and not secret:
            self.status = ("error", iface_("Enter an API key to create this connection"), "")
            self.key.editing = True
            return
        if secret:
            try:
                ui.clean_key(secret)
            except ValueError as error:
                self.status = ("error", iface_("This key can't be used"), str(error.args[0]) if error.args else "")
                return
        before = set(ui.connections().records)
        self.wipe()
        try:
            result = bpy.ops.angelo.new_connection('EXEC_DEFAULT', connection_id=self.connection_id, group=self.group,
                                                   provider=self.provider, method=self.method,
                                                   connection_name=self.name.text, api_key=secret,
                                                   remember_key=self.remember, model=self.model.text.strip(),
                                                   reuse_existing=self.reuse)
        except RuntimeError as error:
            result = {'CANCELLED'}
            self.status = ("error", iface_("The connection was not saved"), str(error).split("\n")[-1].strip())
        secret = ""
        if 'FINISHED' not in result:
            self.status = self.status or ("error", iface_("The connection was not saved"), "")
            return
        created = [rid for rid in ui.connections().records if rid not in before]
        self.record_id = created[0] if created else self.connection_id
        self.connection_id = self.connection_id or self.record_id
        self.started = time.monotonic()
        self._check(host)

    def _check(self, host):
        """Follow the saved connection's own status: checking, works, or the reason with a fix."""
        from . import ui
        record = ui.connections().records.get(self.record_id)
        if record is None:
            self.status, self.animated = None, False
            host.close("done")
            return None
        status = str(ui.connection_status(record))
        if ui.connection_ready(record):
            self.status, self.animated = ("success", iface_("It works. {name} is ready in the chat.", name=record["name"]), ""), False
            self._changed(host)
            host.after(1.2, lambda: host.close("done"))
            return None
        identity = record["mode"] if record.get("reuse_native") else record["id"]
        running = record["id"] in getattr(ui._api_connections, "running", ()) or identity in ui._authentication.running
        if running and time.monotonic() - self.started < CHECK_SECONDS:
            self.status, self.animated = ("info", iface_("Checking the connection…"), ui.shown(ui.connection_status(record))), True
            self._changed(host)
            host.after(POLL, lambda: self._check(host))
            return None
        self.animated = False
        if record["method"] == "API_KEY":
            self.status = ("error", iface_("The key didn't work"), ui.shown(ui.connection_status(record)) + " " +
                           iface_("Paste a new key and connect again."))
            self.key.editing = True
        else:
            self.status = ("warn", iface_("Not connected yet"), ui.shown(ui.connection_status(record)))
        if status.startswith("Connected to "):
            host.close("done")
            return None
        self._changed(host)
        return None

    def closed(self, how):
        self.wipe()


def open_over_chat(**values):
    """The chat's "Use an API key": the sheet over the 3D view, never Preferences."""
    from . import card_host
    card_host.request(ConnectionSheet(**values))
    card_host.pump(lambda: True)


TITLES = (n_("Add connection"), n_("Edit connection"))
