# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linux: whether this install can update itself, and unpacking and checking a downloaded release.

Contract: doc/angelo/updates_and_reports.rst, "Installing updates" and "Linux release signing". Angelo for Linux
is a ``tar.xz`` the person extracts into a folder of their own (``Angelo-<version>-linux-x64``). It updates itself
only when that folder is a release exactly as extracted:

* the folder holds the release's signed manifest (``release_manifest``) for the running version, so a development
  build, a distribution's repackaging or a copy without it keeps the notify-only behaviour (``location``);
* the folder and the one around it are writable and not on a read-only file system (``permission``, for example an
  install in ``/opt``; ``location`` for a read-only mount);
* nothing is in the folder that the manifest does not list (``modified``): the swap moves the whole folder aside and
  the next version removes it, so a file the person keeps there (a ``portable`` settings folder, an add-on copied in)
  would be lost. Only names are compared here; contents are the person's business.

``stage`` unpacks the downloaded tarball (its SHA-256 already checked against the signed feed by
``update_download``) into the update's own folder with ``tarfile``'s ``data`` filter, which refuses absolute paths,
links that leave the folder, device files and set-id bits. The result is accepted only when it is exactly one
folder, ``Angelo-<version>-linux-x64``, that ``release_manifest.check_tree`` accepts against the Linux release keys
this build ships: the release key's signature, then every file's size, SHA-256 and executable bit, every link and
folder, and nothing else. That is the Linux counterpart of the macOS code requirement and the Windows same-publisher
check, and it also catches a partial extraction. ``fingerprint`` records the checked stage cheaply so it is checked
again before the installer starts (``update_flow.Preparer.still_valid``). The installer
(``update_helpers/install_update_linux.sh``) swaps the folders with two renames.

Standard library only; no bpy. Blocking: call it from a worker thread.
"""

import hashlib
import os
import shutil
import stat
import tarfile
from pathlib import Path

from . import owned_paths, release_manifest

PLATFORM = "linux-x64"
SEARCH_DEPTH = 3   # The executable may sit in a sub-folder of the release (bin/); never further up than this.
# How large an update may unpack, as a multiple of its download: the free space the preparation reserves
# (update_flow.UNPACKED_FACTOR) and the unpacking's own stop are this one number.
UNPACK_FACTOR = 6


class StageError(Exception):
    """``code``: archive, layout, or a ``release_manifest.ManifestError`` code."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def install_root(binary):
    """The release folder the running executable belongs to: the nearest folder above it that holds a release
    manifest, or None."""
    try:
        folder = Path(binary).resolve().parent
    except (OSError, RuntimeError):
        return None
    for _ in range(SEARCH_DEPTH):
        if (folder / release_manifest.MANIFEST_NAME).is_file():
            return folder
        if folder.parent == folder:
            break
        folder = folder.parent
    return None


def _kind(mode):
    return "link" if stat.S_ISLNK(mode) else "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"


def unlisted(root, version):
    """The entries in ``root`` (relative, sorted) that are not what its own manifest for ``version`` lists there: a
    name it doesn't list, or a listed name that is now another kind of entry (a released file or link replaced by a
    folder of the person's files). Every real folder is walked, whatever the manifest says. Names and kinds only: no
    file is read or hashed, so this is cheap enough for the once-per-session support check. Raises
    ``release_manifest.ManifestError`` when the manifest cannot be read for that version."""
    listed = release_manifest.parse_manifest(
        release_manifest.read_own_file(root, release_manifest.MANIFEST_NAME, release_manifest.MAX_MANIFEST), version)
    found, pending = [], [("", Path(root))]
    while pending:
        prefix, folder = pending.pop()
        with os.scandir(folder) as listing:
            for item in listing:
                path = prefix + item.name
                if not prefix and item.name in release_manifest.OWN_FILES:
                    continue
                kind = _kind(item.stat(follow_symlinks=False).st_mode)
                if path not in listed or listed[path]["type"] != kind:
                    found.append(path)
                if kind == "dir":
                    pending.append((path + "/", Path(item.path)))
    return sorted(found)


