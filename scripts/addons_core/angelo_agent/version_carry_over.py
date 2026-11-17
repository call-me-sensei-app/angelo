# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Carry the agent state to a newer Blender base on its first launch: moved, never copied, never lost.

Blender keeps a user folder per version (``<product>/5.2``, ``<product>/5.3``) and the agent state folder
``angelo-agent`` lives in its ``config``, so a newer Blender base starts with an empty one. On the first launch of
each version this module moves the state of the newest earlier version that has any (any earlier version, not only
the one before) into the new folder. Blender's own settings beside it are copied, as Blender's own import copies
them, by ``preferences.copy_prev``, which leaves the state out (``settings_import.previous_ignore``). Contract:
``doc/angelo/app_identity.rst``, "Moving to a newer Blender base".

* **Moved with same-volume renames** (``carry_over_moves``): nothing is copied or overwritten, so gigabytes of
  snapshots move in milliseconds; when the new version has no state yet, the whole folder moves in one rename.
  Across volumes an entry is copied, verified, renamed into place and only then removed.
* **Merged when the new side already has state** (an earlier "Use my earlier Angelo settings" copy, finished or
  interrupted, or a build without this module used there): a clash keeps one side in place and the other whole
  in ``kept-from-<version>``, chosen by content and age; the snapshot system is one unit (``carry_over_moves``).
  The chat list is combined (``carry_over_records``).
