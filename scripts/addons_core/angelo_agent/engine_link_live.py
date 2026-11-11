# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine link's live frames: export the linked scene and send it to the editors that show live scenes. Main
thread (``engine_link`` calls ``publish`` when ``engine_link_cadence`` says a frame is due; doc/angelo/engine_link.rst).

Every engine gets the whole scene as one .glb, except one whose adapter makes its own frames (``live_frames``): the
web sends only the objects that changed (``web_link_live``). The bookkeeping is the same for all: one version per
publish, the export's time, a failed export's notice, and what was pushed for the agent's facts.
"""

import os
import time

from . import engine_link_assets as assets, engine_link_cadence as cadence, engine_link_facts as facts


def _whole_scene(link, window, path, found, base):
    """The default frames: the whole scene, the same bytes to every live editor of the asset."""
    engine_adapter = assets.adapter(found["asset"].engine)
    assets.export(window, path, engine_adapter.live_options(found["params"]),
                  getattr(engine_adapter, "LIVE_EXPORT_ID", assets.EXPORT_ID))
    with open(path, "rb") as handle:
        return [(None, {}, handle.read())]


def publish(window, link):
    """Export the linked scene now and send it to the project's editors that show live scenes; returns the bytes
    sent (0: none). ``link`` is the ``engine_link`` module whose state this is. An adapter's
    ``live_frames(link, window, path, found, base)`` returns ``[(clients or None, header
    fields, payload)]`` for the version after ``base``; None means every live editor of the asset, and no frames
    means nothing changed (no version is spent)."""
    engine_link = link
    state = engine_link._state
    found = state["session"]
    state["dirty_at"] = None
    if found is None:
        return 0
    asset = found["asset"]
    if not engine_link.editors(live=True):
        return 0   # No editor of that project shows live scenes: nothing to show it to (it gets one when it asks).
    path = os.path.join(state["folder"], "live.glb")
    frames_of = getattr(assets.adapter(asset.engine), "live_frames", None) or _whole_scene
    state["exporting"] = True
    started = time.perf_counter()
    try:
        frames = frames_of(engine_link, window, path, found, state["version"])
    except Exception as error:
        state["error"] = facts.plain(error, 300)   # Never cut before its paths are removed.
        facts.push_failed(state["facts"], state["error"])
        engine_link._broadcast(asset, {"type": "status", "asset": asset.ident, "code": "export_failed",
                                       "message": "Angelo couldn't export the change; %s shows the last one"
                                       % assets.display_name(asset.engine)}, live=True)
        return 0
    finally:
        state["exporting"] = False
        timed = getattr(assets.adapter(asset.engine), "live_time_counts", None)
        if timed is None or timed():   # A background check's time never lengthens the wait for the next edit.
            state["last_ms"] = (time.perf_counter() - started) * 1000.0
        state["scene"] = engine_link.scene_key(*engine_link._followed(window))
        state["exported_at"] = time.monotonic()   # The cadence's gap runs from here (engine_link_cadence).
    if not frames:
        return 0
    state["version"] += 1
    sent = size = 0
    server = state["server"]
    shown = {client: info for client, info in engine_link.clients().items() if assets.serves(asset, info, True)}
    answering = set()   # The acknowledging editors this version reached: the next frame waits for their answers.
    for clients, fields, body in frames:
        header = dict({"type": "scene", "asset": asset.ident, "version": state["version"],
                       "export_ms": round(state["last_ms"], 1)}, **fields)
        if clients is None:
            reached = engine_link._broadcast(asset, header, body, live=True)
            targets = set(shown) if reached else set()
        else:
            targets = {client for client in clients if server is not None and server.send(client, header, body)}
            reached = len(targets)
        answering |= {client for client in targets if facts.ACKS in assets.capabilities(shown.get(client, {}))}
        sent += reached
        size += len(body) if reached else 0
    facts.pushed(state["facts"], state["version"], size, state["last_ms"], sent)
    state["awaiting"] = frozenset(answering)
    if server is not None and server.lagging_any():   # A page lost frames it was behind on: a full one, at once.
        state["dirty_at"] = cadence.FORCED
    return size