_PRIVATE_GROUPS = {}   # (gid, uid): a proven answer for this session; a directory service may be slow to enumerate.


def private_group(gid, uid, grp_module=None, pwd_module=None):
    """True only when group ``gid`` is shown to be user ``uid``'s private group (the user-private-group convention
    behind umask 002): named after the user, no member but the user, and no other local account with it as its
    primary group. A directory service that does not enumerate its accounts is not seen, so the name rule carries
    the proof there; any lookup that fails counts as shared, for now only: a failure (sssd restarting) is never kept
    for the session, so the next check asks again."""
    if grp_module is None and pwd_module is None and (gid, uid) in _PRIVATE_GROUPS:
        return _PRIVATE_GROUPS[(gid, uid)]
    answer = _prove_private_group(gid, uid, grp_module, pwd_module)
    if grp_module is None and pwd_module is None and answer is not None:
        _PRIVATE_GROUPS[(gid, uid)] = answer
    return bool(answer)


def _prove_private_group(gid, uid, grp_module, pwd_module):
    try:
        grp_module = grp_module or __import__("grp")
        pwd_module = pwd_module or __import__("pwd")
        user = pwd_module.getpwuid(uid).pw_name
        group = grp_module.getgrgid(gid)
        if group.gr_name != user or set(group.gr_mem) - {user}:
            return False
        return not any(entry.pw_gid == gid and entry.pw_uid != uid for entry in pwd_module.getpwall())
    except Exception:   # KeyError for an unknown id, a directory service that fails: no answer, never cached.
        return None


def ownership(root, grp_module=None, pwd_module=None):
    """"" when ``root`` is the person's own and only theirs, ``permission`` when it is someone else's, ``shared``
    when the person's folder is opened to a group or to everyone. A release the person extracted is ``0755``
    whatever their umask (extraction only removes mode bits); a folder the person made and extracted into
    (``tar --strip-components=1``) is ``0775`` under umask 002, which is still theirs alone when that group is
    provably their private one (``private_group``). The update replaces the folder with a ``0755`` one either way."""
    info = os.stat(root)
    if info.st_uid != os.geteuid():
        return "permission"
    if info.st_mode & stat.S_IWOTH or (info.st_mode & stat.S_IWGRP
                                       and not private_group(info.st_gid, info.st_uid, grp_module, pwd_module)):
        return "shared"
    parent = os.stat(Path(root).parent)
    if parent.st_uid != os.geteuid() and not parent.st_mode & stat.S_ISVTX \
            and parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "shared"   # Others may rename the person's folder away under the swap.
    return ""


def support(binary, version):
    """``(reason, root)``: reason "" when this install can replace itself, else ``location``, ``untrusted`` (this
    build trusts no Linux release key, so nothing could pass the check: the download is offered instead of being
    fetched every session), ``modified``, ``permission`` (someone else's folder, such as ``/opt``) or ``shared`` (the
    person's folder, opened to others: ``ownership``)."""
    root = install_root(binary)
    if root is None:
        return "location", None
    try:
        if os.statvfs(root).f_flag & os.ST_RDONLY:
            return "location", root
        if not release_manifest.load_keys():
            return "untrusted", root
        if unlisted(root, version):
            return "modified", root
        reason = ownership(root)
        if reason:
            return reason, root
    except (OSError, release_manifest.ManifestError):
        return "location", root
    if not os.access(root.parent, os.W_OK | os.X_OK) or not os.access(root, os.W_OK | os.X_OK):
        return "permission", root
    return "", root


def staged_path(work, version):
    """Where a checked release waits for the installer: ``<work>/staged/Angelo-<version>-linux-x64``."""
    return Path(work) / "staged" / release_manifest.root_name(version, PLATFORM)


