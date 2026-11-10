# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Delete a conversation and bring it back, as the app does it: what may be deleted, and what moves with it.

``ui`` is the chat's module (``angelo_agent.ui``): its chat list (``_sessions``), open chats (``_controllers``),
drafts and the switch to another chat. No ``bpy`` here (``conversations_ui`` passes the module), so the rules are
host-tested with a stand-in.

Refused, with the reason (``blocker``), never stopped or cut short behind the person's back:

* a chat whose turn runs, or that waits for an approval or has queued tools (Stop or answer it first);
* a chat with a preview waiting for Apply or Discard (deleting would throw the person's preview away);
* a worker's chat whose worker still builds its part, and a lead whose workers are on the rail or still starting
  (parallel work reads and writes those chats);
* a chat a worker Blender writes right now (``sessions.HOSTED``).

A finished worker's chat may be deleted: its tile on the rail is dismissed first, as its own dismiss does. The chat
shown on screen is replaced first by the next most recent chat (its scene comes back, as any switch brings it) or, when
it is the only one, by a new chat; then the chat is closed (its last save) and moved to Recently Deleted
(``conversations``). The scene is never touched: a chat is bound to its document, the document never to the chat.
Its request snapshots stay with the snapshot policy (``conversations``).
"""

from . import conversations
from .ui_text import n_

RUNNING = n_("“{title}” is still working. Stop it, then delete it.")
WAITING = n_("“{title}” is waiting for your answer. Approve or decline it, then delete it.")
PREVIEW = n_("“{title}” has a preview waiting. Apply or discard it, then delete the chat.")
WORKER = n_("A worker is still building the part for “{title}”. Let it finish or stop it, then delete the chat.")
LEAD = n_("“{title}” leads parallel work. Close its workers' tiles, then delete it.")
HOSTED = n_("A worker is saving “{title}” right now. Try again when it has finished.")


def _busy(controller):
    worker = getattr(controller, "_worker", None)
    return bool(getattr(controller, "busy", False) or (worker is not None and worker.is_alive())
                or getattr(controller, "native_jobs", None) or getattr(controller, "delegate_jobs", None))


def blocker(ui, session_id):
    """The reason ``session_id`` cannot be deleted now (an ``n_`` template taking ``title``), or None."""
    from . import parallel_sessions, sessions, worker_sessions
    controller = ui._controllers.get(session_id)
    if controller is not None:
        if any(getattr(call, "approval", False) for call in getattr(controller, "pending", {}).values()):
            return WAITING
        if _busy(controller) or getattr(controller, "pending", None):
            return RUNNING
        if getattr(controller, "_native_preview_cards", None):
            return PREVIEW
    if sessions.HOSTED.get(session_id, (False, 0))[0]:
        return HOSTED
    if parallel_sessions.is_worker(session_id) and not parallel_sessions.ended(session_id):
        return WORKER
    leads = parallel_sessions.lead == session_id and parallel_sessions.workers()
    if leads or worker_sessions.workers_of(session_id) and any(
            not parallel_sessions.ended(worker) for worker in worker_sessions.workers_of(session_id)) \
            or any(item.get("lead") == session_id for item in list(worker_sessions._launching)):
        return LEAD
    return None


def next_chat(ui, session_id):
    """The chat shown after ``session_id`` is deleted: the most recent other one, or None (a new chat)."""
    return next((record["id"] for record in ui._sessions.ordered() if record["id"] != session_id), None)


def delete(ui, session_id, *, switch, dismiss_tile=None):
    """Delete ``session_id`` (``blocker`` must be None). ``switch(next_id or None)`` shows another chat when it is the
    one shown (None: a new chat); ``dismiss_tile(id)`` takes a finished worker's tile off the rail. Returns the
    Recently Deleted entry. Raises ``OSError`` when its files cannot move (the chat then stays in the list)."""
    from . import parallel_sessions
    collection = ui._sessions
    record = dict(collection.records[session_id])
    shown = collection.active == session_id
    if shown:
        switch(next_chat(ui, session_id))
    if dismiss_tile is not None and parallel_sessions.is_worker(session_id):
        dismiss_tile(session_id)
    controller = ui._controllers.pop(session_id, None)
    if controller is not None:
        controller.close()   # Its last save; nothing writes its file after this.
        collection.update(controller.state)
        record = dict(collection.records[session_id])
    # A move that fails raises with the chat still listed; closed, it is opened again when next shown
    # (``ui.get_controller``).
    entry = conversations.delete(ui.state_directory(), record, shown=shown)
    collection.records.pop(session_id, None)
    collection.save()
    ui._drafts.pop(session_id, None)
    search_index(ui).forget(session_id)
    return entry


def restore(ui, session_id, *, switch):
    """Bring ``session_id`` back from Recently Deleted and list it again; it is shown again when it was the chat shown
    when it was deleted (``switch(id)``). Returns its record."""
    directory = ui.state_directory()
    entry = conversations.restore(directory, session_id)
    collection = ui._sessions
    collection.records[session_id] = dict(entry["record"])
    collection.save()
    conversations.forget(directory, session_id)
    search_index(ui).forget(None)
    if entry["shown"]:
        switch(session_id)
    return entry["record"]


def settle(ui):
    """Finish a deletion or an Undo a crash cut short, and remove what expired (``conversations``)."""
    directory = ui.state_directory()
    collection = ui._sessions
    relisted = conversations.settle(directory, collection.records)
    for record in relisted:
        collection.records[record["id"]] = record
    if relisted:
        collection.save()
    conversations.purge(directory)


_indexes = {}


def search_index(ui):
    """The search index of ``ui``'s state folder, one per folder for the session (``conversation_search``)."""
    from .conversation_search import SearchIndex
    directory = str(ui.state_directory())
    if directory not in _indexes:
        _indexes[directory] = SearchIndex(directory)
    return _indexes[directory]
