# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The hidden app's side of a hidden-window session (doc/angelo/mcp_headless_window.rst): this process is Angelo,
started by an app's launcher on a working copy, with its window kept off screen.

* ``register()``: take the state folder's GUI lock or end at once (``headless_window_lock``), hide the app
  (``headless_window_appkit``), read the launcher's pipe on a thread (its first line must name this session), and
  step on a timer.
* ``step()``: the pipe's messages (``close``, ``show``, ``decline``), its end, the person's own launch (SIGUSR2:
  reveal) or bringing it up (a Finder or Dock launch, a file opened), SIGTERM, idle, the caps. **Every end** stops
  the turn, withdraws its cards, saves the working copy in place (never the source) and quits (``_finish``);
  changes are also saved as they happen. ``show``: the window comes up; from then on it is the person's window.

Background threads never touch bpy: the pipe's thread only queues lines; the timer acts on the main thread.
"""

import json
import os
import queue
import threading
import time
from pathlib import Path

from .headless_window import ENV_CAPS, ENV_CLIENT, ENV_FLAG, ENV_PIPE, ENV_SESSION, ENV_SOURCE

IDLE_EXIT = 600.0   # Ten minutes without a call from the app, as the background child (external_mcp.rst).
STEP = 1.0
STOP_WAIT = 15.0    # An end waits this long for a stopped turn to settle, then saves what is committed and quits.
SAVE_IDLE = 2.0     # Changes saved this soon once the agent is idle,
SAVE_BUSY = 20.0    # and at least this often while it works: a crash or a kill keeps the edits up to then.
SHOW_TRIES, SHOW_RETRY = 20, 0.25   # The switch back to a regular app, until it takes.
HIDE_TICKS = 40     # Blender's own window setup runs after the add-on registers: keep it hidden through it,
HIDE_STEP = 0.05    # on ticks this short (the macOS proof saw a window flash at one-second ticks).
SESSION_NAMES = (ENV_FLAG, ENV_PIPE, ENV_SESSION, ENV_SOURCE, ENV_CAPS, ENV_CLIENT)
FOREIGN = "Angelo is working for another app without its window; it answers only that app until it ends."
_inbox = queue.Queue()
_state = {"started": None, "last_call": None, "output": None, "saved_at": None, "ending": None, "stopping": None,
          "registered": False, "shown": False, "chats": [], "spent": {}, "changed": False}


def facts(environ=None):
    """The session's environment: ``environ`` when given; else what ``register`` took (and removed from this
    process's environment, so the processes it starts never inherit a session); else ``os.environ``."""
    if environ is not None:
        return environ
    return _state.get("facts") or os.environ


def client():   # The app this session serves (its launcher's label), or None.
    return facts().get(ENV_CLIENT)


def active(environ=None):
    environ = facts(environ)
    return environ.get(ENV_FLAG) == "1" and bool(environ.get(ENV_SESSION))


def session(environ=None):
    """This session's id (the launcher's) while the app is hidden, or None (outside one, or once shown)."""
    environ = facts(environ)
    return environ.get(ENV_SESSION) if active(environ) and not _state["shown"] else None


def caps(environ=None):
    """The session's caps: the defaults, narrowed by the launcher (``headless_window.caps_from``), never widened."""
    from .headless_window import caps_from
    try:
        return caps_from(json.loads(facts(environ).get(ENV_CAPS) or "{}"))
    except ValueError:
        return caps_from(None)


def foreign(label, environ=None):
    """Whether ``label`` is another app than the one this hidden session serves (it answers that one only)."""
    environ = facts(environ)
    return session(environ) is not None and label != environ.get(ENV_CLIENT)


def touch(clock=time.monotonic):   # An app's call: the idle limit counts from the last.
    _state["last_call"] = clock()


def arm():
    """The app sent Angelo's agent a message: its edits count as changes from now on (review 5, NIT)."""
    _state["armed"] = True


# The launcher's pipe ------------------------------------------------------------------------------------------------

def _read_pipe(fd, expected):
    """Queue the launcher's messages; its first line must name this session, else nothing from it counts."""
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as pipe:
            first = True
            for line in pipe:
                try:
                    message = json.loads(line)
                except ValueError:
                    message = None
                if first:
                    first = False
                    if not isinstance(message, dict) or message.get("session") != expected:
                        break   # Not the launcher that started this session: the session ends.
                    continue
                if isinstance(message, dict):
                    _inbox.put(message)
    except OSError:
        pass
    _inbox.put({"pipe_end": True})