def stage(tarball, version, work, keys=None, signature=None, max_bytes=None, name=None):
    """Check and unpack ``tarball``, then check it is exactly the signed release ``version``; the staged folder and
    the key that signed its manifest.

    The download is opened once (never through a link) and read from that one handle throughout. ``signature`` is
    its ``.minisig`` text: verified against the Linux release keys over those bytes, its trusted comment naming
    ``name`` (the file name the feed publishes; default the download's own), before ``lzma`` or ``tarfile`` read any
    of them, so bytes the release key did not sign are never parsed (the feed's SHA-256 alone named them).
    Unpacking gives every mode explicitly (never the process umask) and stops at ``max_bytes`` (default
    ``UNPACK_FACTOR`` times the download, the free space the preparation reserved). Raises ``StageError``, and then
    nothing of it is left behind (a stage is only ever used through the ``ready.json`` a successful check writes)."""
    keys = release_manifest.load_keys() if keys is None else keys
    try:
        descriptor = os.open(tarball, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise StageError("archive", "The update could not be opened: %s" % error) from None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise StageError("archive", "The update is not a file")
        try:
            release_manifest.verify_minisig(tarball, signature, keys, name or Path(tarball).name, handle)
        except release_manifest.ManifestError as error:
            raise StageError(error.code, str(error)) from None
        handle.seek(0)
        return _unpack(handle, version, work, keys, UNPACK_FACTOR * info.st_size if max_bytes is None else max_bytes)


def _unpack(handle, version, work, keys, limit):
    owned_paths.require_writable(work)
    staged = staged_path(work, version)
    folder = staged.parent
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(mode=0o700, parents=True)
    try:
        try:
            with tarfile.open(fileobj=handle, mode="r:xz") as archive:
                archive.extractall(folder, filter=release_manifest.extraction_filter(limit))
        except release_manifest.ManifestError as error:
            raise StageError(error.code, str(error)) from None
        except (tarfile.TarError, OSError, EOFError, ValueError) as error:
            raise StageError("archive", "The update could not be unpacked: %s" % error) from None
        if list(folder.iterdir()) != [staged] or not staged.is_dir() or staged.is_symlink():
            raise StageError("layout", "The update is not one folder named %s" % staged.name)
        # Every mode given explicitly, the top folder's too: a tarball without a member for it would leave it to
        # the umask (tarfile makes it implicitly), and the person's folder must never be writable by others.
        os.chmod(staged, 0o755)
        try:
            key_id = release_manifest.check_tree(staged, version, keys, PLATFORM)
        except release_manifest.ManifestError as error:
            raise StageError(error.code, str(error)) from None
        return staged, key_id
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def staged_key(staged, keys=None):
    """The key id when the staged release's manifest signature still verifies with this build's keys now (a key
    revoked since, or a build that trusts none, refuses it); raises ``release_manifest.ManifestError``. Reads the
    two small files only: the stage's fingerprint covers the rest."""
    keys = release_manifest.load_keys() if keys is None else keys
    manifest = release_manifest.read_own_file(staged, release_manifest.MANIFEST_NAME, release_manifest.MAX_MANIFEST)
    signature = release_manifest.read_own_file(staged, release_manifest.SIGNATURE_NAME, release_manifest.MAX_SIGNATURE)
    return release_manifest.verify_signature(manifest, signature, keys)


def fetch_signature(url, stream=None):
    """The ``.minisig`` text at ``url`` (an https address on an install host, already chosen through the mirror
    rules), at most ``release_manifest.MAX_MINISIG`` bytes; raises ``StageError`` (``signature_fetch``)."""
    from . import app_http
    stream = stream or app_http.stream
    try:
        status, _headers, body = stream(url, headers={"Accept-Encoding": "identity"}, timeout=30)
    except Exception as error:   # NetworkError and its kin: a transient failure, tried again at a later check.
        raise StageError("signature_fetch", "The update's signature could not be downloaded: %s" % error) from None
    try:
        if status != 200:
            raise StageError("signature_http_%d" % status, "The signature server answered %d" % status)
        data = body.read(release_manifest.MAX_MINISIG + 1)
    except StageError:
        raise
    except Exception as error:
        raise StageError("signature_fetch", "The update's signature could not be read: %s" % error) from None
    finally:
        try:
            body.close()
        except Exception:
            pass
    if len(data) > release_manifest.MAX_MINISIG:
        raise StageError("bad_minisig", "The update's signature is too large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise StageError("bad_minisig", "The update's signature is not text") from None


def fingerprint(tree):
    """A cheap digest of a staged release's layout (names, types, modes, sizes, times): it changes when anything in
    the folder is removed, added or rewritten after the check, so a changed stage is never installed."""
    digest = hashlib.sha256()
    root = Path(tree)
    for folder, folders, files in os.walk(root):
        folders.sort()
        for name in sorted(folders + files):
            path = os.path.join(folder, name)
            info = os.lstat(path)
            target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else ""
            # ctime and the inode too: a same-user rewrite can restore a file's size and mtime, never its ctime.
            digest.update(repr((os.path.relpath(path, root), info.st_mode, info.st_size, info.st_mtime_ns,
                                info.st_ctime_ns, info.st_ino, target)).encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def relaunch_path(binary, root):
    """The running executable's path inside ``root`` (POSIX, relative), which the installer starts afterwards."""
    relative = Path(binary).resolve().relative_to(Path(root).resolve()).as_posix()
    return release_manifest.check_path(relative)


def launcher_name():
    """The release's launcher at its top: the product's short name (``angelo``, application identity)."""
    try:
        import angelo_identity
        return str(angelo_identity.data()["product"]["slug"])
    except (ImportError, KeyError, TypeError, ValueError, OSError):
        return "angelo"


def relaunch_target(binary, root, launcher=None):
    """What the installer starts afterwards, relative to ``root``: the release's own launcher at its top (``launcher``,
    default ``launcher_name()``: it sets up the environment the executable expects) when it is there, else the running
    executable's own path."""
    launcher = launcher_name() if launcher is None else launcher
    candidate = Path(root) / launcher
    plain = isinstance(launcher, str) and launcher not in ("", ".", "..") and "/" not in launcher \
        and "\0" not in launcher
    if plain and candidate.is_file() and not candidate.is_symlink() and os.access(candidate, os.X_OK):
        return launcher
    return relaunch_path(binary, root)


def other_instances(binary, own_pid, proc="/proc"):
    """``[pid]`` of other processes running ``binary`` (another Angelo started from this folder, whose files the swap
    would move away under it), each named by the file the kernel runs."""
    found = []
    try:
        wanted = os.stat(binary)
    except OSError:
        return found
    try:
        names = os.listdir(proc)
    except OSError:
        return found
    for name in names:
        if not name.isdigit() or int(name) == own_pid:
            continue
        try:
            running = os.stat(os.path.join(proc, name, "exe"))
        except OSError:
            continue   # Gone, or another user's process.
        if (running.st_dev, running.st_ino) == (wanted.st_dev, wanted.st_ino):
            found.append(int(name))
    return sorted(found)


def pid_named(name, prefix, suffix=""):
    middle = name[len(prefix):len(name) - len(suffix)] if name.startswith(prefix) and name.endswith(suffix) else ""
    return middle.isdigit()


def installer_leftovers(root):
    """The Linux installer's pid-named folders beside ``root`` (``.<name>.previous-<n>``, ``.<name>.update-<n>``)."""
    try:
        entries = list(root.parent.iterdir())
    except OSError:
        return []
    return [entry for entry in entries if entry != root and os.path.isdir(entry) and not os.path.islink(entry)
            and (pid_named(entry.name, "." + root.name + ".previous-")
                 or pid_named(entry.name, "." + root.name + ".update-"))]
