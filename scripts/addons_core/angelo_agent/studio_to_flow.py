# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's step onward: **Turn into a flow** (1.0.5, ``doc/angelo/studio_entry_points.rst``).

Maintainer (2026-10-23): "Generation Flow is technically an advanced version of Media Studio so maybe add a way to
get there". Media Studio is where you start, Generation Flows where you go further (section 0); its one route there
is this pill at the right end of Studio's top row (section 5), and the flow bar's Media Studio pill is the way back.

A click builds an ordinary ``angelo.flow/1`` flow from Studio's setup as it is now, then opens it through the flow
opener (``flow_open.show``: Manual's Generation Flows tab, Director's flow window):

* Studio's tab picks the step: Image gives a Generate Image step, 3D a Generate 3D step followed by Place in scene
  (with a reference that is the Image to 3D template, filled in).
* The step carries Studio's account and model (the same catalog: ``selected``'s ``connection`` and ``id``), its
  prompt, and the settings that model accepts (``studio_agent.capabilities``; Studio's extra quality values map to
  the nearest one a flow stores). One output, no review: Studio asks for one.
* Each reference becomes an Image step wired to it (3D takes one): a link stays a link; an attached file is a
  session copy, so it is added to the Media Studio library first, as Import does, and the step points at that.
* The flow's bar says, until the flow changes, that steps can be added after it (``flow_window.hints``).