def handle(message):
    """What one message from the launcher asks: ``("shown"|"closed"|"decline"|None, value)``."""
    if message.get("cancel_show"):
        return "cancel_show", None
    if message.get("show"):
        return "shown", message.get("by")
    if message.get("pipe_end") or message.get("close"):
        return "closed", message.get("by")
    if isinstance(message.get("decline"), str):
        return "decline", message["decline"]
    return None, None


# Use and caps -------------------------------------------------------------------------------------------------------

def used(controllers=(), clock=time.monotonic):
    """``{minutes, usd, tokens}`` this session has used: its time, and the spend of every conversation it had (a new
    chat counts with the old one). A ledger that can't be read counts as past every spend cap."""
    minutes = (clock() - _state["started"]) / 60.0 if _state["started"] else 0.0
    spent = _state["spent"]   # Per chat, kept: a chat Angelo closed since still counts what it spent.
    try:
        from . import goal_probes
        for controller in controllers:
            more_tokens, more_usd = goal_probes.usage_now(controller)   # A running request's usage too (P1 audit).
            spent[controller.state["id"]] = (int(more_tokens), float(more_usd))
    except Exception:   # noqa: BLE001 - unreadable spend ends the session (break-it review 2, S3), never zero.
        return {"minutes": round(minutes, 2), "usd": float("inf"), "tokens": float("inf")}
    tokens, usd = sum(value[0] for value in spent.values()), sum(value[1] for value in spent.values())
    return {"minutes": round(minutes, 2), "usd": round(usd, 4), "tokens": tokens}


def cap_reached(limits, spent):
    """The cap ``spent`` reached (``minutes``, ``usd``, ``tokens``), or None."""
    for key in ("minutes", "usd", "tokens"):
        if limits.get(key) is not None and spent.get(key, 0) >= limits[key]:
            return key
    if spent.get("usd") == float("inf"):
        return "usd"
    return None


def state(controller=None):
    """The session's state for ``angelo_read`` (external_agent): caps, what is used, the output, why it ends."""
    if session() is None:
        return None
    output = _state["output"]
    controllers = _state.get("controllers") or ([controller] if controller is not None else [])
    return {"hidden": True, "caps": caps(), "used": used(controllers), "output": output and Path(output).name,
            "ending": _state["ending"]}


# Saving beside the source -------------------------------------------------------------------------------------------

def save(bpy, source=None, clock=time.monotonic):
    """The open document saved where it is: the launcher's working copy beside the source, already named as the
    session's output (``<name>.angelo-<n>.blend``, ``hidden_copy.claim``), never the source. Saving in place
    leaves nothing unsaved, so the quit has nothing to ask in a window nobody sees. The launcher removes the copy only
    while it is still the unedited one it made. Returns the output path."""
    source = Path(source or facts().get(ENV_SOURCE, ""))
    target = Path(bpy.data.filepath or "")
    if not target.name or not source.name or target.resolve() == source.resolve() or target.parent != source.parent:
        raise RuntimeError("Refusing to save: the open file is not this session's copy beside the source")
    preferences = bpy.context.preferences
    paths, prefs_dirty = preferences.filepaths, getattr(preferences, "is_dirty", False)
    versions, paths.save_version = paths.save_version, 0   # No .blend1 beside the person's file; theirs restored.
    try:
        result = bpy.ops.wm.save_mainfile(filepath=str(target), check_existing=False)
    finally:
        paths.save_version = versions
        if hasattr(preferences, "is_dirty"):
            preferences.is_dirty = prefs_dirty   # The person's preferences are not changed by this save.
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not save " + target.name)
    _state["output"], _state["saved_at"], _state["changed"] = str(target), clock(), False
    return str(target)


def changed(bpy):
    """Whether the document has changes to save: once the agent has worked, Blender's dirty flag or an update the
    handler marked (an agent's operator calls can leave ``is_dirty`` False)."""
    return bool(_state.get("armed") and (bpy.data.is_dirty or _state["changed"]))   # Never an unedited copy.


