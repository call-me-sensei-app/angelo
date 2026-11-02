# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI render card (``card_host``): one click from the camera to a restyled image in Media Studio (1.0.8).

Contract: doc/angelo/render_engines.rst, "AI render". Opened by ``angelo.ai_render`` from the engine switcher, the
Render menu, the chat bar's + menu and F3. Three phases, drawn with ``card_kit``:

* **compose** (centred, modal): the scene camera it restyles, the style (a text field holding the last one), the
  image account and model (Nano Banana and Seedream first: ``ai_render.accounts``), the price estimate and what it is
  billed to, and Render. Render is the approval of exactly that one generation (``ai_render.start`` refuses anything
  else). Without a camera or an image account the card says so and Render stays off; Connect opens AI Providers.
* **running** (the 3D view's corner, non-modal, so the viewport stays usable): progress while the flow captures and
  generates. Close leaves it running; Stop ends local monitoring as a flow's Stop does.
* **done / failed** (corner): Open in Media Studio (the Image tab, where the new image is first), Render again, or the
  provider's error. A run that ends after the card was closed brings the card back in its result phase (``_watch``).
"""

import bpy
from bpy.types import Operator

from . import ai_render, card_kit as kit, ui_kit_field as field
from .ui_text import iface_, n_

WIDTH, CORNER_WIDTH = 520, 380
TITLE = n_("AI render")
SUBTITLE = n_("Restyles the view through {camera}. The scene and its render engine stay as they are.")
PLACEHOLDER = n_("Describe the look: watercolour storybook, soft morning light")
STYLE, MODEL = n_("Style"), n_("Model")
BILLED = n_("Billed to {account} · {price}. One image, no automatic retries.")
RENDER, CANCEL, CLOSE, STOP = n_("Render"), n_("Cancel"), n_("Close"), n_("Stop")
CONNECT = n_("Connect in AI Providers")
RUNNING = n_("Restyling the camera's view")
RUNNING_WHY = n_("Captured from {camera}; {model} is making the image. You can keep working.")
DONE = n_("Your AI render is in Media Studio")
DONE_WHY = n_("Open the Image tab to see it, animate it or use it as a reference.")
FAILED = n_("The AI render didn't finish")
OPEN, AGAIN = n_("Open in Media Studio"), n_("Render again")
WARN, ERROR = "warn", "error"   # i18n: notice kinds (ui_kit_status), never shown
_watch = {"run": None, "card": None}   # The last started run, and its card while open (``_watch_run``).


class AiRenderCard(kit.BaseCard):
    kind, priority, cancel_action, default_action, outside_dismisses = "ai_render", 50, "close", None, False

    def __init__(self, scene, entries, memory):
        self.phase, self.error, self.select_open = "compose", "", False
        self.camera = scene.camera.name if scene.camera else ""
        self.ratio = ai_render.camera_aspect(scene)
        self.choices = ai_render.accounts(entries)
        self.entry = ai_render.chosen(self.choices, memory)
        self.style = field.FieldState(text=memory.get("style", ""), editing=True)
        self.run_id = None
        self._version = 0

    @property
    def modal(self):   # Composing takes the 3D view; a running or finished render leaves it to the person.
        return self.phase == "compose"

    @property
    def animated(self):
        return self.phase == "running"

    def version(self):
        return (self._version, self.phase, self.style.text, self.style.caret(), self.style.editing, self.select_open)

    def price(self):
        return ai_render.price(self.entry, self.ratio) if self.entry else None

    # -- layout --------------------------------------------------------------------------------------------
    def layout(self, view, region_w, region_h, scale, ui):
        from .update_card import corner_plan
        if self.phase == "compose":
            def plan(size):
                b = self._compose(view, size, region_w, ui)
                top = min(region_h - 16 * b.s, (region_h + b.height()) / 2)
                return b.place((region_w - b.width) / 2, top, True, (region_w, region_h))
            return kit.fit(plan, region_w, region_h, scale)
        return kit.fit(lambda size: corner_plan(self._result(view, size, region_w, ui), region_w, region_h),
                       region_w, region_h, scale)

    def _heading(self, b, icon, title, subtitle):
        s, top = b.s, b.y
        b.icon(icon, b.pad, b.y, 40)
        text_x = b.pad + 54 * s
        text_w = b.width - text_x - b.pad - 24 * s
        b.paragraph(title, 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        b.paragraph(subtitle, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + 40 * s) + 14 * s

    def _compose(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=26)
        self._heading(b, "spark", iface_(TITLE), iface_(SUBTITLE, camera=self.camera or "—"))
        b.text_field("field", "style", self.style, placeholder=iface_(PLACEHOLDER), label=iface_(STYLE))
        b.gap(12)
        ready = bool(self.camera and self.entry)
        if self.choices:
            options = [(str(index), "%s · %s" % (entry.get("name", entry["id"]), entry.get("account", "")))
                       for index, entry in enumerate(self.choices)]
            b.select("model_open", None, options, str(self.choices.index(self.entry)), open=self.select_open,
                     label=iface_(MODEL), option_action="model")
            b.gap(10)
            b.paragraph(iface_(BILLED, account=self.entry.get("account", ""), price=self._price_text()), 12,
                        kit.MUTED)
        else:
            b.notice(WARN, iface_(ai_render.NO_ACCOUNT), actions=(("connect", None, iface_(CONNECT), "secondary"),))
        if not self.camera:
            b.gap(10)
            b.notice(WARN, iface_(ai_render.NO_CAMERA))
        if self.error:
            b.gap(10)
            b.notice(ERROR, self.error)
        b.gap(18)
        b.buttons([("close", None, iface_(CANCEL), "secondary", True), ("render", None, iface_(RENDER), "primary",
                                                                         ready)])
        b.close_button("close")
        return b

    def _price_text(self):
        from .flow_approvals import price_text
        value = self.price()
        return price_text(value or 0.0, value is None)

    def _result(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(CORNER_WIDTH * scale, region_w - 40 * scale), ui, padding=22)
        model = self.entry.get("name", "") if self.entry else ""
        if self.phase == "running":
            self._heading(b, "spark", iface_(RUNNING), iface_(RUNNING_WHY, camera=self.camera, model=model))
            b.chip("running")   # Pulses while the card is animated: there is no honest fraction for a remote image.
            b.gap(14)
            b.buttons([("stop", None, iface_(STOP), "ghost", True), ("close", None, iface_(CLOSE), "secondary",
                                                                     True)])
        elif self.phase == "done":
            self._heading(b, "check", iface_(DONE), iface_(DONE_WHY))
            b.buttons([("again", None, iface_(AGAIN), "secondary", True), ("open", None, iface_(OPEN), "primary",
                                                                            True)])
        else:
            self._heading(b, "alert", iface_(FAILED), self.error or "")
            b.buttons([("again", None, iface_(AGAIN), "secondary", True), ("close", None, iface_(CLOSE), "primary",
                                                                            True)])
        b.close_button("close")
        return b

    # -- input ---------------------------------------------------------------------------------------------
    def key_event(self, event, host):
        if self.phase != "compose":
            return False
        if self.select_open and event.type == 'ESC':
            self.select_open = False
            return True
        if not self.style.editing:
            return False
        outcome = field.handle_event(self.style, event, clipboard=lambda: bpy.context.window_manager.clipboard)
        if outcome in {"submit", "cancel"}:
            self.style.editing = False   # Return ends the typing; only Render (a click, or its focus) spends money.
        elif outcome is None:
            return False
        self.style.text = self.style.text[:ai_render.MAX_STYLE]
        return True

    def act(self, action, value, host):
        if action == "field":
            self.style.editing = True
        elif action == "paste":
            field.paste(self.style, (bpy.context.window_manager.clipboard or "").strip())
        elif action == "model_open":
            self.select_open = not self.select_open
        elif action == "model":
            self.entry, self.select_open = self.choices[int(value)], False
        elif action == "connect":
            host.close("connect")
            bpy.ops.angelo.flow_connect('INVOKE_DEFAULT')
            return
        elif action == "render":
            self._render(host)
        elif action == "again":
            self.phase, self.error = "compose", ""
            self.style.editing = True
        elif action == "stop":
            from . import flow_host
            flow_host.stop_run(self.run_id)
            host.close("stopped")
        elif action == "open":
            host.close("opened")
            bpy.ops.angelo.open_media_studio('INVOKE_DEFAULT', kind='IMAGE')
            return
        elif action in {"close", "dismiss"}:
            host.close("closed")
            return
        self._version += 1
        host.redraw()

    def _render(self, host):
        from .flow_spec import FlowError
        if self.phase != "compose" or not (self.camera and self.entry):
            return   # A repeated or stale Render after the run started never starts another.
        window = host.window()
        try:
            run = ai_render.start(window, self.style.text.strip(), self.entry, self.price())
        except (FlowError, ValueError, RuntimeError) as error:
            self.error = (iface_(error.template, **error.fields) if isinstance(error, ai_render.Refusal)
                          else str(error))   # A flow's own refusal is English, as the flow editor shows it.
            return
        self.run_id, self.phase, self.error = run.id, "running", ""
        self.style.editing = False
        # An Undo step of its own: a click is no undoable operator, and Undo would otherwise go back past the new flow
        # and its take while the run goes on (Media Studio's Workflows tab does the same).
        bpy.ops.ed.undo_push(message="AI render")  # i18n: undo history entry, as Blender's own are named in English
        _watch.update(run=run.id, card=self)
        host.after(0.5, lambda: _poll(self, host))
        if not bpy.app.timers.is_registered(_watch_run):   # Shows the result if the card is closed before it ends.
            bpy.app.timers.register(_watch_run, first_interval=1.0, persistent=False)

    def closed(self, how):
        if _watch.get("card") is self:
            _watch["card"] = None   # The run goes on; ``_watch_run`` shows its result when it ends.


def _poll(card, host):
    """While the card is open: follow its run; the card shows the result when it ends."""
    from . import flow_host
    state, _asset, error = ai_render.result(flow_host.run_for(card.run_id))
    if state == "running":
        return 0.5
    if _watch.get("run") == card.run_id:
        _watch.update(run=None)   # Shown here: the watcher never brings it back.
    card.phase, card.error = state, str(error) if error else ""   # A provider's own words, never translated.
    card._version += 1
    host.redraw()
    return None


def _watch_run():
    """A run whose card was closed: show its result when it ends (once), then stop watching."""
    from . import card_host, flow_host
    run_id = _watch.get("run")
    if run_id is None:
        return None
    if _watch.get("card") is not None:
        return 1.0
    state, _asset, error = ai_render.result(flow_host.run_for(run_id))
    if state == "running":
        return 1.0
    _watch["run"] = None
    if card_host.is_active("ai_render"):
        return None
    card = AiRenderCard(bpy.context.scene, [], ai_render.remembered())
    card.phase, card.run_id, card.error = state, run_id, str(error) if error else ""
    card_host.request(card)
    card_host.pump(lambda: True)
    return None


class ANGELO_OT_ai_render(Operator):
    bl_idname = "angelo.ai_render"
    bl_label = "AI Render"
    bl_description = ("Restyle the scene camera's view with an image model (Nano Banana, Seedream) after you confirm "
                      "the price; the image goes to Media Studio")

    def execute(self, context):
        from . import card_host, flow_host
        if card_host.is_active("ai_render"):
            return {'FINISHED'}
        card = AiRenderCard(context.scene, flow_host.catalog_entries("IMAGE"), ai_render.remembered())
        card_host.request(card)
        card_host.pump(lambda: True)
        return {'FINISHED'}


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_ai_render)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "ai_render", TITLE, n_("Restyle the camera's view with an image model; the result goes to Media Studio"),
        glyph="ai_render", order=22, section=chat_destinations.CREATE, operator="angelo.ai_render",
        owner="ai_render_card"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("ai_render")
    if bpy.app.timers.is_registered(_watch_run):
        bpy.app.timers.unregister(_watch_run)
    _watch.update(run=None, card=None)
    bpy.utils.unregister_class(ANGELO_OT_ai_render)