Nothing runs and nothing is billed: running is the flow's Run all, with its itemized approval. The definition is
built by the pure ``definition``; Blender is touched only to import references, write the tree and open it.
Studio draws the pill with ``draw_pill`` and sends its click to ``turn_into_flow`` (two lines in Studio's own file).
"""

import bpy
from bpy.types import Operator

from . import card_kit as kit, ui_tokens as tk
from .flow_starters import STUDIO_FLOW
from .ui_text import iface_, n_

ACTION = "to_flow"   # i18n: Studio hit action, never shown
LABEL = n_("Turn into a flow")
TIP = n_("Start a new flow from this Media Studio setup: its model, prompt and references")
HINT = n_("From Media Studio: add steps after it with Add step")
FLOW_NAME = STUDIO_FLOW   # The new flow's name: stored canonical, shown translated (flow_starters.flow_label).
QUALITY = {"xhigh": "high", "max": "high"}   # Studio offers more than a flow stores (studio_spec.OPTIONS).
PILL_H, PILL_PAD, PILL_ICON, PILL_GAP, TEXT = 28, 12, 16, 8, 12   # The shared pill (section 4).
GENERATOR = {"IMAGE": "GENERATE_IMAGE", "3D": "GENERATE_3D"}
STEP = {"IMAGE": "Image", "3D": "3D model"}   # i18n: canonical step names (the templates'), shown translated


def options_for(mode, model, setup):
    """The settings of ``setup`` (Studio's state) this provider mode and model accept, as a flow stores them."""
    from .studio_agent import capabilities
    controls = capabilities(mode, model)
    quality = setup.get("quality", "auto")
    raw = {"aspect": setup.get("aspect"), "resolution": setup.get("resolution"),
           "quality": QUALITY.get(quality, quality), "background": setup.get("background"),
           "size": setup.get("size") or None, "polycount": setup.get("polycount"), "texture": setup.get("texture")}
    options = {}
    for key, value in raw.items():
        if key not in controls or value in (None, ""):
            continue
        if key == "polycount":
            try:
                value = int(str(value).strip())
            except ValueError:
                continue
            limits = controls[key]
            value = max(limits["minimum"], min(limits["maximum"], value))
        elif key == "texture":
            value = bool(value)
        elif isinstance(controls[key], list) and value not in controls[key]:
            continue   # A value this model doesn't offer: the flow keeps its own default.
        options[key] = value
    return options


def definition(kind, chosen, prompt, options, references):
    """The ``angelo.flow/1`` definition of a Studio setup: ``kind`` IMAGE or 3D, ``chosen`` the catalog entry
    (``connection``, ``id``) or None, ``references`` [{kind: ASSET|URL, id}]. Canonical (``flow_spec.normalize``)."""
    from .flow_spec import FORMAT, normalize
    step = STEP[kind]
    fields = {"connection_id": (chosen or {}).get("connection", "") or "", "model": (chosen or {}).get("id", "") or "",
              "prompt": prompt or "", "options": options, "variants": 1, "review": False}
    nodes = [{"id": step, "type": GENERATOR[kind], "location": [-120, 0], "fields": fields}]
    links = []
    sources = references[:1] if kind == "3D" else references[:4]
    for index, source in enumerate(sources):
        name = "Reference" if index == 0 else "Reference.%03d" % index   # Blender's suffix: step_label keeps it.
        nodes.append({"id": name, "type": "IMAGE", "location": [-420, -260 * index], "fields": {"source": source}})
        links.append({"from": name, "to": step, "input": "reference" if kind == "3D" else "references"})
    if kind == "3D":
        place = "Place in scene"   # i18n: canonical step name (the templates'), shown translated
        nodes.append({"id": place, "type": "PLACE_IN_SCENE", "location": [180, 0]})
        links.append({"from": step, "to": place, "input": "model"})
    return normalize({"format": FORMAT, "nodes": nodes, "links": links})


def references_of(references, folder=""):
    """Studio's references as Image step sources: a public link as it is, an attached file added to the library
    (into Studio's current folder), as Import does."""
    from . import studio
    sources = []
    for reference in references:
        url = reference.get("url") or ""
        if url.startswith("https://"):
            sources.append({"kind": "URL", "id": url})
        elif reference.get("path"):
            asset = studio.library().import_image(reference["path"], folder)
            sources.append({"kind": "ASSET", "id": asset["id"]})
    return sources


def turn_into_flow(context, state):
    """Build the flow of Studio's ``state`` and open it in the mode's home for flows. Returns the tree."""
    from . import flow_nodes, flow_open, flow_tree, flow_window, studio
    kind = state.get("kind", "IMAGE")
    chosen = studio.selected(state)
    options = options_for((chosen or {}).get("mode", ""), (chosen or {}).get("id", ""), state)
    references = list(state.get("references") or ())[:1 if kind == "3D" else 4]   # What the step can take.
    folder = state.get("folder", "") if state.get("folder") not in (None, "ALL", "UNFILED") else ""
    # Studio's model even before an account is connected: the step keeps it and asks for the account (1.0.5 slot:
    # the flow lost the model Studio showed chosen).
    flow = definition(kind, chosen if chosen and chosen.get("id") else None, state.get("prompt", ""),
                      options, references_of(references, folder))
    tree = bpy.data.node_groups.new(FLOW_NAME, flow_nodes.TREE)
    flow_tree.flow_id(tree)
    flow_tree.write_flow(tree, flow)
    flow_window.hint(tree, HINT)
    window = context.window or next(iter(context.window_manager.windows), None)
    flow_open.show(window, tree)
    return tree


def pill_width(measure):
    return 2 * PILL_PAD + PILL_ICON + PILL_GAP + measure(iface_(LABEL), TEXT)


def draw_pill(state, width, height, canvas=None):
    """Studio's top-row pill, right-aligned on the tabs' row (section 4's shared pill: PANEL, a 1 px LINE border,
    PANEL_HOVER on hover, TEAL_SOFT pressed, the Flows glyph and the label at 12 px). Adds its hit to Studio's.

    Studio's hook (``studio.top_row_end``) passes the canvas it draws with (``canvas=canvas``); without one (an
    offscreen check) it draws with the chat canvas."""
    if canvas is None:
        from . import chat_view as canvas
    label = iface_(LABEL)
    w = pill_width(lambda text, size: canvas.measure(text, size))
    rect = (width - w - 14, height - 45 + (32 - PILL_H) / 2, w, PILL_H)
    x, y, w, h = rect
    hover = state.get("hover") == (ACTION, None)
    pressed = state.get("pressed") == (ACTION, None)
    canvas.shape(x, y, w, h, kit.LINE, h / 2)
    canvas.shape(x + 1, y + 1, w - 2, h - 2, tk.TEAL_SOFT if pressed else kit.PANEL_HOVER if hover else kit.PANEL,
                 h / 2 - 1)
    from . import canvas_rtl
    with canvas_rtl.upright(x + PILL_PAD, PILL_ICON) as gx:   # The Flows glyph never mirrors.
        draw_glyph(canvas, gx, y + (h - PILL_ICON) / 2, PILL_ICON, kit.TEXT)
    canvas.text(label, x + PILL_PAD + PILL_ICON + PILL_GAP, y + (h - TEXT * 0.72) / 2, TEXT, kit.TEXT)
    state["hits"].append((rect, ACTION, None))
    return rect


def draw_glyph(canvas, x, y, size, color):
    """The Generation Flows glyph (``flow_open.flow_icon``'s three linked nodes) in strokes, ``size`` px square."""
    u = size / 32
    nodes = ((8, 16), (24, 24), (24, 8))
    for bx, by in nodes[1:]:
        canvas.stroke(x + 8 * u, y + 16 * u, x + bx * u, y + by * u, color, max(1.2, 2.6 * u))
    for cx, cy in nodes:
        canvas.shape(x + (cx - 4.5) * u, y + (cy - 4.5) * u, 9 * u, 9 * u, color, 2 * u)


class ANGELO_OT_studio_turn_into_flow(Operator):
    bl_idname = "angelo.studio_turn_into_flow"
    bl_label = "Turn into a flow"
    bl_description = "Start a new flow from this Media Studio setup: its model, prompt and references"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return _studio_area(context) is not None

    def execute(self, context):
        from . import studio
        area = _studio_area(context)
        try:
            turn_into_flow(context, studio.view(area))
        except (ValueError, RuntimeError, OSError) as error:
            self.report({'ERROR'}, iface_(str(error)))
            return {'CANCELLED'}
        return {'FINISHED'}


def _studio_area(context):
    """The Media Studio this acts on: the one in the context, else the first one open."""
    from . import studio
    if studio.is_studio(context.area):
        return context.area
    for window in context.window_manager.windows:
        for area in window.screen.areas if window.screen else ():
            if studio.is_studio(area):
                return area
    return None


def register():
    bpy.utils.register_class(ANGELO_OT_studio_turn_into_flow)


def unregister():
    bpy.utils.unregister_class(ANGELO_OT_studio_turn_into_flow)
