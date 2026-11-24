# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app session's live page (2.0.1, doc/angelo/external_mcp.rst, "Live session page"). Main thread.

When a connected app sends Angelo's agent a message (``external_agent``), its result carries ``live_page``: a
read-only page on this computer the person opens in their app's browser, and the app may open too. It shows the scene
in 3D (the live web preview's exporter, held for pages apart from the person's own preview), the latest views the agent
got, the status, the current step and the stream, the goal's round and score, the time, this conversation's tokens and
cost, and the result.

``Pages`` keeps one page per conversation (``session_page_site``, a site on Angelo's one local server) and publishes
what it shows (``session_page_state.build``) when the conversation changed, at most twice a second and only while a
page is open, so it costs the main thread nothing while nobody looks. The link dies when the conversation ends (the
app disconnects or reconnects with a new key, it moves to a fresh conversation, the file changes, the chat is deleted,
apps' access goes off or the app's access is revoked: ``session_on``, the one test every path here uses) or the
person stops sharing the app's pages in its chat; no message of that app brings a page back, in this conversation or
its next, until the person shares again, a choice kept across restarts (``STOPPED_FILE``). Nothing here can break an app's message or the listener: a page's defect ends that page.
"""

import sys
import time

from . import session_page_ended as page_ended, session_page_pending, swallowed_errors
from .ui_text import n_

INTERVAL = 0.5          # Seconds between publishes of a changing conversation.
SCENE_QUIET = 30.0      # Pages let go of the exporter this long after the last page closed.
STOPPED_FILE = "external-live-pages.json"   # The apps whose session pages the person stopped sharing.
PREVIEW_REQUEST = {"meshes": "AUTO", "textures": "AUTO", "merge": True, "selection_only": False}

PAGE_TEXT = {   # Key -> English; translated as the page's state is built (``_strings``).
    "title": n_("Live session"),
    "with_app": n_("Working with {app}"),
    "read_only": n_("Read-only view of this session. Approvals, Stop and every change stay in Angelo."),
    "status_ready": n_("Ready"), "status_running": n_("Working"),
    "status_waiting_for_approval": n_("Waiting for your approval in Angelo"),
    "status_waiting_for_person": n_("Waiting while you use a tool in Angelo"),
    "status_stopping": n_("Stopping"), "status_done": n_("Done"),
    "closed": n_("This live page has ended: the session ended or sharing was stopped in Angelo."),
    "offline": n_("Angelo isn't answering. The page reconnects when it does."),
    "step": (n_("Now", "Live page"), "Live page"), "stream": n_("Progress"),   # i18n: a catalogue context, never shown
    "result": n_("Result"), "views": n_("Latest views"), "no_views": n_("Views the agent takes appear here."),
    "view_unavailable": n_("Unavailable"),   # A view the page stopped waiting for (its tile's title).
    "scene_loading": n_("Loading the scene…"),
    "scene_none": n_("The scene appears here once Angelo exports it."),
    "scene_unavailable": n_("This browser can't draw the 3D scene. Everything else here still updates."),
    "goal": n_("Goal"), "round": n_("Round {round}"), "score": n_("Score {score}%"), "best": n_("Best {score}%"),
    "pass_at": n_("Passes at {score}%"),
    "elapsed": n_("Session {time}"), "turn_elapsed": n_("This step {time}"), "usage": n_("Usage"),
    "tool_queued": n_("Queued"), "tool_running": n_("Running"), "tool_waiting_for_approval": n_("Waiting for approval"),
    "tool_done": n_("Done"), "tool_failed": n_("Failed"), "tool_stopped": n_("Stopped"),
    "tool_declined": n_("Declined"),
}
SHARED = n_("{app} can show this session on a live page in your browser. Only this computer can open it, and it "
            "shows the scene, the progress and the result, never files or other chats.")
STOPPED = n_("You stopped sharing this session's live page. The link no longer opens.")


class Pages:
    """One live page per conversation, its publishes and its end. ``make_site``: a new ``SessionSite``; ``build``:
    the page's state for a conversation; ``scene_version``: the exporter's counter; ``stopped``: the apps (labels)
    whose sessions the person stopped sharing, a choice about the app that holds for its next conversation, a new chat
    or a move alike (``on_stopped(label, stopped)`` keeps each change); ``on_refused(controller)``: a conversation
    whose app's sharing is stopped asked for its page; ``retry(controller, entry)``: a tick with no change reads
    what the page waits on (``entry["waiting"]``, ``session_page_pending``) when due, True when what it shows
    changed."""

    def __init__(self, make_site, build, clock=time.monotonic, scene_version=lambda: 0, on_shared=None, stopped=(),
                 on_stopped=None, on_refused=None, retry=None):
        self._make_site, self._build, self._clock = make_site, build, clock
        self._scene_version, self._on_shared, self._on_stopped = scene_version, on_shared, on_stopped
        self._on_refused, self._retry = on_refused, retry
        self._entries, self._stopped = {}, set(stopped)

    def link(self, controller, label, app="", key=None):
        """The page's URL for ``controller``'s conversation (made on first use, and again for a new key: another
        connection), or None while the person stopped sharing the app's sessions."""
        identity = controller.state["id"]
        if label in self._stopped:
            if self._on_refused is not None:
                self._on_refused(controller)
            return None
        entry = self._entries.get(identity)
        if entry is not None and entry["key"] != key:
            self.end(identity)
            entry = None
        if entry is None:
            site = self._make_site()
            entry = self._entries[identity] = {"site": site, "label": label, "app": app or label, "key": key,
                                               "seen": None, "at": float("-inf"), "started": self._clock(),
                                               "waiting": session_page_pending.Pending(self._clock)}
            self._publish(entry, controller, first=True)
            if self._on_shared is not None:
                self._on_shared(controller, entry)
        return entry["site"].url

    def site(self, identity):
        entry = self._entries.get(identity)
        return entry["site"] if entry else None

    def entry(self, identity):
        return self._entries.get(identity)

    def shared(self, label):
        """Whether the person allows the app's session pages (not stopped)."""
        return label not in self._stopped

    def live(self, identity):
        return identity in self._entries

    def stop_sharing(self, label):
        """The person's Stop sharing: the app's links die now, and no message of the app brings one back, in this
        conversation or its next, until they share again. True when a page ended."""
        if label not in self._stopped:
            self._stopped.add(label)
            self._kept(label, True)
        ended = [identity for identity, entry in self._entries.items() if entry["label"] == label]
        for identity in ended:
            self.end(identity)
        return bool(ended)

    def share_again(self, label):
        """The person's Share again: the app's next message makes a new link (a new token)."""
        if label in self._stopped:
            self._stopped.discard(label)
            self._kept(label, False)

    def _kept(self, label, stopped):
        if self._on_stopped is not None:
            self._on_stopped(label, stopped)

    def end(self, identity):
        entry = self._entries.pop(identity, None)
        if entry is not None:
            entry["site"].close()
        return entry is not None

    def end_all(self):
        for identity in list(self._entries):
            self.end(identity)

    def viewed(self):
        return any(entry["site"].viewers > 0 for entry in self._entries.values())

    def pump(self, lookup):
        """Each page's session still on (``lookup(conversation)`` its controller, else None: the page ends), and a
        changed conversation published to an open page, at most every ``INTERVAL`` (the first page that looks gets
        a full publish: the send path's skipped the pictures). A page whose build fails ends, alone."""
        for identity in list(self._entries):
            entry = self._entries[identity]
            controller = lookup(identity)
            if controller is None:
                self.end(identity)
                continue
            if entry["site"].viewers <= 0:
                continue
            seen = (controller.version, self._scene_version())
            if self._clock() - entry["at"] < INTERVAL:
                continue
            # Unchanged: published again only when what the page shows changed (its waits are bounded).
            unchanged = seen == entry["seen"]
            if unchanged and self._retry is None:
                continue
            try:
                if unchanged and not self._retry(controller, entry):
                    continue
                self._publish(entry, controller)
            except Exception as error:  # noqa: BLE001 - a page's defect ends that page, never the session.
                page_ended.failed(identity, "it stopped after an error", error, _say)   # i18n: the app's English
                self.end(identity)

    def _publish(self, entry, controller, first=False):
        if not first:   # The send path's publish counts as nothing seen: the first viewer gets it all.
            entry["seen"], entry["at"] = (controller.version, self._scene_version()), self._clock()
        entry["site"].publish(self._build(controller, entry))


# Angelo's own pages, on the main thread ----------------------------------------------------------------------------

_pages = None
_scene = {"held": False, "quiet_since": None}


def pages():
    """The pages, always bound to the apps' host's own store of the person's choices (a notice drawn before any
    send after a restart reads it too); None while there is no host: no page, and no choice to show."""
    global _pages
    from . import external_host
    host = external_host._host
    if host is None:
        return None
    directory = str(host.env.state_directory())
    if _pages is None or _pages.directory != directory:
        if _pages is not None:
            _pages.end_all()
        _pages = Pages(make_site=_make_site, build=_build, scene_version=_scene_version, on_shared=_said_shared,
                       stopped=_read_stopped(directory), on_refused=_said_stopped,
                       retry=session_page_pending.retry,
                       on_stopped=lambda label, stopped: _keep_stopped(directory, label, stopped))
        _pages.directory = directory
    return _pages


def app_key(host, label):
    """The connected app's current key (a reconnect rotates it), or None when it is not connected."""
    entry = host._app_keys.get(label)
    return entry[1] if entry is not None else None


def session_on(host, conversation, label, key=None):
    """Whether an app's session in ``conversation`` goes on, the one test every path here uses: apps' access on
    (``host``, the ``ExternalHost``), the app still connected (with ``key`` when given: a rotated key is another
    connection) and not revoked (``external_access``: the one path that ends an app's access), ``conversation`` its
    current one, and that chat still there, not closing and valid for the file the window has open."""
    from . import external_access
    if host.server is None or label not in host._app_keys:
        return False
    if key is not None and app_key(host, label) != key:
        return False
    if host.mapping.get(label) != conversation:
        return False
    controller = host.env.controllers().get(conversation)
    if controller is None or getattr(controller, "_closing", False) or controller.document_invalid \
            or external_access.revoked(controller):
        return False
    return controller.document == host.env.document_key()


def link(controller, label, app=""):
    """For ``external_agent``: the conversation's live page URL, or None while the person stopped sharing it, the
    session is not on, or the page could not start (an app's message never fails for it)."""
    from . import external_host
    host = external_host._host
    try:
        if host is None or not session_on(host, controller.state["id"], label):
            return None
        url = pages().link(controller, label, app, key=app_key(host, label))
        if url:
            page_ended.started(controller.state["id"])
        return url
    except Exception as error:  # noqa: BLE001 - the page is optional: the message went whatever it raised.
        if _pages is not None:
            _pages.end(controller.state["id"])   # Never half a page listening.
        page_ended.failed(controller.state["id"], "the page could not start", error,   # i18n: the app's English
                          lambda identity, template: _say(identity, template, controller=controller))
        return None


def close(identity, why=""):
    """The conversation's session ended (``external_agent``: a move, a new chat, access off): its link dies now."""
    del why   # Said by the page itself ("this live page has ended").
    if _pages is not None:
        _pages.end(identity)


def stop_sharing(controller):
    """The person's press in ``controller``'s conversation: its app's pages end and stay off (``Pages``)."""
    current = pages()
    if current is not None:
        current.stop_sharing(getattr(controller, "external_client", ""))
        _say(controller.state["id"], STOPPED, controller=controller)


def share_again(controller):
    current = pages()
    if current is not None:
        current.share_again(getattr(controller, "external_client", ""))


def is_live(controller):
    """Whether ``controller``'s conversation has a live page now (its notice offers Stop sharing)."""
    current = pages()
    return current is not None and current.live(controller.state["id"])


def page_url(controller):
    """``controller``'s conversation's live page link while it is live, else None (the notice's Open)."""
    current = pages()
    site = current.site(controller.state["id"]) if current is not None else None
    return site.url if site is not None else None


def is_stopped(controller):
    """Whether the person stopped sharing the pages of ``controller``'s app (its notice offers Share again)."""
    current = pages()
    return current is not None and not current.shared(getattr(controller, "external_client", ""))


def pump_safely(host):
    """``external_host.pump``: pages whose session ended close; open pages get the conversation's changes and the
    scene. Apart from the listener: any defect here ends every page and is printed."""
    try:
        pump(host)
    except Exception as error:  # noqa: BLE001 - pages are optional; the apps' listener goes on.
        identities = list(_pages._entries) if _pages is not None else []
        for identity in identities:   # Each says so: the app (``live_page_ended``) and the person (its notice).
            page_ended.failed(identity, "it stopped after an error", error, _say)   # i18n: the app's English
        if not identities:
            swallowed_errors.report("live session pages ended after an error", error)   # i18n: a log line
        if _pages is not None:
            _pages.end_all()
        _release_scene(force=True)


def pump(host):
    if _pages is None:
        return
    if host is None or host.server is None:   # Apps' access is off: no session goes on, so no page does.
        _pages.end_all()
        _release_scene(force=True)
        return
    controllers = host.env.controllers()

    def lookup(identity):
        entry = _pages.entry(identity)
        if entry is None or not session_on(host, identity, entry["label"], entry["key"]):
            return None
        return controllers.get(identity)
    _pages.pump(lookup)
    _feed_scene(host)


def _read_stopped(directory):
    """The apps whose session pages the person stopped sharing, as kept in ``directory`` (the apps' state)."""
    import json
    from pathlib import Path
    from . import shared_replace
    path = Path(directory) / STOPPED_FILE
    try:
        data = json.loads(shared_replace.read_text(path)) if path.is_file() else {}
    except (OSError, ValueError, UnicodeError):
        return []
    apps = data.get("apps") if isinstance(data, dict) else None
    return sorted({item for item in apps if isinstance(item, str)}) if isinstance(apps, list) else []


def _keep_stopped(directory, label, stopped):
    """One change of the person's choice, merged with the file as it is now (another Angelo may have written it):
    kept whole and owner-only, written where C allows (``owned_paths``); a failed write is printed (the choice still
    holds until Angelo quits)."""
    import json
    import os
    import uuid
    from pathlib import Path
    from . import owned_paths, shared_replace
    apps = set(_read_stopped(directory))
    (apps.add if stopped else apps.discard)(label)
    path = Path(directory) / STOPPED_FILE
    temporary = path.with_name("." + STOPPED_FILE + "." + uuid.uuid4().hex[:12] + ".tmp")
    try:
        owned_paths.require_writable(path)   # C first: nothing is made beside a target it refuses.
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"apps": sorted(apps)}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        shared_replace.replace(temporary, path)
    except OSError as error:
        print("Angelo: the live pages' sharing choice could not be saved:", error)
    finally:
        shared_replace.discard(temporary)


