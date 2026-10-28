# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Running Split Sheet steps: a ready parts sheet in, transparent part crops into the Media Studio library.

Splitting is free and local, so it runs as soon as a Split Sheet step's sheet
is ready (``flow_graph`` reports a ``split_request``), without an approval.
Blender decodes the sheet on the main thread (``bpy.data.images``, native and
fast); a worker thread runs the pure-numpy split (``flow_parts``) and encodes
the crops as PNG files, never touching ``bpy``; the next pump imports the crops
into the library with lineage and stores the split on the node
(``split_json``: ``{key, parts, cell_height, grid, size}`` or ``{key, error}``).

A split is keyed by its sheet asset and layout (``flow_graph.split_key``): the
same sheet and layout are never split twice, and a new sheet makes the old split
stale without deleting its crops (they stay library assets).
"""

import hashlib
import json
import shutil
import struct
import threading
import zlib
from pathlib import Path

import numpy as np

import bpy

from . import private_roots
from .flow_parts import SheetError, split_sheet

_jobs = {}  # (tree name, node name, key) -> {"thread", "result", "error", "directory", "sheet"}
MAX_ACTIVE = 1


def _png(path, rgba):
    """Write an 8-bit RGBA PNG (filter 0, zlib). Small and dependency-free; used off the main thread."""
    height, width = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    Path(path).write_bytes(data)


def _pixels(path):
    """RGB pixels (top row first, float 0..1) of an image file, decoded by Blender. Main thread only."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        if not width or not height:
            raise SheetError("The parts sheet image couldn't be read")
        buffer = np.empty(width * height * 4, np.float32)
        image.pixels.foreach_get(buffer)
        # Blender stores rows bottom-up and in its colour space; sheets are sRGB PNGs, read as stored.
        return np.flipud(buffer.reshape(height, width, 4))[..., :3].copy()
    finally:
        bpy.data.images.remove(image)


def _work(job, pixels, slots, rows):
    """Worker thread: split and write crops. No ``bpy``."""
    try:
        result = split_sheet(pixels, slots, rows=rows)
        for index, part in enumerate(result["parts"]):
            for view in ("front", "back"):
                if part.get(view):
                    path = Path(job["directory"]) / f"{index:02d}-{view}.png"
                    _png(path, part[view].pop("rgba"))
                    part[view]["path"] = str(path)
        job["result"] = result
    except SheetError as error:
        job["error"] = str(error)
    except Exception as error:  # Never let a worker die silently; the step shows why.
        job["error"] = f"the sheet couldn't be split ({type(error).__name__})"


def request(tree, node, split_request, library):
    """Start splitting a ready sheet for one Split Sheet node, once per key."""
    identity = (tree.name, node.name, split_request["key"])
    if identity in _jobs or sum(1 for j in _jobs.values() if j["thread"].is_alive()) >= MAX_ACTIVE:
        return
    stored = _stored(node)
    if stored.get("key") == split_request["key"]:
        return
    job = {"result": None, "error": None, "sheet": split_request["sheet"], "key": split_request["key"],
           "directory": private_roots.mkdtemp("angelo-split-")}
    _jobs[identity] = job
    try:
        asset = library.assets.get(split_request["sheet"])
        if not asset or not asset.get("files"):
            raise SheetError("The parts sheet isn't in the library")
        pixels = _pixels(library.checked_file(asset, asset["files"][0]))
    except (SheetError, ValueError, RuntimeError, OSError) as error:
        job["error"] = str(error) if isinstance(error, SheetError) else "the parts sheet couldn't be read"
        job["thread"] = threading.Thread(target=lambda: None)
        job["thread"].start()
        return
    fields = node.fields()
    job["thread"] = threading.Thread(target=_work, args=(job, pixels, fields["slots"], fields["rows"]),
                                     name="angelo-split-sheet", daemon=True)
    job["thread"].start()


