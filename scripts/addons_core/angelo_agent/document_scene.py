# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's own scene. ``ui.document_key()`` is (the file's load serial, the scene the main window shows): a chat's
``document`` is that key as it was when the chat first held work about it. Another file (a load renews the serial)
invalidates the chat; another scene of the same file never does (break-it E2E-20 R5-M-1; the maintainer, 2026-10-25:
"On the left, show lead + all subagents. When selected, the selected one show as big view. It should be as simple as
this"). Selecting a session only changes what is displayed: a lead keeps its scene, its workers keep building, and
its merges land in its scene.

Blender runs a chat's tools on the scene the window shows, so while a chat's own scene is not shown, whatever needs
the window waits for it (``hold``): it never runs on the shown scene, nothing is discarded, and no lifetime runs
meanwhile (its calls' expiries and the provider's waits pause: ``tool_deadlines.held``), so the turn is never ended for
it and a thinking turn or a lead coordinating its workers goes on (break-it R5M1-L1 M-1, round 2 R2-M-1).
The person's input to such a chat is refused with the reason (``refuse``, ``refused``): an instruction given while
another scene is shown would otherwise run on the chat's scene later (M-5); a voice session ends with a notice.
Selecting the chat (its tile, the chat switcher) brings its scene back (``select``, ``show``). The chat's own call that
changes the shown scene (a script's ``scene.new``) leaves the window on the chat's scene again (``keep_shown``, M-4),
so every key the chat holds stays its own (R2-M-3). A background Angelo (a worker, a headless host) has no person to
bring a scene back, so there another scene invalidates a chat as in 1.0.5 (``unattended``).
Main thread. No bpy at import.
"""

from .ui_text import n_

# The fact first, then the way back that every route shares (a tile, the chat switcher, the scene selector; a chat
# without workers has no tile). The composer wraps it whole (``chat_composer.hint_rows``).
AWAY = n_("This chat works on scene '{scene}'. Switch to that scene to continue.")
GONE = n_("This chat's scene is no longer in this file. Undo can bring it back; otherwise start a new chat.")
VOICE_AWAY = n_("Voice ended: the window shows another scene than this chat's.")
WAITING = n_("A chat waits for its scene to be shown: {scenes}. Switch back to that scene, or stop the chat.")
# Model-facing, English and facts only (break-it R3-M-2): the call left the window on another scene; it is back.
REVERTED = ("The window shows this chat's scene '{scene}' again after this call: this chat works on its own scene, so "  # i18n: model-facing
            "scene '{other}' stays in the file and this chat's later calls apply to '{scene}'.")


def same_file(a, b):
    """Whether two document keys are the same file (the same load serial)."""
    return a is not None and b is not None and a[0] == b[0]


def away(chat, shown_key):
    """Whether ``chat`` is bound to another scene of the file the window shows: its work waits until it is shown."""
    document = getattr(chat, "document", None)
    return (document is not None and document != shown_key and same_file(document, shown_key)
            and not getattr(chat, "document_invalid", False))


def unattended():
    """A background Angelo (a worker, a headless host): no person shows a scene there, so a chat follows its window."""
    try:
        import bpy
        return bool(bpy.app.background)
    except (ImportError, AttributeError):
        return False


def scene_of(chat):
    """``chat``'s own scene in this file, or None (gone, or no document yet). Session ids are stable across Undo."""
    document = getattr(chat, "document", None)
    if document is None:
        return None
    import bpy
    return next((scene for scene in bpy.data.scenes if scene.session_uid == document[1]), None)


def _why(chat, window, shown_key, translate):
    """``translate``'s message for ``chat`` while its scene is not shown (AWAY, or GONE without its scene), else None."""
    if window is None or shown_key is None or not away(chat, shown_key):
        return None
    scene = scene_of(chat)
    if scene is None:
        return translate(GONE)
    return translate(AWAY, scene=scene.name)


def _shown(window=None, shown_key=None):
    """The main window and its key, read from ``ui`` for what was not passed; (None, None) with no Blender window."""
    if window is None or shown_key is None:
        try:
            from . import ui
            window, shown_key = window or ui.main_window(), shown_key or ui.document_key()
        except (ImportError, AttributeError):   # No Blender window (a host test's operator): nothing is away.
            return None, None
    return window, shown_key


def refusal(chat, window=None, shown_key=None):
    """The one message a person's input to ``chat`` gets while its scene is not shown (``Text``), else None."""
    from .display_text import Text
    return _why(chat, *_shown(window, shown_key), Text) if chat is not None else None


def notice(chat):
    """The composer's words while ``chat``'s scene is not shown (display only, in the person's language), else None."""
    from .ui_text import iface_
    return _why(chat, *_shown(), iface_) if chat is not None else None


def refused(operator, chat, window=None):
    """A window action of the person's on ``chat`` (approve, apply, accept, discard): True, with the reason reported in
    the person's language, while its scene is not shown, so nothing runs on the shown scene and nothing is discarded."""
    from .ui_text import rpt_
    why = _why(chat, *_shown(window), rpt_) if chat is not None else None
    if why is not None:
        operator.report({'ERROR'}, why)
    return why is not None


def refuse(chat):
    """A person's request to ``chat`` (typed, voice text, a voice start): raises the reason while its scene is not
    shown, before anything is observed or sent (the request would observe the shown scene and edit the chat's)."""
    why = refusal(chat)
    if why is not None:
        raise RuntimeError(why)


def show(chat, window):
    """``chat``'s own scene comes back into ``window`` (the one route that sets the window's scene). True when the
    window now shows it."""
    scene = scene_of(chat)
    if window is None or scene is None:
        return False
    if window.scene != scene:
        window.scene = scene
    return True


def select(chat, window, shown_key):
    """The person selected ``chat`` in the chat switcher: its scene comes back when it is not shown; a worker's chat
    only changes what is displayed. Never on a rebuild of the chat's dock (break-it R2-M-2)."""
    from . import parallel_sessions
    if away(chat, shown_key) and not parallel_sessions.is_worker(chat.state["id"]):
        show(chat, window)
    return chat


def shown_scene(executor):
    """The session uid of the scene ``executor``'s window shows, or None (no window: a lane, a host test, or a window
    a load freed: ``BlenderExecutor.window`` reads it live, ``live_target``)."""
    try:
        window = getattr(executor, "window", None)
        return window.scene.session_uid if window is not None else None
    except (AttributeError, ReferenceError):
        return None


def keep_shown(chat, executor, before):
    """After ``chat``'s own call: when that call changed the scene its window shows (``scene.new``, a scene delete),
    the window shows the chat's own scene again. Its calls, memory and previews keep their key (break-it R2-M-3), and
    its own act never leaves it waiting for a scene nobody chose to leave (M-4). A call that removed the chat's scene
    leaves it GONE (Undo brings the scene back). Returns what the model is told (``REVERTED``), else None (R3-M-2)."""
    after, document = shown_scene(executor), getattr(chat, "document", None)
    if None in (before, after, document) or after == before or document[1] != before:
        return None
    scene, window = scene_of(chat), executor.window   # Live: the call may have loaded a file (live_target).
    if scene is None or window is None:
        return None
    other = window.scene.name
    window.scene = scene
    return REVERTED.format(scene=scene.name, other=other)


def busy_message(controllers, translate, otherwise):
    """Wherever Angelo waits for every chat to be idle: ``translate``'s sentence naming the scene each busy chat waits
    for, when one does (a held turn counts as running work, and never ends by itself: R3-M-1), else ``otherwise``."""
    scenes = sorted({f"'{getattr(scene_of(chat), 'name', '?')}'" for chat in controllers
                     if chat.busy and getattr(chat, "_held", False)})
    return translate(WAITING, scenes=", ".join(scenes)) if scenes else otherwise


def wait_on_lease(call, holder):
    """``call`` waits for the writer lease ``holder`` (a staged program job) keeps. While the chat holding it waits for
    its own scene, ``call``'s lifetime does not run either: it never expires for another chat's look away (R3-M-3);
    and that chat gives the lease up at its next pump (``yield_writer``), so the shown chat's edit goes ahead instead
    of waiting for a scene nobody shows (break-it R4-M-1). Only a wait on the same holder is credited: a later wait on
    another starts its own count."""
    import time
    holding = getattr(getattr(holder, "backend", None), "controller", None)
    now, (last, before) = time.monotonic(), getattr(call, "_lease_wait_at", (None, None))
    call._lease_wait_at = (now, holder)
    if getattr(holding, "_held", False):
        holding._writer_wanted = True
        if before is holder and getattr(call, "expires", None) is not None:
            call.expires += now - last


def yield_writer(chat):
    """A held chat's pump (``Controller._pump_tools``): when another chat waits for the writer lease a program of
    ``chat``'s keeps (``wait_on_lease``), that program's stage is discarded, nothing live having changed, and the same
    approved program stages again once ``chat``'s scene is shown. A reviewed preview being accepted was consumed and
    cannot stage again: it keeps its exact stage without the lease, and re-checks it before it commits
    (``suspend_accepted_writer``, break-it R5-M-2). Never a commit under way, a preparation another thread owns, or a
    call that wrote live."""
    if not getattr(chat, "_writer_wanted", False):
        return
    chat._writer_wanted = False
    from .program_jobs import WRITER
    from .program_job_state import suspend_accepted_writer
    for call_id, job in list(chat.native_jobs.items()):
        call = chat.pending.get(call_id)
        if (WRITER.owner is not job or call is None or call.native_preparation is not None or call.mutation_started
                or call.committing or call.commit_requested):
            continue
        if getattr(job, "accepting_preview", False):
            suspend_accepted_writer(job)
            continue
        chat._discard_native_job(call)   # A cleanup that could not finish runs first at its next pump.
        call.executing = False   # Staged again from the start (``_pump_native_jobs``), checked as before.


def resume_writer(call, job):
    """A shown chat's accepted preview whose lease went while its chat was held (``yield_writer``) takes the lease
    back before its next poll; while another program keeps it, it waits on it as a new stage does (``wait_on_lease``:
    a held holder gives it up). True once it holds the lease again."""
    from .program_job_state import resume_accepted_writer
    if resume_accepted_writer(job):
        return True
    wait_on_lease(call, job.writer.owner)
    return False


def hold(chat, shown_key):
    """``Controller._pump_tools``: True while ``chat``'s scene is not shown, so its tool calls, observation requests
    and native jobs wait (none expires or is discarded meanwhile) and none counts as work to dispatch (break-it
    R5M1-L1 H-1: no fast re-arm, no spin). A voice session ends once, with a notice (M-5)."""
    if not away(chat, shown_key):
        chat._held_at = chat._writer_wanted = None   # Shown: nothing to yield (``yield_writer``).
        return False
    import time
    now, last = time.monotonic(), getattr(chat, "_held_at", None)
    chat._held_at = now
    for call in list(getattr(chat, "pending", {}).values()) if last is not None else ():
        if getattr(call, "expires", None) is not None:   # Its lifetime does not run while it waits (R2-M-1).
            call.expires += now - last
    voice = getattr(chat, "voice", None)
    if voice is not None and not getattr(voice, "_ended_away", False):
        import threading
        from .ui_text import notice
        voice._ended_away = True
        threading.Thread(target=voice.stop, daemon=True).start()
        chat.emit("message", {"role": "system", **notice(VOICE_AWAY)})
    return True
