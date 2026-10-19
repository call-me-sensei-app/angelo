# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_blend_file`` for the open file: what the document holds, seen as a file.

The scene reads answer for objects of the bound scene. This answers for the
file: how many datablocks of each type it holds and which have no user (data a
save and reload drops), which libraries it links, which external files its
datablocks name and whether each is packed, present or missing, and which
references do not resolve (linked data its library no longer has, node types
this build does not know, group nodes without a group).

One pass over ``bpy.data`` reads identity only (name, users, fake user,
library, missing flag): no datablock contents, no evaluation, nothing written.
External files come from Blender's own path walk (``bpy.data.file_path_map``)
and are named by ``blend_file_paths``: relative to the file or the workspace, or
by base name, never by an absolute path. Each existence check is one ``stat``.

Bounds: 200,000 datablocks, 512 file checks, 20,000 nodes and a two-second
cooperative deadline; a part that stops early says so. The summary shows the
first rows of each part; ``list`` pages one part or one datablock type, 64 rows
a page. Main thread only; refuses while a staged change's private copies are
in the file, because they would be counted as the artist's data.
"""

import os
import time

from . import blend_file_paths as paths
from .read_spec import SECTIONS, validate_read

MAX_DATABLOCKS = 200000
MAX_FILE_CHECKS = 512
MAX_NODES = 20000
DEADLINE_SECONDS = 2.0
SUMMARY_ROWS = 24
MAX_NAME = 128
# Types whose zero user count means nothing: Blender keeps them without users.
NEVER_UNUSED = frozenset({"scenes", "window_managers", "screens", "workspaces", "libraries"})
SKIPPED_COLLECTIONS = frozenset({"all_ids"})
UNUSED_RULE = ("Unused: no user, so a save and reload drops it. Data used only by other unused data is not counted, "
               "and a fake user keeps data that nothing else uses (kept_by_fake_user).")
NODE_OWNERS = ("materials", "worlds", "lights", "textures", "linestyles")
# Datablock types whose external file can be packed into the .blend.
PACKABLE = ("images", "sounds", "fonts", "volumes")


class _Scan:
    """One bounded pass over the open file's datablocks."""

    def __init__(self, bpy, workspace_root):
        self.bpy = bpy
        self.deadline = time.monotonic() + DEADLINE_SECONDS
        self.incomplete = []
        filepath = bpy.data.filepath
        self.blend_folder = os.path.dirname(filepath) if filepath else ""
        self.workspace_root = str(workspace_root) if workspace_root else ""
        self.types, self.unused, self.unresolved = [], [], []
        self.by_library = {}
        self.type_of = {}

    def stop(self, part):
        """Record that ``part`` ended early (a bound or the deadline)."""
        if part not in self.incomplete:
            self.incomplete.append(part)
        return True

    def expired(self, part):
        return time.monotonic() > self.deadline and self.stop(part)

    def collections(self):
        """``(type name, collection)`` for every datablock collection of the open file."""
        data = self.bpy.data
        for prop in data.bl_rna.properties:
            if prop.type == "COLLECTION" and prop.identifier not in SKIPPED_COLLECTIONS:
                self.type_of[prop.fixed_type.identifier] = prop.identifier
                yield prop.identifier, getattr(data, prop.identifier)

    def datablocks(self):
        scanned = 0
        for kind, collection in self.collections():
            row = {"type": kind, "count": 0}
            unused = fake = linked = 0
            for block in collection:
                scanned += 1
                if (scanned > MAX_DATABLOCKS and self.stop("datablocks")) or (scanned % 512 == 0
                                                                              and self.expired("datablocks")):
                    return  # The types read so far are complete; the result says the pass stopped.
                row["count"] += 1
                library = block.library
                if library is not None:
                    linked += 1
                    self.by_library[library.name] = self.by_library.get(library.name, 0) + 1
                    if block.is_missing:
                        self.unresolved.append({"kind": "MISSING_LINKED_DATA", "type": kind,
                                                "name": block.name[:MAX_NAME], "library": library.name[:MAX_NAME]})
                if kind in NEVER_UNUSED:
                    continue
                users = block.users
                if users == 0:
                    unused += 1
                    self.unused.append({"type": kind, "name": block.name[:MAX_NAME]})
                elif users == 1 and block.use_fake_user:
                    fake += 1
            if not row["count"]:
                continue
            for key, value in (("unused", unused), ("kept_by_fake_user", fake), ("linked", linked)):
                if value:
                    row[key] = value
            self.types.append(row)

    def libraries(self):
        rows = []
        for library in self.bpy.data.libraries:
            raw = library.filepath
            packed = library.packed_file is not None or bool(getattr(library, "is_archive", False))
            row = dict(name=library.name[:MAX_NAME], **paths.public_name(raw, self.blend_folder, self.workspace_root))
            row["status"] = paths.state(raw, self.blend_folder, packed=packed)
            row["datablocks"] = self.by_library.get(library.name, 0)
            if library.parent is not None:
                row["indirect"] = True
            if row["status"] == paths.MISSING:
                self.unresolved.append({"kind": "MISSING_LIBRARY", "name": row["name"], "file": row["file"]})
            rows.append(row)
        return rows

    @staticmethod
    def packed(block):
        return getattr(block, "packed_file", None) is not None or len(getattr(block, "packed_files", ())) > 0

    def files(self):
        """External files of every datablock: missing first, then unknown, present and packed."""
        rows, checks, reported = [], 0, set()
        for block, stored in self.bpy.data.file_path_map().items():
            kind = self.block_type(block)
            if kind == "libraries":
                continue  # A library's own file is a row of the libraries part.
            packed = self.packed(block)
            for raw in sorted(stored):
                if raw in paths.NOT_FILES:
                    continue
                row = dict(type=kind, name=block.name[:MAX_NAME],
                           **paths.public_name(raw, self.blend_folder, self.workspace_root))
                if packed:
                    row["status"] = paths.PACKED
                elif (checks >= MAX_FILE_CHECKS and self.stop("external_files")) or self.expired("external_files"):
                    row["status"] = paths.UNKNOWN
                else:
                    checks += 1
                    row["status"] = paths.state(raw, self.blend_folder)
                rows.append(row)
                reported.add((kind, block.name))
        # Blender's path walk leaves out what is packed into the file; those are reported from the datablocks.
        for kind in PACKABLE:
            for block in getattr(self.bpy.data, kind, ()):
                if (kind, block.name) in reported or not self.packed(block):
                    continue
                row = {"type": kind, "name": block.name[:MAX_NAME]}
                raw = getattr(block, "filepath", "")
                if raw not in paths.NOT_FILES:
                    row.update(paths.public_name(raw, self.blend_folder, self.workspace_root))
                rows.append(dict(row, status=paths.PACKED))
        order = {paths.MISSING: 0, paths.UNKNOWN: 1, paths.PRESENT: 2, paths.PACKED: 3}
        rows.sort(key=lambda row: (order[row["status"]], row["type"], row["name"], row.get("file", "")))
        return rows

    def block_type(self, block):
        rna = block.bl_rna
        while rna is not None:
            if rna.identifier in self.type_of:
                return self.type_of[rna.identifier]
            rna = rna.base
        return block.bl_rna.identifier

    def node_trees(self):
        data = self.bpy.data
        for tree in data.node_groups:
            yield "node_groups", tree.name, tree
        for kind in NODE_OWNERS:
            for owner in getattr(data, kind, ()):
                tree = getattr(owner, "node_tree", None)
                if tree is not None:
                    yield kind, owner.name, tree

    def nodes(self):
        scanned = 0
        for kind, name, tree in self.node_trees():
            for node in tree.nodes:
                scanned += 1
                if (scanned > MAX_NODES and self.stop("unresolved")) or (scanned % 256 == 0
                                                                         and self.expired("unresolved")):
                    return
                problem = None
                if node.bl_idname == "NodeUndefined":
                    problem = "UNDEFINED_NODE"
                elif node.type == "GROUP" and node.node_tree is None:
                    problem = "GROUP_NODE_WITHOUT_GROUP"
                if problem:
                    self.unresolved.append({"kind": problem, "type": kind, "name": name[:MAX_NAME],
                                            "node": node.name[:MAX_NAME]})


