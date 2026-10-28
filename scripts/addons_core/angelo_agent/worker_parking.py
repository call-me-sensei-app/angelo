# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A message to a worker-hosted chat while it parks (design A′, ``worker_queue``): it waits for the park, then
relaunches the chat's worker as a parked chat's message does.

A park closes the chat in its worker before it saves (C10), so from the park's start the worker answers any op of
the chat that it hosts no such chat (``worker_chat_closing``). A Send in that window would be refused although the
chat is about to park. So a hosted chat that is parking takes the parked chat's route (``worker_queue.resume``): its
restart is queued ahead of parts as any restart, and starts only once the park ended (``startable``; the parts behind it go meanwhile):
parked, it relaunches; a park that ended without parking it (its save failed and is retried,
``worker_save_recovery``, or the chat merged, ended or stopped meanwhile) refuses the message with nothing sent,
never keeps it waiting on a retry. A chat whose worker runs its turns on a lane (``ANGELO_WORKER_CHAT_HOST=0``) is
never closed by its park: a message reaches it there, as before. Main thread; no bpy.
"""

from .display_text import Text
from .worker_reasons import NOT_OPEN

NOT_PARKED = Text(NOT_OPEN)   # A restart whose park ended without parking its chat: nothing was sent.


def waits(controller):
    """Whether a message to ``controller`` waits for its park: a hosted chat whose park is under way."""
    from . import worker_queue, worker_routing
    return controller.state["id"] in worker_queue._parking and worker_routing.hosted(controller)


def first(queue):
    """The first item of ``queue`` that may start now (``startable``), or None."""
    return next((item for item in queue if startable(item)), None)


def startable(item):
    """Whether queue ``item`` may start: a part, or a restart whose chat's park ended."""
    from . import worker_queue
    if item.get("kind") != worker_queue.RESUME:
        return True
    return item["session"] not in worker_queue._parking