DATA_TYPES = frozenset({"SCENE", "KEY", "PARTICLE", "OBJECT", "MESH", "CURVE", "CURVES", "MATERIAL", "NODETREE",
                        "LIGHT", "CAMERA", "WORLD",
                        "ARMATURE", "LATTICE", "GREASEPENCIL", "POINTCLOUD", "VOLUME", "IMAGE", "TEXTURE",
                        "COLLECTION", "ACTION", "METABALL", "SPEAKER", "LIGHT_PROBE", "TEXT", "FONT"})


def _mark_changed(_scene=None, depsgraph=None):
    """``depsgraph_update_post``: only marks (the timer saves), and only once the agent has worked in this session
    and an update changed data (review 4, S-4): never the file's own load evaluation, a selection, a frame change
    or a viewport redraw."""
    if not _state.get("armed") or depsgraph is None:
        return
    try:
        for update in depsgraph.updates:
            data = update.is_updated_geometry or update.is_updated_shading or update.is_updated_transform
            if data and getattr(update.id, "id_type", None) in DATA_TYPES:
                _state["changed"] = True
                return
    except (AttributeError, ReferenceError):
        return


_mark_changed._bpy_persistent = True   # Kept across a file load, as bpy.app.handlers.persistent does.


def _try_save(bpy, clock, when):
    try:
        save(bpy, clock=clock)
    except Exception as error:   # noqa: BLE001 - the next save, or the end's, tries again.
        _state["saved_at"] = clock()
        print("Angelo: the hidden-window session could not save (%s):" % when, error)
        return False
    return True


# The timer ----------------------------------------------------------------------------------------------------------

def _busy(controllers):   # A turn runs in one of the session's conversations.
    return any(getattr(controller, "busy", False) for controller in controllers)


def _card_waits(controllers):
    """A card waits for the person: what its call staged is not approved, so nothing is saved meanwhile (S-f)."""
    from .approval_wait import awaiting
    return any(awaiting(controller) is not None for controller in controllers)


def withdraw(controller):
    """Decline every card the conversation shows (the session is ending: nobody will answer them)."""
    from .approval_wait import awaiting
    for _ in range(64):
        call = awaiting(controller)
        if call is None:
            return
        controller.decline_tool(call.id)
        if awaiting(controller) is call:
            return   # It could not be declined now: the stopped turn settles it.


def decline(controllers, card):
    """The app's decline of the card it read (``headless_window_relay.card_id``); the only answer an app gives."""
    from . import headless_window_relay
    from .approval_wait import awaiting
    for controller in controllers:
        call = awaiting(controller)
        if call is not None and headless_window_relay.card_id(call) == card:
            controller.decline_tool(call.id)
            headless_window_relay.record(controller, call, client() or "the app")
            return True
    return False


def _finish(bpy, controllers, now, show=None):
    """Every end: stop the turn and withdraw its cards once, let it settle (bounded), save, quit."""
    if _state["stopping"] is None:
        _state["stopping"] = now
        print("Angelo: the hidden-window session ends (%s)." % _state["ending"])   # For the app's log and support.
        for controller in controllers:
            withdraw(controller)
            if getattr(controller, "busy", False):
                controller.stop()   # As the person's Stop: committed edits stay, Undo keeps them.
    if _busy(controllers) and now - _state["stopping"] < STOP_WAIT:
        return STEP
    if changed(bpy) and not _try_save(bpy, lambda: now, "at its end"):
        # Never a silent loss (review 3, S-a): the window comes up for the person with the unsaved work.
        _state["ending"] = None
        _become_window(show, "save_failed")
        return None
    bpy.ops.wm.quit_blender()   # The lock goes with the process (its flock), never before (review 5, 3).
    return None


def _become_window(show, why):
    """The person's window from now on. In this order (review 4, S-1): the lock's record names no session first (so
    nothing treats it as a hidden session again), then a second SIGUSR2 is ignored and SIGTERM is Blender's own, the
    session's change handler and App Nap activity go, the cards' shields restart, and the window comes up."""
    import signal
    _state["shown"], _state["shown_by"] = True, why
    from . import approval_arming, headless_window_appkit, headless_window_lock
    try:
        headless_window_lock.mark(None)   # Holding the state folder as the person's window does, with no session.
        marked = True
    except OSError as error:
        marked = False   # The record still names the session: SIGTERM keeps its saving handler (review 5, NIT).
        print("Angelo: could not mark the state folder's lock as a window's:", error)
    handlers = tuple((getattr(signal, n), signal.SIG_IGN) for n in ("SIGUSR2", "SIGUSR1") if hasattr(signal, n))
    for number, handler in handlers + (((signal.SIGTERM, signal.SIG_DFL),) if marked else ()):
        try:
            signal.signal(number, handler)
        except (ValueError, OSError):
            pass
    _unhook()
    try:
        headless_window_appkit.end_activity()
    except Exception:   # noqa: BLE001 - App Nap applies again at the latest when the app quits.
        pass
    approval_arming.restart()   # Cards drawn while hidden spent their shield: they start again in view.
    _bring_up(show or _show)


