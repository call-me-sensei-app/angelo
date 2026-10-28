# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every folder of Angelo application state on this computer, active or retained: one registry for every privacy
consumer (the file tools' admission, ``files.FileWorkspace``).

* the agent state folder (chats, snapshots, scene maps, task memory, the default workspace's parent) and the account
  store (connections, native CLI profiles);
* the pre-rename agent state folder beside it (``state_paths.legacy_sibling``), where ``state_migration`` leaves the
  native CLI profiles it retains and ``storage.native_profile_directory`` keeps using them;
* the per-user application data folder and its pre-rename one (``storage.data_directory``), where
  ``data_migration`` retains profiles under ``connections`` and other app-wide secrets live;
* each connection's saved native CLI profile (``native_profile``), wherever it is.

A folder that does not exist is listed anyway: it costs nothing and is private if it appears (break-it r2m4c round 2,
R2-M-1).

Folders Angelo makes for its own processes' private state (a worker Blender's, a headless session's: their state,
accounts and logs) are registered by their creators, where they are made, through ``allocate``; each creator also
declares its family (``declare``: every folder named ``<prefix>*`` in the temporary folder) when its module loads, so a
folder a crashed child left is private until its sweep removes it (round 3 M-1). File tools read the live registry on
every check (``registered``, ``families``), so a folder made after a workspace was set up is refused too.
"""

import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid

from . import state_paths, storage


# Every family of folders Angelo's own processes keep private state in (prefix, parent or None for the temporary
# folder): declared here, at this module's import, so a folder a crashed child left is private before any launcher
# module loads (round 5 M-1). Their creators take their prefixes from here.
# worker_process: a parallel-edit worker Blender's state, accounts and log; short, as its deepest path counts on
# Windows (``worker_paths``). Folders of earlier builds keep their name's family: a crash left them private.
WORKER_PREFIX, LEGACY_WORKER_PREFIX = "angw-", "angelo-worker-"
HEADLESS_PREFIX = "angelo-headless-"   # headless_session: a headless session's state, accounts and preferences.
# Every other temporary or scratch folder Angelo makes (round 7 M-4: a converter's batch holds private prompts), by
# its creator. ``allocate`` refuses a prefix that is not here, so the table is complete by construction.
SCRATCH_PREFIXES = ("angelo-assets-", "angelo-flow-drop-", "angelo-split-", "angelo-save-", "angelo-native-preview-",
                    "angelo-kept-", "angelo-team-", "angelo-web-preview-", "angelo-world-light-",
                    "angelo-observations-")
FAMILIES = ((WORKER_PREFIX, None), (LEGACY_WORKER_PREFIX, None), (HEADLESS_PREFIX, None)) + tuple(
    (prefix, None) for prefix in SCRATCH_PREFIXES)
# Native CLI homes an inherited environment can point anywhere (a reused sign-in: round 7 M-1), as runtime_launch and
# codex_runtime read them.
RUNTIME_HOME_VARIABLES = ("CODEX_HOME", "CODEX_SQLITE_HOME", "CLAUDE_CONFIG_DIR")
# Every native CLI profile folder a connection ever named, in the account store: private while it exists on disk,
# whatever the connections say now (a removed connection leaves its retained profile: round 5 M-2).
RETAINED_PROFILES = "retained-native-profiles.json"
# Every state folder and account store Angelo has used, in the per-user data folder (fixed, whatever the overrides
# say now): a former custom ANGELO_STATE_DIR or ANGELO_CONNECTIONS_DIR stays private (round 8 M-2).
USED_STORES = "used-private-stores.json"
LAST_GOOD = "retained-native-profiles.last-good.json"   # Written with it, under the same lock: a fresh process whose
                                                        # list is unreadable still refuses what it held.


def _profiles(records):
    found = []
    for record in records:
        profile = record.get("native_profile") if isinstance(record, dict) else None
        if isinstance(profile, str) and profile and Path(profile).is_absolute() and profile not in found:
            found.append(profile)
    return found


class RetainedUnknown(OSError):
    """The retained-profile list exists but cannot be read or understood now: which folders are private is unknown."""


class Roots(list):
    """``private_roots``' folders. ``complete`` is False when the retained-profile list could not be read: its last
    good entries are kept, so they stay refused, and nothing else is blocked; ``refresh()`` reads again, so recovery
    is automatic (round 7). Convention locations (``private_conventions``) are refused regardless."""
    complete = True
    arguments = None

    def refresh(self):
        """The set now: recomputed only when the list file or the environment's runtime homes changed."""
        if self.arguments is None:
            return self
        state_directory, connection_directory, records, environ = self.arguments
        signature = _registry_signature(connection_directory, environ)   # Every registry file (R9-M-3).
        current = (signature, tuple(runtime_homes(environ)))
        cached = getattr(self, "_cached", None)
        if signature is not None and cached is not None and cached[0] == current:
            return cached[1]
        fresh = _compute(*self.arguments)
        self._cached = (current, fresh)
        return fresh


def retained_profiles(connection_directory, name=None):
    """The native profile folders recorded in the account store (``remember_profiles``): [] when there is no list yet;
    ``RetainedUnknown`` when it exists but cannot be read, or is not a list of absolute folder paths (fail closed)."""
    try:
        text = (Path(connection_directory) / (name or RETAINED_PROFILES)).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as error:
        raise RetainedUnknown(error.errno, "The retained native-profile list can't be read") from error
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if type(value) is not list or any(type(item) is not str or not Path(item).is_absolute() for item in value):
        raise RetainedUnknown(errno.EINVAL, "The retained native-profile list is damaged")
    return value


def _gone(folder):
    """Whether ``folder`` is positively absent (the system says no such entry); any other answer keeps it."""
    try:
        os.lstat(folder)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _last_name(name):
    return LAST_GOOD if name == RETAINED_PROFILES else name.replace(".json", ".last-good.json")


def remember_profiles(connection_directory, records, locked=False, name=RETAINED_PROFILES):
    """Record the native profile folders ``records`` name, keeping each earlier one until it is positively gone.

    A merge under the account store's own lock (``connections.metadata_lock``; ``locked``: the caller holds it), so
    two writers never drop each other's entries (round 6 M-1): the list is read only after the lock is taken. A list
    that cannot be read is never rewritten (its entries would be lost). Atomic (temporary file, rename). Returns
    whether every named folder is recorded; never raises."""
    named = _profiles(records)
    folder = Path(connection_directory)
    if not named and not (folder / name).exists() and not (folder / _last_name(name)).exists():
        return True
    try:
        if locked:
            return _remember(folder, named, name)
        from .connections import metadata_lock
        with metadata_lock(folder, name + ".lock" if name != RETAINED_PROFILES else "connections.lock"):
            return _remember(folder, named, name)
    except (OSError, ValueError, TypeError):
        return False


def _write_list(folder, name, entries):
    descriptor, temporary = tempfile.mkstemp(prefix=".retained-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(entries, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, Path(folder) / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _remember(folder, named, name=RETAINED_PROFILES):
    known = retained_profiles(folder, name)   # RetainedUnknown: nothing is rewritten.
    if not (folder / name).exists():
        known = _last_good(folder, name)   # A missing list beside its last good copy was lost, not new (R9-M-5).
    kept = [item for item in known if item in named or not _gone(item)]
    merged = kept + [item for item in named if item not in kept]
    if merged != known:
        _write_list(folder, name, merged)
    if merged != _last_good(folder, name):
        _write_list(folder, _last_name(name), merged)   # The copy a fresh process falls back to (round 8 M-3).
    return True


def _last_good(folder, name=RETAINED_PROFILES):
    """The last good copy of a list beside it, or [] when there is none or it is unusable too."""
    try:
        return retained_profiles(folder, _last_name(name))
    except RetainedUnknown:
        return []


def _registry_signature(connection_directory, environ):
    """(device, inode, mtime, size) of every registry file this set depends on, or None when one cannot be checked:
    the account store's retained list and its last good copy, and the used-store list and its copy."""
    try:
        data = storage.data_directory()   # The process's own, as where it is written (private_roots).
    except ValueError:
        data = None
    files = [Path(connection_directory) / RETAINED_PROFILES, Path(connection_directory) / LAST_GOOD]
    if data is not None:
        files += [data / USED_STORES, data / _last_name(USED_STORES)]
    signature = []
    for path in files:
        try:
            info = os.stat(path)
            signature.append((info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size))
        except FileNotFoundError:
            signature.append(None)
        except OSError:
            return None
    return tuple(signature)


def _version_root(state_directory):
    """The product folder holding every version's state (``<product>/<major.minor>/config/angelo-agent``), or None:
    an older version's state is private too (round 7 M-3)."""
    path = Path(state_directory)
    parts = path.parent.parent.name.split(".")
    if (path.name == state_paths.STATE_FOLDER and path.parent.name == "config" and len(parts) == 2
            and all(part.isdigit() for part in parts)):
        return path.parent.parent.parent
    return None


def runtime_homes(environ=None):
    """The native CLI homes the environment names (absolute paths only)."""
    environ = os.environ if environ is None else environ
    return [Path(environ[name]) for name in RUNTIME_HOME_VARIABLES
            if environ.get(name) and Path(environ[name]).is_absolute()]


_retained_cache = {}   # account store -> (the list file's (device, inode, mtime, size), its last good entries)


def _retained_now(connection_directory, name=RETAINED_PROFILES):
    """(entries, complete): the list read again only when its file changed; when it cannot be read, its last good
    entries and complete False (fail closed narrowly: those folders stay refused; nothing else is blocked)."""
    folder = str(Path(connection_directory))
    key = (folder, name)
    try:
        info = os.stat(Path(folder) / name)
        signature = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
    except FileNotFoundError:
        if (Path(folder) / _last_name(name)).exists():
            return _fallback(folder, name), False   # Lost, not first use: its last good copy stands (R9-M-5).
        signature = None
    except OSError:
        return _fallback(folder, name), False
    cached = _retained_cache.get(key)
    if cached is not None and cached[0] == signature and signature is not None:
        return cached[1], True
    try:
        entries = retained_profiles(folder, name)
    except RetainedUnknown:
        return _fallback(folder, name), False
    _retained_cache[key] = (signature, entries)
    return entries, True


def _fallback(folder, name=RETAINED_PROFILES):
    """When the list cannot be read: this process's last good read and the last good copy beside it, together."""
    entries = list(_retained_cache.get((folder, name), (None, []))[1])
    return entries + [item for item in _last_good(folder, name) if item not in entries]


def _compute(state_directory, connection_directory, records, environ):
    roots = [Path(state_directory), Path(connection_directory)]
    for extra in (state_paths.legacy_sibling, lambda state, _environ: _version_root(state)):
        try:
            found = extra(state_directory, environ)
        except ValueError:
            found = None
        if found is not None:
            roots.append(found)
    for folder in (storage.DATA_FOLDER, storage.LEGACY_DATA_FOLDER):
        for source in (environ, None):   # The environment named, and the process's own (where Angelo writes).
            try:
                roots.append(storage.data_directory(source, folder=folder))
            except ValueError:
                continue
    retained, complete = _retained_now(connection_directory)
    try:
        stores, _ = _retained_now(storage.data_directory(), USED_STORES)
    except ValueError:
        stores = []
    retained = retained + [item for item in stores if item not in retained]
    unique = Roots()
    unique.complete = complete
    for root in roots + runtime_homes(environ) + [Path(profile) for profile in _profiles(records) + retained]:
        if root not in unique:
            unique.append(root)
    unique.arguments = (state_directory, connection_directory, records, environ)
    return unique


def private_roots(state_directory, connection_directory, records=(), environ=None):
    """The application-state folders for this state folder, account store and connection ``records``, with the
    native CLI homes the environment names; each named profile and home is recorded (``remember_profiles``). The
    result's ``refresh()`` gives the current set, so a workspace consumes later registrations (round 7 M-2)."""
    records = list(records)
    remember_profiles(connection_directory, records + [{"native_profile": str(home)}
                                                       for home in runtime_homes(environ)])
    try:   # Every state folder and account store used, wherever the overrides point later. The used-store list is in
        # the process's own data folder (as the scratch root): ``environ`` names runtime homes, never where Angelo
        # keeps its records, so reading and writing always agree (Linux CI, round 10: XDG_STATE_HOME).
        data = storage.data_directory()
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        remember_profiles(data, [{"native_profile": str(Path(folder).absolute())}
                                 for folder in (state_directory, connection_directory)]
                          + [{"native_profile": str(home)} for home in runtime_homes(environ)], name=USED_STORES)
    except (OSError, ValueError):
        pass
    return _compute(state_directory, connection_directory, records, environ)


_lock = threading.Lock()
_folders = []    # Each private folder a creator made in this process.
_owned = {}   # Each one's (device, inode / file index) -> the token in its owner file: a rename keeps it private,
              # and a freed identity the system gives another folder (Linux reuses inode numbers) does not.
OWNER = ".angelo-private-owner"   # Hidden, so no file tool can name it; written when the folder is made.
_families = [(parent, prefix) for prefix, parent in FAMILIES]   # (parent or None for the temporary folder, prefix).


SCRATCH = "scratch"


def scratch_root(environ=None):
    """Where Angelo's own process and scratch folders are made: ``<per-user data folder>/scratch``, a stable folder
    inside a private root, so a leftover is private by where it is, after a restart or a change of the temporary
    folder too (R9-M-2). The system temporary folder only when the data folder cannot be made."""
    try:
        root = storage.data_directory(environ) / SCRATCH
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root
    except (OSError, ValueError):
        return Path(tempfile.gettempdir())


def declare(prefix, parent=None):
    """A creator's family of private folders: ``<parent or the temporary folder>/<prefix>*``."""
    with _lock:
        if (parent, prefix) not in _families:
            _families.append((parent, prefix))


def allocate(prefix, parent=None):
    """Make a private folder (``tempfile.mkdtemp``) and register it and its family before anything is written there.
    ``prefix`` must be one of ``FAMILIES``: a crashed process's folder is then private from a fresh start."""
    if prefix not in {name for name, _ in FAMILIES}:
        raise ValueError("Add the private folder's prefix to private_roots.FAMILIES")
    declare(prefix, parent)
    folder = Path(tempfile.mkdtemp(prefix=prefix, dir=parent if parent is not None else scratch_root()))
    _own(folder)
    return folder


HANDLES = os.name != "nt"   # Whether folders are held open; Windows' file IDs are not reused soon, so it needs none.
_held = {}   # identity -> a descriptor held open on the folder (POSIX): while it is held the system cannot give the
             # folder's number to another one, and the folder is found wherever it was moved (R9-M-1, R9-M-6).


def _handle_gone(descriptor, identity):
    """True only on positive evidence that the held folder was removed; any uncertain lookup keeps it (R10-M-2).
    Linux: its link count is 0 (``fstat``). macOS (APFS keeps a removed folder's count): the path the system has for
    the handle no longer names it. Elsewhere: never."""
    try:
        if sys.platform.startswith("linux"):
            return os.fstat(descriptor).st_nlink == 0
        if sys.platform == "darwin":
            import fcntl
            where = os.fsdecode(fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0])
            try:
                info = os.stat(where)
            except FileNotFoundError:
                return True
            return (info.st_dev, info.st_ino) != identity
    except (OSError, ValueError, AttributeError):
        return False
    return False


def hold(path):
    """The identity of the folder at ``path``, held open so it keeps naming that folder (POSIX), or as is where the
    platform has no handles (Windows: NTFS file IDs carry a reuse sequence). None when absent or when the handle
    cannot be taken: an identity that is not held is never trusted (R10-M-1)."""
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    if not info.st_ino:
        return None
    identity = (info.st_dev, info.st_ino)
    if not HANDLES:
        return identity
    with _lock:
        if identity in _held:
            return identity
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return None
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != identity:
            os.close(descriptor)
            return None
        _held[identity] = descriptor
    return identity


def alive(identity):
    """True while the held folder exists (moved or not); False when it is gone (its handle released, its ownership
    retired for good) or was never held; None where the platform has no handles."""
    if not HANDLES:
        return None
    with _lock:
        descriptor = _held.get(identity)
    if descriptor is None:
        return False
    if not _handle_gone(descriptor, identity):
        return True
    with _lock:
        if _held.pop(identity, None) is not None:
            os.close(descriptor)
        _owned.pop(identity, None)
    return False


def prune():
    """Release the handles of folders that are gone."""
    with _lock:
        identities_now = list(_held)
    for identity in identities_now:
        alive(identity)


def _own(folder):
    """Register ``folder`` with an owner token written inside it (its identity counts only while the token is there)."""
    token = uuid.uuid4().hex
    try:
        descriptor = os.open(Path(folder) / OWNER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                             0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
    except OSError:
        token = None   # Unwritable: its path and family name still protect it.
    prune()
    identity = hold(folder)
    with _lock:
        _folders.append(Path(folder))
        if identity is not None:
            _owned[identity] = token or ""


def identities():
    """The identity of every private folder a creator made in this process, wherever it is now (renamed or moved:
    break-it r2m4c round 4 M-2). An identity names that folder only while its owner token is in it
    (``owner_token``): a removed folder's number, which Linux gives the next new folder, never makes that one
    private (C2 CI, Linux /tmp)."""
    with _lock:
        return set(_owned)


def owner_token(identity):
    """The token an allocated folder with ``identity`` holds in its owner file, or None."""
    with _lock:
        return _owned.get(identity)


def registered():
    """Every private folder a creator made in this process (cheap: no folder is listed)."""
    with _lock:
        return list(_folders)


def families():
    """[(parent folder, prefix)]: each declared family; its members are matched by name where a file tool meets them
    (``files.FileWorkspace``), never by listing a temporary folder that may hold a great many entries."""
    with _lock:
        declared = list(_families)
    found = []
    for parent, prefix in declared:   # The default: the scratch root, and the temporary folder (an earlier build's).
        for folder in ([Path(parent)] if parent is not None else [scratch_root(), Path(tempfile.gettempdir())]):
            if (folder, prefix) not in found:
                found.append((folder, prefix))
    return found


def mkdtemp(prefix, dir=None):
    """``tempfile.mkdtemp`` for Angelo's own folders: registered (``allocate``)."""
    return str(allocate(prefix, dir))


class TemporaryDirectory(tempfile.TemporaryDirectory):
    """``tempfile.TemporaryDirectory`` for Angelo's own folders: registered (``allocate``), removed as usual."""

    def __init__(self, prefix, dir=None):
        if prefix not in {name for name, _ in FAMILIES}:
            raise ValueError("Add the private folder's prefix to private_roots.FAMILIES")
        super().__init__(prefix=prefix, dir=dir if dir is not None else scratch_root())
        declare(prefix, dir)
        _own(self.name)

    def cleanup(self):
        super().cleanup()
        prune()   # Its handle goes with it.
