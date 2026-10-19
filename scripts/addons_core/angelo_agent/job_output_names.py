# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The names a job's output is published under: its own name, else the next free one with the job's ID.

A job never moves, renames or replaces a file: when its output's name is taken, it writes under the next free name.
One rule for every place that needs it, with no disk access of its own: publication tries the names in order with a
create that never replaces (``job_outputs.Staging``), and the approval and the start receipt state the name the job
writes under before anything is written (``job_host.output_location``), so they never disagree.
"""

from pathlib import PurePosixPath

MAX_NAMES = 64   # Names tried: the output's own, then 63 with the job's ID; past them nothing is published (TAKEN).
MAX_BYTES = 255  # A name's length every file system Angelo writes to allows (UTF-8 bytes: APFS, ext4; NTFS counts less).


def candidates(name, identity, folder_output, has_dependencies):
    """The entry names an output called ``name`` (its last part) is published under, in order: ``name`` itself, then
    ``<name>-<id>``, ``<name>-<id>-2`` ... for a folder; ``<stem>-<id>`` ... (a new folder the set moves into, its files
    keeping their names) for a file with others beside it; or ``<stem>-<id><suffix>`` ... for a lone file."""
    path = PurePosixPath(name)
    if folder_output:
        stem, suffix = path.name, ""
    elif has_dependencies:
        stem, suffix = path.stem, ""
    else:
        stem, suffix = path.stem, path.suffix
    return [path.name] + [_fit(stem, "-%s%s%s" % (identity, "" if number == 1 else "-%d" % number, suffix))
                          for number in range(1, MAX_NAMES)]


def _fit(stem, tail):
    """``stem + tail``, the stem shortened (never the job's ID or the extension) to fit ``MAX_BYTES``."""
    while stem and len((stem + tail).encode("utf-8")) > MAX_BYTES:
        stem = stem[:-1]
    return stem + tail


def published_path(relative, entry, publishes):
    """The workspace-relative path of an output ``relative`` published as top-level ``entry`` (a set's folder holds
    the file under its own name)."""
    path = PurePosixPath(relative)
    if entry == path.name:
        return str(path)
    return str(path.with_name(entry) / path.name) if publishes == "file_set" else str(path.with_name(entry))


def entries(relative, identity, publishes):
    """``(entry, workspace-relative path)`` in publication order for ``relative``.

    ``publishes`` is ``"folder"``, ``"file"`` (one file) or ``"file_set"`` (a file its exporter may write others
    beside: a taken set moves whole into a new ``<stem>-<id>`` folder)."""
    names = candidates(PurePosixPath(relative).name, identity, publishes == "folder", publishes == "file_set")
    return [(name, published_path(relative, name, publishes)) for name in names]


def fallback_name(relative, identity, publishes):
    """The workspace-relative name a job publishes under when ``relative`` is taken as it ends (the first fallback)."""
    return entries(relative, identity, publishes)[1][1]


def free_names(relative, identity, publishes, taken):
    """``(writes, if_taken)``: the path the job writes under now (``relative`` unless ``taken`` says that entry is
    taken, then the next free name) and the one after it, for when that is taken too as the job ends. ``taken(entry)``
    says whether a top-level entry beside the output exists now."""
    free = [path for entry, path in entries(relative, identity, publishes) if not taken(entry)]
    first = free[0] if free else None
    later = [path for path in free if path != first]
    return first, (later[0] if later else None)