def _bring_up(show, tries=SHOW_TRIES):
    """Bring the window up; while the switch back to a regular app doesn't take, try again on a short tick
    (review 5, 2)."""
    try:
        done = show() is not False
    except Exception as error:   # noqa: BLE001 - the session is the person's either way: tried again below.
        print("Angelo: could not bring the window up:", error)
        done = False
    if done or tries <= 1:
        return done
    try:
        import bpy
        bpy.app.timers.register(lambda: _bring_up(show, tries - 1) and None, first_interval=SHOW_RETRY)
    except (ImportError, AttributeError):
        pass
    return False


def _loaded(_scene=None, _data=None):
    """``load_post``: only marks. The session's own copy is the first saved file loaded (Blender's unsaved startup file
    comes before it); any other is one the person opened (a Finder double-click): the session becomes their window."""
    try:
        import bpy
        path = bpy.data.filepath
    except (ImportError, AttributeError):
        return
    if path and _state.get("copy") is None:
        _state["copy"] = path
    elif path and path != _state["copy"] and not _state["shown"]:
        _inbox.put({"show": True, "by": "person"})


_loaded._bpy_persistent = True


def _unhook():
    try:
        import bpy
        if _mark_changed in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_mark_changed)
        if _loaded in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_loaded)
    except (ImportError, AttributeError, ValueError):
        pass


def step(bpy, controllers=(), clock=time.monotonic, show=None, visible=False):
    """Once a second on the main thread. Returns the next interval, or None once the session ended (saved and quit,
    or shown: the person's window from then on)."""
    controllers = [controller for controller in controllers if controller is not None]
    now = clock()
    messages = []
    while True:
        try:
            messages.append(handle(_inbox.get_nowait()))
        except queue.Empty:
            break
    cancel = max((index for index, (what, _value) in enumerate(messages) if what == "cancel_show"), default=-1)
    if cancel >= 0:   # The person chose Quit after their launch asked for a reveal: that reveal is withdrawn.
        messages = [(what, value) for what, value in messages[:cancel] if what != "shown"] + messages[cancel + 1:]
    return step_messages(bpy, controllers, clock, show, visible, messages, now)


def step_messages(bpy, controllers, clock, show, visible, messages, now):
    for what, value in messages:
        if _state["ending"] is not None:
            break
        if what == "shown":
            _become_window(show, value or "app")
            return None
        if what == "closed":
            _state["ending"] = "handed_over" if value == "person" else value or "closed"
        elif what == "decline":
            touch(clock)
            decline(controllers, value)
    if _state["ending"] is None and visible:   # The person brought it up themselves (the Dock, a switcher).
        _become_window(show, "person")
        return None
    if _state["ending"] is None and _state["last_call"] is not None and now - _state["last_call"] >= IDLE_EXIT:
        _state["ending"] = "idle"
    if _state["ending"] is None:
        reached = cap_reached(caps(), used(controllers, clock))
        if reached:
            _state["ending"] = "cap_" + reached
    if _state["ending"] is not None:
        return _finish(bpy, controllers, now, show)
    if _busy(controllers):
        arm()   # The agent worked: its edits count as changes from now on.
    since = now - (_state["saved_at"] or 0.0)
    if (changed(bpy) and since >= (SAVE_BUSY if _busy(controllers) else SAVE_IDLE)
            and not _card_waits(controllers)):
        _try_save(bpy, clock, "as it works")
    return STEP


def _access_on(ui):   # Whether the person allowed apps to reach Angelo (Preferences > AI Providers).
    try:
        prefs = ui.preferences()
    except Exception:  # noqa: BLE001 - unreadable counts as off.
        return False
    return bool(prefs is not None and getattr(prefs, "allow_local_cli", False))


