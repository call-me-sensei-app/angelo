# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine link tools on the host (``engine_link_tool_spec``): the agent's and MCP apps' route to an engine live
link the person has turned on. Main thread.

* STATUS reads ``engine_link`` and its facts (``engine_link_facts``); it changes nothing and never waits.
* OPEN raises the person's request card for an asset a connected editor's project holds, as that editor's Edit in
  Angelo would (``engine_link_agent.ask``); nothing opens here, and the result says so. One card at a time, and a pause
  after the person declines one, so no caller can make the card come back again and again.
  ``engine_link_tool_open`` does it, reading the projects off the main thread.
* PUSH exports the linked scene now (``engine_link.publish``), SAVE saves the asset's .blend as File > Save does
  (``engine_link_agent.save``: the write-back and re-import follow from the save handler), CLOSE ends the link. Each is
  refused with its reason when the link is off or nothing is linked; nothing here turns the link on.
* PUSH and SAVE then wait, briefly and without blocking Angelo (a ``StudioPending`` the controller polls), for what
  the editors acknowledge: only editors that said they acknowledge (``acks``) are waited for, and the result says
  which editors can't confirm. A wait that runs out is a result, not a failure: the push or save stands.

Results name engines, projects and assets the way the engines do; never a path.
"""

import json
import time

import bpy

from . import engine_link, engine_link_agent as agent, engine_link_assets as assets, engine_link_facts as facts
from . import engine_link_tool_open as opening
from .refusal_codes import RefusalError

PUSH_WAIT_S = 8.0      # For live editors to show the pushed scene.
WRITE_WAIT_S = 120.0   # For the background write-back of a non-.blend asset (engine_link_writeback).
ACK_WAIT_S = 30.0      # For the editors' re-import once the asset is written.
# The engine preflight's report on a linked asset: a callable(asset) -> plain dict or None; None looks for
# engine_preflight.link_report, False when this build has none (_preflight_source).
PREFLIGHT = None
PREFLIGHT_FINDINGS = 3
STATUS_BUDGET = 2500   # Bytes of a status answer, as escaped JSON, whatever the editors sent.
OFF = ("The engine live link is off. Only the person turns it on (angelo_app_feature ENGINE_LINK ON asks them on a "
       "card); this tool never does.")
NOTHING_LINKED = ("No engine asset is linked. The person picks one in the engine (Edit in Angelo) and opens it "
                  "here.")


def _engine(engine):
    return assets.display_name(engine)


def _ack_fields(entry, now):
    """An editor's acknowledgements as its row shows them: ``previewed`` / ``preview_failed`` and ``reimported`` /
    ``reimport_failed``, each with seconds ago (a message only on a failure)."""
    out = {}
    for key, value_key in (("preview", "version"), ("save", "save")):
        found = (entry or {}).get(key)
        if found is not None:
            fields = {value_key: found[value_key], "ago_s": facts.ago(found["at"], now)}
            if found.get("instances") is not None:
                fields["instances"] = found["instances"]
            if found.get("message"):
                fields["message"] = found["message"]
            out[found["code"]] = fields
    return out


def _editor(client, info, linked, known, now):
    """One connected editor's row, with what it acknowledged about the linked asset."""
    shown = assets.capabilities(info)
    editor = {"engine": _engine(info.get("engine", "godot")), "project": str(info.get("project_name", ""))[:80],
              "live": "live" in shown, "acks": facts.ACKS in shown}
    if info.get("version"):
        editor["version"] = str(info["version"])[:40]
    if linked is not None:
        editor["linked_project"] = assets.serves(linked, info)
        editor.update(_ack_fields(known["editors"].get(client), now))
    return editor