* **Never while another Angelo runs** (it may be the earlier version, writing into the old folder; this process's own
  children, such as Cycles' GPU listing, do not count): this launch then works on the earlier folder in place
  (``state_paths.session_redirect``, in-process only: never carried to a child or an updated app), so the new folder
  gains no state of its own, and a later launch with no other Angelo moves it all. An unreadable process list counts
  as another Angelo. A move already under way is finished, with renames only. A lock serializes two first launches.
* **Crash-safe and resumable.** The record (``state_paths.CARRY_OVER_RECORD``, in the new version's ``config``)
  names the old folder before the first rename and says ``done`` once every rename is verified; what its record
  rewrites left is named in it and tried again later (``carry_over_finish``). Each step is one rename or the removal
  of a proven copy, so a launch after a crash or a full disk
  plans again from what is left and finishes. A damaged record is kept aside and the carry-over plans again (one
  that cannot be read, or a newer Angelo's, is left alone); a folder that cannot be read is never taken for an empty
  one. The record names the chats that came over: their native contexts started in the old working folder.
* **The earlier version stays usable.** It may stay installed, or come back: after ``done`` it starts with its
  preferences, its update and crash-report choices (``LEFT_BEHIND``), no chats, and one notice saying where they went
  (``NOTE_NAME``). Chats made there come over at the next launch of the newer version with no other Angelo open
  (``_fresh_chats``); nothing here is replaced, so a clash is kept aside and that stint's snapshots stay in the
  ``kept-from`` folder.
* **Native CLI sign-in profiles** (``claude-profile``, ``codex-profile``) found in the state folder are never moved:
  a native CLI may key its stored sign-in to the profile path. ``storage.native_profile_directory`` keeps resolving
  them in the earlier folder (``state_paths.earlier_state_folders``). Accounts saved since 1.0 keep theirs in the
  account folder, which is not per version and never moves.

Standard library only; runs on Blender's main thread when the add-on registers, before anything opens the state.
"""

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from . import carry_over_finish as finish, carry_over_links as links, carry_over_moves as moves, \
    carry_over_processes as processes, folder_moves, shared_replace, state_paths
from .folder_moves import MigrationResult
from .ui_text import n_

RECORD = state_paths.CARRY_OVER_RECORD
RECORD_VERSION = 1
LOCK = ".angelo-carry-over.lock"           # Beside the record, in the new version's ``config``.
KEPT_PREFIX = "kept-from-"                  # ``kept-from-5.2``: one side of a clash, inside the new state folder.
NOTE_NAME = "README-Angelo.txt"           # Left in the old state folder: where the state went.
NOTE_SEEN = ".angelo-moved-notice-shown"   # Beside it, once the earlier version has told the person.
NOTE_TEXT = "Angelo moved this folder's chats, snapshots and workspace to {current}.\n"
NOTE_PROFILES = ("\nThe native sign-in profiles still in this folder ({profiles}) are used by Angelo at their\n"
                 "original paths, so saved Claude and Codex accounts stay signed in. Do not delete or move\n"
                 "them while those accounts are in use.\n")
# The person's choices the earlier version reads: a copy stays, so opening it again never resets them.
LEFT_BEHIND = ("updates.json", "crash-reports.json", "intro.json", "workspace-mode.json")
RENDER_DEVICE = "render-device.json"        # Cycles' saved GPU listing: may be written here before the add-on loads.
LOCK_WAIT = 120.0                           # Seconds another first launch of this version may take to finish.
MOVING, DONE = "moving", "done"
DEFERRED = "deferred"                       # Nothing moved this launch; the next launch tries again.
RETURNED = "returned"                       # Chats made in the earlier version since the move came over.
MOVED_ON = "moved-on"                       # This is the earlier version: its state lives in a newer one now.
ATTENTION = folder_moves.ATTENTION | {DEFERRED, finish.RECORDS}
QUIET = frozenset({"nothing", "isolated"})
RETRIES_BEFORE_HELP = 3                     # Failed launches before the message asks the person to act.


def holds_state(folder):
    """Whether an earlier state folder holds anything to bring (a retained profile or a note alone does not).
    Raises ``OSError`` when it cannot be read: an unreadable folder is never taken for an empty one."""
    found = moves.info(folder)
    if found is None:
        for parent in folder.parents[:2]:
            if os.path.islink(parent) and not os.path.exists(parent):
                raise OSError(f"{parent} is a link to something not there (a disk not attached?)")
        return False
    if moves.stat.S_ISLNK(found.st_mode) or links.is_junction(folder):   # A link or a junction: where it leads.
        if not os.path.exists(folder):
            raise OSError(f"{folder} is a link to something not there (a disk not attached?)")
        return os.path.isdir(folder)
    if not moves.stat.S_ISDIR(found.st_mode):
        return False
    return any(name not in LEFT_BEHIND and name not in {NOTE_NAME, NOTE_SEEN}
               and not (name in state_paths.NATIVE_PROFILES and _profile(folder / name))
               for name in moves.entries(folder))


def find_source(current):
    """``(state folder, version name)`` of the newest earlier version holding state, or ``(None, None)``.
    Raises ``OSError`` when a version folder or a candidate cannot be read."""
    for folder in state_paths.earlier_version_folders(current, strict=True):
        links.finish_relink(folder)   # A lone twin a stopped relink left is the folder's link again.
        if holds_state(folder):
            return folder, folder.parent.parent.name
    return None, None


def read_record(config):
    """The record in ``config`` (a dict), or None. A record that cannot be read raises ``OSError`` (nothing is
    planned from it); a damaged one (not JSON, or not a record) is kept aside (``<record>.damaged-<time>``) and reads
    as none, since every step is safe to plan again from what is on disk. A newer Angelo's record is left alone."""
    path = config / RECORD
    try:
        text = shared_replace.read_text(path)
    except FileNotFoundError:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value, dict) and type(value.get("version")) is int and value["version"] > RECORD_VERSION:
        return value   # Written by a newer Angelo: never rewritten or set aside here.
    if isinstance(value, dict) and value.get("status") in (MOVING, DONE) and isinstance(value.get("into"), str):
        return value
    shared_replace.replace(path, path.with_name("%s.damaged-%d" % (RECORD, time.time())))
    return None


def write_record(config, value):
    temporary = config / (RECORD + ".%d.tmp" % os.getpid())
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(dict(value, version=RECORD_VERSION), handle)
            handle.flush()
            os.fsync(handle.fileno())
        shared_replace.replace(temporary, config / RECORD)   # An earlier version reads it (moved_state_notice).
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


@contextmanager
def exclusive(path, timeout):
    """An inter-process lock on ``path``; raises ``TimeoutError`` after ``timeout`` seconds."""
    from .checkpoint_transactions import lock_descriptor
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            release = lock_descriptor(descriptor, timeout, "busy")
        except RuntimeError:
            raise TimeoutError(str(path)) from None
        try:
            yield
        finally:
            release()
    finally:
        os.close(descriptor)


deferred_source = None   # The earlier state folder a deferred launch uses where it is (``carry_over_on_startup``).


def carry_over(config_directory, environ=None, others_running=None):
    """Perform (or resume) the carry-over into ``config_directory``; returns a ``MigrationResult``. Never raises.
    ``others_running()``: whether another Angelo runs (default: the system's process list)."""
    global deferred_source
    deferred_source = None
    environ = os.environ if environ is None else environ
    override, variable = state_paths.state_override(environ)
    if override is not None:
        return MigrationResult("isolated", f"{variable} selects an isolated state folder; no earlier version's "
                               "state was looked for")
    shared = next((name for name in ("BLENDER_USER_RESOURCES", "BLENDER_USER_CONFIG") if environ.get(name)), None)
    if shared:
        return MigrationResult("isolated", f"{shared} selects one settings folder for every version")
    config = Path(config_directory).absolute()
    if state_paths.per_version(config / state_paths.STATE_FOLDER)[0] is None:
        return MigrationResult("nothing", f"{config} is not a per-version settings folder")
    try:
        config.mkdir(parents=True, exist_ok=True, mode=0o700)
        given = config
        config = config.resolve()   # Through any link in the user folder's path: one spelling for every record.
        current = config / state_paths.STATE_FOLDER
        with exclusive(config / LOCK, LOCK_WAIT):
            return _carry_locked(config, current, others_running or _others_running, given)
    except TimeoutError:
        return MigrationResult(DEFERRED, "Another Angelo is still bringing your chats and snapshots over from the "
                               "earlier version; Angelo finishes it the next time it opens")
    except Exception as error:   # Every failure is reported; nothing has moved past its last finished step.
        return MigrationResult("failed", f"Couldn't check for an earlier version's chats and snapshots "
                               f"({type(error).__name__}: {error}). Nothing was moved this time; Angelo tries again "
                               "the next time it opens.")


def _carry_locked(config, current, others_running, given):
    global deferred_source, last_kept_folder
    record = read_record(config)
    if record is not None and record.get("version", RECORD_VERSION) > RECORD_VERSION:
        return MigrationResult("nothing", "A newer Angelo carried this version's state over")
    if moves.info(current / NOTE_NAME) is not None and moves.info(current / NOTE_SEEN) is None:
        return MigrationResult(MOVED_ON, "This version's chats and snapshots moved to a newer Angelo")
    mine = record is not None and state_paths.same_place(record["into"], current)
    resumed = mine and record["status"] == MOVING
    back, unit, began, returned, touched, moved_at = [], None, time.time(), {}, False, None
    if mine and record["status"] == DONE:
        busy = None   # Asked at most once, and only when there is work: another Angelo may write the same files.
        if record.get("pending") or record.get("leave_pending"):
            busy = others_running()
            earlier = Path(record.get("from", ""))
            told = None if busy else finish.retry_pending(
                lambda value: write_record(config, value), record, current,
                current / (KEPT_PREFIX + str(record.get("from_version", ""))),
                lambda: _leave_behind(earlier, current, [Path(p) for p in record.get("retained", ())], again=True))
            if told is not None:
                return told
        source, back = _with_fresh_chats(current)
        if not back or (busy if busy is not None else others_running()):
            return MigrationResult("nothing", "The earlier version's state was brought over before")
        version, attempts, chats, others = source.parent.parent.name, 0, record.get("chats", []), False
        unit, returned, moved_at = "target", dict(record.get("returned") or {}), record.get("at")
    elif mine and record["status"] == MOVING:   # Finished whoever else runs: both folders are part-moved.
        source, version = Path(record["from"]), str(record.get("from_version", ""))
        attempts, chats, others = record.get("attempts", 0), record.get("chats", []), others_running()
        back, unit, began = record.get("back", []), record.get("unit"), record.get("began", began)
        returned, touched = dict(record.get("returned") or {}), record.get("touched", False)
        moved_at = record.get("moved_at")
        if back and others:
            return MigrationResult("nothing", "Chats from the earlier version wait until it is closed")
    else:
        (source, version), attempts = find_source(current), 0
        if source is not None and others_running():
            deferred_source = source
            return MigrationResult(DEFERRED, (
                f"Your chats and snapshots from Angelo's {version} folder were not moved yet because another Angelo "
                "is open; this session uses them where they are. Quit every Angelo, then open Angelo again to "
                "finish."))
        chats, others = (_chat_ids(source) if source is not None else []), False
    if source is None:
        return MigrationResult("nothing", "No earlier version's state to bring over")
    if _one_folder(source, current):
        return MigrationResult("nothing", "This version's state folder is the earlier version's, through a link")
    _drop_own_cache(current)
    if unit is None:
        unit = moves.unit_plan(source, current, moves.entries(source)) if moves.info(source) is not None else "move"
    # Before any rename moves it; a record from before the field: whatever is left of the link, or its twin.
    linked = (record["linked"] if resumed and "linked" in record
              else links.is_link(source) or links.is_link(links.twin_of(source)))
    start = {"from": str(source), "from_version": version, "into": str(current), "chats": chats, "unit": unit,
             "back": back, "began": began, "returned": returned, "touched": touched, "moved_at": moved_at,
             "linked": linked}
    write_record(config, dict(start, status=MOVING, attempts=attempts + 1, at=time.time()))

    def settled():
        start["unit"] = "move"
        write_record(config, dict(start, status=MOVING, attempts=attempts + 1, at=time.time()))
    # Across volumes a move copies, then removes: never while another Angelo may still write into the old folder.
    mover = moves.Mover(current / (KEPT_PREFIX + version), allow_copy=not others, unit=unit, unit_settled=settled,
                        fresh_since=began, prefer_new=bool(back))
    try:
        retained = _move(source, current, mover)
        finish.check_reachable(current, linked)
        also = [] if given == config else [given.parent.parent / version / "config" / state_paths.STATE_FOLDER]
        at = time.time()
        returned.update((chat, at) for chat in back)
        count = finish.after_renames(   # Done: what records or the earlier version's step left is tried again later.
            lambda value: write_record(config, value), start, current, source, retained, also, mover.kept_root,
            lambda: _leave_behind(source, current, retained),
            dict(status=DONE, moved=mover.moved, copies=mover.copies, kept=mover.kept, attempts=attempts + 1,
                 at=moved_at or at, back=[], returned=returned))
    except Exception as error:   # Each step is a finished rename or removal of a copy: resumable as it is.
        message = (f"Moving your chats and snapshots from {source} stopped ({mover.moved} items moved this time; "
                   f"{type(error).__name__}: {error}). Nothing was deleted")
        # A resumed move whose new folder exists may have renamed things before a crash: never redirected to a part.
        part_moved = touched or mover.moved or mover.kept or (resumed and moves.info(current) is not None)
        if part_moved:
            start["touched"] = True
            _try_record(config, dict(start, status=MOVING, attempts=attempts + 1, at=time.time()))
        twin = links.twin_of(source)
        stranded = links.is_link(twin) and not (links.is_link(source) and links._same(twin, source))
        if not back and stranded:
            deferred_source = twin   # A stopped relink: its twin is the way to the state now, part-moved or not.
        elif not part_moved and not back and moves.info(source) is not None:
            deferred_source = source   # Nothing moved: this session uses them where they are.
        if attempts + 1 >= RETRIES_BEFORE_HELP:
            where = f"{twin} (a link to them)" if stranded else f"{source} and {current}"
            return MigrationResult("failed", message + f"; this has stopped {attempts + 1} times. Your chats are "
                                   f"safe in {where}; the details above say what to fix before opening Angelo again.")
        return MigrationResult("failed", message + "; Angelo continues the move the next time it opens.")
    if mover.kept:
        last_kept_folder = mover.kept_root.name
    if back:
        return MigrationResult(RETURNED, f"Brought {len(back)} chats made in Angelo's {version} folder ({source}) over "
                               f"to {current}: {mover.moved} items moved, {mover.kept} kept in {mover.kept_root}",
                               (str(current),), tuple(map(str, retained)))
    message = (f"Moved the chats, snapshots and workspace of Angelo's {version} folder ({source}) to {current}: "
               f"{mover.moved} items moved, {count} records updated")
    if mover.copies:
        message += f", {mover.copies} earlier copies replaced by their originals"
    if mover.kept:
        message += f", {mover.kept} clashing items kept in {mover.kept_root}"
    if retained:
        message += f"; {len(retained)} native sign-in profile folders stay in {source}"
    return MigrationResult("migrated", message, (str(current),), tuple(map(str, retained)))


def _try_record(config, value):
    try:
        write_record(config, value)
    except OSError:
        pass   # The disk that stopped the move: the next launch plans from what it finds.


def _one_folder(source, current):
    """Whether both names reach one folder (a link, in the person's own setup): nothing to carry."""
    try:
        return os.path.samefile(moves.long_path(source), moves.long_path(current))
    except OSError:
        return False


def _with_fresh_chats(current):
    """``(earlier state folder, its chats made since the move)``, newest version first, or ``(None, [])``."""
    for folder in state_paths.earlier_version_folders(current):
        chats = _fresh_chats(folder, current)
        if chats:
            return folder, chats
    return None, []


def _fresh_chats(source, current):
    """Chats with messages in the earlier folder that this one lacks: made there since the move. A chat that cannot
    be read counts: it may be the person's."""
    found = []
    for chat in _chat_ids(source):
        if moves.info(current / "sessions" / (chat + ".json")) is not None:
            continue
        try:
            value = json.loads(shared_replace.read_text(source / "sessions" / (chat + ".json")))
        except (OSError, ValueError, UnicodeError):
            found.append(chat)
            continue
        if not isinstance(value, dict) or value.get("messages") != []:
            found.append(chat)
    return found


def _drop_own_cache(current):
    """A new folder holding only Cycles' saved GPU listing has no state: the whole move stays one rename."""
    if moves.is_real_dir(current) and moves.entries(current) == [RENDER_DEVICE]:
        os.unlink(moves.long_path(current / RENDER_DEVICE))
        folder_moves.remove_if_empty(Path(moves.long_path(current)))


def _leave_behind(source, current, retained, again=False):
    """The earlier version's note and choices (``carry_over_leave``)."""
    from .carry_over_leave import leave_behind
    return leave_behind(source, current, retained, again)


def _chat_ids(source):
    """The chats the earlier folder holds (``sessions/<id>.json``): the ones whose native contexts stay behind."""
    folder = source / "sessions"
    if not moves.is_real_dir(folder) and not os.path.isdir(folder):
        return []
    return sorted(name[:-5] for name in moves.entries(folder) if name.endswith(".json") and len(name) == 41)


def _move(source, current, mover):
    """Every rename of one carry-over (resumable); returns the native profile folders left in place."""
    links.finish_relink(source)   # A relink of the state folder's own link that a crash stopped (carry_over_links).
    found = moves.info(source)
    if found is None:
        return []   # Every rename finished before an interruption: only the records were left.
    stays = {NOTE_NAME, NOTE_SEEN, *state_paths.NATIVE_PROFILES} | (set(LEFT_BEHIND) if mover.prefer_new else set())
    linked = links.is_link(source)
    retained = [] if linked else [source / name for name in moves.entries(source)
                                  if name in state_paths.NATIVE_PROFILES and _profile(source / name)]
    if moves.info(current) is None and (linked or not retained):
        mover.plan = links.Plan(Path(source), Path(current))   # Everything moves: the link, or the whole folder.
        mover.merge(source, current, ())
        return []
    everything = moves.entries(source)
    if linked:   # Into the state here, through the link; a sign-in profile keeps its path.
        names = [name for name in everything if name not in stays]
    else:
        names = [name for name in everything if name not in stays
                 or (name in state_paths.NATIVE_PROFILES and source / name not in retained)]
    # The plan's view is exactly what merge_top leaves, so a link into anything that stays is pinned.
    mover.plan = links.Plan(Path(source), Path(current), frozenset(set(everything) - set(names)))
    mover.merge_top(source, current, names)
    if linked:
        return []
    if not retained:
        folder_moves.remove_if_empty(Path(moves.long_path(source)))
    return retained


def _profile(path):
    """A native sign-in profile kept where it is: a folder, or a link or junction to one (its CLI keys its sign-in to
    this path, which moving it would change)."""
    return moves.is_real_dir(path) or (links.is_link(path) and os.path.isdir(moves.long_path(path)))


def _others_running():
    """Whether another Angelo runs now (``carry_over_processes``; the tests replace this)."""
    return processes.others_running()


def told_moved_on(config_directory):
    """The earlier version has said where its state went: once."""
    try:
        (Path(config_directory) / state_paths.STATE_FOLDER / NOTE_SEEN).write_text("", encoding="utf-8")
    except OSError:
        pass   # It says so again next time.


_ran = False
last_result = None
last_kept_folder = None   # The ``kept-from-<version>`` folder this launch's carry-over filled, for the notice.


def carry_over_on_startup(config_directory):
    """Run at every add-on registration; reports anything noteworthy to the console. A defect here never stops
    the add-on from loading. Registering again in the same session (a preference read reloads the add-on) keeps
    this session's noteworthy result, and never moves anything: the session has the state open by then."""
    global last_result, _ran
    if _ran:
        return last_result
    _ran = True
    try:
        result = carry_over(config_directory)
    except Exception as error:  # Defensive: carry_over already reports every failure.
        result = MigrationResult("failed", f"Checking for an earlier version's chats and snapshots stopped "
                                 f"unexpectedly ({type(error).__name__}: {error}); nothing more was moved")
    if not (result.status in QUIET and last_result is not None and last_result.status not in QUIET):
        last_result = result
    if result.status in {"failed", DEFERRED} and deferred_source is not None:
        state_paths.session_redirect = deferred_source   # This session (only) works on it in place.
    if result.status in {"migrated", "failed", DEFERRED, RETURNED, finish.RECORDS}:
        print(f"Angelo version carry-over: {result.message}")
    return last_result


def attention_message():
    """The message to show in the AI Providers preferences, or None."""
    return last_result.message if last_result is not None and last_result.status in ATTENTION else None


PRE_RENAME_TITLE = n_("A pre-rename folder needs attention")
EARLIER_TITLE = n_("Your earlier Angelo's chats need attention")


def attention_notices():
    """``[(title, message)]`` for the AI Providers preferences: every folder move that needs the person (English;
    the caller translates both)."""
    from . import data_migration, state_migration
    records = last_result is not None and last_result.status == finish.RECORDS   # The chats moved: its own title.
    found = [(finish.RECORDS_TITLE if records else EARLIER_TITLE, attention_message())]
    found += [(PRE_RENAME_TITLE, module.attention_message()) for module in (data_migration, state_migration)]
    return [(title, message) for title, message in found if message]
