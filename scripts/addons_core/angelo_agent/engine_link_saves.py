# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An engine link save after the file reached the disk: its write-back's start (``engine_link_writeback`` runs it in a
background Angelo) and end, with the asset's stamp and every editor of its project told to re-import it, or why it
wasn't written, and the engine preflight (``engine_preflight_link``). Main thread: ``engine_link``'s save handler and
its timer (``engine_link._tick``). No ``bpy``.
"""

from . import change_clock, engine_link_assets as assets, engine_link_facts as facts, engine_preflight_link
from .engine_link_writeback import SUPERSEDED

UNIDENTIFIED = "the save could not identify the file it wrote"


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


def post(state, broadcast, binary, found, blend):
    """The save-post hook's bookkeeping, still inside the native save handler that identifies the file written."""
    from . import document_file_identity
    asset = found["asset"]
    saved_stat = document_file_identity.saved(blend)   # The native writer's, never a later path stat.
    durable = assets.adapter(asset.engine).durable(asset, found["params"])
    state["saves"] = number = state.get("saves", 0) + 1
    facts.save_started(state["facts"], number, state["version"], durable is not None)
    saved(state, broadcast, binary, found, blend, durable, number, saved_stat)


def saved(state, broadcast, binary, found, blend, durable, number, saved_stat=None):
    """Save ``number`` of the session ``found`` reached ``blend``. With ``durable`` (exporter, options) the asset is
    written in a background Angelo and the editors hear when it ends (``written``); a .blend is the asset: they hear now.
    ``state``, ``broadcast``, ``binary``: as ``written``. ``saved_stat``: the saved .blend's stat, taken by the save
    handler as the save ended: the write-back child checks it reads that file (``WriteBack.request``'s ``saved``) and
    the preflight names it as the save, never a file written there later."""
    asset = found["asset"]
    if durable is not None and saved_stat is None:
        # The save named no file it wrote (``document_file_identity.saved``): no child could tell it reads this save,
        # so none runs, and the editors hear why, never that the file changed.
        facts.save_finished(state["facts"], number, UNIDENTIFIED)
        state["error"] = "write-back: " + UNIDENTIFIED
        broadcast(asset, {"type": "status", "asset": asset.ident, "code": "write_failed",
                          "message": "Angelo couldn't tell that the saved file is the one its save wrote, so it didn't "
                                     "write the %s; save again to write it" % asset.kind})
        return
    if durable is not None:
        # Its identity rides with it and comes back with its end (``written``): the asset, this save, this link and
        # the live scene the file holds; a newer save replaces a queued one (WriteBack.request).
        baked = _baked(state, broadcast, asset, durable)
        key = (asset.path, number, found.get("number", 0), state["version"], baked)
        state.setdefault("saved_identities", {})[number] = saved_stat   # Its preflight's, when it ends.
        state["writeback"].request(key, blend, asset.path, *durable, baked=baked,
                                   stamp={"path": assets.stamp_path(asset), "asset": asset.ident, "save": number,
                                          "link": found.get("number", 0), "writer": assets.WRITER},
                                   saved=saved_stat)
        return
    broadcast(asset, {"type": "saved", "asset": asset.ident, "files": [asset.ident],
                      "scene_version": state["version"], "save": number})
    engine_preflight_link.after_blend_save(state, binary, asset, blend, found["params"],
                                           save_key=facts.save_key(state["facts"], number),
                                           blend_witness=saved_stat,
                                           blend_identity=None if saved_stat is not None else list(change_clock.unique()))


def written(state, broadcast, binary):
    """Finish the write-backs that ended: the stamp, the editors' re-import and the engine preflight, or why the asset
    wasn't written. ``state``: the link's (``engine_link._state``); ``broadcast``: its ``_broadcast``; ``binary``: this
    Angelo, for the preflight."""
    for (target, number, link, covers, baked), error in state["writeback"].finished():
        identities = state.setdefault("saved_identities", {})
        # Keep the original save witness: a coarse step is settled on the preflight's runner, never this UI timer.
        saved_stat = identities.pop(number, None)
        for older in [other for other in identities if other < number]:   # Replaced while queued: never ending.
            del identities[older]
        found = state["session"]   # Only the link that saved it: never a later link, even of the same asset.
        if found is None or found.get("number", 0) != link or not assets.same_file(found["asset"].path, target):
            continue
        asset = found["asset"]
        facts.save_finished(state["facts"], number, error)
        if error is not None and SUPERSEDED in error and state["writeback"].busy():
            continue   # The working file was saved again before this child read it: that save's write-back follows.
        if error is not None and SUPERSEDED in error:
            state["error"] = "write-back: " + SUPERSEDED
            broadcast(asset, {"type": "status", "asset": asset.ident, "code": "write_failed",
                              "message": "The working file changed after Angelo saved it; save again to write the %s"
                              % asset.kind})
            continue
        if error is not None:
            state["error"] = "write-back: " + error
            broadcast(asset, {"type": "status", "asset": asset.ident, "code": "write_failed",
                              "message": "Angelo saved its file but couldn't write the %s" % asset.kind})
            continue
        # The written bytes' digest names this save to the preflight's report (its target_sha256): the child's own,
        # taken before its replace. The asset is never digested again here: a write to it since (the engine, another
        # program) would be stamped as Angelo's, and the next link would open the working .blend as current for it.
        # None when a newer write-back's stamp is there already (its own end names it), or when this child could not
        # record it: the earlier stamp then no longer matches the asset, so the next link imports it.
        facts.save_written_as(state["facts"], number, assets.written_stamp(asset, number, link))
        broadcast(asset, {"type": "saved", "asset": asset.ident, "files": [asset.ident],
                          "scene_version": covers, "save": number})
        engine_preflight_link.after_writeback(state, binary, asset, found["params"], covers,
                                              save_key=facts.save_key(state["facts"], number), baked=baked,
                                              blend_witness=saved_stat,
                                              blend_identity=None if saved_stat is not None else list(change_clock.unique()))