def _linked(asset, now):
    known = agent.link_facts()
    linked = {"asset": asset.ident, "engine": _engine(asset.engine), "kind": asset.kind,
              "open_here": bool(bpy.data.filepath) and assets.same_file(bpy.data.filepath, asset.editable),
              "unsaved": bool(bpy.data.is_dirty), "live_change_waiting": agent.dirty()}
    push = known["push"]
    if push is not None:
        linked["pushed"] = {key: push[key] for key in ("version", "bytes", "export_ms", "editors") if key in push}
        if "at" in push:
            linked["pushed"]["ago_s"] = facts.ago(push["at"], now)
        if push.get("failed"):
            linked["pushed"]["failed"] = push["failed"]
    save = known["save"]
    if save is not None:
        linked["saved"] = {"save": save["save"], "ago_s": facts.ago(save["at"], now), "state": save["state"],
                           "scene_version": save["scene_version"]}
        if save.get("error"):
            linked["saved"]["error"] = save["error"]
    preflight = _preflight(asset, save)
    if preflight is not None:
        linked["preflight"] = preflight
    return linked


def _preflight_source():
    """The preflight's report reader: ``PREFLIGHT`` when set, else ``engine_preflight.link_report`` once that module
    is in this build (it reports the last durable save's checks), else None."""
    global PREFLIGHT
    if PREFLIGHT is None:
        try:
            from . import engine_preflight
            PREFLIGHT = engine_preflight.link_report
        except (ImportError, AttributeError):
            PREFLIGHT = False   # Not in this build: never looked up again.
    return PREFLIGHT or None


def _preflight(asset, save):
    """The engine preflight's facts for ``save`` (the link's fact of the asset's last save), compact: its status,
    counts and at most ``PREFLIGHT_FINDINGS`` findings (code, severity, subject, message), with how many there are;
    ``state`` "pending" until a report describes that save (``_describes``); None with no save or no preflight."""
    source = _preflight_source()
    if source is None or save is None or save.get("state") == "failed":   # Nothing written: nothing to check.
        return None
    try:
        found = source(asset)
    except Exception as error:   # A broken check never hides the link's own facts.
        return {"error": facts.plain("%s: %s" % (type(error).__name__, error))}
    if not isinstance(found, dict) or not _describes(found, save):
        return {"state": "pending", "note": "No check of this save has reported; one may still be running"}
    compact = {key: found[key] for key in ("status", "scene_version", "target", "format", "counts") if key in found}
    if found.get("error"):
        compact["error"] = facts.plain(found["error"], 300)
    findings = found.get("findings")
    if isinstance(findings, list):
        compact["findings_total"] = len(findings)
        compact["findings"] = [{key: facts.plain(item[key]) for key in ("code", "severity", "subject", "message")
                                if isinstance(item, dict) and item.get(key) is not None}
                               for item in findings[:PREFLIGHT_FINDINGS]]
    return compact


def _describes(report, save):
    """Whether a preflight report is of this save (audit 2026-11-03: SAVE read the report before its own check had
    run, and returned the previous save's): it echoes the save's exact name (``engine_link_facts.save_key``), given to
    the check when the save asked for it. A scene version is no name (it moves only with a live push: two saves share
    one, break-it 9-H1), nor is the saved file's identity (made-up inodes and 1-2 s times on FAT, HFS+ or some network
    shares: 10-N1). The bytes it read back (``target_sha256``) must also be what the save's write-back wrote when the
    report names them; a digest this save hasn't recorded can't be checked, so the report isn't taken."""
    key = save.get("key")
    if not isinstance(key, str) or report.get("save_key") != key:
        return False
    digest = report.get("target_sha256")
    return digest is None or digest == save.get("sha256")


def _size(value):
    return len(json.dumps(value))   # As escaped JSON: the most a reply can cost (non-ASCII text escapes to 6 bytes).


