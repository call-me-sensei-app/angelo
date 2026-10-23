# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What dragging Media Studio's items does, as menu items wherever they show: the Asset Browser's context menu and
the Asset Shelf's (Media Studio's own item popup draws them itself, ``studio_asset_ops``).

A Media Studio asset is known by its catalogue (``studio_assets.CATALOGS``, fixed ids) and its item by its file's
name (``<id>.blend``). The actions are ``media_drop``'s: Add to Scene, Apply to Selected (images), Add to Chat.
"""

from pathlib import Path

import bpy

from . import studio_assets

STUDIO_CATALOGS = frozenset(spec[0] for spec in studio_assets.CATALOGS.values())


def studio_item(asset):
    """``(id, kind)`` of a Media Studio asset, or None for any other asset."""
    metadata = getattr(asset, "metadata", None)
    if metadata is None or str(getattr(metadata, "catalog_id", "")) not in STUDIO_CATALOGS:
        return None
    identity = Path(getattr(asset, "full_library_path", "") or "").stem
    return (identity, "IMAGE" if asset.id_type == 'MATERIAL' else "3D") if identity else None


def draw_actions(layout, items):
    """The drag's menu items for ``items`` (``[(id, kind)]``), as one set."""
    identities = "\n".join(dict.fromkeys(identity for identity, _kind in items))
    layout.operator("angelo.media_add_to_scene", icon='ADD').item_ids = identities
    if all(kind == "IMAGE" for _identity, kind in items):
        layout.operator("angelo.media_apply_to_selected", icon='MATERIAL').item_ids = identities
    layout.operator("angelo.media_add_to_chat", icon='PASTEDOWN').item_ids = identities


def _browser_menu(self, context):
    assets = list(getattr(context, "selected_assets", None) or ())
    if not assets and getattr(context, "asset", None) is not None:
        assets = [context.asset]
    items = [found for found in map(studio_item, assets) if found]
    if items:
        self.layout.separator()
        draw_actions(self.layout, items)


def draw_shelf_menu(context, asset, layout):
    """For an Asset Shelf type's ``draw_context_menu``."""
    found = studio_item(asset)
    if found:
        draw_actions(layout, [found])


def register():
    menu = getattr(bpy.types, "ASSETBROWSER_MT_context_menu", None)
    if menu is not None:
        menu.append(_browser_menu)


def unregister():
    menu = getattr(bpy.types, "ASSETBROWSER_MT_context_menu", None)
    if menu is not None:
        menu.remove(_browser_menu)
