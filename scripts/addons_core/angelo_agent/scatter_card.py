# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Scatter card (``card_host``): assets on the active mesh by a biome's rules, or the selected objects as one layer.

Contract: doc/angelo/scatter.rst, "The card". Opened by ``angelo.scatter`` from the chat bar's + menu and F3 (the
operator's EXEC path is the agent's). Drawn with ``card_kit``:

* **compose** (centred, modal): the surface (the active mesh when it opened); **Selected objects** (one layer of
  everything else selected) or **Biome** (a shipped preset). Each biome layer takes its objects from **Use
  Selection** or **Find in Library**: the layer's own Library words (its preset's queries) list up to four models,
  and **Add** imports one through the Library's own import (one Undo step, credits kept) into a collection hidden in
  the viewport and in renders, so only its scattered copies show. Scatter is enabled once every layer has objects;
  nothing is substituted for a layer the person has not filled.
* **done / failed** (corner): instances per layer, the layers that placed nothing, or the reason.

Everything Blender is imported inside the functions that need it, so the card's state and layout are host-tested.
"""

import json

from . import card_kit as kit
from . import scatter_spec
from .scatter_text import BIOME_LABELS, LAYER_LABELS
from .ui_text import iface_, n_

WIDTH, CORNER_WIDTH = 560, 380
RESULTS = 4   # Library models offered per layer.
TITLE = n_("Scatter on {surface}")
SUBTITLE = n_("Places copies of objects on the surface by slope, height and spacing, as one Geometry Nodes object. "
              "The surface and the objects stay as they are.")
NO_SURFACE = n_("Make a mesh active (the surface to scatter on), then open Scatter again.")
FROM_SELECTION, BIOME = n_("Selected objects"), n_("Biome")
SELECTED = n_("Scatters {objects} on {surface}.")
NO_SELECTION = n_("Select the objects to scatter as well as the surface, or choose a biome.")
DENSITY = n_("Per square metre")
NOTHING_YET = n_("Nothing chosen yet")
USE_SELECTION, FIND, ADD = n_("Use Selection"), n_("Find in Library"), n_("Add")
LOADING = n_("Loading the Library…")
NOT_FOUND = n_("The Library has no models for {words}. Select your own objects and use Use Selection.")
FETCHING = n_("Adding {title}…")
PICK_FIRST = n_("Select the objects for {layer} first (not the surface).")
SCATTER, CANCEL, CLOSE = n_("Scatter", "Angelo"), n_("Cancel"), n_("Close")   # As scatter_ui.TITLE.
DONE = n_("Scattered {count} instances")
EMPTY = n_("Nothing was placed for: {names}")
FAILED = n_("Nothing was scattered")
DENSITIES = (0.1, 0.5, 1.0, 4.0)
ASSET_COLLECTION_HIDDEN = True   # Library models added for a layer: hidden in the viewport and renders.
WARN, ERROR = "warn", "error"   # i18n: notice kinds (ui_kit_status), never shown


def presets():
    """``{stem: biome}`` of the shipped presets, sorted by stem."""
    return {path.name.split(".")[0]: scatter_spec.load_biome(path.read_text(encoding="utf-8"))
            for path in sorted(scatter_spec.PRESET_DIR.glob("*.angelo-biome.json"))}


def label(name):
    """A layer's name as shown: a preset layer's translated label, any other name (the person's own) as it is."""
    return iface_(LAYER_LABELS[name]) if name in LAYER_LABELS else name


def layer_words(layer):
    """The Library words of a preset layer: its assets' queries, in order."""
    return [asset["query"] for asset in layer["assets"] if "query" in asset]


class ScatterCard(kit.BaseCard):
    kind, priority, cancel_action, default_action, outside_dismisses = "scatter", 50, "close", None, False

    def __init__(self, surface, selected):
        self.phase, self.error = "compose", ""
        self.surface, self.selected = surface, [name for name in selected if name != surface]
        self.biomes = presets()
        self.mode = "selection" if self.selected else "biome"
        self.biome, self.biome_open = next(iter(self.biomes)), False
        self.density = 1.0
        self.assets = {}            # Biome layer name -> object names.
        self.found = None           # (layer name, [library items]) or (layer name, None) while the catalogue loads.
        self.fetching = None        # (layer name, item title) while an Add downloads.
        self.receipt, self._version = None, 0

    @property
    def modal(self):
        return self.phase == "compose"

    def version(self):
        return (self._version, self.phase, self.mode, self.biome, self.biome_open, repr(self.found), self.fetching)

    def layers(self):
        return self.biomes[self.biome]["layers"]

    def ready(self):
        if not self.surface:
            return False
        if self.mode == "selection":
            return bool(self.selected)
        return all(self.assets.get(layer["name"]) for layer in self.layers())

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

    def _compose(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=26)
        self._heading(b, "scatter", iface_(TITLE, surface=self.surface or "—"), iface_(SUBTITLE))
        if not self.surface:
            b.notice(WARN, iface_(NO_SURFACE))
            b.gap(18)
            b.buttons([("close", None, iface_(CLOSE), "primary", True)])
            b.close_button("close")
            return b
        b.segmented("mode", [("selection", iface_(FROM_SELECTION)), ("biome", iface_(BIOME))], self.mode)
        b.gap(12)
        if self.mode == "selection":
            if self.selected:
                b.paragraph(iface_(SELECTED, objects=", ".join(self.selected[:6]) + (" …" if len(self.selected) > 6
                                                                                     else ""),
                                   surface=self.surface), 13, kit.TEXT)
            else:
                b.notice(WARN, iface_(NO_SELECTION))
            b.gap(12)
            b.paragraph(iface_(DENSITY), 12.5, x=b.pad + 2 * b.s)
            b.gap(4)
            b.segmented("density", [("%g" % d, "%g" % d) for d in DENSITIES], "%g" % self.density)
        else:
            description = BIOME_LABELS[self.biome][1]
            b.select("biome_open", None, [(stem, iface_(BIOME_LABELS[stem][0])) for stem in self.biomes],
                     self.biome, open=self.biome_open, label=iface_(BIOME), option_action="biome")
            b.gap(6)
            b.paragraph(iface_(description), 12, kit.MUTED)
            b.gap(10)
            for layer in self.layers():
                self._layer(b, layer)
        if self.error:
            b.gap(10)
            b.notice(ERROR, self.error)
        b.gap(18)
        b.buttons([("close", None, iface_(CANCEL), "secondary", True),
                   ("scatter", None, iface_(SCATTER), "primary", self.ready() and self.fetching is None)])
        b.close_button("close")
        return b

    def _layer(self, b, layer):
        name = layer["name"]
        chosen = self.assets.get(name)
        b.paragraph(label(name), 13.5, kit.TEXT)
        b.paragraph(", ".join(chosen[:4]) + (" …" if len(chosen) > 4 else "") if chosen else iface_(NOTHING_YET),
                    12, kit.MUTED)
        b.gap(4)
        busy = self.fetching is not None
        b.buttons([("use_selection", name, iface_(USE_SELECTION), "secondary", not busy),
                   ("find", name, iface_(FIND), "ghost", not busy)], align="left", height=30, size=12.5)
        if self.fetching and self.fetching[0] == name:
            b.gap(4)
            b.paragraph(iface_(FETCHING, title=self.fetching[1]), 12, kit.MUTED)
        elif self.found and self.found[0] == name:
            b.gap(4)
            items = self.found[1]
            if items is None:
                b.paragraph(iface_(LOADING), 12, kit.MUTED)
            elif not items:
                b.notice(WARN, iface_(NOT_FOUND, words=", ".join(layer_words(layer))))
            for item in items or ():
                b.list_row("add", item["id"], item["title"], item.get("source", ""), trailing=iface_(ADD))
        b.gap(10)

    def _result(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(CORNER_WIDTH * scale, region_w - 40 * scale), ui, padding=22)
        receipt = self.receipt or {}
        if self.phase == "done":
            lines = ["%s: %d" % (label(layer["name"]), layer["instances"]) for layer in receipt.get("layers", ())]
            self._heading(b, "check", iface_(DONE, count=receipt.get("instances", 0)), "\n".join(lines))
            empty = [label(layer["name"]) for layer in receipt.get("layers", ()) if not layer["instances"]]
            if empty:
                b.notice(WARN, iface_(EMPTY, names=", ".join(empty)))
                b.gap(10)
        else:
            self._heading(b, "alert", iface_(FAILED), self.error)
        b.buttons([("close", None, iface_(CLOSE), "primary", True)])
        b.close_button("close")
        return b

    # -- input ---------------------------------------------------------------------------------------------
    def key_event(self, event, host):
        if self.biome_open and event.type == 'ESC':
            self.biome_open = False
            return True
        return False

    def act(self, action, value, host):
        if action == "mode":
            self.mode, self.error = value, ""
        elif action == "density":
            self.density = float(value)
        elif action == "biome_open":
            self.biome_open = not self.biome_open
        elif action == "biome":
            if value != self.biome:
                self.biome, self.assets, self.found = value, {}, None
            self.biome_open = False
        elif action == "use_selection":
            self._use_selection(value)
        elif action == "find":
            self._find(value, host)
        elif action == "add":
            self._add(value, host)
        elif action == "scatter":
            self._scatter(host)
        elif action in {"close", "dismiss"}:
            host.close("closed")
            return
        self._version += 1
        host.redraw()

    def request(self):
        """The ``angelo.scatter`` properties the card's choices make (its EXEC path, as the agent's)."""
        if self.mode == "selection":
            from . import scatter_ui
            return {"layers": json.dumps(scatter_ui.selection_layers(self.selected, self.density))}
        return {"biome": self.biome, "biome_assets": json.dumps({layer["name"]: list(self.assets[layer["name"]])
                                                                for layer in self.layers()})}

    def _use_selection(self, layer_name):
        import bpy
        names = [obj.name for obj in bpy.context.selected_objects
                 if obj.name != self.surface and obj.type in {'MESH', 'CURVES', 'EMPTY'}]
        if not names:
            self.error = iface_(PICK_FIRST, layer=label(layer_name))
            return
        self.assets[layer_name], self.error, self.found = names[:scatter_spec.MAX_ASSETS], "", None

    def _find(self, layer_name, host):
        from . import edit_tool_hold, library_host
        library = library_host.library()
        if library.catalog is None:
            library.ensure_catalog()
            self.found = (layer_name, None)

            def again():
                if library.catalog is None and library.status != "offline":
                    return 0.3
                self._find(layer_name, host)
                self._version += 1
                host.redraw()
                return None
            host.after(0.3, edit_tool_hold.guarded(again))   # Waits out the person's own operation.
            return
        preset = next(item for item in self.layers() if item["name"] == layer_name)
        items, seen = [], set()
        for words in layer_words(preset):
            found, _total = library.search(words, "model", limit=RESULTS)
            for item in found:
                if item["id"] not in seen and len(items) < RESULTS:
                    seen.add(item["id"])
                    items.append(item)
        self.found = (layer_name, items)

    def _add(self, item_id, host):
        from . import library_host
        if self.found is None or self.fetching is not None:
            return
        layer_name = self.found[0]
        library = library_host.library()
        item = library.get(item_id)
        if item is None:
            return
        self.fetching = (layer_name, item["title"])

        def done(facts, error):
            self.fetching = None
            if error is not None:
                self.error = library_host.describe(error)[0]
            else:
                try:
                    self.assets[layer_name] = import_for_layer(host.window(), item, facts)
                    self.found = None
                except Exception as failure:   # The import's own sentence (library_import.ImportFailed).
                    self.error = str(failure)[:300]
            self._version += 1
            host.redraw()
        library.fetch(item, done=done)

    def _scatter(self, host):
        import bpy
        from . import scatter_ui
        if not self.ready():
            return
        surface = bpy.data.objects.get(self.surface)
        window = host.window()
        try:
            if surface is None or surface.type != 'MESH':
                raise scatter_spec.ScatterError(iface_(NO_SURFACE), code="NOT_FOUND")
            with bpy.context.temp_override(window=window, active_object=surface, object=surface):
                self.receipt = scatter_ui.apply_request(bpy.context, surface, self.request())
            bpy.ops.ed.undo_push(message="Scatter")  # i18n: undo history entry, named in English
            self.phase, self.error = "done", ""
        except scatter_spec.ScatterError as error:
            self.error = str(error)   # The operator's own refusal (English, as the agent reads it), with the fix.


def import_for_layer(window, item, facts):
    """Import a Library model for a scatter layer: the Library's own import (one Undo step, provenance), its
    collection then hidden in the viewport and excluded from renders, so only the scattered copies show."""
    import bpy
    from . import library_import
    with bpy.context.temp_override(window=window):   # The fetch's lease lasts until this returns (library_host).
        result = library_import.import_item(bpy.context, item, facts)
    collection = bpy.data.collections.get(result["collection"])
    if collection is not None and ASSET_COLLECTION_HIDDEN:
        collection.hide_render = True
        for layer in _layer_collections(bpy.context.view_layer.layer_collection, collection):
            layer.hide_viewport = True   # The eye: hidden objects are still evaluated, so scattering reads them.
    return [entry["name"] for entry in result["objects"] if entry["type"] == 'MESH'][:scatter_spec.MAX_ASSETS]


def _layer_collections(root, collection):
    if root.collection == collection:
        yield root
    for child in root.children:
        yield from _layer_collections(child, collection)