def _fit(result):
    """``result`` within ``STATUS_BUDGET`` bytes whatever the editors sent (break-it F2): failure messages go first,
    then long names are cut, then the editors are counted instead of listed."""
    if _size(result) <= STATUS_BUDGET:
        return result
    for editor in result.get("editors", []):
        for value in editor.values():
            if isinstance(value, dict):
                value.pop("message", None)
    if _size(result) <= STATUS_BUDGET:
        return result
    for editor in result.get("editors", []):
        editor["project"] = editor["project"][:24]
        editor.pop("version", None)
    for item in (result.get("linked") or {}, result.get("request") or {}):
        for key in ("asset", "project"):
            if isinstance(item.get(key), str) and len(item[key]) > 200:
                item[key] = item[key][:199] + "\u2026"
    if _size(result) <= STATUS_BUDGET:
        return result
    rows = result.pop("editors", [])
    result["editors_summary"] = {
        "connected": len(rows), "live": sum(row["live"] for row in rows), "acks": sum(row["acks"] for row in rows),
        "linked_project": sum(bool(row.get("linked_project")) for row in rows),
        "previewed": sum("previewed" in row for row in rows), "reimported": sum("reimported" in row for row in rows),
        "failed": sum(any(key.endswith("failed") for key in row) for row in rows)}
    (result.get("linked") or {}).pop("preflight", None)
    return _shorten(result)


def _strings(value):
    """(container, key or index, text) of every string value in ``value`` (never the keys)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                yield value, key, item
            else:
                yield from _strings(item)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                yield value, index, item
            else:
                yield from _strings(item)


def _shorten(result):
    """The last guarantee of ``STATUS_BUDGET``, whatever fields a result holds (break-it 2-4, 3-3): halve the costliest
    string (as escaped JSON) while that saves bytes, asset names last, since the agent names an asset by them; when
    nothing shrinks any more, a fixed minimal answer that says it was cut."""
    while _size(result) > STATUS_BUDGET:
        found = sorted(((key == "asset", -len(json.dumps(text)), place, key, text)
                        for place, key, text in _strings(result)), key=lambda item: item[:2])
        for _named_last, cost, place, key, text in found:
            cut = text[:len(text) // 2] + "..."
            if len(json.dumps(cut)) < -cost:
                place[key] = cut
                break
        else:
            linked = result.get("linked") or {}
            return {"link": result.get("link"), "linked": {"asset": str(linked.get("asset", ""))[:120]} if linked
                    else None, "truncated": "Too much to show; ask again for less"}
    return result


def status():
    """The link's facts for the agent: compact (``STATUS_BUDGET``), English, no paths."""
    from . import engine_suggest_ui
    running = engine_suggest_ui.facts()["running"]
    if not engine_link.running():
        return {"link": "off", "engines_running": running, "next": OFF}
    now = time.monotonic()
    asset = engine_link.session()
    known = agent.link_facts()
    result = {"link": "on", "engines_running": running,
              "editors": [_editor(client, info, asset, known, now)
                          for client, info in sorted(engine_link.clients().items())]}
    if asset is not None:
        result["linked"] = _linked(asset, now)
    else:
        result["linked"] = None
        result["next"] = NOTHING_LINKED
    request = engine_link.pending()
    if request is not None:
        result["request"] = {"asset": request["asset"].ident, "engine": _engine(request["asset"].engine),
                             "project": request["project_name"][:80], "waiting_s": facts.ago(request["at"], now),
                             "by_agent": bool(request.get("by_agent")),
                             "note": "Waiting for the person's answer on Angelo's card."}
    if agent.last_error():
        result["last_error"] = agent.last_error()
    return _fit(result)


def precondition(name, request):
    """What an approval of ``angelo_engine_link`` binds: the link session and the asset its card names. Re-read before
    the call runs, so an approval never acts on an asset linked after the card was shown (the controller refuses a
    changed binding as stale). Refuses before any card when the link is off or nothing is linked."""
    if request.get("action") == "OPEN":   # Its card is the person's (engine_link_agent.ask): only the link must be on.
        if not engine_link.running():
            raise RefusalError(OFF, code="UNSUPPORTED")
        return ("open",)
    asset = _require_linked()
    return (agent.session_number(), asset.engine, asset.ident)   # Never a path.


def _require_linked():
    if not engine_link.running():
        raise RefusalError(OFF, code="UNSUPPORTED")
    asset = engine_link.session()
    if asset is None:
        raise RefusalError(NOTHING_LINKED, code="UNSUPPORTED")
    return asset