def _make_site():
    from .session_page_site import SessionSite
    from .team_page_art import WORDMARK
    return SessionSite(marks={"<!--WORDMARK-->": WORDMARK}, extra_files=_fonts())


def _fonts():
    """Blender's bundled Inter, as the live preview serves it (``web_preview._fonts``); none without Blender."""
    import os
    try:
        import bpy
        folder = bpy.utils.system_resource("DATAFILES", path="fonts") or ""
    except (ImportError, AttributeError, TypeError):
        return {}
    path = os.path.join(str(folder), "Inter.woff2")
    return {"fonts/Inter.woff2": path} if os.path.isfile(path) else {}


def _strings():
    from .ui_text import iface_, is_rtl
    # Arabic catalogues hold Blender's visual form (a browser lays text out); a pair carries its context (S-3).
    pairs = {key: text if isinstance(text, tuple) else (text, None) for key, text in PAGE_TEXT.items()}
    return {key: text if is_rtl() else iface_(text, context) for key, (text, context) in pairs.items()}


def _usage(controller, entry):
    """This conversation's tokens and cost only (never a plan's name or a plan's window: account-wide), computed
    when the ledger moved or a request runs, kept per page."""
    from . import usage_meter, usage_view
    ledger = usage_meter.UsageLedger.shared(controller.telemetry.directory)
    live = usage_meter.in_progress(getattr(controller, "_request_metrics", None), controller.state["provider"]) \
        if controller.busy else None
    signature = (ledger.revision, bool(live) and repr(sorted(live.items())))
    if entry.get("usage_seen") == signature:
        return entry["usage"]
    record = ledger.chat(controller.state["id"]) or {}
    bar = usage_view.bar(usage_meter.combined([record.get("modes"), live]), controller.state["provider"], None,
                         live=bool(live))
    entry["usage_seen"], entry["usage"] = signature, (
        {"tokens": bar["tokens"], "money": bar["money"]} if bar else None)
    return entry["usage"]


