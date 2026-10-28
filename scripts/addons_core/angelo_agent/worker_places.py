# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The places in use under the worker limit: one count that the start queue and the pool of workers launched ahead
both read (the maintainer's audit of 948d877: with a limit of two and two workers restarting, the queue saw no free
place while the pool saw two, and could launch Blenders past the person's limit). No bpy.

A place is in use for every distinct worker Blender, counted once whatever states it is in:
* started and not yet exited (``worker_process.live``): launching, switching to a new part, ready, idle ahead, and
  stopping, until its process has exited;
* bound to a worker chat that is not parked;
* a launch already decided and not begun: a part starting, a parked chat's worker restarting, a worker launched ahead.
"""


def occupied():
    from . import worker_pool, worker_process, worker_queue, worker_routing, worker_sessions
    used = set(worker_process.live())
    for session in worker_sessions._running():
        used.add(worker_routing._workers.get(session) or ("session", session))
    decided = list(worker_sessions._launching) + list(worker_queue._relaunching) + list(worker_pool._pool)
    used.update(entry["worker"] for entry in decided if entry.get("worker") is not None)
    return len(used)


def free():
    """Places a new worker may take now (negative when more run than the limit allows)."""
    from . import parallel_access
    return parallel_access.worker_limit() - occupied()
