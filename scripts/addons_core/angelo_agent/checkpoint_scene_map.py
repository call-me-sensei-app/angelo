# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A checkpoint's capture-time scene map, and which captured scene each restored scene continues.

A file read gives every scene a new ``session_uid``. Within one load of a file (its load serial, the first part of
``ui.document_key()``) a uid names one scene, renamed or not, and a process never reuses one, so a chat's own scene
is its (load serial, uid) key (``document_scene``). A capture records what each scene of the document was called
then: ``{"load": <load serial>, "names": [[session uid, name_full], ...]}``. The captured file holds exactly those
names, so after a Restore reads it, a chat whose scene comes from that load is rebound to the restored scene with the
name its uid had at capture: a rename or a swap of names after the capture never moves it onto another scene
(break-it R5M1 round 2, R2-M-4), and a scene made after the capture is not in the file.

Each restored scene then continues the captured scene of its name, and each chat's key after the load continues its
key before the load (whether the restored file has its scene or not: ``_parents``), so a later Restore of a capture
from any earlier load (Undo restore, or a request checkpoint taken between two Restores) finds each chat's scene the
same way (break-it R4-M-2). A key whose ancestors name two scenes of the capture's load is ambiguous: no answer.

A Restore that read a map also keeps its load's provenance (``_loads``): the loads it descends from and the scenes it
had right after the read. A scene made later in that load is in no capture of those loads, so a chat on it is told its
scene is missing from such a capture rather than bound to a namesake (break-it r2m4c F1, R4-L-7). A map that is
missing, malformed or from a load the chat's scene does not come from, with no such provenance, answers nothing: the
caller rebinds by the name read before the load, as before (``controller_recovery.restored_document``).

Bounded (break-it r2m4c F5): only loads some map this process wrote still names (``_written``), and the current load,
are worth answering for. Each Restore compacts the links to what this load's scenes and the chats' keys reach: a key
keeps its ancestors in those loads and, summarized, the loads its scene is proven absent from (``_absent``), so the
graph is bounded by the scenes, the chats and the retained checkpoints' loads, never by how many Restores ran.