def _page(rows, offset, limit):
    selected = rows[offset:offset + limit]
    more = offset + len(selected) < len(rows)
    return {"total": len(rows), "offset": offset, "items": selected,
            "next_offset": offset + len(selected) if more else None, "truncated": more}


def _preview(rows):
    """The first rows of a part for the summary, saying how to read the rest."""
    part = {"total": len(rows), "items": rows[:SUMMARY_ROWS]}
    if len(rows) > SUMMARY_ROWS:
        part["truncated"] = True
    return part


def _type_page(bpy, kind, offset, limit):
    """One page of a datablock type: name, users, fake user and library, by position."""
    collection = getattr(bpy.data, kind)
    total = len(collection)
    rows = []
    for block in collection[offset:offset + limit]:
        row = {"name": block.name[:MAX_NAME], "users": block.users}
        if block.use_fake_user:
            row["fake_user"] = True
        if block.library is not None:
            row["library"] = block.library.name[:MAX_NAME]
            if block.is_missing:
                row["missing"] = True
        rows.append(row)
    more = offset + len(rows) < total
    return {"type": kind, "total": total, "offset": offset, "items": rows,
            "next_offset": offset + len(rows) if more else None, "truncated": more}


def read(bpy, args, workspace):
    """The open file's summary, or one page of a part of it."""
    from . import native_programs
    if native_programs.runtime_is_staging():
        return {"readable": False, "code": "STAGING", "message": "A staged change is being prepared, and its private "
                "copies are in the file. Read again once it is applied, accepted or discarded."}
    scan = _Scan(bpy, getattr(workspace, "root", None))
    name = bpy.data.filepath
    result = {"readable": True, "scope": "OPEN_FILE", "saved": bool(name), "unsaved_changes": bool(bpy.data.is_dirty)}
    if name:
        result["file"] = paths.base_name(name)
    listed = args.get("list")
    offset, limit = args["offset"], args["limit"]
    kinds = {kind for kind, _collection in scan.collections()}
    if listed is not None and listed not in SECTIONS:
        if listed not in kinds:
            return dict(result, readable=False, code="UNKNOWN_LIST",
                        message="list names a part (%s) or a datablock type." % ", ".join(SECTIONS),
                        types=sorted(kinds))
        return dict(result, **_type_page(bpy, listed, offset, limit))
    # Each part reads only what it reports: the external files need no pass over the datablocks.
    if listed != "FILES":
        scan.datablocks()
        libraries = scan.libraries()
    if listed in (None, "FILES"):
        files = scan.files()
    if listed in (None, "UNRESOLVED"):
        scan.nodes()
    if listed == "UNUSED":
        result.update(list="UNUSED", **_page(scan.unused, offset, limit), unused_rule=UNUSED_RULE)
    elif listed == "FILES":
        result.update(list="FILES", **_page(files, offset, limit))
    elif listed == "LIBRARIES":
        result.update(list="LIBRARIES", **_page(libraries, offset, limit))
    elif listed == "UNRESOLVED":
        result.update(list="UNRESOLVED", **_page(scan.unresolved, offset, limit))
    else:
        counts = {}
        for row in files:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        result.update(datablocks=scan.types, unused=_preview(scan.unused), libraries=_preview(libraries),
                      external_files=dict(_preview(files), **counts), unresolved=_preview(scan.unresolved),
                      unused_rule=UNUSED_RULE)
        if any(part.get("truncated") for part in (result["unused"], result["libraries"], result["external_files"],
                                                  result["unresolved"])):
            result["truncated"] = True
            result["note"] = "A truncated part shows its first rows; page it with list (UNUSED, FILES, LIBRARIES, UNRESOLVED)."
    if scan.incomplete:
        result["incomplete"] = list(scan.incomplete)
        result["truncated"] = True
    return result


def execute(window, arguments, workspace):
    """Main-thread ``angelo_blend_file`` dispatch for both scopes. Returns an answer."""
    import bpy
    from . import scene_queries
    args = validate_read("angelo_blend_file", arguments)
    scene_queries._main_thread()
    if args["file"]:
        from . import blend_file_pending
        from .studio_agent import StudioPending
        result = blend_file_pending.begin(args, workspace)
        if isinstance(result, StudioPending):
            return result
    else:
        result = read(bpy, args, workspace)
    result.update(read_only=True, scene_changed=False)
    return result