def _build(controller, entry):
    from . import external_host, goal_store, session_page_state
    from .external_agent import conversation_state
    from .external_requests import default_roots
    host = external_host._host
    roots = tuple(host.env.private_roots()) + default_roots() if host is not None else default_roots()
    started = getattr(controller, "turn_started", None)
    state = session_page_state.build(
        controller.state.get("messages") or [], conversation=controller.state["id"], version=controller.version,
        turn=conversation_state(controller)["turn"], goal=goal_store.cached_goal(controller),
        usage=_usage(controller, entry), now=time.monotonic(), started=entry["started"],
        turn_started=started[1] if started else None, app=entry["app"], roots=roots,
        scene_version=_scene_version(), strings=_strings())
    return session_page_pending.shown(controller, entry, state)


def _preview_server():
    """The live preview's server while it exports, read only once Blender loaded the exporter (the app always has;
    without Blender nothing exports, and a page shows no scene)."""
    exporter = sys.modules.get(__package__ + ".web_preview")
    return exporter._state["server"] if exporter is not None else None


def _scene_version():
    server = _preview_server()
    return server.version if server is not None else 0


def _feed_scene(host):
    """The 3D view's scene while a page is open: the exporter held for pages (``web_preview.hold``), which exports at
    its timer under its own gates and budget, never in this tick; the person's preview is never touched."""
    from . import web_preview
    if _pages.viewed():
        _scene["quiet_since"] = None
        if not _scene["held"]:
            window = host.env.ui.main_window() if hasattr(host.env, "ui") else None
            if window is None:
                return
            web_preview.hold(web_preview.PAGES, window, PREVIEW_REQUEST)
            _scene["held"] = True
        server = _preview_server()
        for entry in _pages._entries.values():
            entry["site"].scene_feed = server.scene_bytes if server is not None else None
    else:
        _release_scene()