Storage: ``<profile>/checkpoint-scenes/<session>/<checkpoint>.json``, beside the ``checkpoints`` folder, never in
it: Angelo 1.0.5 refuses any record field or file of a checkpoint folder it does not know, and then every Restore
in the profile, while nothing it runs lists the profile folder (``probe_downgrade_checkpoint_formats.py``). Written
after its capture, atomically (temporary file, fsync, rename), under the profile transaction; removed with its
checkpoint (``CheckpointRetentionMixin._remove``) and, when its checkpoint is gone or removed by any other way, by
the snapshot worker's maintenance (``drop_orphans``). Reading never raises: no map, or a malformed one, is None.
"""

from collections import deque
import json
from pathlib import Path
import threading

from .checkpoint_filesystem import (MAX_METADATA_BYTES, _private_directory, _read_json, _unlink_file, _write_json,
                                    list_names)
from .checkpoint_policy import _identity, checkpoint_ref
from .checkpoint_transactions import _transaction

FOLDER = "checkpoint-scenes"
SUFFIX = ".json"
# The map is advisory: past this share of the metadata limit it is left out (Restore then rebinds by name).
BUDGET = MAX_METADATA_BYTES // 8
_parents = {}   # (load serial, session uid) -> {(load serial, session uid) of each earlier key it continues}
_absent = {}    # A compacted key -> the loads its scene is in no capture of; its _parents are then all its ancestors.
_loads = {}     # A Restore's load -> (the loads it descends from, the session uids its scenes had right after the read).
_written = {}   # (profile, session, checkpoint) -> the load of each map this process wrote and has not removed.
_written_lock = threading.Lock()   # The snapshot worker's thread removes maps too (drop_orphans).


def capture(load, scenes):
    """The map for a capture of the document whose load serial is ``load``: what each of ``scenes`` is called now,
    by its session uid. None without a load serial, or past ``BUDGET``."""
    if type(load) is not str or not load:
        return None
    value = clean({"load": load, "names": [[scene.session_uid, scene.name_full] for scene in scenes]})
    if value is None or len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > BUDGET:
        return None
    return value


def clean(value):
    """A copy of a well-formed map, else None: exactly ``load`` (a non-empty string) and ``names`` (one or more
    pairs of a positive integer uid and a string name, no uid and no name twice)."""
    if type(value) is not dict or set(value) != {"load", "names"}:
        return None
    load, names = value["load"], value["names"]
    if type(load) is not str or not load or type(names) is not list or not names:
        return None
    if any(type(pair) is not list or len(pair) != 2 or type(pair[0]) is not int or pair[0] <= 0
           or type(pair[1]) is not str for pair in names):
        return None
    if len({uid for uid, _ in names}) != len(names) or len({name for _, name in names}) != len(names):
        return None
    return {"load": load, "names": [[uid, name] for uid, name in names]}


def _folder(root, session_id):
    return Path(root) / FOLDER / _identity(session_id)


def save(store, identity, value):
    """Keep ``value`` as checkpoint ``identity``'s map; False when there is none or it could not be written (Restore
    then rebinds by name). Never raises for storage: a capture never fails for its map."""
    if clean(value) is None:
        return False
    try:
        with _transaction(store.root), _private_directory(_folder(store.root, store.session_id), create=True) as folder:
            _write_json(folder, _identity(identity) + SUFFIX, clean(value))
        with _written_lock:
            _written[(str(Path(store.root)), store.session_id, identity)] = value["load"]
        return True
    except (OSError, ValueError, RuntimeError):   # RuntimeError: another app holds the profile transaction.
        return False


def read(store, identity):
    """Checkpoint ``identity``'s map, or None (none written, removed, unreadable or malformed)."""
    try:
        with _private_directory(_folder(store.root, store.session_id), lookup_only=True) as folder:
            return clean(_read_json(folder, _identity(identity) + SUFFIX))
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):   # AttributeError: no profile store.
        return None


def remove(root, session_id, identity):
    """Remove one checkpoint's map, if any. Callers hold the profile transaction."""
    with _written_lock:
        _written.pop((str(Path(root)), session_id, identity), None)
    try:
        with _private_directory(_folder(root, session_id)) as folder:
            _unlink_file(folder, _identity(identity) + SUFFIX)
    except FileNotFoundError:
        pass


def _maps(root):
    """{checkpoint ref: (session id, file name)} of every map file, and leftover temporary files [(session, name)]."""
    maps, temporary = {}, []
    try:
        with _private_directory(Path(root) / FOLDER) as base:
            sessions = list_names(base)
    except FileNotFoundError:
        return maps, temporary
    for session in sessions:
        try:
            with _private_directory(_folder(root, session)) as folder:
                names = list_names(folder)
        except (OSError, ValueError):
            continue
        for name in names:
            if name.endswith(".tmp"):
                temporary.append((session, name))
            elif name.endswith(SUFFIX):
                try:
                    maps[checkpoint_ref(session, _identity(name[:-len(SUFFIX)]))] = (session, name)
                except ValueError:
                    continue
    return maps, temporary


def drop_orphans(checkpoints, probed=None):
    """Remove the maps whose checkpoint no longer exists or was removed (PRUNED), and temporary files a crash left.

    A lock-free inventory (``probed``: the maintenance run's own, if it has one) selects the suspects; each is checked
    again under the profile transaction, where no map is being written. An unreadable record keeps its map (fail
    closed). Returns how many maps were removed."""
    from .checkpoint_inventory import inventory
    maps, temporary = _maps(checkpoints.root)
    if not maps and not temporary:
        return 0
    records = (probed or inventory(checkpoints, validate_targets=False, probe=True))["checkpoints"]
    live = {item["ref"] for item in records if item["state"] != "PRUNED"}
    suspects = sorted(ref for ref in maps if ref not in live)
    if not suspects and not temporary:
        return 0
    removed = 0
    with _transaction(checkpoints.root):
        for session, name in temporary:
            try:
                with _private_directory(_folder(checkpoints.root, session)) as folder:
                    _unlink_file(folder, name)
            except (OSError, ValueError):
                continue
        for ref in suspects:
            session, identity = ref.split("/")
            try:
                record = checkpoints._session_store(session).load(identity)
            except FileNotFoundError:
                record = None
            except (OSError, ValueError, TypeError, RecursionError):
                continue
            if record is not None and record["state"] != "PRUNED":
                continue
            try:
                remove(checkpoints.root, session, identity)
                removed += 1
            except (OSError, ValueError):
                continue
    return removed