def _editors(asset, live):
    """[(client, info)] of the asset's editors (``live``: those that show live scenes), split by whether they
    acknowledge: (acknowledging, silent)."""
    serving = [(client, info) for client, info in sorted(engine_link.clients().items())
               if assets.serves(asset, info, live)]
    return ([item for item in serving if facts.ACKS in assets.capabilities(item[1])],
            [item for item in serving if facts.ACKS not in assets.capabilities(item[1])])


def _named(items):
    return [{"engine": _engine(info.get("engine", "godot")), "project": str(info.get("project_name", ""))[:80]}
            for _client, info in items]


def _pending(poll, deadline_s, status_text, note, stopped):
    from .studio_agent import StudioPending
    wait = StudioPending(poll, lambda: None)
    # The controller's own expiry comes well after ours; even then it delivers ``stopped`` (pending_delivery).
    wait.timeout, wait.status_text, wait.activity_note = deadline_s + 30.0, status_text, note
    wait.stopped_result = stopped
    return wait


def _push(window, asset):
    acking, silent = _editors(asset, live=True)
    if not acking and not silent:
        raise RefusalError("No %s editor of this project that shows live scenes is connected; SAVE updates the asset "
                           "there instead" % _engine(asset.engine), code="UNSUPPORTED")
    from . import scene_changes
    # The scene the link follows, never the chat's own window: a second window may show another scene (break-it F5).
    window = agent.followed_window() or window
    if window is None and not bpy.app.background:
        raise _final(RefusalError("No window shows the linked scene to export from", code="UNSUPPORTED"))
    if window is not None and scene_changes.busy(window):
        raise _final(RefusalError("Angelo is in Edit Mode, playing, rendering or in the middle of a tool; the change "
                                  "goes live once that ends", code="STALE_CONTEXT"))
    started, link = time.monotonic(), agent.session_number()
    sent = engine_link.publish(window)
    push = agent.link_facts()["push"]
    if not sent or push is None or push.get("at", 0) < started:
        raise RefusalError("The live update wasn't sent: %s" % ((push or {}).get("failed") or "no editor took it"),
                           code="INTERNAL_ERROR")
    version = push["version"]
    base = {"pushed": {"version": version, "bytes": push["bytes"], "export_ms": push["export_ms"],
                       "editors": push["editors"]}}
    if silent:
        base["unconfirmed"] = _named(silent)   # Their plugin doesn't acknowledge; update Angelo Link there.
    if not acking:
        return dict(base, acknowledged=[])

    def poll():
        if agent.session_number() != link:   # Another link, or none: this push's answers can't come any more.
            return dict(base, state="unknown", note="The link changed before the engines confirmed the update")
        known = agent.link_facts()
        answers = [(item, facts.preview_ack(known, item[0], version, started)) for item in acking]
        if (any(found is None and item[0] in engine_link.clients() for item, found in answers)
                and time.monotonic() - started < PUSH_WAIT_S):
            return None
        return _answered(base, answers, PUSH_WAIT_S)

    return _pending(poll, PUSH_WAIT_S, "Waiting for the engine", "The live update was sent; waiting for the "
                    "engine to show it.", dict(base, stopped="Stopped waiting; the live update was sent."))


def _answered(base, answers, waited_s, **fields):
    """``base`` with each editor's acknowledgement; the editors that gave none within ``waited_s`` (``no_answer``)
    and those that disconnected first (``left``) are named, never dropped (break-it F4)."""
    result = dict(base, **fields)
    connected = engine_link.clients()
    result["acknowledged"] = [dict(_named([item])[0], code=found["code"],
                                   **({"instances": found["instances"]} if found.get("instances") is not None else {}),
                                   **({"message": found["message"]} if found.get("message") else {}))
                              for item, found in answers if found is not None]
    left = [item for item, found in answers if found is None and item[0] not in connected]
    silent = [item for item, found in answers if found is None and item[0] in connected]
    if left:
        result["left"] = _named(left)
    if silent:
        result["no_answer"], result["no_answer_after_s"] = _named(silent), waited_s
    return result


