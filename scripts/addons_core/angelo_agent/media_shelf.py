# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An Asset Shelf in the 3D view's Object mode for models and materials, Media Studio's included (1.0.5).

Evidence: the maintainer (2026-10-23) wants assets generated in Media Studio reachable from the Asset Shelf. Stock
Blender 5.2's 3D-view shelves are brush shelves (sculpt, paint, Grease Pencil), so a generated model or material
could appear in none of them.

* **What it lists**: Object, Collection and Material assets from every asset library (the shelf's library menu
  narrows it), so the *Angelo Media Studio* library's Models and Images sit beside the person's own. The shelf's
  catalog popover adds a catalog as a tab; Blender keeps the enabled tabs in the Preferences
  (``STORE_ENABLED_CATALOGS_IN_PREFERENCES``), so a tab once added stays for every file.
* **Drag and drop**: Blender's own 3D-view drop boxes take an asset dragged from a shelf: a model is added where it
  is dropped, a collection is instanced, and a material dropped on an object is assigned to it. Nothing here
  replaces them.
* **The Angelo Media Studio tab**: Part E's fixed, untranslated parent catalog (``studio_assets.TAB_PATH``, over
  its Models and Images) is added as a tab once per user through ``AssetShelf.enable_catalog`` (an Angelo RNA
  function), never again if the person removes it (``asset-shelf.json``, ``tab_offered``). A library from before
  that parent offers its two catalogs instead, and a catalog renamed into another language moves its kept tab
  (``tabs_to_offer``). ``show_studio_tab(window)`` shows the shelf on that tab (the welcome's "Show my assets").
* **Media Studio and Generation Flows**: the shelf's pills (``asset_shelf_add``). Media Studio opens on its Image tab
  while the shelf shows the Images catalog (``shelf_kind``: ``AssetShelf.active_catalog_path``), else on its 3D
  tab.
* **Collapsed by default, the person's choice kept**: the first time Object mode shows it, the shelf shows only
  its tab strip (``DEFAULT_COLLAPSED``, Angelo's ``RGN_FLAG_COLLAPSED``: the rows region hidden, its header kept),
  so the viewport is not crowded; the pills' chevron (``asset_shelf_add``) or picking a tab expands it to one row of
  small previews. Hidden, collapsed or expanded (the chevron, View > Asset Shelf, the region's edge) is remembered
  per user (``asset-shelf.json`` in the state folder, ``STATE_KEY``) and applied to every Object-mode 3D view of any
  file. The 3D views' draws sample the shelf's visibility (``_sample``, no timer); a change counts as the
  person's only within one Object-mode view of a Manual layout, never across a mode switch or in Director (which
  hides every shelf itself, ``director_layout.configure``).
"""

import bpy
from bpy.app.handlers import persistent

FILE = "asset-shelf.json"
KEY = "object_shelf_shown"   # Before collapsing: True or False (read as expanded or hidden).
STATE_KEY = "object_shelf_state"
STATES = ("hidden", "collapsed", "expanded")
OFFERED = "tab_offered"
MODES = frozenset({'OBJECT'})
ASSET_TYPES = frozenset({'OBJECT', 'COLLECTION', 'MATERIAL'})
PREVIEW_SIZE = 48   # Small previews: one compact row by default.
SETTLE = 0.75       # Seconds in Object mode before the shelf's visibility is the person's (see change).

_seen = {}         # area pointer -> (shelf shown at its last Object-mode draw in a Manual layout, entered at).
_applied = set()   # Area pointers the remembered choice was applied to (once each per session and file).
_pending = None    # The choice waiting to be written (a timer writes it after the draw).
_settling = set()  # Area pointers with a redraw due when they settle.
_offering = {}     # Area pointer -> True once offered, or when the last try waited (library or catalogs not yet there).
RETRY = 10.0       # Seconds between tries while the library has no catalogs yet (Part E syncs ~20 s after startup).
_handlers = []


def _options():
    """The shelf's options; ``DEFAULT_COLLAPSED`` on a binary that has it (Angelo's 1.0.5 build)."""
    options = {'DEFAULT_VISIBLE', 'STORE_ENABLED_CATALOGS_IN_PREFERENCES'}
    try:
        if 'DEFAULT_COLLAPSED' in bpy.types.AssetShelf.bl_rna.properties["bl_options"].enum_items.keys():
            options.add('DEFAULT_COLLAPSED')
    except (AttributeError, KeyError, TypeError):
        pass
    return options


class ANGELO_AST_object_assets(bpy.types.AssetShelf):
    bl_space_type = 'VIEW_3D'
    bl_options = _options()
    bl_default_preview_size = PREVIEW_SIZE

    @classmethod
    def poll(cls, context):
        return context.mode in MODES

    @classmethod
    def asset_poll(cls, asset):
        return asset.id_type in ASSET_TYPES

    @classmethod
    def draw_context_menu(cls, context, asset, layout):
        from . import media_menus   # Media Studio's drag actions as menu items (Part E).
        media_menus.draw_shelf_menu(context, asset, layout)


# -- the person's choice ---------------------------------------------------------------------------------------
def _path():
    from pathlib import Path
    from .ui import state_directory
    return Path(state_directory()) / FILE


def _stored():
    from .studio_state import read_json
    stored = read_json(_path(), {})
    return dict(stored) if isinstance(stored, dict) else {}


def _store(key, value):
    from .studio_state import atomic_json
    stored = _stored()
    stored[key] = value
    try:
        atomic_json(_path(), stored)
    except OSError:
        pass   # Not remembered: Blender's default next time, never an error.


_catalogs = {}   # (catalogue file, its mtime) -> {"3d": path, "image": path}, read once per change.


def studio_catalogs():
    """``{"3d": path, "image": path}``: the Angelo Media Studio library's Models and Images catalogs as named now.
    Their paths follow the interface language (Part E rewrites the catalogue file), their ids never change
    (``studio_assets.CATALOGS``), so the paths are read by id. Empty before the library exists."""
    try:
        from . import studio_assets, studio_state
        cats = studio_assets.asset_directory(studio_state.library_directory()) / studio_assets.CATS_FILE
        stamp = cats.stat().st_mtime_ns
    except (ImportError, OSError, ValueError, AttributeError):
        return {}
    key = (str(cats), stamp)
    if key not in _catalogs:
        kinds = {identity: kind.lower() for kind, (identity, *_rest) in studio_assets.CATALOGS.items()}
        found = {}
        try:
            lines = cats.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for line in lines:
            parts = line.split(":", 2)
            if len(parts) == 3 and not line.startswith("#") and parts[0] in kinds and parts[1]:
                found[kinds[parts[0]]] = parts[1]
        _catalogs.clear()
        _catalogs[key] = found
    return dict(_catalogs[key])


def studio_tab_path():
    """Part E's fixed, untranslated parent catalog ("Angelo Media Studio", ``studio_assets.TAB_PATH``), or None with
    a library whose two catalogs are top-level (before that parent existed)."""
    try:
        from . import studio_assets
    except ImportError:
        return None
    return getattr(studio_assets, "TAB_PATH", None)


def tab_catalogs():
    """What the shelf offers as tabs, by a stable key: the one Angelo Media Studio parent when the library has it,
    else its Models and Images catalogs."""
    parent = studio_tab_path()
    if parent:
        return {"studio": parent} if studio_catalogs() or library_name() else {}
    return studio_catalogs()


def catalog_kind(path, catalogs=None):
    """"3d" or "image" for a catalog path at or under the library's Models or Images catalog, else None (the parent
    tab, the All tab, another library's catalog). Part E's own ``studio_assets.catalog_kind`` when it has one; pure
    with ``catalogs``."""
    if not path:
        return None
    if catalogs is None:
        try:
            from . import studio_assets
            if hasattr(studio_assets, "catalog_kind"):
                return studio_assets.catalog_kind(path)
        except ImportError:
            pass
    for kind, root in (studio_catalogs() if catalogs is None else catalogs).items():
        if path == root or path.startswith(root + "/"):
            return kind
    return None


def tabs_to_offer(catalogs, offered, enabled):
    """What to do with the shelf's tabs: ``(enable, disable, offered)``. ``offered`` is what was offered before
    (kind -> path, or None never). Each catalog is offered once; a catalog renamed since (another interface language)
    moves its tab if the person kept it, and stays away if they removed it. Pure."""
    enable, disable, now = [], [], dict(offered or {})
    for kind, path in sorted(catalogs.items()):
        before = (offered or {}).get(kind)
        if before is None:
            enable.append(path)
        elif before != path:
            if before in enabled:
                disable.append(before)
                enable.append(path)
        now[kind] = path
    return enable, disable, now


def _shelf(window, area):
    """This view's shelf instance (``context.asset_shelf``) when it is Angelo's, else None."""
    region = next((r for r in area.regions if r.type == 'ASSET_SHELF'), None)
    if region is None:
        return None
    with bpy.context.temp_override(window=window, area=area, region=region):
        shelf = getattr(bpy.context, "asset_shelf", None)
    return shelf if shelf is not None and getattr(shelf, "bl_idname", "") == ANGELO_AST_object_assets.__name__ \
        else None


def shelf_kind(window, area):
    """The Media Studio tab the shelf's catalog tab asks for ("3d", "image"), or None (the mode decides)."""
    shelf = _shelf(window, area)
    return catalog_kind(getattr(shelf, "active_catalog_path", "")) if shelf is not None else None


def offer_tab(window, area):
    """Add the library's Models and Images catalogs as the shelf's tabs, once per user (``tabs_to_offer``): True once
    done. Waits (False) before the library exists or on a build without ``AssetShelf.enable_catalog``."""
    catalogs = tab_catalogs()
    shelf = _shelf(window, area) if catalogs else None
    if shelf is None or not hasattr(shelf, "enable_catalog"):
        return False
    offered = _stored().get(OFFERED)
    offered = offered if isinstance(offered, dict) else None
    known = set(catalogs.values()) | set((offered or {}).values())
    enabled = {path for path in known if shelf.is_catalog_enabled(path)}
    enable, disable, now = tabs_to_offer(catalogs, offered, enabled)
    for path in disable:
        if hasattr(shelf, "disable_catalog"):
            shelf.disable_catalog(path)
    for path in enable:
        if not shelf.is_catalog_enabled(path):
            shelf.enable_catalog(path)
    if now != offered:
        _store(OFFERED, now)
    area.tag_redraw()
    return True


def _largest_view(window):
    views = [a for a in window.screen.areas if a.type == 'VIEW_3D'] if window.screen else []
    return max(views, key=lambda a: a.width * a.height, default=None)


def library_name():
    """The Angelo Media Studio library's name in the Preferences (the person may have renamed it), or None."""
    try:
        from . import studio_assets, studio_state
        directory = studio_assets.asset_directory(studio_state.library_directory())
        entry = studio_assets.find_library(bpy.context.preferences.filepaths.asset_libraries, directory)
    except (ImportError, OSError, ValueError, AttributeError):
        return None
    return entry.name if entry is not None else None


def show_studio_tab(window, tries=40):
    """Show ``window``'s 3D view's Asset Shelf on Angelo Media Studio: everything Media Studio made, ready to drag in
    (the welcome's "Show my assets"): that library, on Part E's parent catalog tab (added if missing, then shown)
    or, without it, on its All tab. The library is chosen in both cases: a shelf left on Current File or another
    library would show nothing of Media Studio's on that tab. Returns True when it will show; False without an
    Object-mode 3D view or the library. Call it outside a draw (a timer, an operator)."""
    area, name, parent = _largest_view(window), library_name(), studio_tab_path()
    active = window.view_layer.objects.active if window.view_layer else None
    if area is None or name is None or (active is not None and active.mode != 'OBJECT'):
        return False
    with bpy.context.temp_override(window=window, area=area):
        area.spaces.active.show_region_asset_shelf = True
    remember("expanded")
    pointer = area.as_pointer()

    def choose():
        nonlocal tries
        target = next((a for a in window.screen.areas if a.as_pointer() == pointer), None) if window.screen else None
        shelf = _shelf(window, target) if target is not None else None
        if shelf is None:   # The shelf region shows on a later redraw.
            tries -= 1
            return 0.05 if tries > 0 else None
        try:
            shelf.asset_library_reference = name
            if parent and hasattr(shelf, "enable_catalog"):
                if not shelf.is_catalog_enabled(parent):
                    shelf.enable_catalog(parent)
                shelf.active_catalog_path = parent
            else:
                shelf.active_catalog_path = ""   # The All tab (settable from the 1.0.5 native build).
        except (TypeError, ValueError, AttributeError):
            pass
        target.tag_redraw()
        return None
    bpy.app.timers.register(choose, first_interval=0.01)
    return True


def remembered():
    """"hidden", "collapsed" or "expanded" once the person chose, else None (the shelf's default: collapsed)."""
    from .studio_state import read_json
    stored = read_json(_path(), {})
    stored = stored if isinstance(stored, dict) else {}
    value = stored.get(STATE_KEY)
    if value in STATES:
        return value
    legacy = stored.get(KEY)
    return ("expanded" if legacy else "hidden") if isinstance(legacy, bool) else None


def remember(state):
    from .studio_state import atomic_json, read_json
    if state not in STATES:
        return
    stored = read_json(_path(), {})
    stored = dict(stored) if isinstance(stored, dict) else {}
    stored[STATE_KEY] = state
    stored.pop(KEY, None)
    try:
        atomic_json(_path(), stored)
    except OSError:
        pass   # Not remembered: the next file opens with Blender's default, never an error.


def change(previous, shown, settled):
    """The choice to remember from one sample to the next, or None. A view that entered Object mode less than
    ``SETTLE`` seconds ago is still switching shelves (Blender shows or hides the region over a few draws). Pure."""
    if not settled or previous is None or previous == shown:
        return None
    return shown


def _visible(region):
    return region is not None and region.width > 1 and region.height > 1


def _shown(area):
    """Whether the shelf's rows show (expanded)."""
    return _visible(next((r for r in area.regions if r.type == 'ASSET_SHELF'), None))


def state(area):
    """"expanded" (its rows show), "collapsed" (only its tab strip) or "hidden"."""
    regions = {r.type: r for r in area.regions if r.type in {'ASSET_SHELF', 'ASSET_SHELF_HEADER'}}
    if _visible(regions.get('ASSET_SHELF')):
        return "expanded"
    return "collapsed" if _visible(regions.get('ASSET_SHELF_HEADER')) else "hidden"


def set_collapsed(window, area, collapsed):
    """Collapse ``area``'s Object-mode shelf to its tab strip, or expand it; True when done. Outside a draw."""
    shelf = _shelf(window, area)
    if shelf is None or not hasattr(shelf, "angelo_set_collapsed"):
        return False
    region = next((r for r in area.regions if r.type == 'ASSET_SHELF'), None)
    with bpy.context.temp_override(window=window, area=area, region=region):
        done = bool(shelf.angelo_set_collapsed(collapsed))
    area.tag_redraw()
    return done


def _sample():
    """A 3D view's POST_PIXEL draw: note its Object-mode shelf's visibility, apply the remembered choice once the
    view has settled in Object mode, and remember a change the person made after that."""
    import time
    area, window = bpy.context.area, bpy.context.window
    if area is None or window is None or bpy.context.region is None or bpy.context.region.type != 'WINDOW':
        return
    pointer = area.as_pointer()
    from . import director_layout
    if bpy.context.mode not in MODES or director_layout.is_director(window):
        _seen.pop(pointer, None)   # Another mode's shelf, or Director's hidden one: never the person's choice.
        return
    now, shown = time.monotonic(), state(area)
    _expand_on_tab(window, area, pointer, shown)
    previous, entered = _seen.get(pointer, (None, now))
    settled = now - entered >= SETTLE
    _seen[pointer] = (shown, entered)
    if not settled:
        _redraw_later(pointer)   # One more draw once settled, even if nothing else redraws the view.
        return
    tried = _offering.get(pointer)
    if tried is None or (tried is not True and now - tried >= RETRY):   # Off the draw: a timer reads the file.
        _offering[pointer] = now
        _defer(lambda: _offer(pointer))
    if pointer not in _applied:
        _applied.add(pointer)
        wanted = remembered()
        if wanted is not None and wanted != shown:
            _defer(lambda: _apply(pointer, wanted))
        return
    choice = change(previous, shown, settled)
    if choice is not None:
        global _pending
        _pending = choice
        _defer(_write)


_tabs = {}   # area pointer -> the tab its collapsed shelf showed at the last draw.


def _expand_on_tab(window, area, pointer, current):
    """Picking a tab of a collapsed shelf expands it: the person asked to see that tab's assets."""
    if current != "collapsed":
        _tabs.pop(pointer, None)
        return
    shelf = _shelf(window, area)
    tab = getattr(shelf, "active_catalog_path", None) if shelf is not None else None
    before = _tabs.get(pointer)
    _tabs[pointer] = tab
    if before is not None and tab is not None and tab != before:
        _defer(lambda: _apply(pointer, "expanded"))


def _redraw_later(pointer):
    if pointer in _settling:
        return
    _settling.add(pointer)

    def run():
        _settling.discard(pointer)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas if window.screen else ():
                if area.as_pointer() == pointer:
                    area.tag_redraw()
        return None
    bpy.app.timers.register(run, first_interval=SETTLE)


def _defer(function):
    def run():
        function()
        return None
    bpy.app.timers.register(run, first_interval=0.0)


def _write():
    global _pending
    if _pending is not None:
        remember(_pending)
        _pending = None


def _offer(pointer):
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas if window.screen else ():
            if area.as_pointer() == pointer:
                if offer_tab(window, area):
                    _offering[pointer] = True   # Done for this view and file; a later file checks renames again.
                return


def _apply(pointer, wanted):
    """Hide, collapse or expand one view's shelf (never inside a draw: a property write there is not allowed)."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas if window.screen else ():
            if area.as_pointer() == pointer and area.type == 'VIEW_3D':
                if wanted == "hidden":
                    set_collapsed(window, area, False)   # Hidden in full: the tab strip goes too.
                    with bpy.context.temp_override(window=window, area=area):
                        area.spaces.active.show_region_asset_shelf = False
                elif wanted == "collapsed" and not set_collapsed(window, area, True):
                    with bpy.context.temp_override(window=window, area=area):   # A binary without collapsing.
                        area.spaces.active.show_region_asset_shelf = True
                elif wanted == "expanded":
                    with bpy.context.temp_override(window=window, area=area):
                        area.spaces.active.show_region_asset_shelf = True
                _seen[pointer] = (wanted, _seen.get(pointer, (None, 0.0))[1])
                area.tag_redraw()
                return


@persistent
def _load_post(_):
    _seen.clear()
    _applied.clear()
    _settling.clear()
    _offering.clear()
    _tabs.clear()


def register():
    bpy.utils.register_class(ANGELO_AST_object_assets)
    if not bpy.app.background:
        space = bpy.types.SpaceView3D
        _handlers.append((space, space.draw_handler_add(_sample, (), 'WINDOW', 'POST_PIXEL')))
    bpy.app.handlers.load_post.append(_load_post)


def unregister():
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    for space, handler in _handlers:
        space.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    _load_post(None)
    bpy.utils.unregister_class(ANGELO_AST_object_assets)
