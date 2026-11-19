# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Angelo's own cleanups may remove, and where its own code may write (doc/angelo/script_confinement.rst,
"Angelo's own I/O").

A cleanup that runs later, outside any script (at exit, a temporary folder's removal), names its target from Python
state: a set of folders, an object's path attribute. An agent script that reached that state could change it in
place, and the cleanup would remove what the script named. So the target is checked against a record C keeps of the
files and folders Angelo itself made (``_angelo_guard.own_path``: their identity, held open where the system allows),
never against the Python state alone. What is not on record stays where it is.

Without the native guard (tests that run outside Blender) nothing is recorded and every path counts as owned, as
before.
"""

import contextlib
import os
import shutil

try:
    import _bpy
    _GUARD = getattr(_bpy, "_angelo_guard", None)
except ImportError:   # Outside Blender.
    _GUARD = None
_OWN = getattr(_GUARD, "own_path", None)
_OWNED = getattr(_GUARD, "owned_path", None)
_DISOWN = getattr(_GUARD, "disown_path", None)
_PROCESS_OURS = getattr(_GUARD, "process_ours", None)
_MAKE_DIR = getattr(_GUARD, "make_owned_dir", None)
_MAY_WRITE = getattr(_GUARD, "may_write", None)
_VOUCH = getattr(_GUARD, "vouch_path", None)
_VOUCH_PROGRAM = getattr(_GUARD, "vouch_program", None)
_UNVOUCH = getattr(_GUARD, "unvouch_path", None)
_STORE = getattr(_GUARD, "writable_store", None)
_SCRIPT_MADE = getattr(_GUARD, "script_made", None)
_MAY_RUN = getattr(_GUARD, "may_run", None)
_PROGRAM_CHANGED = getattr(_GUARD, "program_changed", None)
_ADOPT = getattr(_GUARD, "adopt_dir", None)
_MAKE_DIR_BESIDE = getattr(_GUARD, "make_dir_beside", None)
_SCRIPT_SET = getattr(_GUARD, "script_set", None)
_SCRIPT_ORIGINATED = getattr(_GUARD, "script_originated", None)
_OUTPUT_ALLOWED_FOR = getattr(_GUARD, "output_allowed_for", None)
_FOLDER_ALLOWED_FOR = getattr(_GUARD, "folder_allowed_for", None)
_REGISTER = getattr(_GUARD, "register_writable", None)
_DOCUMENT = getattr(_GUARD, "document_folder", None)
_CREATE_BESIDE = getattr(_GUARD, "create_beside", None)


def own(path):
    """Record ``path``, which Angelo just made. While a script runs (Angelo's code called by it) only a path in
    Angelo's private folders made since the script began is recorded."""
    return bool(_OWN(os.fspath(path))) if _OWN is not None else False


def make_dir(parent, prefix):
    """A new folder ``<parent>/<prefix><random>`` inside Angelo's private folders, made and recorded by C at once (no
    birth time needed); None when C can't (outside the app: the caller makes it its own way)."""
    if _MAKE_DIR is None:
        return None
    try:
        return _MAKE_DIR(os.fspath(parent), prefix)
    except (TypeError, ValueError):
        return None


def owned(path):
    """Whether ``path`` names a file or folder Angelo recorded, still that one (not a link put in its place)."""
    try:
        return bool(_OWNED(os.fspath(path))) if _OWNED is not None else True
    except (TypeError, ValueError):
        return False


def disown(path):
    if _DISOWN is not None:
        try:
            _DISOWN(os.fspath(path))
        except (TypeError, ValueError):
            pass


def remove_tree(path):
    """Remove a folder Angelo made, and everything in it; anything else is left. True when removed."""
    if not owned(path):
        return False
    disown(path)
    shutil.rmtree(path, ignore_errors=True)
    return True


def unlink(path):
    """Remove a file Angelo made; anything else is left. True when removed."""
    if not owned(path):
        return False
    disown(path)
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def process_ours(pid):
    """Whether ``pid`` is this process or one of its descendants (C's ``process_ours``), whatever Python state says;
    True without the native guard."""
    try:
        return True if _PROCESS_OURS is None else bool(_PROCESS_OURS(int(pid)))
    except (TypeError, ValueError, OverflowError):
        return False


def entry_name(name):
    """``name`` when it names one entry of an anchored folder itself (no separator, not ``.`` or ``..``): a name from
    Python state can't reach outside the store's folder (a relative path through ``dir_fd`` would)."""
    text = os.fsdecode(name) if isinstance(name, (bytes, os.PathLike)) else name
    if (not isinstance(text, str) or not text or text in (".", "..") or "/" in text or "\\" in text
            or "\0" in text):
        raise ValueError("Snapshot storage entry name is not a single name")
    return name


def may_write(path):
    """Whether Angelo's own code may write, replace or remove ``path``, a path from Python state: inside Angelo's
    private folders, made by Angelo (or inside a folder it made), or vouched for (``vouch``, the person's confirm in a
    file browser of Angelo's). C decides; without the native guard any path may (None or a non-path never may)."""
    try:
        path = os.fspath(path)   # First, with or without the native guard: None or a non-path refuses either way.
        return True if _MAY_WRITE is None else bool(_MAY_WRITE(path))
    except (TypeError, ValueError):
        return False


def _shown(path):
    """A refused path's last name for the message, whatever ``path`` is (None or another value refuses too, never a
    TypeError from the message itself)."""
    try:
        text = os.fspath(path)
    except TypeError:
        return repr(path)
    text = os.fsdecode(text)
    return os.path.basename(text) or text


def require_writable(path):
    """``path`` when ``may_write`` says so; otherwise ``PermissionError`` (nothing is written), with the closed
    refusal code READ_ONLY (``refusal_codes.coded``'s attribute, set here: this module imports nothing of the add-on,
    so a child script can import it as a plain sibling)."""
    if not may_write(path):
        error = PermissionError(
            "Angelo writes only where it made something, where you chose, or in its own folders: %s" % _shown(path))
        error.refusal_code = "READ_ONLY"
        raise error
    return path


def may_run(path):
    """Whether Angelo's own code may start the program ``path`` names, a path from Python state: inside an installed
    folder, vouched for, or in a folder on PATH, and never one a script made. C decides; True without the native
    guard."""
    try:
        return True if _MAY_RUN is None else bool(_MAY_RUN(os.fspath(path)))
    except (TypeError, ValueError):
        return False


def program_changed(path):
    """Whether the program ``path`` resolves to was recorded to run and has changed on disk since (an update installed
    in place, or a rewrite): C keeps refusing it in this process, so only a restart of Angelo starts it."""
    try:
        return _PROGRAM_CHANGED is not None and bool(_PROGRAM_CHANGED(os.fspath(path)))
    except (TypeError, ValueError):
        return False


def require_runnable(path):
    """``path`` when ``may_run`` says so; otherwise ``PermissionError`` (nothing is started), coded READ_ONLY as
    ``require_writable``'s, saying why: an agent script wrote or named it (the person checks it, then forgets the
    folder's agent changes in Preferences: C's record outlives a restart and a reinstall), a recorded program
    changed on disk since (an update in place: restart Angelo), or one Angelo starts by none of its rules (the
    re-review of 4be92cfb84f, S-2)."""
    if not may_run(path):
        if script_made(path) or script_made(os.path.realpath(path)):
            # C's change record (kept across sessions): a path a script wrote, moved, linked or only named (a path
            # property), held as the script's until the person forgets it; a reinstall in place keeps the path.
            message = ("Angelo starts only installed programs, programs on PATH or ones you chose: an agent script "
                       "wrote or named %s, so Angelo won't start it. Check the file (reinstall it if in doubt), then "
                       "choose its folder in Preferences > Forget Agent Changes in a Folder…")
        elif program_changed(path):
            message = ("Angelo starts only installed programs, programs on PATH or ones you chose: %s changed on disk "
                       "after Angelo recorded it (an update, for example). Restart Angelo to use it.")
        else:
            message = ("Angelo starts only installed programs, programs on PATH or ones you chose: %s. Choose the "
                       "program in the connection's settings, or reinstall it with its official installer.")
        error = PermissionError(message % _shown(path))
        error.refusal_code = "READ_ONLY"
        raise error
    return path


def runnable_argv(argv):
    """``argv`` when its program may run (``require_runnable`` on ``argv[0]``)."""
    require_runnable(argv[0])
    return argv


def vouch(path, folder=False, persist=True):
    """Record that the person (or a program they connected) gave Angelo the file ``path`` to write, for this process
    (nothing is kept past it): called where the path enters Angelo, never with a script's code running (C refuses it
    then). True when recorded. A folder only with ``persist=False``: one call's (``vouched_during``), a folder of
    Blender's own; never the session's (a document's folder is never vouched: ``adopt`` what Angelo's tools made)."""
    try:
        return bool(_VOUCH(os.fspath(path), bool(folder), bool(persist))) if _VOUCH is not None else True
    except (TypeError, ValueError, PermissionError):   # Refused while a script runs: nothing is vouched for.
        return False


def vouch_program(path):
    """A program Angelo found by fixed rules, or the person chose, as it enters Angelo (``cli_runtime``): the real
    file its name resolves to (through an installer's ``~/.local/bin/claude`` link to ``versions/X``, a Homebrew or
    npm link), recorded in C to START only, never to write (``GUARD.vouch_program``). C's ``may_run`` is the guard:
    by this record it starts the program only while it is that very file, unchanged since it was recorded (identity,
    size and its times), never one a script made, and, once a script has run, a recorded program that changed stays
    refused for the rest of the process, found again or unvouched (``require_runnable`` says to restart). Its other
    rules (an installed folder, a folder on PATH) need no record. A link retargeted later resolves to a file this does not cover. Never through a link or file a
    script made (C's change record). Before 2.0.1, the found name was vouched to write, which C refuses for a link,
    so every new runtime start through a link failed after any script, and a non-link program became a file scripts
    could write (the break-it review of 29b385a6645, MUST-1). True when the program may run."""
    real = os.path.realpath(path)
    if not script_made(path) and not script_made(real):
        try:
            if _VOUCH_PROGRAM is not None:
                _VOUCH_PROGRAM(os.fspath(real))
        except (TypeError, ValueError, PermissionError):   # Refused while a script runs: nothing is recorded.
            pass
    return may_run(path)


def adopt(path):
    """Record an existing real folder Angelo's own tools write into (a document's ``assets/downloads``, an engine
    project's working folder), made by Angelo in an earlier session, as Angelo's by identity: never through a link at
    its name, never one a script made. True when recorded (outside the app: always)."""
    try:
        return bool(_ADOPT(os.fspath(path))) if _ADOPT is not None else True
    except (TypeError, ValueError, PermissionError):
        return False


def register_writable(path):
    """A folder of Blender's own or the environment's that Angelo writes into (the cache's updates folder, a library
    folder the environment names), named at startup, before any script region began (refused after)."""
    try:
        return bool(_REGISTER(os.fspath(path))) if _REGISTER is not None else True
    except (TypeError, ValueError, PermissionError):
        return False


def document_folder(path):
    """Whether ``path`` is, or lies in, the open document's folder (Blender's own path): the file tools' folder, which
    their anchored descriptors may use. Never a folder other writers may write (outside the app: True)."""
    try:
        return bool(_DOCUMENT(os.fspath(path))) if _DOCUMENT is not None else True
    except (TypeError, ValueError):
        return False


def require_anchorable(path, within=None):
    """For the checkpoint and file-tool folders an anchored descriptor opens: a store's folder must be one Angelo may
    write (``require_writable``); the file tools' are in the open document's folder (``within`` too, when given),
    which only those descriptors may use (never vouched for Angelo's other writers: the review of piece 10)."""
    if not document_folder(path) or (within is not None and not document_folder(within)):
        require_writable(path)


def make_dir_beside(anchor, name, mode=0o700):
    """The folder ``name`` (fixed parts: ``"assets/downloads"``, ``".angelo"``) below ``anchor``, Angelo's by
    identity: each part made by C following no link, or an existing real folder no script made adopted, below an
    anchor C accepts (the open document's folder, a folder holding a file the person named, or one Angelo may write).
    ``PermissionError`` otherwise. Outside the app the folders are made here."""
    target = os.path.join(os.fspath(anchor), *str(name).replace("\\", "/").split("/"))
    if _MAKE_DIR_BESIDE is None:
        os.makedirs(target, mode=mode, exist_ok=True)
        return target
    made = _MAKE_DIR_BESIDE(os.fspath(anchor), str(name), int(mode))
    if made is None:
        error = PermissionError("Angelo writes only where it made something, where you chose, or in its own "
                                "folders: %s" % _shown(target))
        error.refusal_code = "READ_ONLY"
        raise error
    return made


def script_set(owner, struct_type, identifier):
    """Whether a confined script set the property ``identifier`` of data of the RNA type ``struct_type`` (its
    identifier, e.g. ``"RenderSettings"``; data of a type refining it counts) in the data-block ``owner``, or made
    ``owner`` (round 3: a writer that builds an output path from such data judges what it writes; the type keeps a
    strip's ``filepath`` from being the render's). False without the native guard."""
    try:
        return False if _SCRIPT_SET is None else bool(
            _SCRIPT_SET(int(owner.as_pointer()), str(struct_type), str(identifier)))
    except (TypeError, ValueError, AttributeError):
        return True


def script_originated(owner):
    """Whether a confined script made, copied, read, appended or linked the data-block ``owner``."""
    try:
        return False if _SCRIPT_ORIGINATED is None else bool(_SCRIPT_ORIGINATED(int(owner.as_pointer())))
    except (TypeError, ValueError, AttributeError):
        return True


def output_allowed_for(path, owner, data, identifier):
    """For a writer that read ``path`` from the property ``identifier`` of ``data`` (in the data-block ``owner``)
    when that data is the script's: whether it may write it (a place C allows, what the person typed into that very
    property, or a new file below an accepted folder, which C makes and records). True without the native guard."""
    try:
        return True if _OUTPUT_ALLOWED_FOR is None else bool(_OUTPUT_ALLOWED_FOR(
            os.fspath(path), int(owner.as_pointer()), int(data.as_pointer()), str(identifier)))
    except (TypeError, ValueError, AttributeError):
        return False


def folder_allowed_for(folder, owner, data, identifier):
    """``output_allowed_for`` for a writer that writes into the folder it read from that property (a cache, a bake):
    a folder C allows, the one the person typed there, or a new folder below an accepted one, which C makes."""
    try:
        return True if _FOLDER_ALLOWED_FOR is None else bool(_FOLDER_ALLOWED_FOR(
            os.fspath(folder), int(owner.as_pointer()), int(data.as_pointer()), str(identifier)))
    except (TypeError, ValueError, AttributeError):
        return False


def create_beside(named, name):
    """A new file ``name`` (one name, made exclusively) in the folder of ``named``, a file Angelo may write, recorded
    as Angelo's: the temporary a writer fills and renames onto ``named`` (or an output beside it). Its path; a
    ``PermissionError`` when C refuses (outside the app the file is made here, the same way)."""
    if _CREATE_BESIDE is None:
        target = os.path.join(os.path.dirname(os.fspath(named)), name)
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600))
        return target
    made = _CREATE_BESIDE(os.fspath(named), name)
    if made is None:
        error = PermissionError("Angelo writes only where it made something, where you chose, or in its own "
                                "folders: %s" % _shown(named))
        error.refusal_code = "READ_ONLY"
        raise error
    return made


def script_made(path):
    """Whether a confined script wrote, moved or linked ``path`` (C's change record); False without the native
    guard."""
    try:
        return False if _SCRIPT_MADE is None else bool(_SCRIPT_MADE(os.fspath(path)))
    except (TypeError, ValueError):
        return True


def unvouch(path, transient_only=False):
    if _UNVOUCH is not None:
        try:
            _UNVOUCH(os.fspath(path), bool(transient_only))
        except (TypeError, ValueError):
            pass


def name_store(path):
    """The private file earlier 2.0.0 builds kept vouched paths in (startup, once): C removes it and never writes it."""
    try:
        return bool(_STORE(os.fspath(path))) if _STORE is not None else False
    except (TypeError, ValueError, PermissionError):
        return False


@contextlib.contextmanager
def vouched_during(paths, folder=True):
    """Folders (or files) Angelo's code computed from fixed facts and writes within this one call, at the person's
    action (a settings import into Blender's own folders): vouched for while it runs, never saved, forgotten after."""
    done = [path for path in paths if path and vouch(path, folder, persist=False)]
    try:
        yield
    finally:
        for path in done:
            unvouch(path, transient_only=True)   # Never a path vouched for before, which stays.