def _stored(node):
    try:
        value = json.loads(node.split_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def stored_splits(tree):
    """{Split Sheet node name: stored split} for evaluation."""
    return {node.name: _stored(node) for node in tree.nodes if node.bl_idname == "ANGELO_FlowSplitSheetNode"}


def store_grounding(tree, node_name, key, grounding):
    """Keep a fit of a split's parts to the character image (``parts_ground``) with that split, where Place Character
    reads it. Dropped when the node has been split again since (another key). Main thread. True when stored."""
    node = tree.nodes.get(node_name)
    if node is None or node.bl_idname != "ANGELO_FlowSplitSheetNode":
        return False
    stored = _stored(node)
    if stored.get("key") != key or stored.get("parts") is None:
        return False
    stored["grounding"] = grounding
    node.split_json = json.dumps(stored)
    return True


CROP_KEYS = ("split_key", "part", "view")   # A crop is these: the split key covers the sheet asset and its slot layout (rows, slots).


def crop_index(library):
    """{(split key, part, view): crop asset id} of every ready crop a split imported, and under
    ``("sheet", sheet, part, view)`` every such crop of that sheet whatever its key. Built once per ``collect``."""
    index = {}
    for asset in library.assets.values():
        lineage = asset.get("lineage") or {}
        if lineage.get("source") == "split" and asset.get("status") == "ready" and asset.get("files"):
            index.setdefault(tuple(lineage.get(name) for name in CROP_KEYS), asset["id"])
            index.setdefault(("sheet", lineage.get("sheet"), lineage.get("part"), lineage.get("view")), []).append(asset["id"])
    return index


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def existing_crop(index, lineage, library=None, path=None):
    """The crop an earlier split of the same sheet already imported for this part and view, or None.

    The split is deterministic for its key, so splitting the same key again (after Undo, a node that lost its stored
    split, or a run started again from the same sheet in a new flow) gives the same crops: reusing their assets keeps
    every part step's input key, so models already made for them are reused instead of paid for again. When the key
    is new (the split's rules changed), a crop of the same sheet, part and view whose file has the same bytes as the
    new cut at ``path`` is reused too (crops are written deterministically), so only parts whose pixels changed are
    new parts to make.
    """
    found = index.get(tuple(lineage.get(name) for name in CROP_KEYS))
    if found is not None and library is not None:
        asset = library.assets.get(found)
        try:
            library.checked_file(asset, asset["files"][0])   # A crop whose file is gone is cut again.
        except (ValueError, OSError, KeyError, TypeError):
            found = None
    if found is None and library is not None and path is not None:
        same = ("sheet", lineage.get("sheet"), lineage.get("part"), lineage.get("view"))
        candidates = index.get(same) or []
        if candidates:
            wanted = _digest(path)
            for identity in candidates:
                asset = library.assets.get(identity)
                try:
                    if _digest(library.checked_file(asset, asset["files"][0])) == wanted:
                        return identity
                except (ValueError, OSError, KeyError, TypeError):
                    continue
    return found


def collect(tree, library, flow_id):
    """Import finished splits' crops and store each split on its node. Main thread. Returns True on change."""
    changed, index = False, None
    for identity, job in list(_jobs.items()):
        if identity[0] != tree.name or job["thread"].is_alive():
            continue
        index = crop_index(library) if index is None else index
        _jobs.pop(identity)
        node = tree.nodes.get(identity[1])
        try:
            if node is None or node.bl_idname != "ANGELO_FlowSplitSheetNode":
                continue
            if job["error"]:
                node.split_json = json.dumps({"key": job["key"], "error": job["error"]})
                changed = True
                continue
            parts, counts = [], {}
            for part in job["result"]["parts"]:
                counts[part["slot"]] = counts.get(part["slot"], 0) + 1
            for part in job["result"]["parts"]:
                numbered = counts[part["slot"]] > 1 or part["item"] > 0
                part_id = f"{part['slot']}-{part['item'] + 1}" if numbered else part["slot"]
                entry = {"id": part_id, "slot": part["slot"], "kind": part["kind"], "item": part["item"],
                         "numbered": numbered, "box": part["front"]["box"]}
                if part.get("profile"):
                    entry["profile"] = part["profile"]
                for view, key in (("front", "asset"), ("back", "back_asset")):
                    if part.get(view):
                        lineage = {"source": "split", "flow_id": flow_id, "node": node.name, "sheet": job["sheet"],
                                   "split_key": job["key"], "part": part_id, "view": view}
                        found = existing_crop(index, lineage, library, part[view]["path"])
                        if found is None:
                            asset = library.import_image(part[view]["path"])
                            asset.update(prompt=f"Part · {part_id}" + (" · back" if view == "back" else ""),
                                         lineage=lineage)
                            library.update(asset)
                            found = index[tuple(lineage[name] for name in CROP_KEYS)] = asset["id"]
                        entry[key] = found
                parts.append(entry)
            node.split_json = json.dumps({"key": job["key"], "parts": parts, "cell_height": job["result"]["cell_height"],
                                          "grid": job["result"]["grid"], "size": job["result"]["size"]})
            changed = True
        except (ValueError, OSError) as error:
            if node is not None:
                node.split_json = json.dumps({"key": job["key"], "error": f"the parts couldn't be saved ({error})"})
                changed = True
        finally:
            shutil.rmtree(job["directory"], ignore_errors=True)
    return changed


def start(tree, states, library):
    """Split ready sheets (free, local): a Split Sheet step that waits only for its split starts one."""
    for name, state in states.items():
        split_request = state.get("split_request")
        node = tree.nodes.get(name)
        if split_request and node is not None and node.bl_idname == "ANGELO_FlowSplitSheetNode":
            request(tree, node, split_request, library)


def clear():
    """Forget jobs (file load or unregister); their threads finish on their own and are ignored."""
    for job in _jobs.values():
        shutil.rmtree(job["directory"], ignore_errors=True)
    _jobs.clear()