def _show():
    from . import headless_window_appkit
    headless_window_appkit.show_app()


def register(environ=None):
    """Start the session in this process (only when its launcher's environment says so)."""
    environ = os.environ if environ is None else environ
    if not active(environ) or _state["registered"]:
        return False
    _state["registered"] = True
    _state["facts"] = {name: environ[name] for name in SESSION_NAMES if name in environ}
    for name in SESSION_NAMES:
        environ.pop(name, None)
    _state["started"] = _state["last_call"] = time.monotonic()
    import signal
    # The person's own launch reveals this session (headless_window_lock.hand_over): it becomes their window; their
    # Quit in its dialog withdraws that reveal (SIGUSR1).
    signal.signal(signal.SIGUSR2, lambda *_args: _inbox.put({"show": True, "by": "person"}))
    signal.signal(signal.SIGUSR1, lambda *_args: _inbox.put({"cancel_show": True}))
    signal.signal(signal.SIGTERM, lambda *_args: _inbox.put({"close": True, "by": "terminated"}))
    from . import headless_window_appkit, headless_window_lock, ui
    try:
        held = headless_window_lock.take(ui.state_directory(), session(), wait=0, app=client())
    except OSError as error:
        held = False
        print("Angelo: the hidden-window session could not take its state folder's lock:", error)
    if not held:   # Another Angelo holds the state folder: end before doing anything (fails closed).
        _state["ending"] = "window_running"
    try:
        headless_window_appkit.hide_app()
    except Exception as error:   # noqa: BLE001 - shown is worse than hidden, never fatal: the session still works.
        print("Angelo: the hidden-window session could not hide the app:", error)
    try:   # A Finder or Dock launch reopens this running app (no second process): the same reveal (review 5, 1).
        headless_window_appkit.on_reopen(lambda: _inbox.put({"show": True, "by": "person"}))
    except Exception as error:   # noqa: BLE001 - the person's own launch from a terminal still reveals it (SIGUSR2).
        print("Angelo: the hidden-window session could not take reopen events:", error)
    fd = _state["facts"].get(ENV_PIPE)
    if fd and fd.isdigit():
        try:
            os.set_inheritable(int(fd), False)   # Nothing this app starts holds the launcher's pipe.
        except OSError:
            pass
        threading.Thread(target=_read_pipe, args=(int(fd), session()), daemon=True, name="AngeloHiddenPipe").start()
    else:
        _inbox.put({"pipe_end": True})   # No launcher to answer to: nothing keeps the session.
    import bpy
    from . import edit_tool_hold, headless_window_relay

    @edit_tool_hold.guarded   # A save never runs while an operation is half-made: it waits, as Angelo's work does.
    def tick():
        if _state["shown"]:
            return None
        if _state.get("copy") is None and bpy.data.filepath:
            _state["copy"] = bpy.data.filepath   # The session's own file, if it loaded before ``_loaded`` was added.
        if not _state.get("access_checked"):   # Angelo's preferences exist only once the add-on finished registering.
            _state["access_checked"] = True
            if _state["ending"] is None and not _access_on(ui):   # The person's own switch: off ends at once.
                _state["ending"] = "access_off"
        visible = False
        if _state.setdefault("hides", 0) < HIDE_TICKS:
            _state["hides"] += 1
            try:
                headless_window_appkit.hide_app(keep_awake=False)
            except Exception:   # noqa: BLE001 - reported once at register.
                pass
        else:
            try:   # After start-up, only the person brings it up (the Dock, a switcher): theirs from then on.
                visible = not headless_window_appkit.hidden_state()["hidden"]
            except Exception:   # noqa: BLE001 - unknown counts as still hidden.
                visible = False
        current = headless_window_relay.app_controller(ui)
        if current is not None and current.state["id"] not in _state["chats"]:
            _state["chats"].append(current.state["id"])   # A new chat counts with the session's earlier ones.
        _state["controllers"] = [ui._controllers[chat] for chat in _state["chats"] if chat in ui._controllers]
        interval = step(bpy, _state["controllers"], visible=visible)
        return HIDE_STEP if interval is not None and _state["hides"] < HIDE_TICKS else interval
    bpy.app.timers.register(tick, first_interval=0.0, persistent=True)
    if _mark_changed not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_mark_changed)
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)
    return True
