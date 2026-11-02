# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The render engine switcher: EEVEE, Cycles (Preview or Final, on the GPU), every other installed engine and AI
render (1.0.8).

Contract: doc/angelo/render_engines.rst. One list of choices (``choices``) feeds every surface: Director's top-bar
button and its dropdown (``flow_menu``'s ``ENGINES`` kind), the Render menu's Render Engine submenu, and the
operators behind them. A choice is applied by ``angelo.render_engine_set`` with an Undo step.

* **Cycles is ready on first use.** Choosing Cycles here sets the scene's Cycles device to the GPU when Angelo has
  set a GPU up (``angelo_render_device``: the device listing every windowed start runs), so nobody renders on the
  CPU by accident; with no GPU the choice still applies and the row says why Cycles will use the CPU. The two
  presets are sample, noise and denoise settings (``PRESETS``); the active one is read back from the scene's own
  values, so a file saved with one shows it, and hand-tuned settings show as Cycles with no preset ticked.
* **Installed engines**: Workbench and every engine an add-on registered (V-Ray, LuxCore, Octane's add-on...) follow,
  named as their add-on names them, with the add-on's module, from ``bpy.types.RenderEngine`` at the moment the
  dropdown opens, so an add-on enabled or removed mid-session shows at once.
* **AI render** is an action, not an engine: the row opens the AI render card (``ai_render``), which restyles the
  camera's view and leaves the scene's engine as it is.

Nothing here runs at startup or in a draw beyond reading the scene; settings change only on a click.
"""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Menu, Operator

from .ui_text import iface_, n_, rpt_

EEVEE, CYCLES, WORKBENCH = "BLENDER_EEVEE", "CYCLES", "BLENDER_WORKBENCH"
PREVIEW, FINAL = "PREVIEW", "FINAL"
# Cycles presets: what a person tuning Cycles by hand would set for a quick look and for a deliverable. Adaptive
# sampling stops a pixel once its noise is under the threshold; OpenImageDenoise runs on every GPU backend and the
# CPU, so the preset never names a denoiser a machine lacks (OptiX's is NVIDIA-only).
PRESETS = {
    PREVIEW: {"samples": 128, "adaptive_threshold": 0.05, "preview_samples": 32},
    FINAL: {"samples": 1024, "adaptive_threshold": 0.01, "preview_samples": 64},
}
COMMON = {"use_adaptive_sampling": True, "use_denoising": True, "denoiser": "OPENIMAGEDENOISE",
          "use_preview_denoising": True, "time_limit": 0.0}
# An engine's name is a product name: shown as its engine registers it (``bl_label``), never translated anywhere (a
# catalogue maps "Cycles" to its F-curve modifier sense: "Döngüler", "循環"). These stand in only where the build
# lacks the engine's class. doc/angelo/render_engines.rst, "Engine names".
NAMES = {EEVEE: "EEVEE", CYCLES: "Cycles", WORKBENCH: "Workbench"}   # i18n: engine names, never translated
PRESET_LABELS = {PREVIEW: n_("Preview", "Angelo"), FINAL: n_("Final", "Angelo")}
WHY = {EEVEE: n_("Real-time render: fast looks and quick turnarounds"),
       PREVIEW: n_("Path tracing, {samples} samples, denoised: a quick, clean look"),
       FINAL: n_("Path tracing, {samples} samples, denoised: for the deliverable"),
       WORKBENCH: n_("Flat or studio shading for checking layout and timing")}
SHIPPED_WHY = n_("Built in, no add-on needed")
ADDON_WHY = n_("Installed add-on ({module}): its own settings and materials. Angelo's agent asks before using it")
WITH_DEVICE = n_("{why}. {device}")   # A Cycles row's why line and where it renders.
MENU_LABEL = n_("Render Engine")
MENU_WHY = n_("F12, Render Animation and render jobs use the engine you choose")
AI_LABEL = n_("AI Render…")
AI_WHY = n_("Restyle the camera's view with Nano Banana or Seedream; the result goes to Media Studio")


def has_cycles(scene):
    return getattr(scene, "cycles", None) is not None and CYCLES in engine_ids()


def engine_classes():
    """Every registered ``RenderEngine`` class, an add-on's subclass of its own base included."""
    found, pending = [], list(bpy.types.RenderEngine.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls in found:
            continue
        pending.extend(cls.__subclasses__())
        if getattr(cls, "bl_idname", "") and getattr(cls, "is_registered", True):
            found.append(cls)
    return found


def engine_ids():
    """The render engines registered now: Blender's own and every add-on's."""
    return {EEVEE, WORKBENCH} | {cls.bl_idname for cls in engine_classes()}


def preset_of(scene):
    """PREVIEW or FINAL when the scene's Cycles settings are exactly that preset's, else None (tuned by hand)."""
    cycles = getattr(scene, "cycles", None)
    if cycles is None:
        return None
    for name, values in PRESETS.items():
        settings = dict(COMMON, **values)
        if all(_same(getattr(cycles, key, None), value) for key, value in settings.items()
               if key not in {"preview_samples", "use_preview_denoising", "time_limit"}):
            return name
    return None


def _same(found, wanted):
    return abs(found - wanted) < 1e-6 if isinstance(wanted, float) and isinstance(found, float) else found == wanted


def device_line(scene=None):
    """``(text, warning)``: where a Cycles render of ``scene`` runs, as the job card words it (None off Cycles);
    with no scene, where one on Angelo's GPU setting would (what choosing Cycles here gives)."""
    from types import SimpleNamespace
    from . import job_chat, render_device
    if scene is None:
        scene = SimpleNamespace(render=SimpleNamespace(engine=CYCLES), cycles=SimpleNamespace(device="GPU"))
    try:
        shown = render_device.shown(scene)
    except Exception:   # Cycles' preferences unreadable (disabled mid-session): say nothing rather than guess.
        return None
    if shown is None:
        return None
    text, colour = job_chat.device_text({"detail": {"device_shown": shown}})
    return text, not shown.get("gpu")


def current_label(scene):
    """The switcher button's label: the engine, and Cycles' preset when one is set."""
    engine, preset = scene.render.engine, preset_of(scene)
    if engine == CYCLES and preset:
        return cycles_label(preset)
    return engine_name(engine)


def engine_name(identifier):
    """An engine's own name, as it registers it (``bl_label``), never translated."""
    for cls in engine_classes():
        if cls.bl_idname == identifier:
            return getattr(cls, "bl_label", "") or NAMES.get(identifier, identifier)
    return NAMES.get(identifier, identifier)


def cycles_label(preset):
    """"Cycles · Preview": the engine's own name, its preset in the interface language."""
    return iface_("{engine} · {preset}", engine=engine_name(CYCLES), preset=iface_(PRESET_LABELS[preset], "Angelo"))


def every_label():
    """Every label the button can show, so it keeps one width whichever engine is chosen (``top_bar_fit``)."""
    return ([engine_name(EEVEE), engine_name(CYCLES)] + [cycles_label(name) for name in PRESET_LABELS]
            + [name for _identifier, name, _addon in others()])


def choices(scene):
    """The switcher's choices, in order: ``[{value, label, subtitle, glyph, selected, operator, properties}]``.
    A label is an engine's own name or already translated, so ``translate`` is False on every engine row (an
    ``n_`` constant otherwise); a subtitle is translated where drawn (``chat_add_menu._shown``)."""
    engine, preset = scene.render.engine, preset_of(scene)
    rows = [{"value": "engine:" + EEVEE, "label": engine_name(EEVEE), "translate": False, "subtitle": WHY[EEVEE],
             "glyph": "eevee",
             "selected": engine == EEVEE, "operator": "angelo.render_engine_set",
             "properties": (("engine", EEVEE), ("preset", "NONE"))}]
    if has_cycles(scene):
        line = device_line()
        for name in (PREVIEW, FINAL):
            rows.append({"value": "engine:%s:%s" % (CYCLES, name), "label": cycles_label(name), "translate": False,
                         "subtitle": _why(name, line), "glyph": "cycles",
                         "selected": engine == CYCLES and preset == name, "operator": "angelo.render_engine_set",
                         "properties": (("engine", CYCLES), ("preset", name))})
    from . import engine_setup, engine_status
    for identifier, name, addon in others():   # Workbench, then every engine an add-on installed (V-Ray, LuxCore...).
        note = engine_status.row_note(addon) if addon else None   # Its vendor's service or sign-in, when missing.
        rows.append({"value": "engine:" + identifier, "label": name, "translate": False, "subtitle": note or ((ADDON_WHY, {"module": addon}) if addon else WHY.get(
                         identifier, SHIPPED_WHY)),
                     "glyph": "engine", "selected": engine == identifier, "operator": "angelo.render_engine_set",
                     "properties": (("engine", identifier), ("preset", "NONE"))})
    for found in _found(engine_setup):   # Installed but not on yet: choosing one sets it up (engine_setup).
        rows.append({"value": "setup:" + found["module"], "label": found["name"], "translate": False,
                     "subtitle": engine_status.row_note(found["module"], on=False) or engine_setup.SETUP_WHY,
                     "glyph": "engine", "selected": False,
                     "operator": "angelo.render_engine_setup", "properties": (("module", found["module"]),)})
    return rows


def _found(engine_setup):
    try:
        return engine_setup.found()
    except Exception as error:   # A folder that cannot be read lists nothing; the engines that are on still show.
        print("Angelo: render engines: installed add-ons:", error)
        return []


def others():
    """``[(identifier, name, add-on module or "")]``: every registered engine besides EEVEE and Cycles: Blender's
    own (Workbench first, then any other shipped one, such as Hydra Storm when enabled), then every engine an add-on
    installed, by name (``render_extensions.addon_engines``: code outside the shipped scripts)."""
    from . import render_extensions
    installed = render_extensions.addon_engines()
    found = [(WORKBENCH, engine_name(WORKBENCH), "")] if WORKBENCH in engine_ids() else []
    shipped, by_name = [], []
    for cls in engine_classes():
        identifier = cls.bl_idname
        if identifier in {EEVEE, CYCLES, WORKBENCH}:
            continue
        name = getattr(cls, "bl_label", "") or identifier
        if identifier in installed:
            by_name.append((identifier, name, cls.__module__.split(".")[0]))
        else:
            shipped.append((identifier, name, ""))
    order = lambda item: item[1].lower()
    return found + sorted(set(shipped), key=order) + sorted(set(by_name), key=order)


def _why(preset, line):
    """A Cycles preset's why line with where it renders: (template, already translated fields)."""
    why = iface_(WHY[preset], samples=PRESETS[preset]["samples"])
    return (WITH_DEVICE, {"why": why, "device": line[0]}) if line else why


def menu_rows(window):
    """The dropdown's rows (``flow_menu`` ``ENGINES``): a header, the engines, then AI render under its own label."""
    scene = window.scene
    rows = [{"kind": "header", "label": MENU_LABEL, "subtitle": MENU_WHY, "glyph": "engine",
             "value": "engines:header"}]   # i18n: row id, never shown
    rows += [dict(row, kind="row") for row in choices(scene)]
    rows.append({"kind": "section", "label": n_("AI")})
    rows.append({"kind": "row", "value": "ai_render", "label": AI_LABEL, "subtitle": AI_WHY, "glyph": "ai_render",
                 "operator": "angelo.ai_render", "properties": ()})
    return rows


def apply(scene, engine, preset=None, preferences=None):
    """Set ``scene``'s engine; for Cycles also ``preset`` (None: Preview unless the scene already has a preset) and,
    when switching to Cycles from another engine, the GPU when one is set up. Returns ``(engine, preset or None,
    device line or None)``; the line says where Cycles renders now."""
    if engine not in engine_ids():
        raise ValueError(rpt_("The {engine} render engine is not available in this build", engine=engine))
    switching = scene.render.engine != engine
    scene.render.engine = engine
    if engine != CYCLES:
        return engine, None, None
    preset = preset or preset_of(scene) or PREVIEW
    cycles = scene.cycles
    for key, value in dict(COMMON, **PRESETS[preset]).items():
        if hasattr(cycles, key):
            setattr(cycles, key, value)
    if hasattr(cycles, "denoising_use_gpu"):
        cycles.denoising_use_gpu = True   # Denoise where it renders; Cycles falls back to the CPU by itself.
    from . import render_device
    try:
        render_device.setup(preferences)   # Decided at startup already; a first launch still listing keeps going.
    except Exception as error:
        print("Angelo: render engines: GPU setup:", error)
    found = render_device.facts(scene, preferences) or {}
    if switching and cycles.device != "GPU" and found.get("gpu_available"):
        # Choosing Cycles here never renders on the CPU by accident. A scene already on Cycles keeps its device when
        # only the preset changes: a CPU chosen there (memory, OSL) is the person's.
        cycles.device = "GPU"
    return engine, preset, device_line(scene)


class ANGELO_OT_render_engine_set(Operator):
    """Render with this engine; Cycles' Preview and Final set its samples and denoising and use the GPU"""
    bl_idname = "angelo.render_engine_set"
    bl_label = "Set Render Engine"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    engine: EnumProperty(items=lambda self, context: _engine_items(), options={'SKIP_SAVE'})
    preset: EnumProperty(items=(("NONE", "", ""), (PREVIEW, "", ""), (FINAL, "", "")), default="NONE",
                         options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            engine, preset, line = apply(context.scene, self.engine, None if self.preset == "NONE" else self.preset)
        except (ValueError, TypeError, AttributeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        name = current_label(context.scene)
        from . import engine_status
        engine_status.after_choice(context.scene)   # An installed engine not signed in yet: its vendor's sign-in.
        self.report({'WARNING'} if line and line[1] else {'INFO'},
                    rpt_("Rendering with {engine}. {device}", engine=name, device=line[0]) if line else
                    rpt_("Rendering with {engine}", engine=name))
        _redraw()
        return {'FINISHED'}


class ANGELO_OT_render_engine_setup(Operator):
    """Turn this installed render engine on in Angelo and render with it"""
    bl_idname = "angelo.render_engine_setup"
    bl_label = "Set Up Render Engine"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    module: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import engine_setup, engine_status
        # Only an add-on the switcher lists, found again now: never a folder named by the caller.
        found = next((row for row in engine_setup.found() if row["module"] == self.module), None)
        if found is None:
            self.report({'ERROR'}, rpt_(NOT_FOUND, module=self.module))
            return {'CANCELLED'}
        if engine_status.hold_set_up(found):   # Its vendor's sign-in service first: Angelo's card, not theirs.
            return {'FINISHED'}
        try:
            added = engine_setup.set_up(found["module"], found["name"], found["source"])
        except engine_setup.SetupError as error:
            self.report({'ERROR'}, rpt_(error.template, **error.fields))
            return {'CANCELLED'}
        apply(context.scene, added[0])
        self.report({'INFO'}, rpt_("Rendering with {engine}", engine=current_label(context.scene)))
        engine_status.after_set_up(context.window, found["module"], current_label(context.scene))
        _redraw()
        return {'FINISHED'}


NOT_FOUND = n_("The add-on {module} is no longer installed")


_items = []   # Kept alive: Blender reads dynamic enum strings after the callback returns.


def _engine_items():
    _items[:] = [(identifier, identifier, "") for identifier in sorted(engine_ids())]
    return _items


class ANGELO_OT_render_engines(Operator):
    # The top-bar button: open the render engine dropdown under it (flow_menu's ENGINES kind). A comment, not a
    # docstring: bl_description is the tooltip.
    bl_idname = "angelo.render_engines"
    bl_label = "Render Engine"
    bl_description = ("Choose EEVEE or Cycles (Preview or Final, on the GPU), or restyle the camera's view with AI "
                      "render")

    def invoke(self, context, event):
        from . import flow_menu
        # The dropdown's own operator stays modal; this one ends now (returning its RUNNING_MODAL would leave this
        # operator registered as a modal handler with no modal method, alive until quit).
        result = bpy.ops.angelo.flow_menu('INVOKE_DEFAULT', menu=flow_menu.ENGINES)
        return {'CANCELLED'} if 'CANCELLED' in result else {'FINISHED'}

    def execute(self, context):
        return self.invoke(context, None)


class ANGELO_MT_render_engine(Menu):
    # Render > Render Engine: the same choices as the top bar's dropdown, in both modes. A comment, not a docstring:
    # a menu's docstring is its tooltip.
    bl_label = "Render Engine"

    def draw(self, context):
        for row in choices(context.scene):
            text = row["label"] if row.get("translate") is False else iface_(row["label"])
            op = self.layout.operator(row["operator"], text=text, translate=False,
                                      icon='RADIOBUT_ON' if row["selected"] else 'RADIOBUT_OFF')
            for key, value in row["properties"]:
                setattr(op, key, value)


def render_menu(self, context):
    """The top of Blender's Render menu: the engine and AI render, before Render Image."""
    self.layout.menu("ANGELO_MT_render_engine", text=iface_(MENU_LABEL), translate=False, icon='SCENE')
    self.layout.operator("angelo.ai_render", text=iface_(AI_LABEL), translate=False, icon='SHADERFX')
    self.layout.separator()


def draw_top_bar(layout, context, compact=False):
    """Director's engine button, before the mode menu (``director.draw_topbar``), as wide as its widest label so it
    never changes width on a switch; pressed while its dropdown is open."""
    from . import flow_menu, glyph_tint, render_glyphs, top_bar_fit
    label = "" if compact else current_label(context.scene)
    slot = top_bar_fit.fitted_row(layout, context, "engine", label, also=() if compact else every_label())
    # The slot keeps the widest label's width, so the bar never moves on a switch; the button hugs the shown label at
    # the slot's left, its glyph beside it (as the mode menu: a slot-wide button centred a short label far from it).
    slot.alignment = 'LEFT'
    row = slot.row()
    row.ui_units_x = top_bar_fit.label_units(context, (label,))[0]
    pressed = flow_menu.is_open(context.window, flow_menu.ENGINES)
    ink = glyph_tint.colour("tool", pressed)
    row.operator("angelo.render_engines", text=label, translate=False, emboss=True, depress=pressed,
                 icon_value=render_glyphs.icon_id(glyph_of(context.scene), ink))


def glyph_of(scene):
    engine = scene.render.engine
    return "eevee" if engine == EEVEE else "cycles" if engine == CYCLES else "engine"


def _redraw():
    from . import chat_toggle
    chat_toggle.redraw_top_bars()


CLASSES = (ANGELO_OT_render_engine_set, ANGELO_OT_render_engine_setup, ANGELO_OT_render_engines,
           ANGELO_MT_render_engine)


def _menu_rows(chosen, fit_target):
    """The chat bar's + menu group: the same choices as the top bar's dropdown, for the window's scene."""
    from . import chat_destinations as cd
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return []
    prefix = "render_engine:"   # i18n: row id, never shown
    return [cd.leaf("render_engine", prefix + row["value"], row["label"], row["subtitle"], row["glyph"],
                    row["operator"], row["properties"], translate=row.get("translate", True))
            for row in choices(scene)]


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_render.prepend(render_menu)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "render_engine", MENU_LABEL, MENU_WHY, glyph="engine", order=21, section=chat_destinations.CREATE,
        children=_menu_rows, owner="render_engines"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("render_engine")
    bpy.types.TOPBAR_MT_render.remove(render_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    from . import render_glyphs
    render_glyphs.release()
