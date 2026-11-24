# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where an FBX clip set may be written, before anything is (doc/angelo/rigging.rst, "Clips"; script_confinement.rst,
"Angelo's own I/O"). No Blender, so the host suite pins it (test_native_clip_paths).

``model_file(picked)``: the picked file itself when it ends in ``.fbx`` (any case), else it with ``.fbx``.
``clip_files(model, names)``: ``<stem>@<clip>.fbx`` beside it, one per action name, the name's path characters
replaced; two actions that would write one file (they clean to one name, or differ only in case: one file on macOS
and Windows) are refused by name (``ClipNameClash``), never one clip lost.

``claim(model, clips, confirmed=())``: the picked file is checked with C (``owned_paths.require_writable``) first. A
folder or a link at a clip name is refused by name (``NotAFile``): never replaced, so never offered to. An
existing clip file Angelo did not make this session is the person's: it is replaced only when they confirmed it
(``confirmed``: ticked in the same save browser) and C allows it for the call (the export vouches the
confirmed files with ``owned_paths.vouched_during``, C's call scope, refused while a script runs); otherwise
``ReplaceUnconfirmed`` names it, whatever C would answer (before any script region C allows every path; the
question is the same in both states). Then every existing clip file is checked with C, and only then is each
missing clip file made by C beside the picked one (``owned_paths.create_beside``, exclusively). A refusal at any
point writes nothing more and removes the clip files this claim made. Returns the files made, which the export
removes again if it fails.
"""

import os
import re

from . import owned_paths

UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class ClipNameClash(ValueError):
    """Actions whose clips would be one file; ``names`` lists them."""

    def __init__(self, names):
        super().__init__("these actions would write one clip file: %s" % ", ".join(names))
        self.names = list(names)


class NotAFile(PermissionError):
    """A folder or a link where a clip file would go: never replaced, so never offered to; ``paths`` lists them."""

    def __init__(self, paths):
        super().__init__("not a file: %s" % ", ".join(os.path.basename(p) for p in paths))
        self.paths = list(paths)
        self.refusal_code = "READ_ONLY"


class ReplaceUnconfirmed(PermissionError):
    """Existing clip files of the person's that need their confirm first; ``paths`` lists them."""

    def __init__(self, paths):
        super().__init__("these clip files exist: %s" % ", ".join(os.path.basename(p) for p in paths))
        self.paths = list(paths)
        self.refusal_code = "READ_ONLY"


def model_file(picked):
    stem, ext = os.path.splitext(picked)
    return picked if ext.lower() == ".fbx" else stem + ".fbx"


def clip_files(model, names):
    stem, _ = os.path.splitext(model)
    clips, seen = {}, {}
    for name in names:
        path = "%s@%s.fbx" % (stem, UNSAFE.sub("_", name))
        seen.setdefault(os.path.normcase(path).casefold(), []).append(name)
        clips[name] = path
    clashes = [name for group in seen.values() if len(group) > 1 for name in group]
    if clashes:
        raise ClipNameClash(clashes)
    return clips


def _in_the_way(clips):
    """A folder or a link at a clip name (a link is never followed, whatever it points at)."""
    return [path for path in clips if os.path.islink(path) or (os.path.lexists(path) and not os.path.isfile(path))]


def to_replace(clips):
    """The existing clip files Angelo did not make this session: the person's, to be confirmed. A folder or a link
    is not one (``NotAFile``)."""
    odd = set(_in_the_way(clips))
    return [path for path in clips if os.path.lexists(path) and path not in odd and not owned_paths.owned(path)]


def confirmed(shown, theirs):
    """The person's files a ticked Replace covers: those the browser showed that are still there. A file that
    appeared after it drew is not among them, so ``claim`` asks about it again."""
    seen = set(shown)
    return [path for path in theirs if path in seen]


def claim(model, clips, confirmed=()):
    owned_paths.require_writable(model)
    odd = _in_the_way(clips)
    if odd:
        raise NotAFile(odd)
    theirs = to_replace(clips)
    asked = [path for path in theirs if path not in set(confirmed)]
    if asked:
        raise ReplaceUnconfirmed(asked)
    refused = [path for path in theirs if not owned_paths.may_write(path)]
    if refused:   # Confirmed, but C holds no vouch for it (the caller vouches for the call; refused to a script).
        raise ReplaceUnconfirmed(refused)
    for path in clips:
        if os.path.lexists(path):
            owned_paths.require_writable(path)
    made = []
    try:
        for path in clips:
            if not os.path.lexists(path):
                made.append(owned_paths.create_beside(model, os.path.basename(path)))
    except BaseException:
        release(made)
        raise
    return made


def release(made):
    """Remove the clip files a claim made (only files Angelo made: ``owned_paths.unlink``)."""
    for path in made:
        owned_paths.unlink(path)
