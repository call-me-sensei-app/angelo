# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The approval card of ``angelo_library_import``: which library item, how large, and what the import changes.

``approvals.approval_message`` routes ``angelo_library_*`` here. The card names the item from the catalog already in
memory (``library_host.library().get``; no network on the main thread), its kind, licence and download size, and
what Undo reverses. Card text is translated where it is drawn (whole sentences, named placeholders); titles and
licences are the library's own words. Standard library only; no bpy.
"""

from .ui_text import iface_, plural_


def _size(size):
    return "%.1f MB" % (size / 1048576) if size >= 104858 else "%d KB" % max(1, size // 1024)


def _effect(kind, args):
    if kind == "texture":
        if args.get("objects"):
            return plural_(len(args["objects"]), "Becomes one material on the {count} object named in the request.",
                           "Becomes one material on the {count} objects named in the request.")
        return iface_("Becomes one material on the selected meshes.")
    if kind == "hdri":
        return iface_("Becomes the scene's world, replacing the current one.")
    if kind == "vfx":
        return iface_("Adds an upright picture plane at the location asked for, or at the 3D cursor.")
    return iface_("Adds the model at the location asked for, or at the 3D cursor.")


def message(name, args):
    if name != "angelo_library_import":
        return {"title": iface_("Angelo Library action"), "body": name, "warning": ""}
    from . import library_catalog, library_host
    item = library_host.library().get(args["item_id"])
    if item is None:   # The precondition refuses this before a card; a catalog swapped meanwhile lands here.
        return {"title": iface_("Add from the Angelo Library"), "body": iface_("Item {id}", id=args["item_id"]),
                "warning": iface_("This item isn't in the library's catalog any more.")}
    files, resolution = library_catalog.files_for(item, args.get("resolution"))
    size = _size(sum(f["size"] for f in files))
    fields = dict(title=item["title"], license=item["license"] or iface_("licence not stated"), size=size)
    body = (iface_("“{title}” · {resolution} · {license} · {size} download", resolution=resolution, **fields)
            if resolution else iface_("“{title}” · {license} · {size} download", **fields))
    body += "\n" + _effect(item["kind"], args)
    if item["attribution_required"] and item["attribution"]:
        body += "\n" + iface_("Credit required: {attribution}", attribution=item["attribution"])
    return {"title": iface_("Add from the Angelo Library"), "body": body,
            "warning": iface_("Downloads the files from ToonLab, checks them (format, size, checksums) and imports "
                              "them as one Undo step. Nothing in them runs.")}
