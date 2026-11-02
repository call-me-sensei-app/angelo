# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Bake Maps card (``card_host``): every PBR map of the selected objects as a texture set, in one click.

Contract: doc/angelo/long_jobs.rst, "Texture sets". Opened by ``angelo.bake_maps``
from the chat bar's + menu and F3. Drawn with ``card_kit``, three phases like the
AI render card:

* **compose** (centred, modal): the selected meshes, each baked on its own or,
  with two or more selected, the others onto the active one (high to low, with
  the scene's cage and extrusion); every map the materials use or a chosen list;
  packing, size, file format and 16 bits, margin, and a baked material. Bake is
  the person's approval of exactly that job (as F12 is a render's).
* **running** (the 3D view's corner, non-modal): the job's own progress; Stop
  cancels it, Close leaves it running (its chat card and status bar item stay).
* **done / failed** (corner): Open Folder, Use Baked Material (assigns it to the
  object, one Undo step), or the reason.

The job is the agent's ``BAKE_MAPS`` (``job_bake_maps``), started through the same
manager, so it shares one queue, Cancel and the chat's job card.
"""

import bpy
from bpy.types import Operator

from . import card_kit as kit
from .input_policy import modal_options
from .bake_maps_spec import CHANNELS, PACKINGS, card_request
from .bake_maps_text import CHANNEL_LABELS, PACKING_LABELS
from .ui_text import iface_, n_, plural_

WIDTH, CORNER_WIDTH = 540, 380
TITLE = n_("Bake every map")
SUBTITLE = n_("Each PBR map the materials use, as a texture set in this chat's files. Your materials and images stay "
              "as they are.")
NO_SELECTION = n_("Select one or more meshes with UVs and materials, then open Bake Maps again.")
EACH, HIGH_TO_LOW = n_("Each object"), n_("High to low")
ONTO = n_("Bakes {sources} onto {target}, using the scene's cage and extrusion settings.")
AUTO_MAPS = n_("Every map the materials use")
AUTO_WHY = n_("Base color, metallic and roughness, plus normal, emission, alpha, AO and height where present")
PACKING, SIZE, FORMAT, MARGIN = n_("Packing"), n_("Size"), n_("File format"), n_("Margin")
DEEP = n_("16 bits for every map")
DEEP_WHY = n_("Normal and height maps are 16-bit already")
MATERIAL = n_("Make a baked material")
MATERIAL_WHY = n_("A new material wired to the maps; nothing is assigned until you choose")
BAKE, CANCEL, CLOSE, STOP = n_("Bake"), n_("Cancel"), n_("Close"), n_("Stop")
RUNNING = n_("Baking the texture set")
RUNNING_WHY = n_("{maps} of {objects}. You can keep working; Stop discards it.")
DONE = n_("Your texture set is ready")
DONE_WHY = n_("{count} files in {folder}.")
FAILED = n_("The texture set wasn't baked")
OPEN, USE = n_("Open Folder"), n_("Use Baked Material")
SIZES = (1024, 2048, 4096, 8192)
MARGINS = (4, 8, 16, 32)
WARN, ERROR = "warn", "error"   # i18n: notice kinds (ui_kit_status), never shown


def selection(context):
    """``(objects, active)``: the selected meshes (names), active first when it is one of them."""
    meshes = [obj.name for obj in context.selected_objects if obj.type == 'MESH']
    active = context.active_object.name if context.active_object and context.active_object.name in meshes else None
    if active:
        meshes.remove(active)
        meshes.insert(0, active)
    return meshes, active


class BakeMapsCard(kit.BaseCard):
    kind, priority, cancel_action, default_action, outside_dismisses = "bake_maps", 50, "close", None, False

    def __init__(self, objects, active):
        self.phase, self.error, self.select_open = "compose", "", False
        self.objects, self.active = list(objects), active
        self.high_to_low, self.auto, self.maps = False, True, set(CHANNELS)
        self.packing, self.size, self.image_format = "SEPARATE", 2048, "PNG"
        self.deep, self.margin, self.material = False, 16, True
        self.job, self.record, self._version = None, None, 0

    @property
    def modal(self):
        return self.phase == "compose"

    @property
    def animated(self):
        return self.phase == "running"

    def version(self):
        progress = round(self.record.get("progress", 0.0), 3) if self.record else 0
        return (self._version, self.phase, self.select_open, progress)

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
        top = b.y
        b.icon(icon, b.pad, b.y, 40)
        text_x = b.pad + 54 * b.s
        text_w = b.width - text_x - b.pad - 24 * b.s
        b.paragraph(title, 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        b.paragraph(subtitle, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + 40 * b.s) + 14 * b.s

    def _label(self, b, text):
        b.paragraph(text, 12.5, x=b.pad + 2 * b.s)
        b.y += 4 * b.s

    def _compose(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=26)
        self._heading(b, "maps", iface_(TITLE), iface_(SUBTITLE))
        if not self.objects:
            b.notice(WARN, iface_(NO_SELECTION))
            b.gap(18)
            b.buttons([("close", None, iface_(CLOSE), "primary", True)])
            b.close_button("close")
            return b
        b.paragraph(", ".join(self.objects[:6]) + (" …" if len(self.objects) > 6 else ""), 13, kit.TEXT)
        b.gap(10)
        if len(self.objects) > 1 and self.active:
            b.segmented("mode", [("each", iface_(EACH)), ("high", iface_(HIGH_TO_LOW))],
                        "high" if self.high_to_low else "each")
            if self.high_to_low:
                b.gap(6)
                b.paragraph(iface_(ONTO, sources=", ".join(self.objects[1:4]), target=self.active), 12, kit.MUTED)
            b.gap(12)
        b.toggle("auto", None, iface_(AUTO_MAPS), self.auto, caption=iface_(AUTO_WHY))
        if not self.auto:
            b.gap(6)
            for channel in CHANNELS:
                b.checkbox("map", channel, iface_(CHANNEL_LABELS[channel]), channel in self.maps)
        b.gap(12)
        self._label(b, iface_(SIZE))
        b.segmented("size", [(str(size), "%dK" % (size // 1024)) for size in SIZES], str(self.size))
        b.gap(12)
        self._label(b, iface_(FORMAT))
        b.segmented("format", [("PNG", "PNG"), ("OPEN_EXR", "OpenEXR")], self.image_format)
        if self.image_format == "PNG":
            b.gap(6)
            b.toggle("deep", None, iface_(DEEP), self.deep, caption=iface_(DEEP_WHY))
        b.gap(12)
        self._label(b, iface_(MARGIN))
        b.segmented("margin", [(str(px), "%d px" % px) for px in MARGINS], str(self.margin))
        b.gap(12)
        b.toggle("material", None, iface_(MATERIAL), self.material, caption=iface_(MATERIAL_WHY))
        b.gap(12)
        b.select("packing_open", None, [(name, iface_(PACKING_LABELS[name])) for name in PACKINGS], self.packing,
                 open=self.select_open, label=iface_(PACKING), option_action="packing")
        if self.error:
            b.gap(10)
            b.notice(ERROR, self.error)
        b.gap(18)
        ready = self.auto or bool(self.maps)
        b.buttons([("close", None, iface_(CANCEL), "secondary", True), ("bake", None, iface_(BAKE), "primary", ready)])
        b.close_button("close")
        return b

    def _result(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(CORNER_WIDTH * scale, region_w - 40 * scale), ui, padding=22)
        record = self.record or {}
        if self.phase == "running":
            files = record.get("detail", {}).get("files", 0)
            self._heading(b, "maps", iface_(RUNNING), iface_(RUNNING_WHY, maps=plural_(files, "{count} map",
                                                                                       "{count} maps"),
                                                             objects=", ".join(self.objects[:3])))
            b.progress(record.get("progress", 0.0))
            b.gap(14)
            b.buttons([("stop", None, iface_(STOP), "ghost", True), ("close", None, iface_(CLOSE), "secondary", True)])
        elif self.phase == "done":
            receipt = record.get("receipt", {})
            folder = receipt.get("output") or record.get("request", {}).get("output", "")
            self._heading(b, "check", iface_(DONE), iface_(DONE_WHY, count=receipt.get("maps", 0), folder=folder))
            buttons = [("open", None, iface_(OPEN), "secondary", True)]
            if any(isinstance(name, str) for name in receipt.get("materials") or []):
                buttons.append(("use", None, iface_(USE), "primary", True))
            b.buttons(buttons)
        else:
            from .job_chat import STATUS_LABELS
            reason = record.get("reason") or iface_(STATUS_LABELS.get(record.get("status"), ""))
            self._heading(b, "alert", iface_(FAILED), reason)
            b.buttons([("close", None, iface_(CLOSE), "primary", True)])
        b.close_button("close")
        return b

    # -- input ---------------------------------------------------------------------------------------------
    def key_event(self, event, host):
        if self.select_open and event.type == 'ESC':
            self.select_open = False
            return True
        return False

    def act(self, action, value, host):
        if action == "mode":
            self.high_to_low = value == "high"
        elif action == "auto":
            self.auto = not self.auto
        elif action == "map":
            self.maps ^= {value}
        elif action == "size":
            self.size = int(value)
        elif action == "format":
            self.image_format = value
        elif action == "deep":
            self.deep = not self.deep
        elif action == "margin":
            self.margin = int(value)
        elif action == "material":
            self.material = not self.material
        elif action == "packing_open":
            self.select_open = not self.select_open
        elif action == "packing":
            self.packing, self.select_open = value, False
        elif action == "bake":
            self._bake(host)
        elif action == "stop":
            from . import job_ui
            if self.job:
                job_ui.manager().cancel(self.job, by="person")
        elif action == "open":
            from . import job_ui
            try:
                job_ui.open_output(self.record)
            except Exception as error:   # Moved or deleted since: said on the card.
                self.error = str(error)
        elif action == "use":
            self._use_material()
        elif action in {"close", "dismiss"}:
            host.close("closed")
            return
        self._version += 1
        host.redraw()

    def request(self):
        """The job request this card's choices make (``bake_maps_spec.card_request``)."""
        return card_request(self.size, self.packing, self.image_format, self.margin, self.material,
                            None if self.auto else self.maps, self.deep,
                            bpy.path.clean_name(self.active or self.objects[0]) or "Object")

    def _bake(self, host):
        from . import job_host, job_ui, ui
        from .job_spec import JobRequestError
        from .scene_queries import object_handle
        if self.phase != "compose":
            return
        objects = [bpy.data.objects.get(name) for name in self.objects]
        if any(obj is None for obj in objects):
            self.error = iface_(NO_SELECTION)
            return
        arguments = self.request()
        if self.high_to_low and self.active:
            target = bpy.data.objects[self.active]
            sources = [obj for obj in objects if obj is not target]
            cage = bpy.context.scene.render.bake.cage_object if bpy.context.scene.render.bake.use_cage else None
            arguments.update(target=object_handle(target), sources=[object_handle(obj) for obj in sources])
            if cage is not None:
                arguments["cage"] = object_handle(cage)
        else:
            arguments["targets"] = [object_handle(obj) for obj in objects]
        controller, window = ui.get_controller(), host.window()
        try:
            # The agent's own admission (job_host): the same refusals, and a taken folder name gets the next free one.
            request, runner = job_host._admit(controller, window, arguments, person=True)
            record = job_ui.manager().submit(controller.state["id"], "", request, runner)
        except (JobRequestError, RuntimeError, ReferenceError) as error:
            self.error = str(error)   # The job's own refusal (English, as the agent reads it), with the fix.
            return
        self.job, self.record, self.phase, self.error = record["id"], record, "running", ""
        job_ui.redraw()
        host.after(0.5, lambda: _poll(self, host))

    def _use_material(self):
        made = (self.record or {}).get("receipt", {}).get("materials") or []   # One per target, in order.
        targets = [self.active] if self.high_to_low and self.active else self.objects
        for name, material_name in zip(targets, made):
            if not isinstance(material_name, str):
                continue   # That target's material could not be made: its object keeps its own.
            obj, material = bpy.data.objects.get(name), bpy.data.materials.get(material_name)
            if obj is None or material is None:
                continue
            for slot in obj.material_slots:
                slot.material = material
        bpy.ops.ed.undo_push(message="Use baked material")  # i18n: undo history entry, named in English


def _poll(card, host):
    """While the card is open: follow its job and show its result when it ends."""
    from . import job_ui
    from .job_store import TERMINAL
    record = job_ui.manager().record(card.job) if card.job else None
    if record is None:
        return None
    card.record = record
    if record["status"] in TERMINAL:
        card.phase = "done" if record["status"] == "done" else "failed"
        card._version += 1
        host.redraw()
        return None
    card._version += 1
    host.redraw()
    return 0.5


class ANGELO_OT_bake_maps_note(Operator):
    """The texture-set job's own report after each bake: Blender's bake reports that its map was "saved to internal
    image, save it externally or pack it", which is about the job's scratch image, removed at once; the latest report
    is the one the top bar shows, so this one replaces it. Modal for one timer tick: Angelo shows a report only from
    an operator it runs itself (one that Python calls has its reports printed, never shown)."""
    bl_idname = "angelo.bake_maps_note"
    bl_label = "Bake Maps Progress"
    bl_options = {'INTERNAL'} | modal_options(bl_idname)   # Auto-save never waits for it (input_policy).

    done: bpy.props.IntProperty(options={'SKIP_SAVE'})
    total: bpy.props.IntProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        self._timer = context.window_manager.event_timer_add(0.01, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':   # Passes every event on: it only waits for its own tick.
            return {'PASS_THROUGH'}
        from .ui_text import rpt_
        context.window_manager.event_timer_remove(self._timer)
        self.report({'INFO'}, rpt_("Baking the texture set: {done} of {total} bakes done", done=self.done,
                                   total=self.total))
        return {'FINISHED'}


class ANGELO_OT_bake_maps(Operator):
    bl_idname = "angelo.bake_maps"
    bl_label = "Bake Maps"
    bl_description = ("Bake every PBR map the selected objects' materials use (base color, metallic, roughness, "
                      "normal, emission, alpha, AO, height) into a texture set, as a background job")

    def execute(self, context):
        from . import card_host
        if card_host.is_active("bake_maps"):
            return {'FINISHED'}
        card_host.request(BakeMapsCard(*selection(context)))
        card_host.pump(lambda: True)
        return {'FINISHED'}


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_bake_maps)
    bpy.utils.register_class(ANGELO_OT_bake_maps_note)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "bake_maps", n_("Bake maps…"), n_("Every PBR map of the selection as a texture set, packed for your engine"),
        glyph="bake_maps", order=24, section=chat_destinations.CREATE, operator="angelo.bake_maps",
        owner="bake_maps_card"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("bake_maps")
    bpy.utils.unregister_class(ANGELO_OT_bake_maps_note)
    bpy.utils.unregister_class(ANGELO_OT_bake_maps)
