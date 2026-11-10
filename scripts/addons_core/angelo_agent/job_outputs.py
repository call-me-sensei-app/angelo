# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a background job writes, and how its output is published or discarded. No Blender dependency.

A job's output is a path in the chat's file workspace, admitted by the file
tools' rules (``files.FileWorkspace``). A job never moves, renames or replaces a
file: a name that is taken is never a refusal, the job writes under the next
free name (``job_output_names``), and its approval, start receipt and result
say which.

Every effect goes through the file tools' anchored I/O (``checkpoint_filesystem``:
POSIX directory descriptors, verified Windows handles). ``Staging`` holds the
workspace root and every folder down to the output's folder as verified,
no-follow handles from the moment it creates the job's hidden staging folder
(``.angelo-job-<id>``, carrying a marker naming its job, unreadable by the file
tools) until it has published or discarded it. Blender itself writes by path, so
``verify`` walks that path again without following a link and requires the very
folders it holds: at start, on every progress poll and before publishing. A link,
junction or moved folder anywhere on the way refuses the job (``OutputMoved``).

Publication is all or nothing and never overwrites: a folder output becomes a
new folder; a file output is published with every dependency its exporter wrote
beside it (USD textures), in their relative layout. Each name is created by a
call that fails on an existing name (a new folder; a new hard link, or on a volume without links a move, through
the held handles: ``job_output_links``), so a file that appears at any moment is never overwritten: the output takes
the next free name, a set the next free folder, whole. A failure takes back what this publication made (moved files
go back to staging; what can't is named, ``left_published``). Cancel or failure removes the staging folder, except
one that holds what a failed publication couldn't publish (kept, ``job_manager.kept_outputs``); an animation's
finished frames are kept (``job_recovery``).
"""

import errno
import os
import stat
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

from . import anchored_windows, job_output_links as pairs
from .job_output_links import held as _held, link_tree as _link_tree, publish_entry as _publish_entry
from .job_output_links import remove_made, restore, staged_identities, MoveJournal, names as _names
from .job_output_links import create_directory as _create_directory, remove_entry as _remove_entry, charge_metadata as _charge_metadata
from . import checkpoint_filesystem as cf
from .job_output_names import as_asked, candidates
from .job_manager import JobStartError
from .job_spec import JobRequestError
from .ui_text import n_

STAGING_PREFIX = ".angelo-job-"
MARKER = ".angelo-job"
MAX_DEPTH = 8          # Folder levels an exporter may write below the output.
MAX_ENTRIES = 20000    # Files and folders a publication walks.
REMOVE_ALL = float("inf")   # Removing the job's own folder is unbounded: a bounded one left big stagings behind.
# Reasons the model reads in English and the job card translates (job_chat), so they are fixed sentences.
MOVED = n_("A folder on the way to the job's output is a link or junction, or was moved; jobs write only inside "
           "the workspace, so nothing was published")
TAKEN = n_("The output's name and every name tried after it are taken; nothing was published")
PARTLY = "Publishing stopped partway: files it moved couldn't be moved back and stay published"
UNSAFE = n_("The job's staging folder holds something that can't be published safely (a link, a shared file, or "
            "too many or too deeply nested files); nothing was published")


class OutputMoved(JobStartError):
    """The output's folders are no longer the verified ones; nothing is written or published."""


def admit_output(workspace, relative):
    """The absolute output path for ``relative`` in ``workspace`` (raises JobRequestError, which changes nothing)."""
    if not cf.anchored_files_supported():
        raise JobRequestError("Background jobs need the file tools' anchored folder access, unavailable here",
                              code="UNSUPPORTED")
    try:
        path = as_asked(workspace.path(relative), relative)
    except OSError as error:   # A name this file system can't hold (longer than 255 bytes): said, no local path.
        raise JobRequestError("output %s can't be used here (%s); choose a shorter name"
                              % (relative, error.strerror or type(error).__name__), code="VALIDATION") from None
    except ValueError as error:
        path = _link_at_name(workspace, relative)   # A link at the name is a taken name: never followed or touched.
        if path is None:
            raise JobRequestError(str(error), code="VALIDATION") from None
    if path == workspace.root:
        raise JobRequestError("output must name a file or folder inside the workspace, not the workspace itself",
                              code="VALIDATION")
    return path   # A taken name is no refusal: the job writes under the next free name (job_output_names).


def _link_at_name(workspace, relative):
    """The output path when only its last part is a link (live or dangling) in a folder the file tools admit, else
    None: the job writes under the next free name beside it, and publication never follows or touches a link."""
    from .files import relative_path
    parts = PurePosixPath(str(relative).replace("\\", "/")).parts
    try:
        relative_path(str(relative))   # A hidden or private name is never an output, a link or not.
    except ValueError:
        return None
    if not parts:
        return None
    try:
        parent = workspace.path("/".join(parts[:-1])) if len(parts) > 1 else workspace.root
    except ValueError:
        return None
    path = Path(parent) / parts[-1]
    return path if path.is_symlink() or getattr(path, "is_junction", lambda: False)() else None


def _identity(folder):
    info = cf.directory_info(folder)
    return info.st_dev, info.st_ino


def _kind(folder, name):
    """``"file"`` (a regular file with one link), ``"folder"``; anything else (a link) is refused."""
    info = cf.entry_info(folder, name)
    if stat.S_ISDIR(info.st_mode):
        return "folder"
    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        return "file"
    raise OutputMoved(UNSAFE)


def _entry_identity(folder, name):
    info = cf.entry_info(folder, name)
    return info.st_dev, info.st_ino


def _exists(folder, name):
    try:
        cf.entry_info(folder, name)
    except FileNotFoundError:
        return False
    return True


class _Budget:
    def __init__(self, left=MAX_ENTRIES):
        self.left, self.files = left, 0

    def spend(self):
        self.left -= 1
        if self.left < 0:
            raise OutputMoved(UNSAFE)


def _remove(folder, name, budget, depth=0):
    """Remove one entry of a held folder and, for a real folder, everything in it (links are removed, not followed)."""
    budget.spend()
    info = cf.entry_info(folder, name)
    if stat.S_ISDIR(info.st_mode) and depth <= MAX_DEPTH:
        with cf._private_child(folder, name) as inner:
            for child in _names(inner):
                _remove(inner, child, budget, depth + 1)
    _remove_entry(folder, name)


class Staging:
    """A job's staging folder and the verified folders down to it, held from creation to publication or discard."""

    skip_requested = as_set = False
    announce = None

    def __init__(self, root, output, identity, skip_requested=False, as_set=False, announce=None):
        self.root, self.output, self.identity = Path(root), Path(output), identity
        # The requested name was taken when the job was approved: it writes under the next free name, as its
        # approval said, even if that name is freed meanwhile (job_host); a set (``as_set``) into a new folder.
        self.skip_requested, self.as_set, self.announce = skip_requested, as_set, announce
        self._identities = {}           # Name -> (device, file ID) of each entry this publication made.
        self._moved = MoveJournal()                # Each file it moved (a volume without links): job_output_links.publish_entry.
        self.left_published = []        # Workspace-relative paths of moved files an unpublish couldn't move back.
        self.name = STAGING_PREFIX + identity
        self.path = self.output.parent / self.name
        self._chain = ExitStack()       # Workspace root .. the output's folder.
        self._held = []                 # (path, identity) of each, root first.
        self.parent = self.folder = None
        self.published = None           # The published output's absolute path.
        self.linked = 0                 # Files the publication linked.
        self._made = []                 # Names this publication created in ``_target``.
        self._target = None
        self._created, self._staging_metadata = False, set()   # Legacy metadata has no removal authority.

    # Creation and verification ------------------------------------------

    def create(self):
        """Hold the folders down to the output (creating missing ones), then create the marked staging folder."""
        cf.require_anchored_files()
        try:
            folder = self._chain.enter_context(cf._private_directory(self.root))
            self._held.append((self.root, _identity(folder)))
            path = self.root
            for part in self.output.parent.relative_to(self.root).parts:
                try:
                    pairs.make_parent(folder, part)
                except FileExistsError:
                    pass  # An existing folder: _private_child refuses it if it is a link or junction.
                folder = self._chain.enter_context(cf._private_child(folder, part))
                path = path / part
                self._held.append((path, _identity(folder)))
            self.parent = folder
            self._staging_metadata = pairs.create_staging(
                folder, self.name, lambda: setattr(self, "_created", True))
            self.folder = self._chain.enter_context(cf._private_child(folder, self.name))
            descriptor = cf._open_file(self.folder, MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(self.identity)
        except ValueError:
            self.discard()
            raise OutputMoved(MOVED) from None
        except OSError as error:
            self.discard()
            if error.errno in (errno.ELOOP, errno.ENOTDIR):
                raise OutputMoved(MOVED) from None
            raise JobStartError("Couldn't create the job's staging folder (%s)" % (error.strerror or type(error).__name__)) from None
        return self

    def verify(self):
        """The output's path still walks, without a link, to the very folders held since creation."""
        try:
            with cf._private_directory(self.path, within=self.root) as current:
                same = _identity(current) == _identity(self.folder)
            for path, identity in self._held:
                with cf._private_directory(path, within=self.root) as current:
                    same = same and _identity(current) == identity
        except (ValueError, OSError):
            same = False
        if not same:
            raise OutputMoved(MOVED)

    # Reading ---------------------------------------------------------------

    def files(self, suffixes):
        """Whole output files in the staging folder, sorted (the marker excluded)."""
        if self.folder is None:
            return []
        names = []
        for name in _names(self.folder):
            if name != MARKER and name.lower().endswith(tuple(suffixes)):
                info = cf.entry_info(self.folder, name)
                if stat.S_ISREG(info.st_mode):
                    names.append(name)
        return sorted(names)

    def tree(self):
        """Every regular staged payload as a sorted relative posix path (marker metadata excluded).
        A link, a shared file, or more or deeper entries than a publication walks refuses the job
        (``OutputMoved``), as publishing them would.
        """
        if self.folder is None:
            return []
        found, budget = [], _Budget()

        def walk(folder, prefix, depth):
            if depth > MAX_DEPTH:
                raise OutputMoved(UNSAFE)
            for name in _names(folder, budget):
                if depth == 0 and name == MARKER:
                    continue
                budget.spend()
                if _kind(folder, name) == "file":
                    found.append(prefix + name)
                else:
                    with cf._private_child(folder, name) as inner:
                        walk(inner, prefix + name + "/", depth + 1)

        walk(self.folder, "", 0)
        return sorted(found)

    def remove_files(self, paths):
        """Remove staged files by relative posix path (an unfinished frame's): they are never published."""
        for path in paths:
            *folders, name = PurePosixPath(path).parts
            with ExitStack() as stack:
                folder = self.folder
                for part in folders:
                    folder = stack.enter_context(cf._private_child(folder, part))
                cf.remove_entry(folder, name)

    # Publication -------------------------------------------------------------
    def _candidate(self, folder_output, has_dependencies):
        """Publication names in approval order (job_output_names)."""
        return candidates(self.output.name, self.identity, folder_output, has_dependencies)

    def _announce(self, name):
        """Persist a multi-file publication's destination before its first link, including unfinished crash output."""
        if self.announce is not None:
            self.announce(None if name is None else str(PurePosixPath(
                *(self.output.parent / name).relative_to(self.root).parts)))

    def _make(self, target, name, create):
        """``create(target, name)``, never over an existing name: True when made (and its identity kept for
        ``unpublish``: what ``create`` returns: a new link's is its staging file's, known before it exists, a copy's
        and a new folder's are taken on a handle of what it made), False when the name is taken or too long for
        this file system. Never a lookup of the name after it is made: it could find another process's entry."""
        try:
            made = create(target, name)
        except FileExistsError:
            return False
        except anchored_windows.UnusableName:   # A name this file system can't hold: the next one.
            return False
        except OSError as error:
            if error.errno != errno.ENAMETOOLONG:
                raise
            return False
        self._made.append(name)
        self._identities[name] = made
        return True

    def _new_folder(self, target, name):
        """A new folder ``name`` in ``target`` (``_make``'s ``create``); its identity, taken on a handle of it."""
        return _create_directory(target, name, self.folder, self._moved)

    def publish_folder(self):
        """The staging folder's files become a new output folder, under the first free name; returns its path."""
        self.verify()
        self.tree()  # Validate the complete owned pair inventory before creating any destination.
        try:
            return self._publish_folder()
        except BaseException:
            self.unpublish()   # Nothing stays half published.
            raise

    def _publish_folder(self):
        budget = _Budget()
        names = self._candidate(True, False)
        for name in names[1:] if self.skip_requested else names:
            self._target, self._made, self._identities, self._moved = self.parent, [], {}, MoveJournal()
            if not self._make(self.parent, name, self._new_folder):
                continue
            self._announce(name)
            with _held(self.parent, name, self._identities[name]) as made:   # A failure: publish_folder unpublishes.
                _link_tree(self.folder, made, budget, moved=self._moved, placed=(name,))
            self.published, self.linked = self.output.parent / name, budget.files
            return self.published
        raise OutputMoved(TAKEN)

    def publish_file(self, main):
        """Publish main plus its complete dependency set; collisions choose the next approved file/set-folder name.
        The approval's as_set choice remains in force even without dependencies (job_output_names).
        """
        self.verify()
        self.tree()  # Unknown nested metadata must refuse before the main file moves.
        dependencies = [name for name in _names(self.folder) if name not in {MARKER, main}]
        if _kind(self.folder, main) != "file":
            raise OutputMoved(UNSAFE)
        try:
            return self._publish_file(main, dependencies)
        except BaseException:
            self.unpublish()   # Nothing stays half published.
            raise

    def _publish_file(self, main, dependencies):
        set_like = bool(dependencies) or self.as_set   # A set falls back to a folder, as job_output_names says.
        names = self._candidate(False, set_like)
        budget = _Budget()
        self._target, self._made, self._identities, self._moved = self.parent, [], {}, MoveJournal()
        if not set_like:
            for name in names[1:] if self.skip_requested else names:
                if self._make(self.parent, name,
                              lambda target, entry: _publish_entry(self.folder, main, target, entry, self._moved,
                                  before_move=lambda: _charge_metadata(self.folder, main, budget))):
                    self.published, self.linked = self.output.parent / name, 1   # One link: never half published.
                    return self.published
            raise OutputMoved(TAKEN)
        names_free = not any(_exists(self.parent, name) for name in [self.output.name, *dependencies])
        if names_free and not self.skip_requested:
            self._announce(self.output.name)
            try:
                self._publish_set(self.parent, main, dependencies, budget)
                self.published, self.linked = self.output, budget.files
                return self.published
            except FileExistsError:   # A name of the set appeared meanwhile: what was made goes, the set moves whole.
                if not self.unpublish():   # Never a wrapper publication of a set missing what stays out here.
                    raise OSError(errno.EIO, PARTLY, ", ".join(self.left_published)) from None
                budget = _Budget()
        for wrapper in names[1:]:   # The set as a whole moves to a new folder, so its relative references resolve.
            self._target, self._made, self._identities, self._moved = self.parent, [], {}, MoveJournal()
            if not self._make(self.parent, wrapper, self._new_folder):
                continue
            self._announce(wrapper + "/" + self.output.name)
            with _held(self.parent, wrapper, self._identities[wrapper]) as folder:   # A failure: unpublished.
                self._publish_set(folder, main, dependencies, budget, inside=(wrapper,))
            self.published, self.linked = self.output.parent / wrapper / self.output.name, budget.files
            return self.published
        raise OutputMoved(TAKEN)

    def _publish_set(self, target, main, dependencies, budget, inside=False):
        """The set's entries at ``target``: at the output's folder each is kept for ``unpublish``; inside a new folder
        this publication made, that folder is."""
        within = tuple(inside or ())   # Inside the new folder this publication made (its name), or at the target.

        def make(name, create):   # The identity of what it made.
            if inside:
                return create(target, name)
            if not self._make(target, name, create):
                raise FileExistsError(errno.EEXIST, "taken", name)
            return self._identities[name]

        def entry(staged):   # A staged file published as ``name``, recorded where it went (job_output_links).
            return lambda folder, name: _publish_entry(self.folder, staged, folder, name, self._moved, (),
                                                       (*within, name), before_move=lambda:
                                                       _charge_metadata(self.folder, staged, budget))
        make(self.output.name, entry(main))
        budget.files += 1
        for name in dependencies:
            budget.spend()
            if _kind(self.folder, name) == "file":
                make(name, entry(name))
                budget.files += 1
                continue
            made_folder = make(name, self._new_folder)
            with cf._private_child(self.folder, name) as inner, _held(target, name, made_folder) as made:
                _link_tree(inner, made, budget, 1, self._moved, (name,), (*within, name))

    def unpublish(self):
        """Remove what the last publication created: only the entries it made, each still the very entry it made
        (an entry swapped in under one of its names is left as it is); the staging copy stays whole: every file a
        publication moved (a volume without links) goes back into staging first (``restore``). Returns whether all of
        it went: a moved file that can't be moved back stays published, never removed, and is named in
        ``left_published``."""
        ours = None
        for name in reversed(self._made):
            try:
                if (_entry_identity(self._target, name) == self._identities.get(name)
                        and not restore(self.folder, self._target, name, self._moved)):   # Moved back: nothing left.
                    ours = (staged_identities(self.folder) | self._moved.metadata) if ours is None else ours
                    remove_made(self.folder, self._target, name, ours)   # Only what this publication made.
            except (ValueError, OSError, OutputMoved):
                pass   # What can't be taken back stays (NotRestored): its records stay in ``_moved``.
        left = [PurePosixPath(*(self.output.parent / Path(*place)).relative_to(self.root).parts).as_posix()
                for place, *_rest in self._moved]
        self.left_published += left
        self._made, self._identities, self._moved, self.published = [], {}, MoveJournal(), None
        self._announce(None)
        return not left

    def relative_output(self):
        """The published output relative to the workspace (posix separators), for receipts."""
        return str(PurePosixPath(*self.published.relative_to(self.root).parts)) if self.published else None

    def discard(self):
        """Remove the staging folder (through the held folders) and release them; safe to call twice."""
        try:
            if self.folder is not None and self.parent is not None:
                pairs.require_owned_partner(self.parent, self.name, self._staging_metadata)
                for name in _names(self.folder):
                    _remove(self.folder, name, _Budget(REMOVE_ALL))
        except (ValueError, OSError, OutputMoved):
            pass
        finally:   # With the folders released, nothing may unpublish through them: what was published is final.
            self.folder, self._made, self._target, self.published = None, [], None, None
            parent, self.parent = self.parent, None
            try:
                if parent is not None and self._created:
                    _remove_entry(parent, self.name, self._staging_metadata)
            except (ValueError, OSError):
                pass
            self._created = False
            self._chain.close()

    def close(self):
        """Release the held folders after a publication (the staging folder was emptied and removed)."""
        self.discard()


def _marked(folder, identity):
    try:
        descriptor = cf._open_file(folder, MARKER, os.O_RDONLY)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            return handle.read(64) == identity
    except (ValueError, OSError, UnicodeError):
        return False


def is_staging(path, identity):
    """Only a folder this job created: the right name, a real folder (no link on the way) and its marker."""
    path = Path(path)
    if path.name != STAGING_PREFIX + identity:
        return False
    try:
        with cf._private_directory(path) as folder:
            return _marked(folder, identity)
    except (ValueError, OSError):
        return False


def discard_path(path, identity):
    """Remove a staging folder a previous run left (a crash): only this job's, held while it is checked and removed."""
    path = Path(path)
    if path.name != STAGING_PREFIX + identity:
        return False
    try:
        with cf._private_directory(path.parent) as parent:
            pairs.require_owned_partner(parent, path.name, set())  # No legacy pair authority.
            with cf._private_child(parent, path.name) as folder:
                if not _marked(folder, identity):
                    return False
                for name in _names(folder):
                    _remove(folder, name, _Budget(REMOVE_ALL))
            cf.remove_entry(parent, path.name)
    except (ValueError, OSError, OutputMoved):
        return False
    return True
