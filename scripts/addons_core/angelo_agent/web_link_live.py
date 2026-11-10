# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web link's live frames: only the objects that changed (doc/angelo/web_link.rst, "Live frames"). Main thread.

The page keeps every object it already has; a live update must never rebuild the developer's scene. So after each
still period Angelo exports only the objects whose data changed since the last frame (a **delta**), and the page
touches only those:

* **What changed** comes from the depsgraph (``note``): every object it re-evaluated, plus the users of a mesh,
  material, image, node group or other data it updated, in the original objects and in what the depsgraph evaluated
  (a material a modifier or geometry nodes set; an instanced collection's objects, with their instancer). Pixel
  writes reach no depsgraph update, so the link polls the images (``pixels_changed``). Scene and collection updates
  (an object added, removed, hidden) need no object: each export reads the full tree's members first, so removals
  are seen anyway.
* **The export** is Blender's glTF exporter with the web link's export id. ``filter_tree`` (the exporter's
  ``gather_tree_filter_tag_hook``) records every node a full export would hold, then keeps only the changed objects,
  their ancestors as empties (so every transform stays local to its real parent), and the armatures and bones their
  skins need. ``mark_nodes`` (``gather_scene_hook``) gives every node its key (``extras.angelo_uid``: the object's
  ``session_uid``, an armature's bone as ``<armature>/<bone>``, an object inside a collection instance under its
  instancer's key, so keys are unique in a file and the same in every export of this process) and its role:
  ``content`` (the page applies it) or ``parent`` (only where a content node hangs). ``image`` (``gather_image_hook``)
  names each image by the digest of its bytes and sends an image every receiving page already has as a 1 x 1 stub.
* **Digests** (``node_digests``): each content node's name, transform, mesh data, materials (with their images'
  digests), skin, camera and light. A node whose digest is the one the page has is skipped by the page; a frame where
  nothing changed is not sent at all.
* **Full frames** hold every node (all ``content``, no stubs): a page's first frame, a page that asked again
  (``resync``: its delta didn't fit what it shows), a page that fell behind (``LinkServer.lagging``), and any frame
  after Angelo couldn't tell what changed. The page applies a full frame by the same digests, so unchanged objects
  stay untouched there too.
* **Delta frames** say ``base``, the version they apply to; the server never drops one (``LinkServer.send``), and a
  page that has another version asks for a full frame instead of applying it.
"""

import hashlib
import json
import time

from .web_link_digest import (DIGEST, KEY, ROLE, complete_keys, image_digests, node_digests,  # noqa: F401
                              read_glb, stamp_digests, write_glb)
from .web_link_tree import (CONTENT, PARENT, STUB, _export, filter_tree, image, mark_nodes,  # noqa: F401
                            node_key, owners_of, stub_png)
from . import web_link_users as users

FULL, DELTA = "full", "delta"
FULL_SHARE = 0.5   # Changed objects past this share of the scene: one full frame costs no more than a filtered one.
VERIFY_SECONDS = 2.0   # After a delta, once the scene is still this long, a whole export checks what the page has,
VERIFY_SHARE = 3.0     # and this many times a whole export's time, so a check takes at most about a quarter of the
#                        still time before it (break-it round 8, M4);
VERIFY_MAX_SECONDS = 60.0   # each check that finds nothing doubles the time to the next, up to this (round 7, M1).
SETTLE_EXPORTS = 4   # Exports of a frame with particle copies at most, until two agree (``_frames``).
POLL_SECONDS = 0.5   # The images' pixel revisions are read this often while no publish is pending (round 8, M1).
_session = {"asset": None, "digests": {}, "owners": {}, "dirty": set(), "dirty_data": set(), "unsure": True,
            "sent": {}, "images": {}, "resync": set(), "image_stamps": {}, "physics": {}, "verify_due": None,
            "budget_start": 0, "verify_wait": VERIFY_SECONDS, "checked": False, "poll_at": 0.0, "checked_at": None,
            "whole_ms": 0.0, "fail_wait": 0.0, "file_start": 0, "shading": 0, "frames": (None, {}),
            "settled": True}


def reset(asset=None):
    """Forget every page's state: a new link (or none)."""
    _session.update(asset=asset, digests={}, owners={}, dirty=set(), dirty_data=set(), unsure=True, sent={},
                    images={}, resync=set(), image_stamps={}, physics={}, verify_due=None, budget_start=0,
                    verify_wait=VERIFY_SECONDS, checked=False, poll_at=0.0, checked_at=None, whole_ms=0.0,
                    fail_wait=0.0, file_start=0, shading=0, frames=(None, {}))


def unsure(*_args):
    """Angelo can't tell what changed (an undo, a redo, a file read can give objects new session uids): the next
    frame is a full one (``bpy.app.handlers.undo_post`` and kin, while a web link is live)."""
    _session["unsure"] = True


def forget(client):
    """An editor left: its next connection starts from a full frame."""
    _session["sent"].pop(client, None)
    _session["images"].pop(client, None)
    _session["resync"].discard(client)


def resync(client):
    """A page asked for a full frame (its delta didn't fit what it shows)."""
    _session["resync"].add(client)


# What changed ------------------------------------------------------------------------------------------------------

def note(depsgraph):
    """Remember what this depsgraph update re-evaluated (``engine_link._on_depsgraph``, the followed scene's)."""
    import bpy
    for update in depsgraph.updates:
        block = getattr(update.id, "original", None) or update.id
        if isinstance(block, bpy.types.Object):
            _session["dirty"].add(block.session_uid)
        elif not isinstance(block, (bpy.types.Scene, bpy.types.Collection)):
            _session["dirty_data"].add(block.session_uid)
            _session["shading"] += 1   # Materials, node trees, images: which sequence frames are read may change.


def _uses(material, wanted, seen):
    """Whether ``material``'s node tree (groups included, each once) uses a datablock in ``wanted``."""
    tree = getattr(material, "node_tree", None)
    pending = [tree] if tree is not None else []
    while pending:
        tree = pending.pop()
        if tree.session_uid in wanted:
            return True
        if tree.session_uid in seen:
            continue
        seen.add(tree.session_uid)
        for node in tree.nodes:
            for name in ("image", "node_tree", "texture", "object"):
                value = getattr(node, name, None)
                if value is not None and getattr(value, "session_uid", None) in wanted:
                    return True
            group = getattr(node, "node_tree", None)
            if group is not None:
                pending.append(group)
    return False


RENDERED = {"RENDER_RESULT", "COMPOSITING"}   # Pictures made by rendering, never by a material.
FINGERPRINT_PIXELS = 1024 * 1024   # A dirty image this size or smaller is fingerprinted each frame (a few ms),
FINGERPRINT_BUDGET = 4 * 1024 * 1024   # up to this many pixels in all per frame (break-it round 5, M4).
_PIXELS = {}                       # One reused buffer per size, for the fingerprint.


def _fingerprint(image, budget):
    """A digest of a dirty image's pixels, or None for one too large to read each frame or past this frame's
    ``budget`` (a one-item list of pixels left) (break-it round 4, M2: a second edit through the API while the image
    stays dirty reaches no update)."""
    width, height = image.size
    count = width * height * image.channels
    if not image.is_dirty or not count or width * height > min(FINGERPRINT_PIXELS, budget[0]):
        return None
    budget[0] -= width * height
    import numpy
    buffer = _PIXELS.get(count)
    if buffer is None:
        _PIXELS.clear()
        buffer = _PIXELS[count] = numpy.empty(count, dtype=numpy.float32)
    image.pixels.foreach_get(buffer)
    return hashlib.blake2b(memoryview(buffer), digest_size=16).hexdigest()   # Hashed in place, never copied.


def image_changes(scene=None):
    """Images whose pixels changed since the last frame without a depsgraph update (pixels set through the API, a
    bake or a script that saves, another program writing the file: break-it N3, round 3 M2): those whose unsaved
    state changed either way (``buffers_free`` drops unsaved pixels: round 8, M2), those whose file changed, and those
    whose pixels were written: ``Image.angelo_pixel_revision``, which Angelo's native code bumps on every pixel write
    (painting, Python, bakes) at no cost to read; in a build without it, a fingerprint of each dirty image up to
    ``FINGERPRINT_PIXELS``. Each is checked once per frame, never re-encoded while it stays as it was (round 3, M1;
    round 5, M3). Render results and Viewer pictures never travel in a glTF. ``scene``: the followed scene, whose
    frame and views name the files a sequence or a multi-view image reads (``web_link_users.file_stamp``)."""
    import bpy
    found, before, now, budget = set(), _session["image_stamps"], {}, [FINGERPRINT_BUDGET]
    images = [image for image in bpy.data.images if image.type not in RENDERED and getattr(image, "users", 1)]
    files = _file_stamps(images, scene, before)
    dirty = [image for image in images if image.is_dirty]
    start = _session["budget_start"] % len(dirty) if dirty else 0
    order = dirty[start:] + dirty[:start] + [image for image in images if not image.is_dirty]
    printed = 0
    for image in order:
        old = before.get(image.session_uid)
        revision = getattr(image, "angelo_pixel_revision", None)   # Angelo's native count of pixel writes.
        if revision is not None:
            third = revision
        else:   # Fingerprints rotate over the dirty images where the last budget stopped (break-it rounds 6, 7).
            third = _fingerprint(image, budget)
            if third is None and image.is_dirty and old is not None:
                third = old[2]   # Skipped this frame: keep what was last seen, never a change.
            elif third is not None:
                printed += 1
        if files.get(image.session_uid) is DEFERRED:
            continue   # Not stamped yet: seen first in a later call, never counted as a change.
        stamp = (bool(image.is_dirty), files.get(image.session_uid), third)
        now[image.session_uid] = stamp
        if old is not None and stamp != old:
            found.add(image.session_uid)
    _session["image_stamps"] = now
    _session["budget_start"] = start + max(printed, 1)
    return found


FILE_BUDGET = 256   # Files stamped per poll or frame at most, in turn over the images (break-it round 10, L1),
HASH_BYTES = 64 * 1024 * 1024   # and new bytes hashed (``web_link_users.file_stamp``; the image crossing it whole).
DEFERRED = object()   # An image not stamped yet: it is first seen in a later call.


def _file_stamps(images, scene, before):
    """{image session uid: ``web_link_users.file_stamp``} of the file-backed ``images``, in turn from where the last
    call stopped, up to ``FILE_BUDGET`` files (each UDIM tile, frame and view is one; the image that crosses the budget
    is stamped whole, once a turn) and ``HASH_BYTES`` of bytes not hashed before; an image past either keeps its last
    stamp (``DEFERRED`` when it has none).
    The frames a sequence's Image Texture nodes read are found again only when the scene's frame or a material,
    node tree or image changed (``note``)."""
    import bpy
    filed = [image for image in images if image.source in users.FILED]
    if not filed:
        return {}
    key = (getattr(scene, "frame_current", None), _session["shading"], len(bpy.data.materials),
           len(bpy.data.node_groups))
    if scene is not None and _session["frames"][0] != key and any(image.source == "SEQUENCE" for image in filed):
        _session["frames"] = (key, users.sequence_frames(scene))
    frames, render = _session["frames"][1], getattr(scene, "render", None)
    start = _session["file_start"] % len(filed)
    stamps, left, reached, hashing = {}, FILE_BUDGET, 0, [HASH_BYTES]
    for image in filed[start:] + filed[:start]:
        old = before.get(image.session_uid)
        if left <= 0 and old is not None:
            stamps[image.session_uid] = old[1]   # Its turn comes in a later call.
            continue
        try:
            stamps[image.session_uid] = stamp = users.file_stamp(
                image, render, frames.get(image.session_uid, ()), hashing)
        except users.Deferred:   # Too many new bytes this call: its last stamp, or none yet (never a change).
            stamps[image.session_uid] = old[1] if old is not None else DEFERRED
            left = 0
            continue
        left -= len(stamp) if stamp else 1   # Each file statted: every tile, frame and view (round 11, L3).
        reached += 1
    _session["file_start"] = start + max(reached, 1)
    return stamps


def pixels_changed(scene=None):
    """Whether an image's pixels, unsaved state or file changed with no depsgraph update (``engine_link._tick`` asks
    while no publish is pending; read at most every ``POLL_SECONDS``): pixels written through the API, ``update``,
    ``scale``, ``buffers_free``, a bake's restore, a file rewritten on disk. Those images' users join the next frame
    (break-it round 8, M1). Only with Angelo's native pixel revision: without it, telling means reading pixels."""
    import bpy
    now = time.monotonic()
    if now < _session["poll_at"] or "angelo_pixel_revision" not in bpy.types.Image.bl_rna.properties:
        return False
    _session["poll_at"] = now + POLL_SECONDS
    found = image_changes(scene)
    _session["dirty_data"] |= found
    return bool(found)


def due(scene=None):
    """When ``engine_link`` should publish while no edit waits (``_tick``, with no publish pending): now, when pixels
    changed with no depsgraph update (``pixels_changed``); 0.0 (at once), when the still-scene check is due; else
    None. The check's time is kept here, never as the link's ``dirty_at``, so the poll runs while a check waits
    (break-it round 9, M1: pixel writes were unseen for up to a minute after a delta)."""
    if pixels_changed(scene):
        return time.monotonic()
    check, now = _session["verify_due"], time.monotonic()
    return 0.0 if check is not None and now >= check else None


def physics_changes(objects):
    """Objects whose game physics settings changed since the last frame. ``Object.angelo_physics`` runs no Python
    when set (doc/angelo/game_physics.rst), so a change can reach no depsgraph update; its settings are compared
    here, a few values per object per frame (break-it round 4, M1)."""
    found, before, now = set(), _session["physics"], {}
    for obj in objects:
        stamp = _physics_stamp(obj)   # The stored settings only: an object that never had any costs one lookup.
        if stamp is not None:
            now[obj.session_uid] = stamp
        if now.get(obj.session_uid) != before.get(obj.session_uid) and (obj.session_uid in before or stamp):
            found.add(obj.session_uid)
    _session["physics"] = now
    return found


def _physics_stamp(obj):
    try:
        from io_scene_gltf2.angelo_physics.props import stored
    except ImportError:
        return None
    group = stored(obj)
    return repr(sorted(group.to_dict().items())) if group else None


def changed_objects(objects, depsgraph=None):
    """The session uids of the objects whose data changed since the last frame: re-evaluated objects; ``objects``
    whose data, materials or the images and groups those use were updated; and, with ``depsgraph``, every object the
    depsgraph evaluated from changed data that its original doesn't show: a material a modifier or geometry nodes
    set, an instanced object whose collection isn't in the scene, with every object that instances it (break-it
    round 8, M2; ``web_link_users.evaluated_users``, whose cost never follows how many copies there are)."""
    dirty, data = set(_session["dirty"]), _session["dirty_data"]
    if not data:
        return dirty
    materials, seen = {}, set()

    def uses(material):
        if material.session_uid not in materials:
            materials[material.session_uid] = material.session_uid in data or _uses(material, data, seen)
        return materials[material.session_uid]
    for obj in objects:
        if obj.session_uid in dirty:
            continue
        if getattr(obj.data, "session_uid", None) in data or any(
                slot.material is not None and uses(slot.material) for slot in obj.material_slots):
            dirty.add(obj.session_uid)
    if depsgraph is not None:
        dirty |= users.evaluated_users(depsgraph, data, uses, objects)
    return dirty


# Frames ------------------------------------------------------------------------------------------------------------

MAX_REMOVED_BYTES = 32 * 1024   # A delta lists removals in its header (at most 64 KiB); more is a full frame.


def _clients(link, found):
    from . import engine_link_assets as assets
    server = link._state["server"]
    return [client for client, info in sorted(server.clients().items())
            if assets.serves(found["asset"], info, live=True)] if server is not None else []


def _export_glb(window, path, found, content, known_images):
    """Write the live .glb with ``content`` (None: every object) and the images every page has as stubs; returns
    (document, binary, objects a full export holds)."""
    from . import engine_link_assets as assets, web_link_assets as web
    _export.update(active=True, content=content, known_images=known_images, objects=set(), owners={},
                   filtered=False, marked=False)
    try:
        assets.export(window, path, web.live_options(found["params"]), web.LIVE_EXPORT_ID)
    finally:
        _export["active"] = False
    if not (_export["filtered"] and _export["marked"]):   # The exporter logs a failed hook and goes on.
        raise RuntimeError("The live frame's glTF hooks didn't run")
    with open(path, "rb") as handle:
        document, binary = read_glb(handle.read())
    complete_keys(document, _export["owners"])   # Nodes written after the hooks ran (instancing, physics).
    keys = [(node.get("extras") or {}).get(KEY) for node in document.get("nodes", [])]
    if None in keys or len(set(keys)) != len(keys):   # Never a frame the page could apply to the wrong object.
        raise RuntimeError("A live frame's nodes don't each have their own key")
    if any(DIGEST not in (image.get("extras") or {}) for image in document.get("images", [])):
        raise RuntimeError("A live frame's image has no digest")   # The image hook failed (break-it N6).
    return document, binary, set(_export["objects"])


def _as_set(chain):
    return chain if isinstance(chain, (set, frozenset)) else {chain}


def live_time_counts():
    """Whether the last publish's time counts toward the stillness wait (``engine_link_live``): never a check's."""
    return not _session["checked"]


def live_frames(link, window, path, found, base):
    """The web adapter's frames (``engine_link_live.publish``; ``link``: the ``engine_link`` module): one delta or
    full frame for every live page of the asset, or none when nothing it shows changed.

    A frame that raises (the exporter failing, a full disk) is tried again: what it would have sent stays noted, and
    ``due`` asks for it after a back-off that is at least ``VERIFY_SHARE`` times the failed attempt and doubles with
    each raise in a row, up to ``VERIFY_MAX_SECONDS``; a frame that returns resets it (break-it round 13: a failed
    check was retried every 2 s, back to back on a large scene, and a failed delta or first frame never again)."""
    started = time.perf_counter()
    try:
        frames = _live_frames(link, window, path, found, base)
    except Exception:
        wait = min(VERIFY_MAX_SECONDS, max(VERIFY_SECONDS, 2.0 * _session["fail_wait"],
                                            VERIFY_SHARE * (time.perf_counter() - started)))
        _session["fail_wait"] = wait
        _session["verify_due"] = time.monotonic() + wait
        raise
    _session["fail_wait"] = 0.0
    return frames


def _live_frames(link, window, path, found, base):
    state = link._state
    identity = (found.get("number"), id(state["server"]))
    if _session["asset"] != identity:
        reset(identity)
    clients = _clients(link, found)
    server = state["server"]
    lagging = {client for client in clients if server.lagging(client)}   # Read (and cleared) for every client.
    behind = {client for client in clients if _session["sent"].get(client) != base or client in _session["resync"]
              or client in lagging}
    scene, view_layer = link._followed(window)
    objects = list(scene.objects) if scene is not None else []
    _session["dirty_data"] |= image_changes(scene)
    _export["physics_changed"] = frozenset(physics_changes(objects))
    _session["dirty"] |= _export["physics_changed"]
    try:
        changed = None if _session["unsure"] or behind else changed_objects(
            objects, getattr(view_layer, "depsgraph", None))
    except Exception:   # Unsure what changed: a whole frame, never a link that stops (break-it round 10, H1).
        changed = None
    check = _session["verify_due"]
    verify = (changed is not None and not changed and check is not None and time.monotonic() >= check)
    emitters = frozenset(obj.session_uid for obj in objects if any(   # Only systems whose copies are exported.
        system.settings.render_type in {"OBJECT", "COLLECTION"} for system in getattr(obj, "particle_systems", ())))
    if changed is not None and objects and len(changed) > FULL_SHARE * len(objects):
        changed = None   # Most of the scene changed: one full frame.
    _session["checked"] = verify
    if verify:   # The scene has been still since a delta: one whole export checks what the pages have.
        changed = None
    started = time.perf_counter()
    try:
        frames = _frames(window, path, found, base, clients, changed, emitters, verify)
    except RuntimeError:
        if changed is None:
            raise
        frames = None   # A delta that couldn't be made right is never sent: a whole frame instead (round 3, H1).
    if frames is None:   # Too many removals for a delta's header, or a refused delta.
        frames = _frames(window, path, found, base, clients, None, emitters)
    _session["dirty"].clear()
    _session["dirty_data"].clear()
    _session["unsure"] = False
    _session["resync"].difference_update(clients)
    now = time.monotonic()
    if changed is None:   # A whole export: what the next check will cost.
        _session["whole_ms"] = (time.perf_counter() - started) * 1000.0
    if verify:   # Nothing found: the next check waits twice as long; a correction brings it back.
        _session["checked_at"] = now
        _session["verify_wait"] = (VERIFY_SECONDS if frames else
                                   min(VERIFY_MAX_SECONDS, _session["verify_wait"] * 2.0))
    if verify:   # A check is never followed by another until the next frame sends something.
        _session["verify_due"] = None
    elif frames and (changed is not None or not _session["settled"]):
        #     A delta, or a whole frame whose particle copies never settled (round 11, L1; a settled whole frame has
        #     nothing to miss: round 12, M2): a whole export later checks what deciding what changed could have
        #     missed, once the scene is still for a while in proportion to that export's cost, and no sooner than the
        #     backed-off time after the last check. Editing pushes only the first: a check waits for a pause, never
        #     for the back-off to fit between two edits (break-it round 8, M3). ``due`` tells the link when.
        still = max(VERIFY_SECONDS, VERIFY_SHARE * _session["whole_ms"] / 1000.0,
                    10.0 * state.get("last_ms", 0.0) / 1000.0)
        spaced = now if _session["checked_at"] is None else _session["checked_at"] + _session["verify_wait"]
        _session["verify_due"] = max(now + still, spaced)
    # Nothing sent (a selection, a value set back, a poll that found no visible change) leaves a due check as it is
    # (break-it round 9, L1).
    return frames


def _frames(window, path, found, base, clients, changed, emitters=frozenset(), verify=False):
    """The frames of one export: a delta of ``changed`` objects, or a whole frame (``changed`` None). A ``verify``
    whole frame is sent only when a digest moved or a node went, and may send pictures the pages have as stubs: it
    corrects whatever deciding which objects changed missed (doc/angelo/web_link.rst, "Live frames")."""
    full = changed is None
    known_images = frozenset() if (full and not verify) or not clients else frozenset.intersection(
        *(frozenset(_session["images"].get(client, ())) for client in clients))
    document, binary, present = _export_glb(window, path, found, changed, known_images)
    digests = node_digests(document, binary)
    _session["settled"] = True
    if emitters and (full or emitters & changed):
        # A particle system re-evaluated after a change writes its copies' rotations (aligned to their velocity)
        # only from a later evaluation: export again until two exports agree, at most SETTLE_EXPORTS in all (break-it
        # round 5; round 12, L1: one more export didn't settle a system's first evaluation).
        for _attempt in range(SETTLE_EXPORTS - 1):
            document, binary, present = _export_glb(window, path, found, changed, known_images)
            previous, digests = digests, node_digests(document, binary)
            if digests == previous:
                break
        else:
            _session["settled"] = False   # Still moving: a still-scene check follows even a whole frame.
    owners = dict(_export["owners"])
    known = _session["digests"]
    if full:
        removed = sorted(key for key in known if key not in digests)
        if verify and not removed and all(known.get(key) == digest for key, digest in digests.items()):
            return []   # The pages have it all.
    else:
        removed = sorted(key for key, chain in _session["owners"].items()
                         if key not in digests and (_as_set(chain) - present or _as_set(chain) & changed))
        if len(json.dumps(removed)) > MAX_REMOVED_BYTES:
            return None
        if not removed and all(known.get(key) == digest for key, digest in digests.items()):
            return []   # Nothing the page shows changed (a selection, a value set back).
    stamp_digests(document, digests)
    body = write_glb(document, binary)
    for key in removed:
        known.pop(key, None)
        _session["owners"].pop(key, None)
    if full:
        known.clear()
        _session["owners"].clear()
    known.update(digests)
    _session["owners"].update(owners)
    images = image_digests(document)
    for client in clients:
        _session["sent"][client] = base + 1
        _session["images"][client] = set(images) if full else _session["images"].get(client, set()) | images
    fields = {"kind": FULL} if full else {"kind": DELTA, "base": base, "removed": removed}
    return [(clients, fields, body)]