def _release_scene(force=False):
    """Pages let go of the exporter ``SCENE_QUIET`` after the last page closed (at once with ``force``)."""
    if not _scene["held"]:
        return
    from . import web_preview   # Blender's: only once pages hold the exporter.
    now = time.monotonic()
    if _scene["quiet_since"] is None and not force:
        _scene["quiet_since"] = now
        return
    if force or now - _scene["quiet_since"] >= SCENE_QUIET:
        _scene.update(held=False, quiet_since=None)
        web_preview.release(web_preview.PAGES)


def _said_shared(controller, entry):
    _say(controller.state["id"], SHARED, controller=controller, app=entry["app"])


def _said_stopped(controller):
    """A conversation of an app whose sharing is stopped (a new chat, a move) says so once, with Share again."""
    for message in reversed(controller.state.get("messages") or []):
        template = (message.get("i18n") or [None])[0]
        if template in (SHARED, STOPPED):
            if template == STOPPED:
                return
            break
    _say(controller.state["id"], STOPPED, controller=controller)


def _say(identity, template, controller=None, **fields):
    """The conversation's own notice of the page (with Stop sharing, or Share again: ``chat_notices``)."""
    from .ui_text import notice
    if controller is None:
        from . import external_host
        host = external_host._host
        controller = host.env.controllers().get(identity) if host is not None else None
    if controller is not None:
        controller.emit("message", {"role": "system", "notice": True, **notice(template, **fields)})


def unregister():
    global _pages
    if _pages is not None:
        _pages.end_all()
    _release_scene(force=True)
    _pages = None
