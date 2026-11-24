# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""@-mentions in the composer: pick an object, collection or material of the chat's scene by name.

Typing ``@`` at the start of a word opens a short list above the composer (``draw``) of the scene's objects,
collections and materials whose names match what follows it (``query_at``, ``candidates``); Up and Down move,
Return or Tab inserts ``@<name>`` (``accept``), Escape closes it and typing goes on as usual. Picking never selects
anything: the selection is scene state the agent reads, and a mention is only words.

Each pick is remembered by the item's ``session_uid`` (``controller.mention_picks``, the last ``MAX_PICKS``), so an
item renamed between the pick and Send is still the one meant. At Send (``console.submit``) every pick whose
``@<name>`` is still in the message becomes one ``mentioned_items`` entry of that request's observation: its kind,
current name, the name as written when it differs, and an object's handle (``scene_queries.object_handle``). An item
deleted since is said as ``missing``. Objects the message names literally are also flagged in the objects list by
``turn_context``, as before. A worker's chat works on its own copy of the document, so its picks are not resolved
(its names still are, literally).

Matching is case-insensitive after NFKC (``conversation_search.fold``); prefix matches come first. Bounded:
``MAX_SCAN`` names per kind, ``MAX_SHOWN`` rows. Main thread (``bpy`` is passed in). No bpy at import.
"""

import unicodedata

KINDS = ("OBJECT", "COLLECTION", "MATERIAL")
MAX_QUERY = 64
MAX_SCAN = 2000
MAX_SHOWN = 8
MAX_PICKS = 64
MAX_MENTIONED = 24


def fold(text):
    return unicodedata.normalize("NFKC", text).casefold()


def query_at(body, caret):
    """``(start, query)`` when the caret ends an ``@`` word (``@`` at the start or after whitespace, no whitespace
    after it, at most ``MAX_QUERY`` characters), else None. ``start`` is the ``@``'s index."""
    if type(body) is not str or not 0 < caret <= len(body):
        return None
    start = caret - 1
    while start >= 0 and caret - start <= MAX_QUERY + 1:
        char = body[start]
        if char == "@":
            if start == 0 or body[start - 1].isspace():
                return start, body[start + 1:caret]
            return None
        if char.isspace():
            return None
        start -= 1
    return None


def scene_items(bpy, scene):
    """``[(kind, name, uid)]`` of the scene's objects, its collections (nested) and the file's own materials."""
    items = []
    for obj in list(scene.objects)[:MAX_SCAN]:
        items.append(("OBJECT", obj.name, obj.session_uid))
    for collection in list(scene.collection.children_recursive)[:MAX_SCAN]:
        items.append(("COLLECTION", collection.name, collection.session_uid))
    for material in list(bpy.data.materials)[:MAX_SCAN]:
        if material.library is None:
            items.append(("MATERIAL", material.name, material.session_uid))
    return items


def candidates(items, query, limit=MAX_SHOWN):
    """The rows to show for ``query``: names starting with it first, then names containing it; each kind's order
    kept. An empty query lists the first items."""
    wanted = fold(query)
    starts, contains = [], []
    for item in items:
        name = fold(item[1])
        if name.startswith(wanted):
            starts.append(item)
        elif wanted in name:
            contains.append(item)
        if len(starts) >= limit:
            break
    return (starts + contains)[:limit]


def accept(body, caret, start, name):
    """``(body, caret)`` with the ``@query`` at ``start``..``caret`` replaced by ``@<name> ``."""
    inserted = "@" + name + " "
    return body[:start] + inserted + body[caret:], start + len(inserted)


def remember(controller, kind, name, uid):
    picks = getattr(controller, "mention_picks", None)
    if picks is None:
        picks = controller.mention_picks = {}
    key = "@" + name
    picks.pop(key, None)
    picks[key] = (kind, uid)
    while len(picks) > MAX_PICKS:
        del picks[next(iter(picks))]


def _find(bpy, scene, kind, uid):
    if kind == "OBJECT":
        pool = scene.objects
    elif kind == "COLLECTION":
        pool = scene.collection.children_recursive
    else:
        pool = bpy.data.materials
    return next((item for item in pool if item.session_uid == uid), None)


def _ends_word(message, end):
    """Whether a name written up to ``end`` ends there: the message ends, a space follows, or punctuation that no
    letter or digit follows ("@Cube." ends a sentence; "@Cube.001" and "@Cube-2" are other names, as Blender writes
    them)."""
    if end >= len(message) or message[end].isspace():
        return True
    if unicodedata.category(message[end])[0] != "P" or message[end] == "_":
        return False
    return end + 1 >= len(message) or not message[end + 1].isalnum()


def resolve(bpy, scene, message, picks, handle_of):
    """``mentioned_items`` for ``message``: each pick still written in it, in order of first appearance."""
    found = []
    for written, (kind, uid) in (picks or {}).items():
        at = message.find(written)
        if at < 0:
            continue
        if not _ends_word(message, at + len(written)):
            continue   # "@Cube" written inside "@Cube.001" or "@Cube_2": another name (audit F6).
        found.append((at, written, kind, uid))
    items = []
    for _, written, kind, uid in sorted(found)[:MAX_MENTIONED]:
        item = _find(bpy, scene, kind, uid)
        if item is None:
            items.append({"kind": kind, "as_written": written[1:], "missing": True})
            continue
        entry = {"kind": kind, "name": item.name}
        if item.name != written[1:]:
            entry["as_written"] = written[1:]
        if kind == "OBJECT":
            entry["handle"] = handle_of(item)
        elif kind == "COLLECTION":
            entry["objects"] = len(item.all_objects)
        items.append(entry)
    return items


def for_request(controller, message):
    """The request's ``mentioned_items`` (``console.submit``), or None; then the picks are spent. The scene is looked
    up only when something was picked, so a request without @ costs nothing here."""
    picks = getattr(controller, "mention_picks", None)
    if not picks:
        return None
    from . import document_scene
    scene = document_scene.scene_of(controller)
    if scene is None:
        controller.mention_picks = {}
        return None
    import bpy
    from . import scene_queries
    items = resolve(bpy, scene, message, picks, scene_queries.object_handle)
    controller.mention_picks = {}
    return items or None
