# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker-hosted chat's mirror lets go of its chat without the main thread waiting (design A′, C2).

``MirrorController.close`` (the chat closes, or the add-on ends) sends ``chat.close`` on a daemon thread of its own:
a close may wait up to ``worker_chat_lifecycle.CLOSE_SECONDS`` for a wedged worker, and Blender's window never waits
for it. When the add-on ends (``ui.unregister``, after every mirror's close), ``wait_closed`` waits for all of them
together within one bound, as ``worker_routing.stop_all`` waits for its workers, never one bound per chat. Nothing
here touches Blender data, and nothing runs from atexit. Standard library only and no ``bpy``.
"""

import threading
import time

ALL_SECONDS = 6.0   # The add-on's end waits this long in all for its mirrors' closes (each is bounded too).
_closes = []        # The close threads that may still run.


def closing(session):
    """``chat.close`` for ``session`` on a daemon thread; the thread, or None when nothing hosts the chat."""
    from . import worker_chat_lifecycle
    if worker_chat_lifecycle.hosted(session) is None:
        return None   # Already handed back (its worker stopped or closed it): nothing to send.

    def run():
        try:
            worker_chat_lifecycle.close_chat(session, "closed")
        except worker_chat_lifecycle.ChatOpFailed as error:
            print("Angelo: a worker chat could not be closed in its worker:", error)
        except Exception as error:   # noqa: BLE001 - a close thread's failure is logged, never lost.
            print("Angelo: a worker chat's close failed:", type(error).__name__, error)
    thread = threading.Thread(target=run, daemon=True, name="AngeloMirrorClose")
    _closes[:] = [item for item in _closes if item.is_alive()] + [thread]
    thread.start()
    return thread


def wait_closed(seconds=ALL_SECONDS):
    """Every close still running ends, or ``seconds`` pass in all (one deadline shared by every chat)."""
    deadline = time.monotonic() + seconds
    for thread in list(_closes):
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    _closes[:] = [item for item in _closes if item.is_alive()]
