# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An engine link save after the file reached the disk: its write-back's start (``engine_link_writeback`` runs it in a
background Angelo) and end, with the asset's stamp and every editor of its project told to re-import it, or why it
wasn't written, and the engine preflight (``engine_preflight_link``). Main thread: ``engine_link``'s save handler and
its timer (``engine_link._tick``). No ``bpy``.
"""

from . import engine_link_assets as assets, engine_link_facts as facts, engine_preflight_link


def _baked(state, broadcast, asset, durable):
    """The current baked materials a write-back assigns first (export_baked); the editor hears which keep their own."""
    if durable[0] != "gltf":
        return ()
    try:
        return assets.baked_for_save(lambda code, text: broadcast(
            asset, {"type": "status", "asset": asset.ident, "code": code, "message": text}))
    except Exception as error:   # Still written, with every object's own materials.
        state["error"] = "baked materials: %s" % type(error).__name__
        return ()


def saved(state, broadcast, binary, found, blend, durable, number):
    """Save ``number`` of the session ``found`` reached ``blend``. With ``durable`` (exporter, options) the asset is
    written in a background Angelo and the editors hear when it ends (``written``); a .blend is the asset: they hear now.
    ``state``, ``broadcast``, ``binary``: as ``written``."""
    asset = found["asset"]
    if durable is not None:
        # Its identity rides with it and comes back with its end (``written``): the asset, this save, this link and
        # the live scene the file holds; a newer save replaces a queued one (WriteBack.request).
        baked = _baked(state, broadcast, asset, durable)
        key = (asset.path, number, found.get("number", 0), state["version"], baked)
        state["writeback"].request(key, blend, asset.path, *durable, baked=baked,
                                   stamp={"path": assets.stamp_path(asset), "asset": asset.ident})
        return
    broadcast(asset, {"type": "saved", "asset": asset.ident, "files": [asset.ident],
                      "scene_version": state["version"], "save": number})
    engine_preflight_link.after_blend_save(state, binary, asset, blend, found["params"],
                                           save_key=facts.save_key(state["facts"], number))


def written(state, broadcast, binary):
    """Finish the write-backs that ended: the stamp, the editors' re-import and the engine preflight, or why the asset
    wasn't written. ``state``: the link's (``engine_link._state``); ``broadcast``: its ``_broadcast``; ``binary``: this
    Angelo, for the preflight."""
    for (target, number, link, covers, baked), error in state["writeback"].finished():
        found = state["session"]   # Only the link that saved it: never a later link, even of the same asset.
        if found is None or found.get("number", 0) != link or not assets.same_file(found["asset"].path, target):
            continue
        asset = found["asset"]
        facts.save_finished(state["facts"], number, error)
        if error is not None:
            state["error"] = "write-back: " + error
            broadcast(asset, {"type": "status", "asset": asset.ident, "code": "write_failed",
                              "message": "Angelo saved its file but couldn't write the %s" % asset.kind})
            continue
        try:   # The written bytes' digest names this save to the preflight's report (its target_sha256).
            facts.save_written_as(state["facts"], number, assets.write_stamp(asset))
        except OSError as failure:
            state["error"] = "stamp: %s" % failure
        broadcast(asset, {"type": "saved", "asset": asset.ident, "files": [asset.ident],
                          "scene_version": covers, "save": number})
        engine_preflight_link.after_writeback(state, binary, asset, found["params"], covers,
                                              save_key=facts.save_key(state["facts"], number), baked=baked)