def _born(key):
    """The loads whose captures cannot hold ``key``'s scene: it was made in a Restore's load after that Restore read
    its file (not among the scenes it had then), so no capture of a load that Restore descends from has it."""
    provenance = _loads.get(key[0])
    return provenance[0] if provenance is not None and key[1] not in provenance[1] else frozenset()


def _evidence(document):
    """(``document``'s key and every earlier key it continues, the loads its scene is proven absent from): those that
    every line of its ancestry ends in. A compacted key lists all its ancestors and summarizes its ends."""
    start = tuple(document)
    seen, queue, keys, ends = {start}, deque([start]), {start}, []
    while queue:
        key = queue.popleft()
        parents = _parents.get(key, ())
        if key in _absent:
            keys.update(parents)
            ends.append(_absent[key])
            continue
        if not parents:
            ends.append(_born(key))
        for parent in parents:
            keys.add(parent)
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)
    return keys, (frozenset.intersection(*ends) if ends else frozenset())


def captured_name(value, document):
    """(True, the name ``document``'s scene had when the map ``value`` was captured, or None when it was not in that
    capture) when that scene comes from the capture's load, or was made in a Restore's load after a Restore of that
    load's file; (False, None) when the map cannot say, or when the keys it continues name two scenes of that load."""
    value = clean(value)
    if value is None or document is None:
        return False, None
    keys, absent = _evidence(document)
    uids = {uid for load, uid in keys if load == value["load"]}
    if len(uids) == 1:
        return True, dict((uid, name) for uid, name in value["names"]).get(uids.pop())
    return (True, None) if not uids and value["load"] in absent else (False, None)


def continues(key, document):
    """A chat's ``key`` after a load stands for ``document``'s scene, its scene before the load."""
    if tuple(key) != tuple(document):
        _parents.setdefault(tuple(key), set()).add(tuple(document))


def remember_restored(value, load, scenes, held):
    """After a Restore read the file captured with the map ``value`` into the load ``load``: each restored scene
    continues the captured scene of its name, and the load keeps its provenance. Only what this load's scenes and the
    chats' keys (``held``) reach is kept, compacted to the loads a map still names."""
    value = clean(value)
    keys = [(load, scene.session_uid) for scene in scenes]
    relevant = {load}
    if value is not None:
        captured = {name: uid for uid, name in value["names"]}
        for scene in scenes:
            if scene.name_full in captured:
                continues((load, scene.session_uid), (value["load"], captured[scene.name_full]))
        source = _loads.get(value["load"], (frozenset(),))[0]
        _loads[load] = (frozenset({value["load"]}) | source, frozenset(uid for _, uid in keys))
        relevant.add(value["load"])   # Its map was just read.
    with _written_lock:
        relevant.update(_written.values())
    summaries = {}
    for start in keys + [tuple(document) for document in held if document]:
        found, absent = _evidence(start)
        summaries[start] = ({key for key in found if key != start and key[0] in relevant}, absent & relevant)
    _parents.clear()
    _absent.clear()
    for start, (ancestors, absent) in summaries.items():
        if ancestors or absent:
            _parents[start], _absent[start] = ancestors, frozenset(absent)
    for known in list(_loads):
        if known in relevant:
            _loads[known] = (_loads[known][0] & relevant, _loads[known][1])
        else:
            del _loads[known]
