# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's OPEN (``engine_link_tool_host``): find the asset in the connected editors' projects, then raise the
person's request card for it as that editor's Edit in Angelo would (``engine_link_agent.ask``).

* **Finding it reads the projects' folders**, by an engine's name or a plain file name (``engine_link_names``), and a
  project can be huge or on a slow or network drive: that reading runs off the main thread (``engine_link_reads``),
  bounded in entries and time. The main thread waits a moment for it (most lookups end there, and the tool answers at
  once), else the tool returns a ``StudioPending`` the controller polls, as PUSH and SAVE do.
* **Deciding stays on the main thread**, when the reading is done: ambiguity, refusals, the card policy and the card.
  A refusal is the same result either way: raised at once, or the result ``refusal_codes.error_result`` makes of it,
  marked unchanged as ``engine_link_tool_host.execute`` marks it, so a typed plan returns it for a correction.
"""

from . import engine_link, engine_link_agent as agent, engine_link_assets as assets, engine_link_facts as facts
from . import engine_link_names as names, engine_link_reads as reads
from .refusal_codes import RefusalError, error_result

LATE = ("Angelo couldn't read the engine's project in time to find %s (a very large project, or a slow or network "
        "drive). Name it as its engine does")


def open_asset(request, controller, off, pending):
    """OPEN's answer: a result, a ``StudioPending`` while the projects are read (``pending``, the host's), or a raised
    refusal (``off``: the host's text for a link that is off)."""
    if not engine_link.running():
        raise RefusalError(off, code="UNSUPPORTED")
    engine, project = request.get("engine", "").lower(), request.get("project")
    targets, refusals = [], []
    for _client, info in sorted(engine_link.clients().items()):
        adapter = assets.adapter(info.get("engine", "godot"))
        if adapter is None or engine and info.get("engine", "godot") != engine:
            continue
        if project is not None and str(info.get("project_name", "")) != project:
            continue
        if not assets.opens_by_name(adapter):
            refusals.append("%s opens a model only through Edit in Angelo in %s (Angelo needs the file it was "
                            "imported from)" % (adapter.DISPLAY, adapter.DISPLAY))
            continue
        targets.append((adapter, dict(info)))
    name = request["asset"]
    try:
        lookup = reads.Lookup(lambda: [_found(adapter, info, name) for adapter, info in targets])
    except RuntimeError as error:   # No thread to read on (break-it 10-N4).
        raise RefusalError("Angelo couldn't start reading the projects to find %s: %s"
                           % (name[:200], facts.plain(error, 200)), code="INTERNAL_ERROR") from None
    if lookup.wait():
        return _ask(request, controller, lookup, refusals, off)

    def poll():
        if not lookup.ready():
            return None
        try:
            return _ask(request, controller, lookup, refusals, off)
        except RefusalError as error:
            if error.code != "INTERNAL_ERROR":
                error.partial_changes_possible = False   # As execute() marks one raised at once.
            return error_result(error)
        except Exception as error:   # Its own words, never the controller's generic monitoring failure.
            return error_result(RefusalError("Opening %s failed: %s" % (request["asset"][:200],
                                                                       facts.plain(error, 300)), code="INTERNAL_ERROR"))

    return pending(poll, reads.READ_S, "Reading the engine's project",
                         "Looking for the asset in the connected editors' projects.",
                         {"opened": False, "stopped": "Stopped while looking for the asset; nothing was asked."})


def _found(adapter, info, name):
    """Off the main thread: (asset, info, None) for the one asset ``name`` means in ``info``'s project, or
    (None, info, AssetError) with why not."""
    try:
        return names.resolve(adapter, info.get("project", ""), name), info, None
    except assets.AssetError as error:
        return None, info, error


def _ask(request, controller, lookup, refusals, off):
    """On the main thread, once ``lookup`` is ready: refuse, or raise the card. Never a guess (break-it 8-1)."""
    kind, found = lookup.outcome
    if kind == "late":
        raise RefusalError(LATE % request["asset"][:200], code="VALIDATION")
    if kind == "error":
        raise RefusalError("Looking for %s failed: %s" % (request["asset"][:200], facts.plain(found, 300)),
                           code="INTERNAL_ERROR")
    if not engine_link.running():   # Turned off while the projects were read.
        raise RefusalError(off, code="UNSUPPORTED")
    candidates, refusals, unsure, seen = [], list(refusals), [], set()
    for asset, info, error in found:
        if error is not None:
            (unsure if error.code in names.UNSURE else refusals).append(
                "%s (%s): %s" % (assets.display_name(info.get("engine", "godot")),
                                 str(info.get("project_name", ""))[:80], error.english()))
        elif (asset.engine, asset.project, asset.ident) not in seen:
            seen.add((asset.engine, asset.project, asset.ident))
            candidates.append((asset, info))
    if unsure:   # One project's single match doesn't settle another's several.
        listed = ["%s (%s): %s" % (assets.display_name(asset.engine), str(info.get("project_name", ""))[:80],
                                   asset.ident) for asset, info in candidates]
        several = len({str(info.get("project", "")) for info in engine_link.clients().values()}) > 1
        raise RefusalError("Which asset %s means is unclear: %s. Name it as its engine does%s"
                           % (request["asset"][:200], names.joined(unsure + listed),
                              ", with engine and project" if several else ""), code="VALIDATION")
    if not candidates:
        reason = names.joined(list(dict.fromkeys(refusals))) or "no connected editor matches"
        raise RefusalError("No connected editor's project holds %s: %s" % (request["asset"][:200], reason),
                           code="VALIDATION")
    if len(candidates) > 1:
        raise RefusalError("%s is in several connected projects (%s); name engine and project"
                           % (request["asset"][:200], ", ".join("%s %s" % (assets.display_name(a.engine),
                                                                        str(i.get("project_name", ""))[:80])
                                                                for a, i in candidates[:8])), code="VALIDATION")
    asset, info = candidates[0]
    if engine_link.session() is not None and engine_link.session() == asset:
        return {"already_open": asset.ident, "engine": assets.display_name(asset.engine),
                "note": "It is the linked asset; nothing to ask."}
    shown = engine_link.pending()
    if shown is not None:
        raise RefusalError("A request card for %s is already waiting for the person's answer; nothing new was asked"
                           % shown["asset"].ident, code="WRITER_BUSY")
    live = any(assets.serves(asset, other, live=True) for other in engine_link.clients().values())
    try:
        agent.ask(asset, info.get("project_name") or "", live, agent.caller_of(controller))
    except RuntimeError as error:
        raise RefusalError(str(error), code="LIMIT_EXCEEDED") from None
    return {"asked": asset.ident, "engine": assets.display_name(asset.engine),
            "project": str(info.get("project_name", ""))[:80], "opened": False,
            "note": ("Nothing is open yet. The person answers on Angelo's card (Open, Save and Open, or Not now); "
                     "opening closes the current file, and the asset opens in its own chat, not this one.")}