def _save(window, asset):
    if window is None and not bpy.app.background:
        raise _final(RefusalError("This chat has no window to save from", code="UNSUPPORTED"))
    if not bpy.data.filepath or not assets.same_file(bpy.data.filepath, asset.editable):
        raise _final(RefusalError("The open file is not the linked asset's .blend, so saving it wouldn't update %s"
                                  % asset.ident, code="STALE_CONTEXT"))
    link = agent.session_number()
    try:
        number = agent.save(window)
    except RuntimeError as error:   # Already free of paths (engine_link_agent.save).
        raise RefusalError(facts.plain(error, 300), code="INTERNAL_ERROR") from None
    acking, silent = _editors(asset, live=False)
    started = time.monotonic()
    base = {"save": number, "asset": asset.ident}
    if silent:
        base["unconfirmed"] = _named(silent)

    def poll():
        if agent.session_number() != link:   # Ended or replaced while waiting: never another link's facts.
            return dict(base, state="unknown", note="The link ended before the save was confirmed")
        known = agent.link_facts()
        save, now = known["save"] or {}, time.monotonic()
        if save.get("save") != number:   # A newer save replaced this one's write-back.
            return dict(base, state="superseded", note="A newer save replaced this one; STATUS shows it")
        if save.get("state") == "writing":
            if now - started < WRITE_WAIT_S:
                return None
            return dict(base, state="writing", note="The write-back is still running; STATUS shows when it ends")
        if save.get("state") == "failed":
            return dict(base, state="failed", error=save.get("error", ""))
        answers = [(item, facts.save_ack(known, item[0], number)) for item in acking]
        if (any(found is None and item[0] in engine_link.clients() for item, found in answers)
                and now - save.get("written_at", now) < ACK_WAIT_S):
            return None
        preflight = _preflight(asset, save)   # Read once written, and only this save's (``_describes``).
        return _answered(base, answers, ACK_WAIT_S, state="written",
                         **({"preflight": preflight} if preflight is not None else {}))

    first = poll()
    if first is not None:
        return first
    return _pending(poll, WRITE_WAIT_S + ACK_WAIT_S, "Waiting for the engine",
                    "Saved; waiting for the asset to be written and re-imported in the engine.",
                    dict(base, stopped="Stopped waiting; the save stands and the write-back goes on."))


def execute(window, name, request, controller=None):
    """The tool's answer. Every refusal but INTERNAL_ERROR is raised before the action did anything (nothing sent,
    saved, asked or ended), and says so: a typed plan returns it to the model for a correction instead of ending the
    request (``claude_outcomes._UNCHANGED_WHEN_REFUSED``). A ``_final`` one, which no other request could change while
    the person's state stands, also changed nothing but ends the request with its reason shown: a correction would
    only put another approval card in front of the person (break-it 8-3)."""
    try:
        return _execute(window, name, request, controller)
    except RefusalError as error:
        if error.code != "INTERNAL_ERROR":
            error.partial_changes_possible = False   # refusal_codes.error_result: scene_changed False.
        raise


def _final(error):
    """``error`` marked final in its details (``claude_outcomes.correctable_failure`` returns no correction)."""
    error.details["final"] = True
    return error


def _execute(window, name, request, controller):
    from .engine_link_tool_spec import STATUS
    if name == STATUS:
        return status()
    action = request["action"]
    if action == "OPEN":   # Reads the projects off the main thread (engine_link_tool_open).
        return opening.open_asset(request, controller, OFF, _pending)
    asset = _require_linked()
    if action == "PUSH":
        return _push(window, asset)
    if action == "SAVE":
        return _save(window, asset)
    engine_link.end()
    return {"closed": asset.ident, "engine": _engine(asset.engine),
            "note": "The file stays open in Angelo; the engine shows the asset as last saved."}
